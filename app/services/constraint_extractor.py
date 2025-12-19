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

    # Meta-command patterns for complete context reset
    META_RESET_PATTERNS = {
        'ru': [
            'забудь всё', 'забудь все',  # Both ё and е variants
            'забудь про всё', 'забудь про все',  # Both ё and е variants
            'previous intents', 'начать заново', 'сбросить', 'начни сначала'
        ],
        'en': ['forget everything', 'start over', 'reset', 'previous intents', 'clear context', 'start fresh'],
        'es': ['olvida todo', 'empezar de nuevo', 'resetear', 'borrar todo'],
        'he': ['שכח הכל', 'התחל מחדש', 'איפוס']
    }

    # Keyword patterns for negations/exclusions
    FORGET_KEYWORDS = {
        'ru': ['забудь', 'забудьте', 'не нужен', 'не надо', 'не хочу'],
        'en': ['forget', 'don\'t need', 'don\'t want', 'not interested'],
        'es': ['olvida', 'no necesito', 'no quiero'],
        'he': ['שכח', 'לא צריך', 'לא רוצה']
    }

    # Blacklist of sentence words that indicate extraction failure
    SENTENCE_FRAGMENT_WORDS = {
        'ru': ['списке', 'врачей', 'сказал', 'сказала', 'записать', 'нашли', 'выдали',
               'попросил', 'попросила', 'которого', 'которую', 'потом', 'после'],
        'en': ['list', 'said', 'told', 'asked', 'found', 'showed', 'doctor', 'doctors',
               'which', 'whom', 'that', 'then', 'after'],
        'es': ['lista', 'dijo', 'preguntó', 'encontró', 'mostró', 'médico', 'médicos',
               'cual', 'quien', 'entonces', 'después'],
        'he': ['רשימה', 'אמר', 'אמרה', 'ביקש', 'מצא', 'הראה', 'רופא', 'רופאים']
    }

    SWITCH_KEYWORDS = {
        'ru': ['вместо', 'лучше', 'хочу', 'давайте', 'переключимся'],
        'en': ['instead', 'rather', 'want', 'let\'s switch'],
        'es': ['en lugar de', 'prefiero', 'quiero'],
        'he': ['במקום', 'עדיף', 'רוצה']
    }

    # Time-related keywords for extraction
    TOMORROW_KEYWORDS = ['завтра', 'tomorrow', 'mañana', 'מחר']
    TODAY_KEYWORDS = ['сегодня', 'today', 'hoy', 'היום']

    # Time extraction patterns (captures hour)
    TIME_PATTERNS = [
        r'(\d{1,2})\s*(?:утра|am|часов?|ч\.?)',  # 11 утра, 11am, 11 часов
        r'в\s*(\d{1,2})(?:\s|$|,|\.|:)',  # в 11
        r'на\s*(\d{1,2})(?:\s|$|,|\.)',  # на 11
        r'at\s*(\d{1,2})',  # at 11
        r'^(\d{1,2})$',  # Just "11" alone
        r',\s*(\d{1,2})(?:\s|$)',  # "Завтра, 11"
    ]

    def detect_meta_reset(self, message: str, language: str = 'ru') -> bool:
        """
        Detect if user wants to reset conversation context entirely.

        This is for meta-commands like "forget everything" or "previous intents"
        that should trigger a complete context reset, not just entity exclusion.

        Returns:
            True if meta-reset pattern detected, False otherwise
        """
        message_lower = message.lower()
        patterns = self.META_RESET_PATTERNS.get(language, self.META_RESET_PATTERNS['en'])

        for pattern in patterns:
            if pattern in message_lower:
                logger.info(f"🔄 Meta-reset detected: '{pattern}' in message")
                return True

        return False

    def detect_forget_pattern(self, message: str, language: str = 'ru') -> Optional[List[str]]:
        """
        Detect 'Forget about X' patterns with validation.

        Returns:
            List of entities to exclude (e.g., ["Dan", "пломба"])
        """
        message_lower = message.lower()
        keywords = self.FORGET_KEYWORDS.get(language, self.FORGET_KEYWORDS['en'])

        entities_to_exclude = []

        for keyword in keywords:
            if keyword in message_lower:
                # Extract what comes after the keyword - TIGHTENED with length limit
                pattern = rf'{keyword}\s+(про\s+)?([а-яёa-z\s]{{3,25}}?)(?:\s+и\s+|\s*$|[.,!?])'
                matches = re.findall(pattern, message_lower, re.IGNORECASE)

                for match in matches:
                    entity = match[-1].strip()
                    if entity and self._validate_extracted_constraint(entity, language):
                        entities_to_exclude.append(entity)
                    elif entity:
                        logger.warning(f"🚫 Invalid entity in forget pattern: '{entity}'")

        return entities_to_exclude if entities_to_exclude else None

    def _validate_extracted_constraint(self, entity: str, language: str = 'ru') -> bool:
        """
        Validate extracted constraint to prevent garbage extraction.

        Returns True if entity is valid, False if it's garbage/sentence fragment.

        Validation layers:
        1. Length check (>50 chars → reject)
        2. Word count (>4 words → reject)
        3. Sentence fragment detection (contains blacklist words → reject)
        4. Verb detection (ends in verb suffixes → reject)
        """
        if not entity or not isinstance(entity, str):
            return False

        entity = entity.strip()

        # Layer 1: Length check
        if len(entity) > 50:
            logger.warning(f"🚫 Rejected constraint (too long): '{entity[:50]}...'")
            return False

        # Layer 2: Word count
        words = entity.split()
        if len(words) > 4:
            logger.warning(f"🚫 Rejected constraint (too many words): '{entity}'")
            return False

        # Layer 3: Sentence fragment detection
        blacklist = self.SENTENCE_FRAGMENT_WORDS.get(language, self.SENTENCE_FRAGMENT_WORDS['en'])
        entity_lower = entity.lower()
        for blacklist_word in blacklist:
            if blacklist_word in entity_lower:
                logger.warning(f"🚫 Rejected constraint (contains '{blacklist_word}'): '{entity}'")
                return False

        # Layer 4: Verb detection (Russian-specific)
        if language == 'ru':
            # Check for verb endings (past tense, infinitive)
            if any(entity_lower.endswith(suffix) for suffix in ['ли', 'ла', 'ло', 'ть', 'ти', 'чь']):
                logger.warning(f"🚫 Rejected constraint (verb detected): '{entity}'")
                return False

        return True

    def detect_switch_pattern(self, message: str, language: str = 'ru') -> Optional[Tuple[str, str]]:
        """
        Detect 'Instead of X, I want Y' patterns with validation.

        Returns:
            Tuple of (exclude_entity, desired_entity) or None
        """
        message_lower = message.lower()

        # Tightened patterns with length limits to prevent sentence capture
        # Pattern 1: Explicit "instead of" switch
        patterns = [
            r'вместо\s+([а-яё\s]{3,25}?)\s+(?:хочу|желаю|нужн[оа])\s+([а-яё\s]{3,25}?)(?:\s*$|[.,!?])',
            # Pattern 2: "not X, but Y" - MORE RESTRICTIVE (shorter capture, Cyrillic only)
            r'не\s+([а-яё\s]{3,20}?),?\s+а\s+([а-яё\s]{3,20}?)(?:\s*$|[.,!?])',
            # Pattern 3: English
            r'instead of\s+([a-z\s]{3,25}?),?\s+(?:I want|prefer)\s+([a-z\s]{3,25}?)(?:\s*$|[.,!?])'
        ]

        for pattern in patterns:
            match = re.search(pattern, message_lower, re.IGNORECASE)
            if match:
                exclude_entity = match.group(1).strip()
                desired_entity = match.group(2).strip()

                # CRITICAL: Validate both entities before returning
                if not self._validate_extracted_constraint(exclude_entity, language):
                    logger.warning(f"🚫 Invalid exclude entity in switch: '{exclude_entity}'")
                    continue

                if not self._validate_extracted_constraint(desired_entity, language):
                    logger.warning(f"🚫 Invalid desired entity in switch: '{desired_entity}'")
                    continue

                # Both entities passed validation
                return (exclude_entity, desired_entity)

        return None

    def extract_date_time(self, message: str, reference_date: datetime, language: str = 'ru') -> Optional[Dict[str, Any]]:
        """
        Extract date and time from user message.

        Args:
            message: User message like "Завтра, 11" or "mañana a las 10"
            reference_date: Current date for calculation
            language: Language code

        Returns:
            Dict with:
            - date: "2025-12-19" (ISO format)
            - time: "11:00" or None
            - display: "завтра в 11:00"
        """
        message_lower = message.lower().strip()
        result = {'date': None, 'time': None, 'display': None}

        # Extract date
        if any(kw in message_lower for kw in self.TOMORROW_KEYWORDS):
            target_date = reference_date + timedelta(days=1)
            result['date'] = target_date.strftime('%Y-%m-%d')
            result['display'] = 'завтра' if language == 'ru' else 'tomorrow'
        elif any(kw in message_lower for kw in self.TODAY_KEYWORDS):
            result['date'] = reference_date.strftime('%Y-%m-%d')
            result['display'] = 'сегодня' if language == 'ru' else 'today'

        # Extract time
        for pattern in self.TIME_PATTERNS:
            match = re.search(pattern, message_lower)
            if match:
                hour = int(match.group(1))
                if 0 <= hour <= 23:
                    # Assume AM for hours 6-11 if no AM/PM specified
                    result['time'] = f"{hour:02d}:00"
                    if result['display']:
                        result['display'] += f" в {hour}:00" if language == 'ru' else f" at {hour}:00"
                    else:
                        result['display'] = f"{hour}:00"
                    break

        # Return None if nothing was extracted
        if result['date'] is None and result['time'] is None:
            return None

        logger.info(f"📅 Extracted date/time from '{message}': {result}")
        return result

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
