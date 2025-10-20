"""
Intent Router Module

Provides regex-based intent detection for conversation messages.
Detects user intents like greetings, booking requests, confirmations, denials,
and topic changes. Uses context-aware logic to handle ambiguous inputs.

Target: 70% intent accuracy baseline (ML upgrade to 96% deferred to future).

Key Features:
- Regex-based pattern matching for Russian and English
- Context-aware greeting detection (only in GREETING state)
- Ambiguity detection for short/unclear responses
- Topic change detection for clinic info queries
- Supports confirmation/denial detection with disambiguation
- Returns structured IntentResult with topic/entities
"""

import re
from typing import Optional, Dict, Any

from .models import ConversationState, IntentResult


class Intent:
    """
    Intent types for conversation messages.

    Constants:
        GREETING: Initial greeting (e.g., "привет", "hello")
        BOOKING_INTENT: User wants to book appointment (e.g., "записаться")
        CONFIRM: User confirms (e.g., "да", "yes")
        DENY: User denies/cancels (e.g., "нет", "no")
        TOPIC_CHANGE: User asks about clinic info (e.g., "адрес", "hours")
        DISAMBIGUATE: Unclear/ambiguous response (e.g., "да" alone, short messages)
        INFORMATION: User providing information (default intent)
        ACKNOWLEDGMENT: Casual acknowledgment mid-conversation (e.g., "привет" during booking)
    """
    GREETING = "greeting"
    BOOKING_INTENT = "booking_intent"
    CONFIRM = "confirm"
    DENY = "deny"
    TOPIC_CHANGE = "topic_change"
    DISAMBIGUATE = "disambiguate"
    INFORMATION = "information"
    ACKNOWLEDGMENT = "acknowledgment"


class IntentRouter:
    """
    Regex-based intent detection (70% baseline accuracy).

    Detects user intent from message text using pattern matching.
    Context-aware: uses current_state to determine if greeting should
    trigger state reset or just acknowledgment.

    Usage:
        >>> router = IntentRouter()
        >>> intent = router.detect_intent("записаться к доктору", ConversationState.GREETING)
        >>> print(intent)  # Intent.BOOKING_INTENT

        >>> # Context-aware greeting
        >>> intent = router.detect_intent("привет", ConversationState.GREETING)
        >>> print(intent)  # Intent.GREETING
        >>> intent = router.detect_intent("привет", ConversationState.AWAITING_CONFIRMATION)
        >>> print(intent)  # Intent.ACKNOWLEDGMENT (not a greeting mid-conversation)
    """

    def detect_intent(
        self,
        message: str,
        current_state: ConversationState,
        last_prompt: Optional[Dict[str, Any]] = None
    ) -> IntentResult:
        """
        Detect user intent from message with context awareness.

        Context-aware intent detection that considers the current conversation state
        and last prompt. Returns structured IntentResult with topic and entities.

        Args:
            message: User's message text
            current_state: Current FSM conversation state
            last_prompt: Context from last question asked (optional)

        Returns:
            IntentResult: Structured intent with label, topic, and entities

        Example:
            >>> router = IntentRouter()
            >>> intent = router.detect_intent("сколько стоят виниры", ConversationState.GREETING)
            >>> print(intent.label)  # Intent.TOPIC_CHANGE
            >>> print(intent.topic)  # "pricing"
            >>> print(intent.entities)  # {"service": "veneers"}
        """
        msg_lower = message.lower().strip()

        # Context-aware confirmation/denial (if last_prompt set)
        if last_prompt and last_prompt.get("kind") == "yes_no":
            if self._is_confirmation(msg_lower):
                return IntentResult(
                    label=Intent.CONFIRM,
                    entities={"prompt_context": last_prompt["question"]}
                )
            elif self._is_negation(msg_lower):
                return IntentResult(
                    label=Intent.DENY,
                    entities={"prompt_context": last_prompt["question"]}
                )

        # Greeting detection (context-aware)
        if self._is_greeting(msg_lower):
            # Only treat as greeting if in GREETING state
            # Otherwise it's just casual acknowledgment
            if current_state == ConversationState.GREETING:
                return IntentResult(label=Intent.GREETING)
            else:
                return IntentResult(label=Intent.ACKNOWLEDGMENT)

        # Booking intent
        if self._is_booking_intent(msg_lower):
            return IntentResult(label=Intent.BOOKING_INTENT)

        # Confirmation (context-dependent)
        if self._is_confirmation(msg_lower):
            # If message is just "да" with no context, flag for disambiguation
            if msg_lower in ["да", "yes"] and current_state not in [
                ConversationState.AWAITING_CONFIRMATION,
                ConversationState.DISAMBIGUATING
            ]:
                return IntentResult(label=Intent.DISAMBIGUATE)
            return IntentResult(label=Intent.CONFIRM)

        # Negation
        if self._is_negation(msg_lower):
            return IntentResult(label=Intent.DENY)

        # Topic change (user asking about clinic info, not booking)
        if self._is_topic_change(msg_lower):
            topic = self._classify_topic(msg_lower)
            entities = self._extract_entities(msg_lower, topic)
            return IntentResult(
                label=Intent.TOPIC_CHANGE,
                topic=topic,
                entities=entities
            )

        # Ambiguous short responses
        if len(msg_lower) <= 3 or msg_lower in ["да", "нет", "ok", "ок"]:
            return IntentResult(label=Intent.DISAMBIGUATE)

        # Default: user providing information
        return IntentResult(label=Intent.INFORMATION)

    def _is_greeting(self, msg: str) -> bool:
        """
        Check if message is a greeting.

        Matches common Russian and English greetings.

        Args:
            msg: Lowercase message text

        Returns:
            bool: True if message contains greeting pattern

        Example:
            >>> router = IntentRouter()
            >>> router._is_greeting("привет")
            True
            >>> router._is_greeting("здравствуйте, хочу записаться")
            True
        """
        patterns = [
            r'\b(привет|здравствуйте|добрый день|добрый вечер|доброе утро)\b',
            r'\b(hi|hello|hey|good morning|good afternoon)\b'
        ]
        return any(re.search(p, msg, re.IGNORECASE) for p in patterns)

    def _is_booking_intent(self, msg: str) -> bool:
        """
        Check if message expresses booking intent.

        Matches keywords related to scheduling appointments.

        Args:
            msg: Lowercase message text

        Returns:
            bool: True if message contains booking keywords

        Example:
            >>> router = IntentRouter()
            >>> router._is_booking_intent("хочу записаться к доктору")
            True
            >>> router._is_booking_intent("нужен приём")
            True
        """
        patterns = [
            r'\b(записаться|запись|хочу к|нужен приём)\b',
            r'\b(appointment|book|schedule|see doctor)\b'
        ]
        return any(re.search(p, msg, re.IGNORECASE) for p in patterns)

    def _is_confirmation(self, msg: str) -> bool:
        """
        Check if message is confirmation.

        Matches affirmative responses in Russian and English.

        Args:
            msg: Lowercase message text

        Returns:
            bool: True if message contains confirmation pattern

        Example:
            >>> router = IntentRouter()
            >>> router._is_confirmation("да")
            True
            >>> router._is_confirmation("подтверждаю")
            True
        """
        patterns = [
            r'^(да|yes|ага|угу|подтверждаю|согласен|правильно)$',
            r'\b(confirm|correct|that\'s right)\b'
        ]
        return any(re.search(p, msg, re.IGNORECASE) for p in patterns)

    def _is_negation(self, msg: str) -> bool:
        """
        Check if message is negation.

        Matches negative responses and cancellation keywords.

        Args:
            msg: Lowercase message text

        Returns:
            bool: True if message contains negation pattern

        Example:
            >>> router = IntentRouter()
            >>> router._is_negation("нет")
            True
            >>> router._is_negation("не подходит")
            True
        """
        patterns = [
            r'^(нет|no|не подходит|отменить|не то)$',
            r'\b(cancel|wrong|incorrect)\b'
        ]
        return any(re.search(p, msg, re.IGNORECASE) for p in patterns)

    def _is_topic_change(self, msg: str) -> bool:
        """
        Check if user is changing topic (asking about clinic info).

        Matches queries about clinic address, hours, prices, etc.

        Args:
            msg: Lowercase message text

        Returns:
            bool: True if message contains topic change keywords

        Example:
            >>> router = IntentRouter()
            >>> router._is_topic_change("какой у вас адрес?")
            True
            >>> router._is_topic_change("сколько стоит приём?")
            True
        """
        patterns = [
            r'\b(адрес|где находится|как добраться|телефон|контакт)\b',
            r'\b(часы работы|когда открыто|график|расписание)\b',
            r'\b(цена|стоимость|сколько стоит|прайс)\b',
            r'\b(address|location|phone|hours|price|cost)\b'
        ]
        return any(re.search(p, msg, re.IGNORECASE) for p in patterns)

    def _classify_topic(self, msg: str) -> str:
        """
        Classify topic for TOPIC_CHANGE intents.

        Args:
            msg: Lowercase message text

        Returns:
            str: Topic type (pricing, hours, address, phone, services, general)

        Example:
            >>> router = IntentRouter()
            >>> router._classify_topic("сколько стоят виниры")
            'pricing'
            >>> router._classify_topic("когда вы работаете")
            'hours'
        """
        if re.search(r'\b(цена|стоимость|сколько стоит|прайс|price|cost)\b', msg, re.I):
            return "pricing"

        if re.search(r'\b(часы работы|когда открыто|график|расписание|hours)\b', msg, re.I):
            return "hours"

        if re.search(r'\b(адрес|где находится|как добраться|location|address)\b', msg, re.I):
            return "address"

        if re.search(r'\b(телефон|контакт|связаться|phone|contact)\b', msg, re.I):
            return "phone"

        if re.search(r'\b(услуги|что делаете|чем занимаетесь|services)\b', msg, re.I):
            return "services"

        return "general"

    def _extract_entities(self, msg: str, topic: str) -> Dict[str, Any]:
        """
        Extract entities based on topic.

        Args:
            msg: Lowercase message text
            topic: Classified topic

        Returns:
            dict: Extracted entities

        Example:
            >>> router = IntentRouter()
            >>> router._extract_entities("сколько стоят виниры", "pricing")
            {'service': 'veneers'}
        """
        entities = {}

        if topic == "pricing":
            # Extract service name
            service_patterns = {
                "veneers": r'\b(винир|veneer)',
                "cleaning": r'\b(чистк|cleaning)',
                "filling": r'\b(пломб|filling)',
                "implant": r'\b(имплант|implant)',
                "crown": r'\b(корон|crown)',
                "whitening": r'\b(отбелива|whitening)',
            }

            for service, pattern in service_patterns.items():
                if re.search(pattern, msg, re.I):
                    entities["service"] = service
                    break

        return entities
