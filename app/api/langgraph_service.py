"""
LangGraph Service API Endpoint
FastAPI service for direct LangGraph processing
Achieves <500ms response times for text messages
"""

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from typing import Optional, Dict, Any, List
import time
import logging
import asyncio
from datetime import datetime
import os
import sys

# Initialize logger first
logger = logging.getLogger(__name__)

# Import orchestrators from the copied location
try:
    from app.services.orchestrator.base_langgraph import BaseLangGraphOrchestrator
    from app.services.orchestrator.templates.healthcare_template import HealthcareLangGraph
    from app.services.orchestrator.templates.general_template import GeneralLangGraph
    langgraph_available = True
except ImportError as e:
    logger.warning(f"LangGraph not available: {e}")
    langgraph_available = False
    BaseLangGraphOrchestrator = None
    HealthcareLangGraph = None
    GeneralLangGraph = None

router = APIRouter(prefix="/langgraph", tags=["langgraph"])


class MessageRequest(BaseModel):
    """Request model for LangGraph processing"""
    session_id: str
    text: str
    metadata: Optional[Dict[str, Any]] = {}
    use_healthcare: bool = True  # Default to healthcare for clinics
    enable_rag: bool = True
    enable_memory: bool = True


class MessageResponse(BaseModel):
    """Response model for LangGraph processing"""
    session_id: str
    response: str
    latency_ms: float
    intent: Optional[str] = None
    routing_path: str = "langgraph_direct"
    metadata: Dict[str, Any] = {}
    audit_trail: Optional[List[Dict[str, Any]]] = None


# Initialize orchestrators
orchestrators = {}

if langgraph_available:
    # Healthcare orchestrator for medical conversations
    if HealthcareLangGraph:
        try:
            orchestrators["healthcare"] = HealthcareLangGraph(
                phi_middleware=None,  # Would integrate with actual PHI service
                appointment_service=None,  # Would integrate with appointment service
                enable_emergency_detection=True
            )
            logger.info("Healthcare orchestrator initialized")
        except Exception as e:
            logger.error(f"Failed to initialize healthcare orchestrator: {e}")

    # General orchestrator for non-medical conversations
    if GeneralLangGraph:
        try:
            orchestrators["general"] = GeneralLangGraph(
                llm_client=None,  # Would integrate with LLM service
                response_style="friendly",
                enable_suggestions=True
            )
            logger.info("General orchestrator initialized")
        except Exception as e:
            logger.error(f"Failed to initialize general orchestrator: {e}")
            # Fallback to base
            if BaseLangGraphOrchestrator:
                try:
                    orchestrators["general"] = BaseLangGraphOrchestrator(
                        compliance_mode=None,
                        enable_memory=True,
                        enable_rag=True
                    )
                    logger.info("Using base orchestrator as fallback")
                except Exception as e2:
                    logger.error(f"Failed to initialize base orchestrator: {e2}")
else:
    logger.warning("LangGraph not available - orchestrators disabled")


# State manager for session persistence
class SessionStateManager:
    """Simple in-memory session state manager"""

    def __init__(self):
        self.sessions = {}
        self.session_metrics = {}

    async def get_or_create(self, session_id: str) -> Dict[str, Any]:
        """Get or create session state"""
        if session_id not in self.sessions:
            self.sessions[session_id] = {
                "created_at": datetime.utcnow().isoformat(),
                "message_count": 0,
                "context": {},
                "history": []
            }
        return self.sessions[session_id]

    async def update(self, session_id: str, state: Dict[str, Any]):
        """Update session state"""
        self.sessions[session_id] = state
        self.sessions[session_id]["updated_at"] = datetime.utcnow().isoformat()

    def track_metric(self, session_id: str, latency_ms: float):
        """Track session metrics"""
        if session_id not in self.session_metrics:
            self.session_metrics[session_id] = []
        self.session_metrics[session_id].append(latency_ms)


# Global state manager
state_manager = SessionStateManager()


@router.post("/process", response_model=MessageResponse)
async def process_message(request: MessageRequest):
    """
    Process message through LangGraph orchestrator
    Target: <500ms response time for text

    Args:
        request: Message request with text and metadata

    Returns:
        Processed response with latency metrics
    """
    start_time = time.perf_counter()

    try:
        # Get or create session state
        session_state = await state_manager.get_or_create(request.session_id)
        session_state["message_count"] += 1

        # Check if orchestrators are available
        if not orchestrators:
            return MessageResponse(
                session_id=request.session_id,
                response="LangGraph service is currently unavailable. Please try again later.",
                latency_ms=(time.perf_counter() - start_time) * 1000,
                routing_path="langgraph_direct",
                metadata={"error": "LangGraph not installed"}
            )

        # Select orchestrator based on request
        if request.use_healthcare and "healthcare" in orchestrators:
            orchestrator = orchestrators["healthcare"]
            logger.debug(f"Using healthcare orchestrator for session {request.session_id}")
        elif "general" in orchestrators:
            orchestrator = orchestrators["general"]
            logger.debug(f"Using general orchestrator for session {request.session_id}")
        else:
            return MessageResponse(
                session_id=request.session_id,
                response="No orchestrators available. Please check system configuration.",
                latency_ms=(time.perf_counter() - start_time) * 1000,
                routing_path="langgraph_direct",
                metadata={"error": "No orchestrators configured"}
            )

        # Add context from session state
        enhanced_metadata = {
            **request.metadata,
            "message_count": session_state["message_count"],
            "session_context": session_state.get("context", {})
        }

        # Process through LangGraph
        try:
            result = await asyncio.wait_for(
                orchestrator.process(
                    message=request.text,
                    session_id=request.session_id,
                    metadata=enhanced_metadata
                ),
                timeout=2.0  # 2 second timeout for fast responses
            )
        except asyncio.TimeoutError:
            logger.warning(f"LangGraph timeout for session {request.session_id}")
            result = {
                "session_id": request.session_id,
                "response": "I'm still processing your request. Please wait a moment.",
                "error": "timeout"
            }

        # Calculate latency
        latency_ms = (time.perf_counter() - start_time) * 1000

        # Track metrics
        state_manager.track_metric(request.session_id, latency_ms)

        # Update session state with response context
        if "context" in result:
            session_state["context"].update(result["context"])

        session_state["history"].append({
            "timestamp": datetime.utcnow().isoformat(),
            "message": request.text,
            "response": result.get("response", ""),
            "latency_ms": latency_ms
        })

        await state_manager.update(request.session_id, session_state)

        # Log performance
        if latency_ms > 500:
            logger.warning(f"High latency: {latency_ms:.2f}ms for session {request.session_id}")
        else:
            logger.info(f"Processed in {latency_ms:.2f}ms for session {request.session_id}")

        # Prepare response
        response = MessageResponse(
            session_id=request.session_id,
            response=result.get("response", "I'm here to help. Please tell me more."),
            latency_ms=latency_ms,
            intent=result.get("intent"),
            routing_path="langgraph_direct",
            metadata={
                "message_count": session_state["message_count"],
                "orchestrator": "healthcare" if request.use_healthcare else "general"
            },
            audit_trail=result.get("audit_trail", [])
        )

        return response

    except Exception as e:
        latency_ms = (time.perf_counter() - start_time) * 1000
        logger.error(f"Error processing message: {e}")

        return MessageResponse(
            session_id=request.session_id,
            response="I encountered an error processing your message. Please try again.",
            latency_ms=latency_ms,
            routing_path="langgraph_direct",
            metadata={"error": str(e)}
        )


@router.get("/health")
async def health_check():
    """Health check endpoint for LangGraph service"""
    return {
        "status": "healthy",
        "service": "langgraph",
        "orchestrators": list(orchestrators.keys()),
        "sessions_active": len(state_manager.sessions)
    }


@router.get("/metrics")
async def get_metrics():
    """Get performance metrics for LangGraph service"""
    all_latencies = []
    for session_latencies in state_manager.session_metrics.values():
        all_latencies.extend(session_latencies)

    if not all_latencies:
        return {
            "status": "no_data",
            "sessions": 0
        }

    # Calculate statistics
    avg_latency = sum(all_latencies) / len(all_latencies)
    sorted_latencies = sorted(all_latencies)
    p50 = sorted_latencies[len(sorted_latencies) // 2]
    p95_index = int(len(sorted_latencies) * 0.95)
    p95 = sorted_latencies[min(p95_index, len(sorted_latencies) - 1)]
    p99_index = int(len(sorted_latencies) * 0.99)
    p99 = sorted_latencies[min(p99_index, len(sorted_latencies) - 1)]

    # Count how many meet target
    under_500ms = sum(1 for l in all_latencies if l < 500)
    target_percentage = (under_500ms / len(all_latencies)) * 100

    return {
        "status": "ok",
        "sessions": len(state_manager.sessions),
        "total_messages": len(all_latencies),
        "latency": {
            "avg_ms": round(avg_latency, 2),
            "p50_ms": round(p50, 2),
            "p95_ms": round(p95, 2),
            "p99_ms": round(p99, 2),
            "min_ms": round(min(all_latencies), 2),
            "max_ms": round(max(all_latencies), 2)
        },
        "target_performance": {
            "target_ms": 500,
            "meeting_target_pct": round(target_percentage, 2),
            "messages_under_target": under_500ms
        }
    }


@router.delete("/session/{session_id}")
async def clear_session(session_id: str):
    """Clear a specific session"""
    if session_id in state_manager.sessions:
        del state_manager.sessions[session_id]

    if session_id in state_manager.session_metrics:
        del state_manager.session_metrics[session_id]

    return {"status": "cleared", "session_id": session_id}


@router.post("/batch")
async def process_batch(messages: List[MessageRequest]):
    """
    Process multiple messages in parallel
    Useful for testing and bulk operations

    Args:
        messages: List of message requests

    Returns:
        List of responses with timing information
    """
    start_time = time.perf_counter()

    # Process all messages in parallel
    tasks = [process_message(msg) for msg in messages]
    responses = await asyncio.gather(*tasks, return_exceptions=True)

    # Handle any exceptions
    processed_responses = []
    for i, response in enumerate(responses):
        if isinstance(response, Exception):
            logger.error(f"Batch processing error for message {i}: {response}")
            processed_responses.append(MessageResponse(
                session_id=messages[i].session_id,
                response="Error processing message",
                latency_ms=0,
                routing_path="langgraph_direct",
                metadata={"error": str(response)}
            ))
        else:
            processed_responses.append(response)

    total_time_ms = (time.perf_counter() - start_time) * 1000

    return {
        "responses": processed_responses,
        "batch_metrics": {
            "total_messages": len(messages),
            "total_time_ms": round(total_time_ms, 2),
            "avg_time_per_message_ms": round(total_time_ms / len(messages), 2)
        }
    }


# Export router for inclusion in main app
__all__ = ["router"]