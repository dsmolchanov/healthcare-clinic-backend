"""
ControlModeGateStep - Check session control mode and route accordingly.

HITL (Human-in-the-Loop) Control Mode System - Phase 2

When control_mode is 'human' or 'paused':
- Store user message for human operator
- Increment unread counter
- Short-circuit pipeline (no LLM processing)
"""

import logging
from typing import Tuple
from datetime import datetime, timezone

from ..base import PipelineStep
from ..context import PipelineContext

logger = logging.getLogger(__name__)


class ControlModeGateStep(PipelineStep):
    """
    Control mode gate that checks if human is in control of the session.

    When control_mode is 'human' or 'paused':
    1. Store user message for human operator to see
    2. Increment unread_for_human_count
    3. Set response to empty and short-circuit pipeline
    4. Optionally mark chat as unread in WhatsApp (best-effort)

    When control_mode is 'agent':
    - Continue with normal LLM processing
    """

    def __init__(self, supabase_client=None, memory_manager=None):
        """
        Initialize with required dependencies.

        Args:
            supabase_client: Supabase client for database operations
            memory_manager: ConversationMemory for message storage
        """
        self._supabase = supabase_client
        self._memory_manager = memory_manager

    @property
    def name(self) -> str:
        return "control_mode_gate"

    async def execute(self, ctx: PipelineContext) -> Tuple[PipelineContext, bool]:
        """
        Execute control mode gate check.

        Returns:
            (context, should_continue)
            - If control_mode is 'agent': (context, True) - continue pipeline
            - If control_mode is 'human'/'paused': (context, False) - short-circuit
        """
        # Get control mode from session
        control_mode = self._get_control_mode(ctx)

        if control_mode in ('human', 'paused'):
            # Human is in control - DO NOT process with LLM
            logger.info(
                f"⛔ Control mode gate: session {ctx.session_id[:8] if ctx.session_id else 'unknown'}... "
                f"under {control_mode} control, routing to human operator"
            )

            # Store the user message for human to see
            await self._store_message_for_human(
                session_id=ctx.session_id,
                content=ctx.message,
                from_phone=ctx.from_phone,
                metadata={
                    'message_sid': ctx.message_sid,
                    'profile_name': ctx.profile_name,
                    'control_mode': control_mode
                }
            )

            # Increment unread counter
            await self._increment_unread_count(ctx.session_id)

            # Set response metadata
            ctx.response = ""  # No response to user
            ctx.response_metadata = {
                "control_mode": control_mode,
                "routed_to": "human_operator",
                "processing_time_ms": 0,
                "hitl_gated": True
            }

            # Short-circuit pipeline
            return ctx, False

        # control_mode == 'agent' or not set - continue with normal processing
        logger.debug(
            f"✅ Control mode check passed: session "
            f"{ctx.session_id[:8] if ctx.session_id else 'unknown'}... (agent mode)"
        )

        return ctx, True

    def _get_control_mode(self, ctx: PipelineContext) -> str:
        """Get control mode from session, defaulting to 'agent'."""
        if ctx.session and isinstance(ctx.session, dict):
            return ctx.session.get('control_mode', 'agent')
        return 'agent'

    async def _store_message_for_human(
        self,
        session_id: str,
        content: str,
        from_phone: str,
        metadata: dict
    ) -> None:
        """Store user message for human operator to see."""
        if not session_id:
            logger.warning("Cannot store message for human - no session_id")
            return

        try:
            if self._memory_manager:
                # Use memory manager if available (preferred)
                await self._memory_manager.store_message(
                    session_id=session_id,
                    role='user',
                    content=content,
                    phone_number=from_phone,
                    metadata={
                        **metadata,
                        'pending_human_review': True
                    }
                )
            elif self._supabase:
                # Fallback to direct database insert
                self._supabase.table('conversation_logs').insert({
                    'session_id': session_id,
                    'role': 'user',
                    'message_content': content,
                    'phone_number': from_phone,
                    'metadata': {
                        **metadata,
                        'pending_human_review': True
                    }
                }).execute()

            logger.debug(
                f"📬 Stored user message for human operator: "
                f"session {session_id[:8]}..."
            )

        except Exception as e:
            logger.error(f"Failed to store message for human: {e}")

    async def _increment_unread_count(self, session_id: str) -> None:
        """Increment unread_for_human_count on session."""
        if not session_id or not self._supabase:
            return

        try:
            # Try RPC function first (atomic increment)
            try:
                self._supabase.rpc(
                    'increment_unread_count',
                    {'p_session_id': session_id}
                ).execute()
                return
            except Exception:
                pass

            # Fallback: direct update (not atomic but usually okay)
            result = self._supabase.table('conversation_sessions').select(
                'unread_for_human_count'
            ).eq('id', session_id).single().execute()

            current_count = 0
            if result.data:
                current_count = result.data.get('unread_for_human_count', 0) or 0

            self._supabase.table('conversation_sessions').update({
                'unread_for_human_count': current_count + 1
            }).eq('id', session_id).execute()

        except Exception as e:
            logger.warning(f"Failed to increment unread count: {e}")
