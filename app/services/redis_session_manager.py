"""
Redis-based Session Manager
Replaces in-memory session storage with persistent Redis storage
"""

import os
import json
import uuid
import logging
from typing import Dict, Optional, List, Any
from datetime import datetime, timedelta
import redis.asyncio as redis
import hashlib

logger = logging.getLogger(__name__)

class RedisSessionManager:
    """
    Manages conversation sessions using Redis for persistence
    """

    def __init__(self):
        self.redis = redis.Redis(
            host=os.environ.get('REDIS_HOST', 'localhost'),
            port=int(os.environ.get('REDIS_PORT', 6379)),
            db=int(os.environ.get('REDIS_DB', 0)),
            password=os.environ.get('REDIS_PASSWORD'),
            decode_responses=True
        )

        # Session configuration
        self.session_ttl = 86400  # 24 hours
        self.message_history_limit = 50  # Keep last 50 messages

    def _hash_phone(self, phone: str) -> str:
        """
        Hash phone number for privacy

        Args:
            phone: Phone number to hash

        Returns:
            Hashed phone number
        """
        salt = os.environ.get('PHONE_HASH_SALT', 'default_salt')
        return hashlib.sha256(f"{phone}:{salt}".encode()).hexdigest()[:16]

    async def get_or_create_session(
        self,
        phone: str,
        clinic_id: str,
        organization_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Get existing session or create new one

        Args:
            phone: Patient phone number
            clinic_id: Clinic identifier
            organization_id: Organization identifier

        Returns:
            Session dictionary
        """

        session_key = f"session:{clinic_id}:{self._hash_phone(phone)}"

        try:
            # Try to get existing session
            session_data = await self.redis.get(session_key)

            if session_data:
                session = json.loads(session_data)

                # Update last activity
                session['last_activity'] = datetime.utcnow().isoformat()

                # Save updated session
                await self.redis.setex(
                    session_key,
                    self.session_ttl,
                    json.dumps(session)
                )

                logger.info(f"Retrieved existing session: {session['id']}")
                return session

            # Create new session
            session = {
                'id': str(uuid.uuid4()),
                'clinic_id': clinic_id,
                'organization_id': organization_id or clinic_id,
                'phone_hash': self._hash_phone(phone),
                'created_at': datetime.utcnow().isoformat(),
                'last_activity': datetime.utcnow().isoformat(),
                'messages': [],
                'context': {},
                'appointment_ids': [],
                'language': None,  # Will be detected
                'consent_given': False,
                'message_count': 0
            }

            # Store in Redis
            await self.redis.setex(
                session_key,
                self.session_ttl,
                json.dumps(session)
            )

            logger.info(f"Created new session: {session['id']}")
            return session

        except Exception as e:
            logger.error(f"Session management error: {e}")

            # Fallback to basic session
            return {
                'id': str(uuid.uuid4()),
                'clinic_id': clinic_id,
                'phone_hash': self._hash_phone(phone),
                'created_at': datetime.utcnow().isoformat(),
                'messages': [],
                'error': str(e)
            }

    async def add_message(
        self,
        session_id: str,
        clinic_id: str,
        phone: str,
        role: str,
        content: str,
        metadata: Optional[Dict] = None
    ):
        """
        Add message to session history

        Args:
            session_id: Session identifier
            clinic_id: Clinic identifier
            phone: Phone number
            role: Message role (user/assistant)
            content: Message content
            metadata: Additional metadata
        """

        session_key = f"session:{clinic_id}:{self._hash_phone(phone)}"

        try:
            # Get session
            session_data = await self.redis.get(session_key)

            if not session_data:
                # Session expired or doesn't exist
                session = await self.get_or_create_session(phone, clinic_id)
            else:
                session = json.loads(session_data)

            # Create message
            message = {
                'id': str(uuid.uuid4()),
                'role': role,
                'content': content,
                'timestamp': datetime.utcnow().isoformat(),
                'metadata': metadata or {}
            }

            # Add to messages
            session['messages'].append(message)

            # Trim message history if needed
            if len(session['messages']) > self.message_history_limit:
                session['messages'] = session['messages'][-self.message_history_limit:]

            # Update counters
            session['message_count'] = session.get('message_count', 0) + 1
            session['last_activity'] = datetime.utcnow().isoformat()

            # Save updated session
            await self.redis.setex(
                session_key,
                self.session_ttl,
                json.dumps(session)
            )

            logger.debug(f"Added message to session {session_id}")

        except Exception as e:
            logger.error(f"Failed to add message: {e}")

    async def get_session_context(
        self,
        phone: str,
        clinic_id: str
    ) -> Dict[str, Any]:
        """
        Get session context for AI responses

        Args:
            phone: Phone number
            clinic_id: Clinic identifier

        Returns:
            Context dictionary with conversation history
        """

        session_key = f"session:{clinic_id}:{self._hash_phone(phone)}"

        try:
            session_data = await self.redis.get(session_key)

            if not session_data:
                return {
                    'messages': [],
                    'context': {},
                    'new_conversation': True
                }

            session = json.loads(session_data)

            # Get recent messages for context
            recent_messages = session['messages'][-10:]  # Last 10 messages

            return {
                'session_id': session['id'],
                'messages': recent_messages,
                'context': session.get('context', {}),
                'appointment_ids': session.get('appointment_ids', []),
                'language': session.get('language'),
                'consent_given': session.get('consent_given', False),
                'message_count': session.get('message_count', 0),
                'new_conversation': False
            }

        except Exception as e:
            logger.error(f"Failed to get session context: {e}")
            return {
                'messages': [],
                'context': {},
                'error': str(e)
            }

    async def update_session_context(
        self,
        phone: str,
        clinic_id: str,
        context_updates: Dict[str, Any]
    ):
        """
        Update session context

        Args:
            phone: Phone number
            clinic_id: Clinic identifier
            context_updates: Dictionary of context updates
        """

        session_key = f"session:{clinic_id}:{self._hash_phone(phone)}"

        try:
            session_data = await self.redis.get(session_key)

            if not session_data:
                session = await self.get_or_create_session(phone, clinic_id)
            else:
                session = json.loads(session_data)

            # Update context
            session['context'].update(context_updates)

            # Update other fields if provided
            for key in ['language', 'consent_given', 'appointment_ids']:
                if key in context_updates:
                    session[key] = context_updates[key]

            # Save updated session
            await self.redis.setex(
                session_key,
                self.session_ttl,
                json.dumps(session)
            )

            logger.debug(f"Updated session context for {self._hash_phone(phone)}")

        except Exception as e:
            logger.error(f"Failed to update session context: {e}")

    async def get_active_sessions_count(self, clinic_id: str) -> int:
        """
        Get count of active sessions for a clinic

        Args:
            clinic_id: Clinic identifier

        Returns:
            Number of active sessions
        """

        try:
            pattern = f"session:{clinic_id}:*"
            cursor = 0
            count = 0

            # Use SCAN to count keys without blocking
            while True:
                cursor, keys = await self.redis.scan(
                    cursor,
                    match=pattern,
                    count=100
                )
                count += len(keys)

                if cursor == 0:
                    break

            return count

        except Exception as e:
            logger.error(f"Failed to count active sessions: {e}")
            return 0

    async def cleanup_expired_sessions(self):
        """
        Clean up expired sessions (called by background task)
        """

        try:
            # Redis handles expiry automatically with TTL
            # This method is for any additional cleanup if needed

            logger.info("Session cleanup completed")

        except Exception as e:
            logger.error(f"Session cleanup failed: {e}")

    async def get_session_stats(self, clinic_id: str) -> Dict[str, Any]:
        """
        Get session statistics for a clinic

        Args:
            clinic_id: Clinic identifier

        Returns:
            Statistics dictionary
        """

        try:
            active_count = await self.get_active_sessions_count(clinic_id)

            # Get additional stats from Redis
            stats_key = f"stats:{clinic_id}:{datetime.utcnow().date()}"
            daily_stats = await self.redis.hgetall(stats_key)

            return {
                'active_sessions': active_count,
                'daily_messages': int(daily_stats.get('messages', 0)),
                'daily_new_sessions': int(daily_stats.get('new_sessions', 0)),
                'daily_appointments': int(daily_stats.get('appointments', 0))
            }

        except Exception as e:
            logger.error(f"Failed to get session stats: {e}")
            return {
                'active_sessions': 0,
                'error': str(e)
            }

    async def increment_stat(
        self,
        clinic_id: str,
        stat_name: str,
        increment: int = 1
    ):
        """
        Increment a statistic counter

        Args:
            clinic_id: Clinic identifier
            stat_name: Name of statistic
            increment: Amount to increment
        """

        try:
            stats_key = f"stats:{clinic_id}:{datetime.utcnow().date()}"

            # Increment counter
            await self.redis.hincrby(stats_key, stat_name, increment)

            # Set expiry to 30 days
            await self.redis.expire(stats_key, 2592000)

        except Exception as e:
            logger.error(f"Failed to increment stat: {e}")


# Global session manager instance
session_manager = RedisSessionManager()
