# clinics/backend/app/services/followup_scheduler.py

import logging
from typing import Dict, Any, Optional, List
from datetime import datetime, timedelta, timezone
from openai import AsyncOpenAI
import json
import os

logger = logging.getLogger(__name__)

class FollowupScheduler:
    """Schedules automatic follow-ups based on conversation context"""

    def __init__(self):
        self._client = None
        self.model = os.environ.get('FOLLOWUP_SCHEDULER_MODEL', 'gpt-4o')

    @property
    def client(self) -> AsyncOpenAI:
        """Lazy-load OpenAI client"""
        if self._client is None:
            api_key = os.environ.get('OPENAI_API_KEY')
            if not api_key:
                raise RuntimeError("OPENAI_API_KEY environment variable not set")
            self._client = AsyncOpenAI(api_key=api_key)
        return self._client

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
- urgent: Within 1 hour (medical emergency, pain, immediate need)
- high: Within 4 hours (important medical question, scheduling conflict)
- medium: Within 24 hours (general questions, appointment booking)
- low: Within 48 hours (routine info, non-urgent)
"""

        try:
            logger.info(f"Analyzing conversation for follow-up scheduling...")

            completion = await self.client.chat.completions.create(
                model=self.model,
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
                temperature=0.3,
                response_format={"type": "json_object"}
            )

            analysis = json.loads(completion.choices[0].message.content)

            # Calculate followup datetime
            followup_at = None
            if analysis.get('should_schedule'):
                hours = analysis.get('hours_until_followup', 24)
                followup_at = datetime.now(timezone.utc) + timedelta(hours=hours)

                logger.info(
                    f"📅 Scheduled follow-up for {session_id}: "
                    f"{followup_at.isoformat()} (urgency: {analysis.get('urgency')})"
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

            # Default: schedule for 24 hours if agent has pending action
            if last_agent_action:
                return {
                    'should_schedule': True,
                    'followup_at': datetime.now(timezone.utc) + timedelta(hours=24),
                    'urgency': 'medium',
                    'context_summary': f"Agent promised: {last_agent_action}",
                    'reasoning': 'Default 24-hour follow-up due to analysis failure',
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
