"""
Unified State Manager for Conversation State.

Phase 3A of Agentic Flow Architecture Refactor.

This module provides a unified interface to conversation state.
It wraps the underlying session storage to present a consistent ConversationState view.

NOTE: FSM system removed in Phase 1.3 cleanup. All state management
now uses the AI path via session data.

NOTE: This class only tracks state. Tool permissions are enforced
by ToolStateGate reading from x_meta (Phase 1A).
"""

import logging
from typing import Optional, Dict, Any
from datetime import datetime, timezone

from .state_model import ConversationState, FlowState, TurnStatus

logger = logging.getLogger(__name__)


class UnifiedStateManager:
    """
    Unified access to conversation state.

    This manager provides consistent state access via session data.

    Usage:
        manager = UnifiedStateManager(
            session_id="...",
            clinic_id="...",
            redis_client=redis,
        )
        state = await manager.get_state()
        if state.allows_booking_tools():
            # Proceed with booking
    """

    def __init__(
        self,
        session_id: str,
        clinic_id: str,
        redis_client,
        supabase_client=None,
        use_fsm: bool = False,  # Deprecated, kept for API compatibility
    ):
        """
        Initialize UnifiedStateManager.

        Args:
            session_id: Conversation session identifier
            clinic_id: Clinic identifier
            redis_client: Redis client for session data
            supabase_client: Supabase client (optional)
            use_fsm: Deprecated parameter, ignored (FSM removed in Phase 1.3)
        """
        self.session_id = session_id
        self.clinic_id = clinic_id
        self.redis = redis_client
        self.supabase = supabase_client

        if use_fsm:
            logger.warning("use_fsm=True is deprecated - FSM removed in Phase 1.3")

    def _get_session_key(self, phone: str = None) -> str:
        """Get Redis key for session data."""
        # Try multiple key patterns used in the codebase
        if phone:
            return f"session:{self.clinic_id}:{phone}"
        return f"session:{self.session_id}"

    async def get_state(self, session_data: Optional[Dict[str, Any]] = None) -> ConversationState:
        """
        Get current composite conversation state.

        Args:
            session_data: Optional pre-loaded session data (avoids Redis lookup)

        Returns:
            ConversationState with flow_state and turn_status
        """
        return await self._get_session_state(session_data)

    async def _get_session_state(self, session_data: Optional[Dict[str, Any]] = None) -> ConversationState:
        """
        Get state from AI path session.

        Infers FlowState from session data (episode_type, etc.)
        """
        if session_data is None:
            session_data = await self._load_session_data()

        return ConversationState.from_session(session_data)

    async def _load_session_data(self) -> Dict[str, Any]:
        """Load session data from Redis."""
        try:
            # Try session ID key
            key = f"session:{self.session_id}"
            data = self.redis.hgetall(key)

            if data:
                return self._decode_redis_data(data)

            # Try clinic-prefixed key pattern
            # This would require phone number, which we might not have
            # Return empty dict to trigger defaults
            return {}

        except Exception as e:
            logger.warning(f"Error loading session data: {e}")
            return {}

    def _decode_redis_data(self, data: Dict[bytes, bytes]) -> Dict[str, Any]:
        """Decode Redis hash data (handles bytes keys/values)."""
        result = {}
        for k, v in data.items():
            key = k.decode('utf-8') if isinstance(k, bytes) else k
            value = v.decode('utf-8') if isinstance(v, bytes) else v

            # Try to parse JSON values
            if isinstance(value, str):
                try:
                    import json
                    result[key] = json.loads(value)
                except (json.JSONDecodeError, TypeError):
                    result[key] = value
            else:
                result[key] = value

        return result

    def _parse_datetime(self, value: Optional[str]) -> Optional[datetime]:
        """Parse ISO datetime string."""
        if not value:
            return None
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            return None

    async def update_turn_status(
        self,
        status: TurnStatus,
        action: Optional[str] = None
    ):
        """
        Update turn status (e.g., when agent promises followup).

        Args:
            status: New turn status
            action: Description of pending action (for agent_action_pending)
        """
        try:
            key = f"session:{self.session_id}"
            updates = {
                'turn_status': status.value,
            }

            if action:
                updates['last_agent_action'] = action
                updates['pending_since'] = datetime.now(timezone.utc).isoformat()

            # Update Redis hash
            self.redis.hset(key, mapping=updates)

            logger.info(f"Updated turn status to {status.value} for session {self.session_id}")

        except Exception as e:
            logger.error(f"Error updating turn status: {e}")

    async def update_flow_state(
        self,
        state: FlowState,
        episode_type: Optional[str] = None
    ):
        """
        Update flow state.

        Args:
            state: New flow state
            episode_type: Optional episode type update
        """
        try:
            key = f"session:{self.session_id}"
            updates = {
                'conversation_state': state.value,
            }

            if episode_type:
                updates['episode_type'] = episode_type

            # Update Redis hash
            self.redis.hset(key, mapping=updates)

            logger.info(f"Updated flow state to {state.value} for session {self.session_id}")

        except Exception as e:
            logger.error(f"Error updating flow state: {e}")

    async def mark_resolved(self):
        """Mark conversation as resolved (terminal state)."""
        await self.update_turn_status(TurnStatus.RESOLVED)
        await self.update_flow_state(FlowState.COMPLETED)

    async def mark_escalated(self, reason: Optional[str] = None):
        """Mark conversation as escalated to human."""
        await self.update_turn_status(TurnStatus.ESCALATED, action=reason)
        await self.update_flow_state(FlowState.ESCALATED)

    @classmethod
    def from_context(cls, ctx) -> "UnifiedStateManager":
        """
        Create UnifiedStateManager from PipelineContext.

        Args:
            ctx: PipelineContext with session data

        Returns:
            Configured UnifiedStateManager instance
        """
        return cls(
            session_id=ctx.session_id,
            clinic_id=ctx.effective_clinic_id,
            redis_client=ctx.redis_client if hasattr(ctx, 'redis_client') else None,
            supabase_client=ctx.supabase_client if hasattr(ctx, 'supabase_client') else None,
        )


# Singleton instance getter
_state_manager_instances: Dict[str, UnifiedStateManager] = {}


def get_state_manager(
    session_id: str,
    clinic_id: str,
    redis_client,
    supabase_client=None,
    use_fsm: bool = False,  # Deprecated, kept for API compatibility
) -> UnifiedStateManager:
    """
    Get or create UnifiedStateManager for a session.

    Uses a simple in-memory cache to avoid creating multiple instances
    for the same session.

    Note: use_fsm parameter is deprecated and ignored (FSM removed in Phase 1.3).
    """
    key = f"{session_id}:{clinic_id}"

    if key not in _state_manager_instances:
        _state_manager_instances[key] = UnifiedStateManager(
            session_id=session_id,
            clinic_id=clinic_id,
            redis_client=redis_client,
            supabase_client=supabase_client,
        )

    return _state_manager_instances[key]


def clear_state_manager_cache():
    """Clear the state manager instance cache (for testing)."""
    global _state_manager_instances
    _state_manager_instances = {}
