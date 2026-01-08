"""
FSM Orchestrator Service API Endpoint
FastAPI service for FSM-based conversation processing
Achieves <500ms response times for text messages

Phase 6: Uses pure FSM orchestrator (legacy LangGraph removed).
"""

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from typing import Optional, Dict, Any, List
import time
import logging
import asyncio
from datetime import datetime

# Initialize logger first
logger = logging.getLogger(__name__)

# Import FSM orchestrator
try:
    from app.services.orchestrator.fsm_orchestrator import FSMOrchestrator
    from app.services.llm import LLMFactory
    fsm_available = True
except ImportError as e:
    logger.warning(f"FSM Orchestrator not available: {e}")
    fsm_available = False
    FSMOrchestrator = None
    LLMFactory = None

router = APIRouter(prefix="/langgraph", tags=["langgraph"])


class MessageRequest(BaseModel):
    """Request model for FSM processing"""
    session_id: str
    text: str
    metadata: Optional[Dict[str, Any]] = {}
    clinic_id: Optional[str] = None
    language: str = "en"


class MessageResponse(BaseModel):
    """Response model for FSM processing"""
    session_id: str
    response: str
    latency_ms: float
    intent: Optional[str] = None
    routing_path: str = "fsm_direct"
    metadata: Dict[str, Any] = {}
    audit_trail: Optional[List[Dict[str, Any]]] = None


# State manager for session persistence
class SessionStateManager:
    """Simple in-memory session state manager"""

    def __init__(self):
        self.sessions = {}
        self.session_metrics = {}
        self.fsm_states = {}  # FSM state persistence

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

    def get_fsm_state(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Get FSM state for session"""
        return self.fsm_states.get(session_id)

    def set_fsm_state(self, session_id: str, fsm_state: Dict[str, Any]):
        """Set FSM state for session"""
        self.fsm_states[session_id] = fsm_state

    def track_metric(self, session_id: str, latency_ms: float):
        """Track session metrics"""
        if session_id not in self.session_metrics:
            self.session_metrics[session_id] = []
        self.session_metrics[session_id].append(latency_ms)


# Global state manager
state_manager = SessionStateManager()

# Orchestrator cache
_orchestrator_cache: Dict[str, FSMOrchestrator] = {}


def get_orchestrator(clinic_id: str) -> Optional[FSMOrchestrator]:
    """Get or create FSM orchestrator for clinic"""
    if not fsm_available:
        return None

    if clinic_id not in _orchestrator_cache:
        try:
            llm_factory = LLMFactory()
            _orchestrator_cache[clinic_id] = FSMOrchestrator(
                clinic_id=clinic_id,
                llm_factory=llm_factory,
                supabase_client=None,
                appointment_tools=None,
                price_tool=None,
                clinic_profile={},
            )
            logger.info(f"FSM orchestrator created for clinic {clinic_id}")
        except Exception as e:
            logger.error(f"Failed to create FSM orchestrator: {e}")
            return None

    return _orchestrator_cache[clinic_id]


@router.post("/process", response_model=MessageResponse)
async def process_message(request: MessageRequest):
    """
    Process message through FSM orchestrator
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

        # Get orchestrator
        clinic_id = request.clinic_id or "default"
        orchestrator = get_orchestrator(clinic_id)

        if not orchestrator:
            return MessageResponse(
                session_id=request.session_id,
                response="FSM service is currently unavailable. Please try again later.",
                latency_ms=(time.perf_counter() - start_time) * 1000,
                routing_path="fsm_direct",
                metadata={"error": "FSM orchestrator not available"}
            )

        # Get FSM state for multi-turn continuity
        fsm_state = state_manager.get_fsm_state(request.session_id)

        # Process through FSM
        try:
            result = await asyncio.wait_for(
                orchestrator.process(
                    message=request.text,
                    session_id=request.session_id,
                    state=fsm_state,
                    language=request.language,
                ),
                timeout=2.0  # 2 second timeout for fast responses
            )
        except asyncio.TimeoutError:
            logger.warning(f"FSM timeout for session {request.session_id}")
            result = {
                "session_id": request.session_id,
                "response": "I'm still processing your request. Please wait a moment.",
                "error": "timeout"
            }

        # Save FSM state for next turn
        if result.get("state"):
            state_manager.set_fsm_state(request.session_id, result["state"])

        # Calculate latency
        latency_ms = (time.perf_counter() - start_time) * 1000

        # Track metrics
        state_manager.track_metric(request.session_id, latency_ms)

        # Update session history
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
            intent=result.get("route"),
            routing_path="fsm_direct",
            metadata={
                "message_count": session_state["message_count"],
                "tools_called": result.get("tools_called", []),
                "route": result.get("route"),
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
            routing_path="fsm_direct",
            metadata={"error": str(e)}
        )


@router.get("/health")
async def health_check():
    """Health check endpoint for FSM service"""
    return {
        "status": "healthy",
        "service": "fsm",
        "orchestrators": list(_orchestrator_cache.keys()),
        "sessions_active": len(state_manager.sessions)
    }


@router.get("/metrics")
async def get_metrics():
    """Get performance metrics for FSM service"""
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

    if session_id in state_manager.fsm_states:
        del state_manager.fsm_states[session_id]

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
                routing_path="fsm_direct",
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
