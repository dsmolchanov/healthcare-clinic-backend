"""
Session State Service using Redis Hash

Manages conversation session state with Redis HMSET for efficient atomic updates.

Session state tracks:
- current_intent: Current conversation intent (e.g., "service_inquiry", "booking")
- last_service_mentioned: Last service ID discussed
- pending_action: Next expected action (e.g., "offer_booking", "confirm_appointment")
- conversation_turn: Turn number for multi-turn tracking
- language: User's preferred language
- created_at: Session creation timestamp
- updated_at: Last update timestamp
"""

import json
import logging
import time
from typing import Dict, Any, Optional
from dataclasses import dataclass, asdict
from datetime import datetime

logger = logging.getLogger(__name__)


@dataclass
class SessionState:
    """Session state data structure"""
    session_id: str
    current_intent: Optional[str] = None
    last_service_mentioned: Optional[str] = None
    pending_action: Optional[str] = None
    conversation_turn: int = 0
    language: str = "es"  # Default Spanish
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    def to_dict(self) -> Dict[str, str]:
        """Convert to dict with string values for Redis Hash"""
        data = asdict(self)
        # Convert None to empty string for Redis
        return {k: str(v) if v is not None else "" for k, v in data.items()}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'SessionState':
        """Create SessionState from Redis Hash data"""
        # Convert string values back to appropriate types
        return cls(
            session_id=data.get('session_id', ''),
            current_intent=data.get('current_intent') or None,
            last_service_mentioned=data.get('last_service_mentioned') or None,
            pending_action=data.get('pending_action') or None,
            conversation_turn=int(data.get('conversation_turn', 0)),
            language=data.get('language', 'es'),
            created_at=data.get('created_at'),
            updated_at=data.get('updated_at')
        )


class SessionService:
    """
    Redis-based session state management using Hash (HMSET) for atomic updates.

    Benefits of Redis Hash:
    - Atomic field updates with HSET
    - Get all fields with HGETALL
    - Efficient memory usage
    - Field-level TTL support
    """

    def __init__(self, redis_client, default_ttl: int = 3600):
        """
        Initialize session service

        Args:
            redis_client: Redis client instance
            default_ttl: Session TTL in seconds (default 1 hour)
        """
        self.redis = redis_client
        self.default_ttl = default_ttl

    def _make_key(self, session_id: str) -> str:
        """Generate session key with hash-tag for Redis Cluster"""
        return f"session:{{{session_id}}}"

    async def create_session(self, session_id: str, language: str = "es") -> SessionState:
        """
        Create new session

        Args:
            session_id: Unique session identifier
            language: User's preferred language

        Returns:
            SessionState object
        """
        now = datetime.utcnow().isoformat()
        state = SessionState(
            session_id=session_id,
            language=language,
            created_at=now,
            updated_at=now
        )

        key = self._make_key(session_id)
        # Use HSET to set all fields atomically
        self.redis.hset(key, mapping=state.to_dict())
        self.redis.expire(key, self.default_ttl)

        logger.info(f"✅ Created session {session_id} (language: {language})")
        return state

    async def get_session(self, session_id: str) -> Optional[SessionState]:
        """
        Get session state

        Args:
            session_id: Session identifier

        Returns:
            SessionState or None if not found
        """
        key = self._make_key(session_id)
        data = self.redis.hgetall(key)

        if not data:
            logger.debug(f"Session {session_id} not found")
            return None

        # Decode bytes to strings if necessary
        if data and isinstance(list(data.keys())[0], bytes):
            data = {k.decode(): v.decode() for k, v in data.items()}

        state = SessionState.from_dict(data)
        logger.debug(f"Retrieved session {session_id} (turn: {state.conversation_turn})")
        return state

    async def update_session(
        self,
        session_id: str,
        current_intent: Optional[str] = None,
        last_service_mentioned: Optional[str] = None,
        pending_action: Optional[str] = None,
        increment_turn: bool = False
    ) -> bool:
        """
        Update session state fields atomically

        Args:
            session_id: Session identifier
            current_intent: Update current intent
            last_service_mentioned: Update last service
            pending_action: Update pending action
            increment_turn: Increment conversation turn

        Returns:
            True if updated, False if session not found
        """
        key = self._make_key(session_id)

        # Check if session exists
        if not self.redis.exists(key):
            logger.warning(f"Cannot update non-existent session {session_id}")
            return False

        updates = {}
        if current_intent is not None:
            updates['current_intent'] = current_intent
        if last_service_mentioned is not None:
            updates['last_service_mentioned'] = last_service_mentioned
        if pending_action is not None:
            updates['pending_action'] = pending_action

        # Always update timestamp
        updates['updated_at'] = datetime.utcnow().isoformat()

        # Increment turn if requested
        if increment_turn:
            current_turn = int(self.redis.hget(key, 'conversation_turn') or 0)
            updates['conversation_turn'] = str(current_turn + 1)

        # Atomic update with HSET
        if updates:
            self.redis.hset(key, mapping=updates)
            # Refresh TTL
            self.redis.expire(key, self.default_ttl)

        logger.debug(f"Updated session {session_id}: {updates}")
        return True

    async def delete_session(self, session_id: str) -> bool:
        """
        Delete session

        Args:
            session_id: Session identifier

        Returns:
            True if deleted, False if not found
        """
        key = self._make_key(session_id)
        deleted = self.redis.delete(key)
        if deleted:
            logger.info(f"🗑️ Deleted session {session_id}")
        return bool(deleted)

    async def get_active_sessions(self, pattern: str = "*") -> int:
        """
        Get count of active sessions

        Args:
            pattern: Key pattern for filtering

        Returns:
            Number of active sessions
        """
        keys = self.redis.keys(f"session:{pattern}")
        return len(keys)

    async def extend_session(self, session_id: str, ttl: Optional[int] = None) -> bool:
        """
        Extend session TTL

        Args:
            session_id: Session identifier
            ttl: New TTL in seconds (uses default if None)

        Returns:
            True if extended, False if session not found
        """
        key = self._make_key(session_id)
        ttl = ttl or self.default_ttl
        return bool(self.redis.expire(key, ttl))
