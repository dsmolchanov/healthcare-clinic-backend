"""
Langfuse Integration for LLM Observability (v3 SDK)
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
    from langfuse import Langfuse, observe

    # Check if credentials are available
    LANGFUSE_ENABLED = bool(os.getenv("LANGFUSE_PUBLIC_KEY"))

    if LANGFUSE_ENABLED:
        langfuse_client = Langfuse(
            public_key=os.getenv("LANGFUSE_PUBLIC_KEY"),
            secret_key=os.getenv("LANGFUSE_SECRET_KEY"),
            host=os.getenv("LANGFUSE_HOST", "https://us.cloud.langfuse.com")
        )
        logger.info("✅ Langfuse LLM observability enabled (v3 SDK)")
    else:
        langfuse_client = None
        logger.info("Langfuse disabled (no LANGFUSE_PUBLIC_KEY set)")

except ImportError as e:
    logger.warning(f"Langfuse not installed: {e}. LLM observability disabled.")
    LANGFUSE_ENABLED = False
    langfuse_client = None
    observe = None


class LLMObservability:
    """
    Wraps all LLM calls with automatic tracing using Langfuse v3 API.
    Tracks cost, latency, and quality metrics for optimization.
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
        Wrap LLMSlotExtractor with Langfuse tracing using v3 API.
        """
        if not self.enabled or not langfuse_client:
            # Fallback: call directly without tracing
            return await llm_extractor.extract_slots(
                message=message,
                missing_slots=missing_slots,
                clinic_id=clinic_id
            )

        # Use v3 context manager API
        with langfuse_client.start_as_current_span(
            name="slot-extraction",
            metadata={
                "clinic_id": clinic_id,
                "fsm_state": fsm_state,
                "missing_slots": missing_slots,
                "session_id": session_id
            }
        ) as span:
            # Create generation observation
            with langfuse_client.start_as_current_observation(
                name="extract-slots",
                as_type="generation",
                metadata={
                    "model": os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
                    "missing_slots": missing_slots,
                    "message_length": len(message)
                }
            ) as generation:
                generation.update(input=message)

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

                    # Update generation with output
                    generation.update(
                        output=result,
                        metadata={
                            "slots_extracted": slots_extracted,
                            "extraction_count": len(slots_extracted),
                            "avg_confidence": avg_confidence,
                            "success": True
                        }
                    )

                    # Score the extraction quality
                    langfuse_client.score_current_trace(
                        name="extraction_confidence",
                        value=avg_confidence
                    )

                    return result

                except Exception as e:
                    # Log error to Langfuse
                    generation.update(
                        output=None,
                        metadata={
                            "error": str(e),
                            "success": False
                        }
                    )
                    raise

    def score_booking_outcome(
        self,
        session_id: str,
        success: bool,
        reason: Optional[str] = None,
        booking_id: Optional[str] = None
    ):
        """
        Score booking outcome for the current trace.
        """
        if not self.enabled or not langfuse_client:
            return

        try:
            langfuse_client.create_score(
                name="booking_success",
                value=1.0 if success else 0.0,
                comment=reason or ("Booking successful" if success else "Booking failed"),
                trace_id=session_id  # Use session_id as trace reference
            )

            logger.debug(f"Scored booking outcome: success={success}, session={session_id}")

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
        Track RAG query performance and quality using v3 API.
        """
        if not self.enabled or not langfuse_client:
            return

        try:
            with langfuse_client.start_as_current_span(
                name="rag-query",
                metadata={
                    "session_id": session_id,
                    "docs_retrieved": len(retrieved_docs),
                    "used_in_response": response_used_rag,
                    "doc_sources": [doc.get("source") for doc in retrieved_docs[:3]],
                    "avg_score": (
                        sum(doc.get("score", 0) for doc in retrieved_docs) / len(retrieved_docs)
                        if retrieved_docs else 0
                    )
                }
            ) as span:
                span.update(input=query)

                # Score RAG quality
                if retrieved_docs:
                    avg_score = sum(doc.get("score", 0) for doc in retrieved_docs) / len(retrieved_docs)
                    langfuse_client.score_current_span(
                        name="rag_relevance",
                        value=avg_score
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
    Decorator to automatically track LLM calls with Langfuse v3 API.

    Usage:
        @track_llm_call(name="intent-classification", model="gpt-4o-mini")
        async def classify_intent(message: str) -> str:
            # ... LLM call ...
    """
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            if not LANGFUSE_ENABLED or not langfuse_client:
                # If Langfuse not enabled, just call function
                return await func(*args, **kwargs)

            # Use v3 context manager
            with langfuse_client.start_as_current_observation(
                name=name,
                as_type="generation",
                metadata={
                    "model": model or os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
                    "function": func.__name__,
                    "args_count": len(args),
                    "kwargs_keys": list(kwargs.keys())
                }
            ) as generation:
                try:
                    result = await func(*args, **kwargs)

                    # Update with success
                    generation.update(
                        output=result,
                        metadata={"success": True}
                    )

                    return result

                except Exception as e:
                    # Update with error
                    generation.update(
                        output=None,
                        metadata={"error": str(e), "success": False}
                    )
                    raise

        return wrapper
    return decorator


# Utility function to flush Langfuse events on shutdown
async def flush_langfuse():
    """
    Flush all pending Langfuse events to ensure they're sent before shutdown.
    """
    if LANGFUSE_ENABLED and langfuse_client:
        try:
            langfuse_client.flush()
            logger.info("✅ Langfuse events flushed")
        except Exception as e:
            logger.warning(f"Failed to flush Langfuse: {e}")
