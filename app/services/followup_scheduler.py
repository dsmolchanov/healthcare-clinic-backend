# clinics/backend/app/services/followup_scheduler.py

import logging
from typing import Dict, Any, Optional, List
from datetime import datetime, timedelta, timezone
import json
import os

logger = logging.getLogger(__name__)

class FollowupScheduler:
    """Schedules automatic follow-ups based on conversation context"""

    def __init__(self, llm_factory=None):
        self._llm_factory = llm_factory
        self.model = os.environ.get('FOLLOWUP_SCHEDULER_MODEL', 'gemini-3-flash')

    async def _get_factory(self):
        """Get or create LLM factory instance"""
        if self._llm_factory is None:
            from app.services.llm import get_llm_factory
            self._llm_factory = await get_llm_factory()
        return self._llm_factory

    async def analyze_and_schedule_followup(
        self,
        session_id: str,
        last_10_messages: List[Dict[str, Any]],
        last_agent_action: str
    ) -> Dict[str, Any]:
        """
        Analyze conversation and determine when to follow up

        Returns:
            {
                'should_schedule': bool,
                'followup_at': Optional[datetime],
                'urgency': 'low' | 'medium' | 'high' | 'urgent',
                'context_summary': str,
                'reasoning': str
            }
        """

        # Format conversation for LLM
        conversation_text = "\n".join([
            f"{msg['role']}: {msg['content']}"
            for msg in last_10_messages
        ])

        prompt = f"""You are a scheduling assistant for a healthcare clinic. Analyze this conversation and determine:

1. Does the agent need to follow up? (yes/no)
2. If yes, how urgent is it?
3. When should the follow-up happen?

Conversation:
{conversation_text}

Agent's pending action: "{last_agent_action}"

Consider:
- Did agent promise to "check with team" or "get back" to them?
- Is this a medical concern (higher urgency)?
- Has significant time passed already?
- Is this affecting patient care?

Return ONLY a JSON object:
{{
    "should_schedule": true/false,
    "urgency": "low" | "medium" | "high" | "urgent",
    "hours_until_followup": number,  // How many hours from now
    "context_summary": "brief summary for agent when following up",
    "reasoning": "why this timing and urgency"
}}

Urgency guidelines:
- urgent: Within 15 minutes (medical emergency, pain, immediate need)
- high: Within 1 hour (important medical question, scheduling conflict)
- medium: Within 1-2 hours (general questions, appointment booking)
- low: Within 4-6 hours (routine info, non-urgent)
"""

        try:
            logger.info(f"Analyzing conversation for follow-up scheduling...")

            factory = await self._get_factory()
            response = await factory.generate(
                messages=[
                    {
                        "role": "system",
                        "content": "You are an expert at triaging healthcare conversations and determining appropriate follow-up timing. Return valid JSON only."
                    },
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                model=self.model,
                temperature=0.3,
                response_format={"type": "json_object"}
            )

            analysis = json.loads(response.content)

            # Calculate followup datetime with improved urgency mapping
            followup_at = None
            if analysis.get('should_schedule'):
                # OPTIMIZATION: Shorten default delays to prevent user drop-off
                urgency = analysis.get('urgency', 'medium')

                # Default hours based on urgency (shorter than before!)
                urgency_hours = {
                    'urgent': 0.25,   # 15 minutes
                    'high': 1,        # 1 hour
                    'medium': 1.5,    # 1.5 hours (was 24!)
                    'low': 4          # 4 hours (was 48!)
                }

                hours = analysis.get('hours_until_followup') or urgency_hours.get(urgency, 1.5)
                followup_at = datetime.now(timezone.utc) + timedelta(hours=hours)

                logger.info(
                    f"📅 Scheduled follow-up for {session_id}: "
                    f"{followup_at.isoformat()} (urgency: {urgency}, hours: {hours})"
                )

            return {
                'should_schedule': analysis.get('should_schedule', False),
                'followup_at': followup_at,
                'urgency': analysis.get('urgency', 'medium'),
                'context_summary': analysis.get('context_summary', ''),
                'reasoning': analysis.get('reasoning', ''),
                'analysis_model': self.model
            }

        except Exception as e:
            logger.error(f"Follow-up scheduling analysis failed: {e}", exc_info=True)

            # Default: schedule for 2 hours if agent has pending action (was 24h!)
            if last_agent_action:
                return {
                    'should_schedule': True,
                    'followup_at': datetime.now(timezone.utc) + timedelta(hours=2),
                    'urgency': 'medium',
                    'context_summary': f"Agent promised: {last_agent_action}",
                    'reasoning': 'Default 2-hour follow-up due to analysis failure',
                    'error': str(e)
                }

            return {
                'should_schedule': False,
                'error': str(e)
            }

    async def store_scheduled_followup(
        self,
        session_id: str,
        followup_at: datetime,
        context: Dict[str, Any]
    ):
        """Store the scheduled follow-up in database"""

        try:
            from app.memory.conversation_memory import get_memory_manager
            manager = get_memory_manager()

            # Serialize datetime objects in context for JSONB storage
            serialized_context = {}
            for key, value in context.items():
                if isinstance(value, datetime):
                    serialized_context[key] = value.isoformat()
                else:
                    serialized_context[key] = value

            manager.supabase.table('conversation_sessions').update({
                'scheduled_followup_at': followup_at.isoformat(),
                'followup_context': serialized_context,
                'updated_at': datetime.utcnow().isoformat()
            }).eq('id', session_id).execute()

            logger.info(f"✅ Stored scheduled follow-up for {session_id}")

        except Exception as e:
            logger.error(f"Failed to store scheduled follow-up: {e}")

    async def create_user_notification(
        self,
        phone_number: str,
        clinic_id: str,
        followup_hours: float,
        urgency: str,
        language: str = 'en'
    ) -> str:
        """
        Create user-facing notification about follow-up timing

        Args:
            phone_number: User's phone number
            clinic_id: Clinic ID
            followup_hours: Hours until follow-up
            urgency: Urgency level
            language: User's language

        Returns:
            Notification message in user's language
        """
        try:
            from app.services.language_fallback_service import get_language_fallback_service

            # Get language-specific service
            lang_service = get_language_fallback_service()

            # If language not provided, detect it
            if language == 'en':
                from app.memory.conversation_memory import get_memory_manager
                from app.db.supabase_client import get_supabase_client

                manager = get_memory_manager()
                language = await lang_service.get_user_language(
                    phone_number, clinic_id, manager.supabase
                )

            # Get localized notification
            notification = lang_service.get_followup_notification(
                language=language,
                hours=int(followup_hours),
                urgency=urgency
            )

            logger.info(f"Created follow-up notification for {phone_number} in {language}: {notification}")
            return notification

        except Exception as e:
            logger.error(f"Failed to create user notification: {e}")
            # Fallback to English
            if followup_hours < 1:
                return "I'll follow up with you within an hour."
            elif followup_hours <= 2:
                return f"I'll follow up with you in {int(followup_hours)} hour(s)."
            else:
                return f"I'll follow up with you in {int(followup_hours)} hours."
