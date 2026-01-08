"""
FSM Voice Adapter - Bridges FSM Orchestrator to LiveKit Agents.

Phase 6: Updated to use FSM orchestrator (legacy LangGraph removed).

This module provides a unified interface for voice services to use the same
FSM-based orchestration as text channels.

Usage:
    adapter = LangGraphVoiceAdapter(clinic_id="clinic123")
    async for chunk in adapter.stream_response(message, session_id, metadata):
        yield chunk

Reference: https://docs.livekit.io/agents/models/llm/plugins/langchain/
"""

import asyncio
import logging
from dataclasses import dataclass, field
from typing import AsyncIterator, Dict, Any, Optional, List
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


@dataclass
class StreamingResponse:
    """
    Streaming response chunk.

    Attributes:
        content: Text content of this chunk
        is_final: True if this is the last chunk
        node_name: Which node generated this chunk (for debugging)
        metadata: Additional metadata
    """
    content: str
    is_final: bool = False
    node_name: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


class LangGraphVoiceAdapter:
    """
    Adapts FSM Orchestrator for voice services.

    Phase 6: Now uses FSM orchestrator (legacy LangGraph removed).
    Note: FSM doesn't support token streaming, so this adapter simulates
    streaming by yielding the complete response as chunks.

    Usage:
        adapter = LangGraphVoiceAdapter(clinic_id="clinic123")

        # Stream response (yields complete response as chunks)
        async for chunk in adapter.stream_response(message, session_id, metadata):
            send_to_tts(chunk.content)
    """

    def __init__(
        self,
        clinic_id: str,
        supabase_client: Optional[Any] = None,
        agent_config: Optional[Dict[str, Any]] = None,
    ):
        """
        Initialize the voice adapter.

        Args:
            clinic_id: Clinic identifier for context
            supabase_client: Optional Supabase client (lazy-loaded if not provided)
            agent_config: Optional agent configuration
        """
        self.clinic_id = clinic_id
        self._supabase_client = supabase_client
        self._agent_config = agent_config
        self._orchestrator = None  # Lazy-loaded
        self._fsm_states: Dict[str, Dict[str, Any]] = {}  # Session state storage

    async def _get_orchestrator(self):
        """Lazy-load the FSM orchestrator instance."""
        if self._orchestrator is None:
            from app.services.orchestrator.fsm_orchestrator import FSMOrchestrator
            from app.services.llm import LLMFactory

            # Get supabase client if not provided
            if self._supabase_client is None:
                try:
                    from app.db.supabase_client import get_supabase_client
                    self._supabase_client = get_supabase_client()
                except Exception as e:
                    logger.warning(f"[voice-adapter] Could not get supabase client: {e}")

            llm_factory = LLMFactory(supabase_client=self._supabase_client)

            self._orchestrator = FSMOrchestrator(
                clinic_id=self.clinic_id,
                llm_factory=llm_factory,
                supabase_client=self._supabase_client,
                appointment_tools=None,  # Will be lazy-loaded by orchestrator
                price_tool=None,
                clinic_profile={},
            )
            logger.info(f"[voice-adapter] Initialized FSM orchestrator for clinic {self.clinic_id}")

        return self._orchestrator

    async def stream_response(
        self,
        message: str,
        session_id: str,
        metadata: Optional[Dict[str, Any]] = None,
        patient_id: Optional[str] = None,
    ) -> AsyncIterator[StreamingResponse]:
        """
        Stream FSM response.

        Note: FSM doesn't support token streaming like LangGraph, so this
        yields the complete response as a single chunk. For voice applications,
        consider using TTS streaming on the output side.

        Args:
            message: User message text
            session_id: Session identifier
            metadata: Additional metadata (participant info, etc.)
            patient_id: Optional patient ID (falls back to extracting from metadata)

        Yields:
            StreamingResponse chunks with token content
        """
        metadata = metadata or {}
        start_time = datetime.now(timezone.utc)

        # Resolve patient_id
        if patient_id is None:
            patient_id = (
                metadata.get("participant_id") or
                metadata.get("patient_id") or
                metadata.get("from_phone", "").replace("+", "") or
                session_id
            )

        logger.info(
            f"[voice-adapter] Processing message for session {session_id}"
        )

        try:
            # Get response from FSM orchestrator
            result = await self.get_response(message, session_id, metadata, patient_id)

            response_text = result.get("response", "")
            total_time_ms = (datetime.now(timezone.utc) - start_time).total_seconds() * 1000

            logger.info(
                f"[voice-adapter] Completed in {total_time_ms:.0f}ms"
            )

            # Yield the complete response
            # For voice applications, TTS will handle streaming on the output side
            yield StreamingResponse(
                content=response_text,
                is_final=True,
                node_name="fsm",
                metadata={
                    "total_time_ms": total_time_ms,
                    "route": result.get("route"),
                    "tools_called": result.get("tools_called", []),
                },
            )

        except Exception as e:
            logger.error(f"[voice-adapter] Processing failed: {e}", exc_info=True)
            yield StreamingResponse(
                content="I apologize, but I'm having trouble processing your request. Please try again.",
                is_final=True,
                node_name="error",
                metadata={"error": str(e)},
            )

    async def get_response(
        self,
        message: str,
        session_id: str,
        metadata: Optional[Dict[str, Any]] = None,
        patient_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Get full response from FSM orchestrator (non-streaming).

        Args:
            message: User message text
            session_id: Session identifier
            metadata: Additional metadata
            patient_id: Optional patient ID

        Returns:
            Dict with response, route, tools_called, etc.
        """
        metadata = metadata or {}

        # Resolve patient_id
        if patient_id is None:
            patient_id = (
                metadata.get("participant_id") or
                metadata.get("patient_id") or
                metadata.get("from_phone", "").replace("+", "") or
                session_id
            )

        # Get orchestrator instance
        orchestrator = await self._get_orchestrator()

        # Get FSM state for this session
        fsm_state = self._fsm_states.get(session_id)

        # Detect language from metadata
        language = metadata.get("language", metadata.get("detected_language", "en"))

        # Process through FSM
        result = await orchestrator.process(
            message=message,
            session_id=session_id,
            state=fsm_state,
            language=language,
        )

        # Save updated state
        if result.get("state"):
            self._fsm_states[session_id] = result["state"]

        return result

    async def close(self):
        """Clean up resources."""
        if self._orchestrator is not None:
            self._orchestrator = None
            self._fsm_states.clear()
            logger.info(f"[voice-adapter] Closed adapter for clinic {self.clinic_id}")


# Factory function for easier instantiation
async def create_voice_adapter(
    clinic_id: str,
    supabase_client: Optional[Any] = None,
) -> LangGraphVoiceAdapter:
    """
    Create a voice adapter for a clinic.

    Args:
        clinic_id: Clinic identifier
        supabase_client: Optional Supabase client

    Returns:
        Configured LangGraphVoiceAdapter
    """
    adapter = LangGraphVoiceAdapter(
        clinic_id=clinic_id,
        supabase_client=supabase_client,
    )
    # Pre-initialize the orchestrator
    await adapter._get_orchestrator()
    return adapter
