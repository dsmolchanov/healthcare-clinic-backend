"""
Session management using Redis for scalability
"""

import json
import uuid
import redis
from datetime import datetime, timedelta
from typing import Dict, Any, Optional
import os


class RedisSessionManager:
    """
    Redis-based session manager for WhatsApp conversations
    """

    def __init__(self, redis_client=None):
        """Initialize Redis session manager"""
        self.redis_client = redis_client or redis.Redis(
            host=os.environ.get('REDIS_HOST', 'localhost'),
            port=int(os.environ.get('REDIS_PORT', 6379)),
            db=int(os.environ.get('REDIS_DB', 0)),
            decode_responses=True
        )

    async def get_or_create_session(self, phone: str, clinic_id: str) -> Dict[str, Any]:
        """
        Get existing session or create new one

        Args:
            phone: Patient phone number
            clinic_id: Clinic identifier

        Returns:
            Session dictionary
        """
        session_key = f"session:{clinic_id}:{phone}"

        # Try to get existing session
        session_data = self.redis_client.get(session_key)

        if session_data:
            # Parse existing session
            return json.loads(session_data)

        # Create new session
        new_session = {
            'id': str(uuid.uuid4()),
            'clinic_id': clinic_id,
            'phone': phone,
            'created_at': datetime.utcnow().isoformat(),
            'last_activity': datetime.utcnow().isoformat(),
            'messages': [],
            'context': {},
            'consent_given': False
        }

        # Store with 24 hour TTL
        self.redis_client.setex(
            session_key,
            86400,  # 24 hours in seconds
            json.dumps(new_session)
        )

        return new_session

    async def update_session(self, phone: str, clinic_id: str, updates: Dict[str, Any]) -> Dict[str, Any]:
        """
        Update existing session

        Args:
            phone: Patient phone number
            clinic_id: Clinic identifier
            updates: Dictionary of updates to apply

        Returns:
            Updated session dictionary
        """
        session = await self.get_or_create_session(phone, clinic_id)

        # Apply updates
        session.update(updates)
        session['last_activity'] = datetime.utcnow().isoformat()

        # Save back to Redis with refreshed TTL
        session_key = f"session:{clinic_id}:{phone}"
        self.redis_client.setex(
            session_key,
            86400,  # 24 hours
            json.dumps(session)
        )

        return session

    async def add_message(self, session_id: str, message: str, sender: str) -> None:
        """
        Add a message to the session history

        Args:
            session_id: Session ID
            message: Message content
            sender: Message sender ('user' or 'bot')
        """
        # Find session by ID
        for key in self.redis_client.scan_iter("session:*"):
            session_data = self.redis_client.get(key)
            if session_data:
                session = json.loads(session_data)
                if session.get('id') == session_id:
                    # Add message
                    session['messages'].append({
                        'sender': sender,
                        'message': message,
                        'timestamp': datetime.utcnow().isoformat()
                    })

                    # Keep only last 50 messages
                    session['messages'] = session['messages'][-50:]

                    # Update last activity
                    session['last_activity'] = datetime.utcnow().isoformat()

                    # Save back
                    self.redis_client.setex(
                        key,
                        86400,
                        json.dumps(session)
                    )
                    break

    async def get_conversation_context(self, session_id: str) -> Dict[str, Any]:
        """
        Get conversation context for a session

        Args:
            session_id: Session ID

        Returns:
            Context dictionary with messages and metadata
        """
        # Find session by ID
        for key in self.redis_client.scan_iter("session:*"):
            session_data = self.redis_client.get(key)
            if session_data:
                session = json.loads(session_data)
                if session.get('id') == session_id:
                    return {
                        'messages': session.get('messages', []),
                        'context': session.get('context', {}),
                        'consent_given': session.get('consent_given', False),
                        'last_activity': session.get('last_activity')
                    }

        return {'messages': [], 'context': {}}

    async def delete_session(self, phone: str, clinic_id: str) -> bool:
        """
        Delete a session

        Args:
            phone: Patient phone number
            clinic_id: Clinic identifier

        Returns:
            True if deleted, False if not found
        """
        session_key = f"session:{clinic_id}:{phone}"
        return bool(self.redis_client.delete(session_key))


class WhatsAppSessionManager(RedisSessionManager):
    """
    WhatsApp-specific session manager extending Redis session manager
    """

    async def mark_consent_given(self, phone: str, clinic_id: str) -> None:
        """Mark that privacy consent has been given"""
        await self.update_session(phone, clinic_id, {'consent_given': True})

    async def check_consent(self, phone: str, clinic_id: str) -> bool:
        """Check if consent has been given"""
        session = await self.get_or_create_session(phone, clinic_id)
        return session.get('consent_given', False)


async def check_session_validity(session: Dict[str, Any]) -> bool:
    """
    Check if a session is still valid (within 24 hour window)

    Args:
        session: Session dictionary

    Returns:
        True if valid, False if expired
    """
    if not session.get('last_activity'):
        return False

    last_activity = datetime.fromisoformat(session['last_activity'])
    time_since = datetime.utcnow() - last_activity

    # Session is valid if last activity was within 24 hours
    return time_since < timedelta(hours=24)
