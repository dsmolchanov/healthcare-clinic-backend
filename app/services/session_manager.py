"""
Session Lifecycle Management
Manages conversation session boundaries with automatic temporal segmentation
"""

from typing import Optional, Dict, Tuple
from datetime import datetime, timedelta
from enum import Enum
import logging

logger = logging.getLogger(__name__)


class SessionState(str, Enum):
    """Session lifecycle states"""
    ACTIVE = "active"
    DORMANT = "dormant"
    CLOSED = "closed"


class SessionSplitSignal:
    """Calculates session split score based on multiple signals"""

    # Weights for split signals (tune per clinic)
    WEIGHT_TIME_GAP_24H = 0.3
    WEIGHT_TIME_GAP_48H = 0.6
    WEIGHT_TIME_GAP_72H = 1.0  # Alone is enough
    WEIGHT_TOPIC_DRIFT_MEDIUM = 0.4
    WEIGHT_TOPIC_DRIFT_HIGH = 0.8
    WEIGHT_HARD_CORRECTION = 0.7
    WEIGHT_OUTCOME_EVENT = 1.0
    WEIGHT_EXPLICIT_RESET = 1.0

    SPLIT_THRESHOLD = 1.0  # Start new session when score >= 1.0

    @classmethod
    def calculate_split_score(
        cls,
        time_gap_hours: float,
        topic_drift: Optional[float] = None,  # 0-1 semantic distance
        has_hard_correction: bool = False,
        has_outcome_event: bool = False,
        has_explicit_reset: bool = False
    ) -> float:
        """Calculate session split score from signals"""
        score = 0.0

        # Time gap signals
        if time_gap_hours >= 72:
            score += cls.WEIGHT_TIME_GAP_72H
        elif time_gap_hours >= 48:
            score += cls.WEIGHT_TIME_GAP_48H
        elif time_gap_hours >= 24:
            score += cls.WEIGHT_TIME_GAP_24H

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

        logger.debug(
            f"Split score: {score:.2f} (gap={time_gap_hours:.1f}h, "
            f"drift={topic_drift}, correction={has_hard_correction})"
        )

        return score


class SessionManager:
    """Manages conversation session lifecycle with automatic boundary detection"""

    def __init__(self, redis_client, supabase_client):
        self.redis = redis_client
        self.supabase = supabase_client

    def _make_session_key(self, phone: str, clinic_id: str) -> str:
        return f"session:{clinic_id}:{phone}"

    async def check_and_manage_boundary(
        self,
        phone: str,
        clinic_id: str,
        message: str,
        current_time: datetime
    ) -> Tuple[str, bool]:  # (session_id, is_new_session)
        """
        Check if session boundary should be created.

        Returns:
            Tuple of (session_id, is_new_session_flag)
        """
        key = self._make_session_key(phone, clinic_id)
        session_data = self.redis.hgetall(key)

        if not session_data:
            # First time user or expired session
            session_id, _ = await self._create_new_session(phone, clinic_id, current_time)
            return session_id, True

        session_id = session_data.get('session_id')
        last_activity_str = session_data.get('last_activity')
        last_activity = datetime.fromisoformat(last_activity_str)
        session_state = SessionState(session_data.get('state', 'active'))

        time_gap_hours = (current_time - last_activity).total_seconds() / 3600

        # Calculate split score
        split_score = SessionSplitSignal.calculate_split_score(
            time_gap_hours=time_gap_hours,
            # TODO: Add topic drift calculation in future enhancement
            # TODO: Detect hard corrections from message (Phase 2 integration)
            # TODO: Check for outcome events from appointments table
        )

        # Check if we should split
        if split_score >= SessionSplitSignal.SPLIT_THRESHOLD:
            logger.info(
                f"🔄 Session split triggered (score={split_score:.2f}, gap={time_gap_hours:.1f}h)"
            )

            # Archive old session
            await self._archive_session(session_id, current_time)

            # Create new session
            new_session_id, _ = await self._create_new_session(
                phone, clinic_id, current_time, previous_session_id=session_id
            )

            return new_session_id, True
        else:
            # Update heartbeat
            self.redis.hset(key, 'last_activity', current_time.isoformat())
            self.redis.hset(key, 'state', SessionState.ACTIVE.value)

            return session_id, False

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

        response = self.supabase.table('conversation_sessions').insert(new_session).execute()
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

    async def _archive_session(self, session_id: str, current_time: datetime):
        """Archive session in Supabase"""
        self.supabase.table('conversation_sessions').update({
            'ended_at': current_time.isoformat(),
            'status': 'ended'
        }).eq('id', session_id).execute()

        logger.info(f"🗄️ Archived session {session_id}")

    async def get_carryover_data(self, previous_session_id: str) -> Dict:
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
        """
        # Query mem0 for profile-level data
        # For now, return placeholder - implement in Phase 5 enhancement
        return {
            'profile_data': {},
            'pending_reminders': []
        }
