"""
Langfuse Integration for LLM Observability
Tracks: cost per booking, prompt effectiveness, RAG quality, slot extraction accuracy
"""

import os
import logging
from typing import Dict, List, Optional, Any
from functools import wraps
import asyncio

logger = logging.getLogger(__name__)

# Initialize Langfuse client
try:
    from langfuse import Langfuse
    from langfuse.decorators import observe, langfuse_context

    langfuse_client = Langfuse(
        public_key=os.getenv("LANGFUSE_PUBLIC_KEY"),
        secret_key=os.getenv("LANGFUSE_SECRET_KEY"),
        host=os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com")
    )

    LANGFUSE_ENABLED = bool(os.getenv("LANGFUSE_PUBLIC_KEY"))

    if LANGFUSE_ENABLED:
        logger.info("✅ Langfuse LLM observability enabled")
    else:
        logger.info("Langfuse disabled (no LANGFUSE_PUBLIC_KEY set)")

except ImportError:
    logger.warning("Langfuse not installed. LLM observability disabled.")
    LANGFUSE_ENABLED = False
    langfuse_client = None


class LLMObservability:
    """
    Wraps all LLM calls with automatic tracing
    Tracks cost, latency, and quality metrics for optimization
    """

    def __init__(self):
        self.enabled = LANGFUSE_ENABLED

    async def extract_slots_with_tracing(
        self,
        message: str,
        missing_slots: List[str],
        clinic_id: str,
        session_id: str,
        fsm_state: str,
        llm_extractor: Any
    ) -> Dict:
        """
        Wrap LLMSlotExtractor with Langfuse tracing

        Args:
            message: User message text
            missing_slots: Slots to extract
            clinic_id: Clinic ID for context
            session_id: Session/conversation ID
            fsm_state: Current FSM state
            llm_extractor: Instance of LLMSlotExtractor

        Returns:
            Dictionary of extracted slots with confidence scores
        """
        if not self.enabled:
            # Fallback: call directly without tracing
            return await llm_extractor.extract_slots(
                message=message,
                missing_slots=missing_slots,
                clinic_id=clinic_id
            )

        # Create trace for this extraction
        trace = langfuse_client.trace(
            name="slot-extraction",
            session_id=session_id,
            metadata={
                "clinic_id": clinic_id,
                "fsm_state": fsm_state,
                "missing_slots": missing_slots
            }
        )

        # Create generation span
        generation = trace.generation(
            name="extract-slots",
            model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
            input=message,
            metadata={
                "missing_slots": missing_slots,
                "message_length": len(message)
            }
        )

        try:
            # Call actual LLM extraction
            result = await llm_extractor.extract_slots(
                message=message,
                missing_slots=missing_slots,
                clinic_id=clinic_id
            )

            # Extract metrics
            slots_extracted = list(result.keys())
            avg_confidence = (
                sum(s.get('confidence', 0) for s in result.values()) / len(result)
                if result else 0
            )

            # End generation with success metrics
            generation.end(
                output=result,
                metadata={
                    "slots_extracted": slots_extracted,
                    "extraction_count": len(slots_extracted),
                    "avg_confidence": avg_confidence,
                    "success": True
                }
            )

            # Score the extraction quality
            trace.score(
                name="extraction_confidence",
                value=avg_confidence
            )

            return result

        except Exception as e:
            # Log error to Langfuse
            generation.end(
                output=None,
                metadata={
                    "error": str(e),
                    "success": False
                }
            )

            # Re-raise for normal error handling
            raise

    def score_booking_outcome(
        self,
        session_id: str,
        success: bool,
        reason: Optional[str] = None,
        booking_id: Optional[str] = None
    ):
        """
        Link booking outcome to all LLM traces in session

        This allows us to track which LLM interactions led to successful bookings
        vs failures/dropoffs.

        Args:
            session_id: Session/conversation ID
            success: Whether booking succeeded
            reason: Failure reason if applicable
            booking_id: Created appointment ID if successful
        """
        if not self.enabled:
            return

        try:
            # Update trace with booking outcome
            langfuse_client.score(
                name="booking_success",
                value=1.0 if success else 0.0,
                trace_id=session_id,
                comment=reason or ("Booking successful" if success else "Booking failed")
            )

            if booking_id:
                # Add booking ID to trace metadata
                langfuse_client.trace(
                    id=session_id,
                    metadata={"booking_id": booking_id}
                )

        except Exception as e:
            logger.warning(f"Failed to score booking outcome: {e}")
            # Don't fail the booking flow for observability errors

    def track_rag_query(
        self,
        session_id: str,
        query: str,
        retrieved_docs: List[Dict],
        response_used_rag: bool
    ):
        """
        Track RAG query performance and quality

        Args:
            session_id: Session/conversation ID
            query: RAG query text
            retrieved_docs: Documents retrieved from vector search
            response_used_rag: Whether the response actually used RAG context
        """
        if not self.enabled:
            return

        try:
            trace = langfuse_client.trace(
                name="rag-query",
                session_id=session_id,
                input=query,
                output={
                    "docs_retrieved": len(retrieved_docs),
                    "used_in_response": response_used_rag
                },
                metadata={
                    "doc_sources": [doc.get("source") for doc in retrieved_docs[:3]],
                    "avg_score": (
                        sum(doc.get("score", 0) for doc in retrieved_docs) / len(retrieved_docs)
                        if retrieved_docs else 0
                    )
                }
            )

            # Score RAG quality
            if retrieved_docs:
                trace.score(
                    name="rag_relevance",
                    value=sum(doc.get("score", 0) for doc in retrieved_docs) / len(retrieved_docs)
                )

        except Exception as e:
            logger.warning(f"Failed to track RAG query: {e}")


# Global instance for easy access
llm_observability = LLMObservability()


def track_llm_call(
    name: str,
    model: Optional[str] = None
):
    """
    Decorator to automatically track LLM calls with Langfuse

    Usage:
        @track_llm_call(name="intent-classification", model="gpt-4o-mini")
        async def classify_intent(message: str) -> str:
            # ... LLM call ...
    """
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            if not LANGFUSE_ENABLED:
                # If Langfuse not enabled, just call function
                return await func(*args, **kwargs)

            # Create trace
            generation = langfuse_client.generation(
                name=name,
                model=model or os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
                metadata={
                    "function": func.__name__,
                    "args_count": len(args),
                    "kwargs_keys": list(kwargs.keys())
                }
            )

            try:
                result = await func(*args, **kwargs)

                # End with success
                generation.end(
                    output=result,
                    metadata={"success": True}
                )

                return result

            except Exception as e:
                # End with error
                generation.end(
                    output=None,
                    metadata={"error": str(e), "success": False}
                )
                raise

        return wrapper
    return decorator


# Utility function to flush Langfuse events on shutdown
async def flush_langfuse():
    """
    Flush all pending Langfuse events to ensure they're sent before shutdown
    """
    if LANGFUSE_ENABLED and langfuse_client:
        try:
            langfuse_client.flush()
            logger.info("✅ Langfuse events flushed")
        except Exception as e:
            logger.warning(f"Failed to flush Langfuse: {e}")
