"""
Context Relevance Checker

Determines if pending action context is relevant to the user's current message.
Prevents bleeding of old/unrelated appointment contexts into new conversations.

Uses semantic similarity to check if:
- User is following up on pending action
- User is asking about something completely different
"""

import logging
from typing import Dict, Any, Optional, Tuple
import os
import json

logger = logging.getLogger(__name__)


class ContextRelevanceChecker:
    """Checks if context is relevant to current user message"""

    def __init__(self, llm_factory=None):
        self._llm_factory = llm_factory
        self.model = os.environ.get('CONTEXT_CHECKER_MODEL', 'gpt-4o-mini')

    async def _get_factory(self):
        """Get or create LLM factory instance"""
        if self._llm_factory is None:
            from app.services.llm import get_llm_factory
            self._llm_factory = await get_llm_factory()
        return self._llm_factory

    async def is_context_relevant(
        self,
        current_message: str,
        pending_action: str,
        conversation_history: list = None
    ) -> Tuple[bool, float, str]:
        """
        Check if pending action context is relevant to current message

        Args:
            current_message: User's current message
            pending_action: The pending action from previous conversation
            conversation_history: Last few messages for additional context

        Returns:
            Tuple of (is_relevant, confidence, reasoning)
        """

        # Quick heuristic checks first (fast path)
        if not pending_action or not current_message:
            return False, 0.0, "Missing context"

        # Check for explicit negation keywords
        negation_keywords = {
            'ru': ['не нужен', 'не надо', 'не хочу', 'другое', 'забудь', 'отмена'],
            'es': ['no necesito', 'no quiero', 'otro', 'diferente', 'olvida', 'cancelar'],
            'en': ['don\'t need', 'don\'t want', 'something else', 'different', 'forget', 'cancel'],
            'he': ['לא צריך', 'לא רוצה', 'משהו אחר', 'שונה', 'ביטול'],
            'pt': ['não preciso', 'não quero', 'outro', 'diferente', 'esquecer', 'cancelar']
        }

        current_lower = current_message.lower()
        for lang, keywords in negation_keywords.items():
            if any(keyword in current_lower for keyword in keywords):
                logger.info(f"❌ Explicit negation detected: '{current_message}'")
                return False, 0.0, "User explicitly rejected pending action"

        # Use LLM for semantic relevance check
        try:
            prompt = f"""You are analyzing if a user's new message is related to a pending action from a previous conversation.

Pending action: "{pending_action}"

User's new message: "{current_message}"

Is the user following up on the pending action, or asking about something completely different?

Return ONLY a JSON object:
{{
    "is_relevant": true/false,
    "confidence": 0.0 to 1.0,
    "reasoning": "brief explanation"
}}

Guidelines:
- If user asks about a completely different service/appointment, return false
- If user provides information related to pending action, return true
- If user explicitly says they don't need the pending action, return false
- If unclear but seems related, return confidence < 0.7
"""

            if conversation_history:
                # Add recent history for better context
                history_text = "\n".join([
                    f"{msg.get('role', 'user')}: {msg.get('content', '')}"
                    for msg in conversation_history[-3:]
                ])
                prompt += f"\n\nRecent conversation:\n{history_text}"

            factory = await self._get_factory()
            response = await factory.generate(
                messages=[
                    {
                        "role": "system",
                        "content": "You are an expert at determining conversation context relevance. Return valid JSON only."
                    },
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                model=self.model,
                temperature=0.1,
                response_format={"type": "json_object"}
            )

            result = json.loads(response.content)

            is_relevant = result.get('is_relevant', False)
            confidence = float(result.get('confidence', 0.0))
            reasoning = result.get('reasoning', '')

            logger.info(
                f"Context relevance check: {is_relevant} "
                f"(confidence: {confidence:.2f}) - {reasoning}"
            )

            return is_relevant, confidence, reasoning

        except Exception as e:
            logger.error(f"Context relevance check failed: {e}", exc_info=True)
            # Conservative default: assume not relevant to avoid confusion
            return False, 0.0, f"Error checking relevance: {str(e)}"

    async def extract_current_intent(
        self,
        message: str,
        language: str = 'en'
    ) -> Dict[str, Any]:
        """
        Extract the user's current intent from their message

        Args:
            message: User's message
            language: User's language

        Returns:
            Dict with intent, entities, and urgency
        """

        try:
            prompt = f"""Analyze this user message and extract the intent:

Message: "{message}"
Language: {language}

Return ONLY a JSON object:
{{
    "intent": "service_inquiry" | "book_appointment" | "reschedule" | "cancel" | "ask_price" | "ask_availability" | "other",
    "entities": {{
        "service_name": "extracted service if mentioned",
        "doctor_name": "extracted doctor if mentioned",
        "date": "extracted date if mentioned"
    }},
    "is_new_request": true/false,  // Is this a new request or follow-up?
    "urgency": "high" | "medium" | "low"
}}
"""

            factory = await self._get_factory()
            response = await factory.generate(
                messages=[
                    {
                        "role": "system",
                        "content": "You are an expert at extracting conversation intent. Return valid JSON only."
                    },
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                model=self.model,
                temperature=0.1,
                response_format={"type": "json_object"}
            )

            result = json.loads(response.content)
            logger.info(f"Extracted intent: {result.get('intent')} (new_request: {result.get('is_new_request')})")

            return result

        except Exception as e:
            logger.error(f"Intent extraction failed: {e}")
            return {
                'intent': 'other',
                'entities': {},
                'is_new_request': True,
                'urgency': 'medium'
            }


# Singleton instance
_context_checker: Optional[ContextRelevanceChecker] = None


def get_context_relevance_checker() -> ContextRelevanceChecker:
    """Get or create singleton ContextRelevanceChecker instance"""
    global _context_checker
    if _context_checker is None:
        _context_checker = ContextRelevanceChecker()
    return _context_checker
