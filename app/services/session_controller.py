import logging
import uuid
from datetime import datetime
from typing import Any, Dict, Optional, Tuple
from dataclasses import dataclass

from app.services.session_manager import SessionManager, ResetType
from app.memory.conversation_memory import ConversationMemoryManager
from app.services.conversation_constraints import ConstraintsManager

logger = logging.getLogger(__name__)

@dataclass
class SessionContext:
    session_id: str
    is_new_session: bool
    reset_type: ResetType
    session_obj: Dict[str, Any]
    previous_session_summary: Optional[str] = None

class SessionController:
    """
    Controller for managing session boundaries, locking, and resets.
    """

    def __init__(
        self, 
        session_manager: SessionManager, 
        memory_manager: ConversationMemoryManager,
        constraints_manager: ConstraintsManager
    ):
        self.session_manager = session_manager
        self.memory_manager = memory_manager
        self.constraints_manager = constraints_manager

    async def manage_session(
        self,
        phone_number: str,
        clinic_id: str,
        message_body: str,
        channel: str
    ) -> SessionContext:
        """
        Manage session boundary, acquire lock, and handle resets.
        
        Returns:
            SessionContext object containing session details.
        """
        
        # Step 1: Acquire distributed lock for entire boundary section
        async with self.session_manager.boundary_lock.acquire(phone_number, clinic_id):

            # Step 2: Check boundary FIRST (within lock)
            managed_session_id, is_new_session, reset_type = await self.session_manager.check_and_manage_boundary(
                phone=phone_number,
                clinic_id=clinic_id,
                message=message_body,
                current_time=datetime.utcnow()
            )

            # Step 3: Get session by EXPLICIT ID (never rely on "current" session)
            session = await self.memory_manager.get_session_by_id(managed_session_id)

            if not session:
                # Boundary created new session but it's not in cache yet - fetch it
                session = await self.memory_manager.get_or_create_session(
                    phone_number=phone_number,
                    clinic_id=clinic_id,
                    channel=channel
                )

        # Lock released - boundary decision made, session determined
        
        # Handle resets and context injection
        previous_session_summary = await self._handle_resets(
            reset_type=reset_type,
            is_new_session=is_new_session,
            session_id=managed_session_id,
            phone_number=phone_number,
            clinic_id=clinic_id,
            session=session
        )

        return SessionContext(
            session_id=managed_session_id,
            is_new_session=is_new_session,
            reset_type=reset_type,
            session_obj=session,
            previous_session_summary=previous_session_summary
        )

    async def _handle_resets(
        self,
        reset_type: ResetType,
        is_new_session: bool,
        session_id: str,
        phone_number: str,
        clinic_id: str,
        session: Dict[str, Any]
    ) -> Optional[str]:
        """
        Handle side effects of session resets (clearing constraints, memories, etc.)
        and retrieve previous session summary if applicable.
        """
        previous_session_summary = None
        
        # Generate correlation ID for logging
        correlation_id = str(uuid.uuid4())[:8]
        masked_phone = f"{phone_number[:3]}***{phone_number[-4:]}" if len(phone_number) > 7 else f"{phone_number[:3]}***"

        logger.info(
            f"[{correlation_id}] Session boundary check: phone={masked_phone}, "
            f"session_id={session_id[:8]}, is_new={is_new_session}, "
            f"reset_type={reset_type}"
        )

        if is_new_session:
            logger.info(f"[{correlation_id}] 🆕 NEW SESSION created (previous session archived)")
        elif reset_type == ResetType.SOFT:
            logger.info(f"[{correlation_id}] 🔄 SOFT RESET (constraints cleared)")

        if reset_type == ResetType.HARD:
            # HARD RESET (3 days): Clear ALL constraints, new session
            logger.info(f"🔄 HARD RESET: New session {session_id}")
            await self.constraints_manager.clear_constraints(session_id)

            # Clear stale memories (older than 72 hours)
            cleared_count = await self.memory_manager.clear_stale_memories(
                phone_number=phone_number,
                clinic_id=clinic_id,
                older_than_hours=72
            )
            if cleared_count > 0:
                logger.info(f"🗑️ Hard reset: cleared {cleared_count} stale memories")

            # Get profile-level carryover data (language, allergies, hard bans)
            carryover = await self.session_manager.get_carryover_data(session_id)

            # Restore profile-level constraints from carryover
            if carryover.get('hard_doctor_bans'):
                for doctor in carryover['hard_doctor_bans']:
                    await self.constraints_manager.update_constraints(
                        session_id,
                        exclude_doctor=doctor
                    )

        elif reset_type == ResetType.SOFT:
            # SOFT RESET (4 hours): New session created, inject previous summary
            logger.info(f"🔄 SOFT RESET: New session created: {session_id}")

            # Clear constraints for new session
            await self.constraints_manager.clear_constraints(session_id)

            # Get previous session summary to inject as context
            if session and session.get('metadata', {}).get('previous_session_id'):
                previous_session_id = session['metadata']['previous_session_id']

                try:
                    # Fetch previous session summary
                    prev_session = await self.memory_manager.get_session_by_id(previous_session_id)

                    if prev_session and prev_session.get('session_summary'):
                        previous_session_summary = prev_session['session_summary']
                        logger.info(f"📋 Injected previous session summary ({len(previous_session_summary)} chars)")

                except Exception as e:
                    logger.warning(f"Failed to get previous session summary: {e}")

        elif is_new_session:
            # Fallback for legacy code path (first time user)
            logger.info(f"🆕 New session detected: {session_id}")
            await self.constraints_manager.clear_constraints(session_id)

            # Get carryover data from previous session (language, allergies, etc.)
            carryover = await self.session_manager.get_carryover_data(session_id)

        return previous_session_summary
