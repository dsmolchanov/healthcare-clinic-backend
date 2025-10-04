# clinics/backend/app/services/response_analyzer.py

import logging
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

class ResponseAnalyzer:
    """
    Phase 8: Fast heuristic-based response analysis (no LLM)

    Replaces 7s LLM call with <10ms pattern matching
    Performance: 7000ms → <10ms (700x faster)
    """

    def __init__(self):
        # No OpenAI client needed - pure heuristics
        logger.info("✅ ResponseAnalyzer initialized (heuristic mode, no LLM)")

    async def analyze_agent_response(
        self,
        response: str,
        conversation_context: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Analyze agent response using fast heuristics (<10ms)

        Previously: 7s LLM call
        Now: Instant regex + simple logic

        Returns:
            {
                'turn_status': 'user_turn' | 'agent_action_pending' | 'resolved',
                'promises_followup': bool,
                'followup_action': Optional[str],
                'asks_question': bool,
                'question_text': Optional[str],
                'provides_complete_info': bool,
                'reasoning': str
            }
        """
        response_lower = response.lower()

        # Check for promises to follow up
        followup_indicators = [
            'let me check', "i'll check", 'get back to you',
            'let me verify', "i'll verify", "i'll find out",
            'checking with', 'consulting', 'looking into',
            'let me see', "i'll see", 'need to check'
        ]
        promises_followup = any(ind in response_lower for ind in followup_indicators)

        # Extract followup action if promised
        followup_action = None
        if promises_followup:
            # Simple extraction - just use the relevant sentence
            for ind in followup_indicators:
                if ind in response_lower:
                    idx = response_lower.index(ind)
                    # Extract up to 100 chars or first period
                    followup_action = response[idx:idx+100].split('.')[0]
                    break

        # Check if asks question
        asks_question = '?' in response
        question_text = None
        if asks_question:
            # Extract last question
            questions = [s.strip() for s in response.split('?') if s.strip()]
            question_text = questions[-1] + '?' if questions else None

        # Determine turn status
        if promises_followup:
            turn_status = 'agent_action_pending'
        elif asks_question:
            turn_status = 'user_turn'
        elif any(word in response_lower for word in ['goodbye', 'bye', 'take care', 'have a great']):
            turn_status = 'resolved'
        else:
            turn_status = 'user_turn'  # Default

        logger.info(f"Fast response analysis: turn_status={turn_status}, promises_followup={promises_followup}")

        return {
            'turn_status': turn_status,
            'promises_followup': promises_followup,
            'followup_action': followup_action,
            'asks_question': asks_question,
            'question_text': question_text,
            'provides_complete_info': not promises_followup,
            'reasoning': 'Heuristic-based analysis (instant, no LLM)'
        }
