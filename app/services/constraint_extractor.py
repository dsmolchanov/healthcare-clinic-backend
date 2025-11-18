"""
Constraint Extraction Service
Extracts conversation constraints from user messages using keyword detection + LLM
"""

from typing import Optional, Tuple, List, Dict, Any
import re
from datetime import datetime, timedelta
import json
import logging

logger = logging.getLogger(__name__)


class ConstraintExtractor:
    """Extracts constraints from user messages"""

    # Keyword patterns for negations/exclusions
    FORGET_KEYWORDS = {
        'ru': ['забудь', 'забудьте', 'не нужен', 'не надо', 'не хочу'],
        'en': ['forget', 'don\'t need', 'don\'t want', 'not interested'],
        'es': ['olvida', 'no necesito', 'no quiero'],
        'he': ['שכח', 'לא צריך', 'לא רוצה']
    }

    SWITCH_KEYWORDS = {
        'ru': ['вместо', 'лучше', 'хочу', 'давайте', 'переключимся'],
        'en': ['instead', 'rather', 'want', 'let\'s switch'],
        'es': ['en lugar de', 'prefiero', 'quiero'],
        'he': ['במקום', 'עדיף', 'רוצה']
    }

    def detect_forget_pattern(self, message: str, language: str = 'ru') -> Optional[List[str]]:
        """
        Detect 'Forget about X' patterns.

        Returns:
            List of entities to exclude (e.g., ["Dan", "пломба"])
        """
        message_lower = message.lower()
        keywords = self.FORGET_KEYWORDS.get(language, self.FORGET_KEYWORDS['en'])

        entities_to_exclude = []

        for keyword in keywords:
            if keyword in message_lower:
                # Extract what comes after the keyword
                pattern = rf'{keyword}\s+(про\s+)?(.+?)(?:\s+и\s+|\s*$|[.,!?])'
                matches = re.findall(pattern, message_lower, re.IGNORECASE)

                for match in matches:
                    entity = match[-1].strip()
                    if entity:
                        entities_to_exclude.append(entity)

        return entities_to_exclude if entities_to_exclude else None

    def detect_switch_pattern(self, message: str, language: str = 'ru') -> Optional[Tuple[str, str]]:
        """
        Detect 'Instead of X, I want Y' patterns.

        Returns:
            Tuple of (exclude_entity, desired_entity) or None
        """
        message_lower = message.lower()

        # Pattern: "вместо X хочу Y" or "не X, а Y"
        patterns = [
            r'вместо\s+(.+?)\s+хочу\s+(.+?)(?:\s*$|[.,!?])',
            r'не\s+(.+?),?\s+а\s+(.+?)(?:\s*$|[.,!?])',
            r'instead of\s+(.+?),?\s+(?:I want|prefer)\s+(.+?)(?:\s*$|[.,!?])'
        ]

        for pattern in patterns:
            match = re.search(pattern, message_lower, re.IGNORECASE)
            if match:
                exclude_entity = match.group(1).strip()
                desired_entity = match.group(2).strip()
                return (exclude_entity, desired_entity)

        return None

    def normalize_time_window(self, time_expression: str, reference_date: datetime, language: str = 'ru') -> Optional[Tuple[str, str, str]]:
        """
        Normalize relative time expressions to absolute dates.

        Args:
            time_expression: "следующая неделя", "next week", etc.
            reference_date: Current date for calculation
            language: Language code

        Returns:
            Tuple of (start_date_iso, end_date_iso, display_str) or None
        """
        time_lower = time_expression.lower()

        # Detect "next week"
        next_week_keywords = {
            'ru': ['следующая неделя', 'следующей неделе', 'на следующей неделе'],
            'en': ['next week'],
            'es': ['la próxima semana', 'próxima semana'],
            'he': ['שבוע הבא']
        }

        keywords = next_week_keywords.get(language, next_week_keywords['en'])

        for keyword in keywords:
            if keyword in time_lower:
                # Calculate next Monday-Sunday
                days_until_monday = (7 - reference_date.weekday()) % 7
                if days_until_monday == 0:
                    days_until_monday = 7  # If today is Monday, next week starts in 7 days

                start_date = reference_date + timedelta(days=days_until_monday)
                end_date = start_date + timedelta(days=6)

                # Format display string based on language
                if language == 'ru':
                    display = f"{start_date.day}–{end_date.day} {self._month_name_ru(end_date.month)}"
                else:
                    display = f"{start_date.strftime('%d')}–{end_date.strftime('%d %B')}"

                return (
                    start_date.date().isoformat(),
                    end_date.date().isoformat(),
                    display
                )

        return None

    def _month_name_ru(self, month: int) -> str:
        """Get Russian month name (genitive case for date ranges)"""
        months = {
            1: "января", 2: "февраля", 3: "марта", 4: "апреля",
            5: "мая", 6: "июня", 7: "июля", 8: "августа",
            9: "сентября", 10: "октября", 11: "ноября", 12: "декабря"
        }
        return months.get(month, "")

    async def extract_with_llm(
        self,
        message: str,
        conversation_history: List[Dict],
        llm_client
    ) -> Dict[str, Any]:
        """
        Use LLM to extract constraints when keyword matching insufficient.

        Returns dict with:
            - desired_service: str | None
            - desired_doctor: str | None
            - excluded_doctors: List[str]
            - excluded_services: List[str]
            - confidence: float (0-1)
        """

        prompt = f"""Extract conversation constraints from this message.

Message: "{message}"

Recent conversation:
{json.dumps(conversation_history[-3:], indent=2, ensure_ascii=False)}

Extract:
1. What service does the user want? (e.g., "veneers", "cleaning")
2. What doctor do they want? (e.g., "Andrea", "Dr. Smith")
3. What doctors should be EXCLUDED? (e.g., user said "forget about Dan")
4. What services should be EXCLUDED? (e.g., user said "not fillings")

Respond ONLY with JSON:
{{
  "desired_service": "service name or null",
  "desired_doctor": "doctor name or null",
  "excluded_doctors": ["doctor1", "doctor2"],
  "excluded_services": ["service1", "service2"],
  "confidence": 0.95
}}
"""

        try:
            response = await llm_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=200
            )

            result = json.loads(response.choices[0].message.content)
            return result
        except Exception as e:
            logger.error(f"LLM constraint extraction failed: {e}")
            return {
                "desired_service": None,
                "desired_doctor": None,
                "excluded_doctors": [],
                "excluded_services": [],
                "confidence": 0.0
            }
