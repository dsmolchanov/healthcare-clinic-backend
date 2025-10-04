# clinics/backend/app/services/escalation_handler.py

import logging
from typing import Dict, Any, Optional
from datetime import datetime
import os

logger = logging.getLogger(__name__)

class EscalationHandler:
    """Handles conversation escalation to human agents"""

    HOLDING_MESSAGES = [
        "Thank you for your patience. We're working on your request and will have an answer for you shortly.",
        "We appreciate your patience as we're consulting with our team to provide you with the best response.",
        "Your request is being reviewed by our specialists. We'll get back to you as soon as possible.",
        "We're looking into this for you. Thank you for your understanding.",
    ]

    def __init__(self):
        self.supabase = None  # Will be injected

    async def escalate_conversation(
        self,
        session_id: str,
        reason: str,
        escalated_to_user_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Escalate a conversation to human agent

        Returns:
            {
                'escalated': bool,
                'escalation_id': str,
                'assigned_to': Optional[str],
                'holding_message': str
            }
        """

        logger.warning(f"🚨 Escalating conversation {session_id}: {reason}")

        # Update session status
        update_data = {
            'turn_status': 'escalated',
            'escalation_reason': reason,
            'escalated_to_user_id': escalated_to_user_id,
            'updated_at': datetime.utcnow().isoformat()
        }

        try:
            from app.memory.conversation_memory import get_memory_manager
            manager = get_memory_manager()

            manager.supabase.table('conversation_sessions').update(
                update_data
            ).eq('id', session_id).execute()

            # Create escalation record (for tracking)
            escalation_record = {
                'session_id': session_id,
                'reason': reason,
                'assigned_to': escalated_to_user_id,
                'metadata': metadata or {},
                'created_at': datetime.utcnow().isoformat(),
                'status': 'pending'
            }

            # TODO: Store in escalation_queue table
            # TODO: Send Slack/email notification to staff

            logger.info(f"✅ Conversation escalated successfully")

            # Return holding message
            import random
            holding_message = random.choice(self.HOLDING_MESSAGES)

            return {
                'escalated': True,
                'escalation_id': session_id,
                'assigned_to': escalated_to_user_id,
                'holding_message': holding_message
            }

        except Exception as e:
            logger.error(f"Failed to escalate conversation: {e}", exc_info=True)
            return {
                'escalated': False,
                'error': str(e),
                'holding_message': self.HOLDING_MESSAGES[0]  # Default message
            }

    async def check_if_should_escalate(
        self,
        conversation_context: str,
        user_message: str
    ) -> Dict[str, Any]:
        """
        Determine if conversation should be escalated

        Uses heuristics:
        - User explicitly asks for human
        - Repeated failed queries (detected in context)
        - Complex medical questions

        Returns:
            {
                'should_escalate': bool,
                'reason': str,
                'confidence': float
            }
        """

        # Simple keyword matching for now
        escalation_keywords = [
            'speak to human', 'talk to person', 'real person',
            'speak to agent', 'human agent', 'representative',
            'manager', 'supervisor',
            # Multilingual
            'hablar con humano', 'persona real',  # Spanish
            'לדבר עם אדם', 'נציג אמיתי'  # Hebrew
        ]

        user_lower = user_message.lower()

        for keyword in escalation_keywords:
            if keyword in user_lower:
                logger.warning(f"Escalation keyword detected: {keyword}")
                return {
                    'should_escalate': True,
                    'reason': f"User requested human agent (keyword: '{keyword}')",
                    'confidence': 0.95
                }

        # Check for repeated questions (TODO: implement with LLM)
        # Check for complex medical terms (TODO: implement with LLM)

        return {
            'should_escalate': False,
            'reason': None,
            'confidence': 0.0
        }

    async def get_holding_message(self, session_id: str) -> str:
        """Get an appropriate holding message for escalated conversation"""
        import random
        return random.choice(self.HOLDING_MESSAGES)
