"""
Session Lifecycle Management
Manages conversation session boundaries with automatic temporal segmentation
"""

from typing import Optional, Dict, Tuple
from datetime import datetime, timedelta
from enum import Enum
import logging
import asyncio
from app.services.locks import BoundaryLock
from app.services.session_summarizer import SessionSummarizer

logger = logging.getLogger(__name__)


class SessionState(str, Enum):
    """Session lifecycle states"""
    ACTIVE = "active"
    DORMANT = "dormant"
    CLOSED = "closed"


class ResetType(str, Enum):
    """Types of session reset"""
    NONE = "none"
    SOFT = "soft"  # 4 hours - clear episode data (desired service, time window)
    HARD = "hard"  # 3 days - clear all but profile (language, allergies, hard bans)


class SessionSplitSignal:
    """Calculates session split score based on multiple signals"""

    # Weights for split signals (tune per clinic)
    WEIGHT_TIME_GAP_4H = 0.5  # Soft reset threshold
    WEIGHT_TIME_GAP_24H = 0.3
    WEIGHT_TIME_GAP_48H = 0.6
    WEIGHT_TIME_GAP_72H = 1.0  # Hard reset threshold
    WEIGHT_TOPIC_DRIFT_MEDIUM = 0.4
    WEIGHT_TOPIC_DRIFT_HIGH = 0.8
    WEIGHT_HARD_CORRECTION = 0.7
    WEIGHT_OUTCOME_EVENT = 1.0
    WEIGHT_EXPLICIT_RESET = 1.0

    SOFT_RESET_THRESHOLD = 0.5  # Clear episode data
    HARD_RESET_THRESHOLD = 1.0  # Start new session

    @classmethod
    def calculate_split_score(
        cls,
        time_gap_hours: float,
        topic_drift: Optional[float] = None,  # 0-1 semantic distance
        has_hard_correction: bool = False,
        has_outcome_event: bool = False,
        has_explicit_reset: bool = False
    ) -> Tuple[float, 'ResetType']:
        """
        Calculate session split score from signals.

        Returns:
            Tuple of (score, reset_type)
        """
        score = 0.0

        # Time gap signals with hierarchy
        if time_gap_hours >= 72:  # 3 days
            score += cls.WEIGHT_TIME_GAP_72H
        elif time_gap_hours >= 48:
            score += cls.WEIGHT_TIME_GAP_48H
        elif time_gap_hours >= 24:
            score += cls.WEIGHT_TIME_GAP_24H
        elif time_gap_hours >= 4:  # 4 hours - soft reset trigger
            score += cls.WEIGHT_TIME_GAP_4H

        # Topic drift
        if topic_drift is not None:
            if topic_drift > 0.7:
                score += cls.WEIGHT_TOPIC_DRIFT_HIGH
            elif topic_drift > 0.4:
                score += cls.WEIGHT_TOPIC_DRIFT_MEDIUM

        # Event signals
        if has_hard_correction:
            score += cls.WEIGHT_HARD_CORRECTION
        if has_outcome_event:
            score += cls.WEIGHT_OUTCOME_EVENT
        if has_explicit_reset:
            score += cls.WEIGHT_EXPLICIT_RESET

        # Determine reset type
        reset_type = ResetType.NONE
        if score >= cls.HARD_RESET_THRESHOLD:
            reset_type = ResetType.HARD
        elif score >= cls.SOFT_RESET_THRESHOLD:
            reset_type = ResetType.SOFT

        logger.debug(
            f"Split score: {score:.2f} reset={reset_type} (gap={time_gap_hours:.1f}h, "
            f"drift={topic_drift}, correction={has_hard_correction})"
        )

        return score, reset_type


class SessionManager:
    """Manages conversation session lifecycle with automatic boundary detection"""

    def __init__(self, redis_client, supabase_client):
        self.redis = redis_client
        self.supabase = supabase_client
        self.boundary_lock = BoundaryLock(redis_client)  # NEW: Token-based lock
        self.summarizer = SessionSummarizer()  # NEW: Session summary generator

    def _make_session_key(self, phone: str, clinic_id: str) -> str:
        return f"session:{clinic_id}:{phone}"

    async def check_and_manage_boundary(
        self,
        phone: str,
        clinic_id: str,
        message: str,
        current_time: datetime
    ) -> Tuple[str, bool, Optional[ResetType]]:  # (session_id, is_new_session, reset_type)
        """
        Check if session boundary or soft reset should be triggered.

        Returns:
            Tuple of (session_id, is_new_session_flag, reset_type)
            - reset_type=SOFT: Clear episode data (desired service, time window)
            - reset_type=HARD: New session created
            - reset_type=NONE: Continue session
        """
        key = self._make_session_key(phone, clinic_id)
        session_data = self.redis.hgetall(key)

        if not session_data:
            # First time user or expired session
            session_id, _ = await self._create_new_session(phone, clinic_id, current_time)
            return session_id, True, ResetType.HARD

        session_id = session_data.get('session_id')
        last_activity_str = session_data.get('last_activity')
        last_activity = datetime.fromisoformat(last_activity_str)
        session_state = SessionState(session_data.get('state', 'active'))

        time_gap_hours = (current_time - last_activity).total_seconds() / 3600

        # Calculate split score and reset type
        split_score, reset_type = SessionSplitSignal.calculate_split_score(
            time_gap_hours=time_gap_hours,
            # TODO: Add topic drift calculation in future enhancement
            # TODO: Detect hard corrections from message (Phase 2 integration)
            # TODO: Check for outcome events from appointments table
        )

        # Handle reset based on type
        if reset_type == ResetType.HARD:
            logger.info(
                f"🔄 HARD RESET: Creating new session (score={split_score:.2f}, gap={time_gap_hours:.1f}h)"
            )

            # Archive old session
            await self._archive_session(session_id, current_time)

            # Create new session
            new_session_id, _ = await self._create_new_session(
                phone, clinic_id, current_time, previous_session_id=session_id
            )

            return new_session_id, True, ResetType.HARD

        elif reset_type == ResetType.SOFT:
            logger.info(
                f"🔄 SOFT RESET: Creating new session (score={split_score:.2f}, gap={time_gap_hours:.1f}h)"
            )

            # Phase 6: Soft reset now creates new session (like hard reset)
            # But with summary context injection from previous session

            # Archive old session (with summary generation)
            await self._archive_session(session_id, current_time)

            # Create new session with previous session link
            new_session_id, _ = await self._create_new_session(
                phone, clinic_id, current_time, previous_session_id=session_id
            )

            return new_session_id, True, ResetType.SOFT  # is_new_session=True now

        else:
            # Update heartbeat - normal continuation
            self.redis.hset(key, 'last_activity', current_time.isoformat())
            self.redis.hset(key, 'state', SessionState.ACTIVE.value)

            return session_id, False, ResetType.NONE

    async def _create_new_session(
        self,
        phone: str,
        clinic_id: str,
        current_time: datetime,
        previous_session_id: Optional[str] = None
    ) -> Tuple[str, Dict]:
        """Create new session in Supabase and Redis"""

        # Create in Supabase for persistence
        new_session = {
            'user_identifier': phone,
            'channel_type': 'whatsapp',
            'metadata': {
                'clinic_id': clinic_id,
                'phone_number': phone,
                'previous_session_id': previous_session_id
            },
            'started_at': current_time.isoformat(),
            'status': 'active'
        }

        response = self.supabase.schema('healthcare').table('conversation_sessions').insert(new_session).execute()
        session_id = response.data[0]['id']

        # Create in Redis for fast access
        key = self._make_session_key(phone, clinic_id)
        self.redis.hset(key, 'session_id', session_id)
        self.redis.hset(key, 'started_at', current_time.isoformat())
        self.redis.hset(key, 'last_activity', current_time.isoformat())
        self.redis.hset(key, 'state', SessionState.ACTIVE.value)
        self.redis.hset(key, 'previous_session_id', previous_session_id or '')
        self.redis.expire(key, 86400 * 14)  # 2 weeks TTL

        logger.info(f"📂 Created new session {session_id} (previous={previous_session_id})")

        return session_id, new_session

    async def _archive_session(
        self,
        session_id: str,
        current_time: datetime,
        generate_summary: bool = True  # NEW parameter
    ):
        """
        Archive session and optionally generate AI summary.

        IMPORTANT: This function returns immediately and does NOT block on summary generation.
        Summary is generated asynchronously in background.
        """

        # Check if already archived (idempotency)
        try:
            result = self.supabase.schema('healthcare').table('conversation_sessions').select('status, ended_at').eq(
                'id', session_id
            ).maybe_single().execute()

            if result.data and result.data.get('status') in ('closed', 'ended'):
                logger.info(f"Session {session_id[:8]} already archived, skipping")
                return
        except Exception as e:
            logger.warning(f"Error checking session status: {e}")

        # Archive session SYNCHRONOUSLY (fast DB update)
        updates = {
            'ended_at': current_time.isoformat(),
            'status': 'closed'  # Changed from 'ended' to 'closed' per plan
        }

        if generate_summary:
            updates['summary_status'] = 'pending'  # Mark as pending

        self.supabase.schema('healthcare').table('conversation_sessions').update(updates).eq('id', session_id).execute()

        logger.info(f"🗄️ Archived session {session_id[:8]}")

        # Generate summary ASYNCHRONOUSLY (fire-and-forget, non-blocking)
        if generate_summary:
            asyncio.create_task(
                self.summarizer.generate_and_store_summary(session_id, current_time)
            )
            logger.debug(f"🔄 Queued summary generation for session {session_id[:8]} (async)")

    async def get_carryover_data(self, session_id: str) -> Dict:
        """
        Retrieve data that should carry over to new session.

        Carryover whitelist (from Profile tier):
        - language preference
        - allergies
        - hard doctor bans (user safety)

        DO NOT carry over (Episode tier):
        - desired_service
        - time_window
        - soft preferences

        Returns:
            Dict with profile-level data to restore in new session
        """
        carryover = {
            'language_preference': None,
            'allergies': [],
            'hard_doctor_bans': [],
            'hard_service_bans': []
        }

        try:
            # Get session from Supabase to find previous session
            session_response = self.supabase.schema('healthcare').table('conversation_sessions').select('*').eq('id', session_id).limit(1).execute()

            if not session_response.data:
                return carryover

            session = session_response.data[0]
            previous_session_id = session.get('metadata', {}).get('previous_session_id')

            if not previous_session_id:
                return carryover

            # Get constraints from previous session (if available in Redis)
            from app.services.conversation_constraints import ConstraintsManager
            from app.config import get_redis_client

            constraints_manager = ConstraintsManager(get_redis_client())
            prev_constraints = await constraints_manager.get_constraints(previous_session_id)

            # Extract profile-level data (hard bans only, not episode-level desires)
            # Hard bans are exclusions that persist across sessions for safety/preference
            carryover['hard_doctor_bans'] = list(prev_constraints.excluded_doctors) if prev_constraints.excluded_doctors else []
            carryover['hard_service_bans'] = list(prev_constraints.excluded_services) if prev_constraints.excluded_services else []

            # TODO: Get language preference from patient profile
            # TODO: Get allergies from patient profile (medical safety)

            logger.info(
                f"📋 Carryover data: {len(carryover['hard_doctor_bans'])} doctor bans, "
                f"{len(carryover['hard_service_bans'])} service bans"
            )

        except Exception as e:
            logger.warning(f"Failed to get carryover data: {e}")

        return carryover
