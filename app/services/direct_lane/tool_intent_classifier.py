# File: clinics/backend/app/services/direct_lane/tool_intent_classifier.py

from dataclasses import dataclass
from typing import Optional, Dict, Any, List
import re
from enum import Enum
import time
import logging
from app.services.language_service import LanguageService

logger = logging.getLogger(__name__)

class DirectToolIntent(str, Enum):
    """Tool intents that can bypass LangGraph orchestration"""
    FAQ_QUERY = "faq_query"
    PRICE_QUERY = "price_query"
    CHECK_AVAILABILITY = "check_availability"
    BOOK_APPOINTMENT = "book_appointment"
    CANCEL_APPOINTMENT = "cancel_appointment"
    RESCHEDULE_APPOINTMENT = "reschedule_appointment"
    UNKNOWN = "unknown"

@dataclass
class ToolIntentMatch:
    """Result of tool intent classification"""
    intent: DirectToolIntent
    confidence: float  # 0.0 - 1.0
    extracted_args: Dict[str, Any]
    reasoning: str
    language: str = "en"
    duration_ms: int = 0

class ToolIntentClassifier:
    """
    Fast intent classifier using regex patterns + optional lightweight LLM.

    STRICT BUDGETS:
    - Regex classification: < 5ms
    - LLM argument completion (if needed): < 250ms
    - Total budget: < 300ms (fail fast if exceeded)
    """

    # Multilingual patterns (en/es/pt)
    PATTERNS = {
        "en": {
            "faq": [
                r"\b(what|when|where|why|how) (is|are|does|do|can)\b",
                r"\b(hours|schedule|open|closed|location|address|parking)\b",
                r"\b(insurance|accept|take|cover|payment|cash|credit)\b",
                r"\b(policy|policies|cancel|refund|reschedule)\b",
            ],
            "price": [
                r"\b(cost|price|fee|charge|expensive|cheap|afford)\b",
                r"\b(how much)\b",
                r"\b(pay|payment|bill|total)\b",
            ],
            "availability": [
                r"\b(available|availability|free|open|slot|appointment)\b.{0,20}\b(today|tomorrow|next week|this week)\b",
                r"\b(schedule|book|reserve|make).{0,20}\b(appointment|visit|consultation)\b",
                r"\b(can i|could i|want to|need to).{0,20}\b(see|meet|visit)\b",
            ],
            "booking": [
                r"\b(book|schedule|reserve).{0,30}\b(for|on|at)\s+\d{1,2}[:/]\d{2}",
                r"\b(confirm|yes|ok|proceed).{0,20}\b(book|appointment|reservation)\b",
            ],
        },
        "es": {
            "faq": [
                r"\b(qué|cuándo|dónde|por qué|cómo) (es|son|hace|puedo)\b",
                r"\b(horarios|horario|abierto|cerrado|ubicación|dirección)\b",
                r"\b(seguro|aceptan|toman|cubre|pago|efectivo|crédito)\b",
            ],
            "price": [
                r"\b(costo|precio|tarifa|cargo|caro|barato)\b",
                r"\b(cuánto cuesta|cuánto es)\b",
            ],
            "availability": [
                r"\b(disponible|disponibilidad|libre|abierto|cita)\b.{0,20}\b(hoy|mañana|próxima semana)\b",
                r"\b(agendar|reservar|programar).{0,20}\b(cita|visita|consulta)\b",
            ],
            "booking": [
                r"\b(reservar|agendar|programar).{0,30}\b(para|en|a las)\s+\d{1,2}[:/]\d{2}",
                r"\b(confirmar|sí|ok|proceder).{0,20}\b(reserva|cita)\b",
            ],
        },
        "pt": {
            "faq": [
                r"\b(o que|quando|onde|por que|como) (é|são|faz|posso)\b",
                r"\b(horários|horário|aberto|fechado|localização|endereço)\b",
            ],
            "price": [
                r"\b(custo|preço|taxa|caro|barato)\b",
                r"\b(quanto custa|quanto é)\b",
            ],
            "availability": [
                r"\b(disponível|disponibilidade|livre|aberto|consulta)\b.{0,20}\b(hoje|amanhã|próxima semana)\b",
            ],
            "booking": [
                r"\b(reservar|agendar|marcar).{0,30}\b(para|em|às)\s+\d{1,2}[:/]\d{2}",
                r"\b(confirmar|sim|ok|prosseguir).{0,20}\b(reserva|consulta)\b",
            ],
        },
    }

    def __init__(self):
        # Initialize LanguageService for unified detection
        self.language_service = LanguageService()
        # Compile patterns for performance
        self._compiled_patterns = {}
        for lang, patterns in self.PATTERNS.items():
            self._compiled_patterns[lang] = {
                intent: [re.compile(p, re.IGNORECASE) for p in pattern_list]
                for intent, pattern_list in patterns.items()
            }

    def classify(
        self,
        message: str,
        context: Optional[Dict] = None,
        max_duration_ms: int = 300
    ) -> ToolIntentMatch:
        """
        Classify message intent with STRICT TIME BUDGET.

        Args:
            message: User message
            context: Session context (previous intent, state, etc.)
            max_duration_ms: Maximum time allowed (default 300ms)

        Returns:
            ToolIntentMatch with intent, confidence, extracted args
        """
        start_time = time.time()
        message_lower = message.lower()

        # Detect language using LanguageService (single source of truth)
        language = self.language_service.detect_sync(message_lower)

        # Get patterns for detected language
        patterns = self._compiled_patterns.get(language, self._compiled_patterns["en"])

        # Check booking first (most specific, context-dependent)
        if booking_match := self._match_booking(message_lower, context, patterns.get("booking", []), language):
            booking_match.duration_ms = int((time.time() - start_time) * 1000)
            return booking_match

        # Check availability
        if avail_match := self._match_availability(message_lower, context, patterns.get("availability", []), language):
            avail_match.duration_ms = int((time.time() - start_time) * 1000)
            return avail_match

        # Check FAQ
        if faq_match := self._match_faq(message_lower, context, patterns.get("faq", []), language):
            faq_match.duration_ms = int((time.time() - start_time) * 1000)
            return faq_match

        # Check price
        if price_match := self._match_price(message_lower, context, patterns.get("price", []), language):
            price_match.duration_ms = int((time.time() - start_time) * 1000)
            return price_match

        # No match
        duration_ms = int((time.time() - start_time) * 1000)
        return ToolIntentMatch(
            intent=DirectToolIntent.UNKNOWN,
            confidence=0.0,
            extracted_args={},
            reasoning="No direct tool pattern matched",
            language=language,
            duration_ms=duration_ms
        )

    # NOTE: _detect_language was removed in Phase 1B.
    # Use self.language_service.detect_sync() instead (single source of truth).

    def _match_faq(
        self,
        message: str,
        context: Optional[Dict],
        patterns: List,
        language: str
    ) -> Optional[ToolIntentMatch]:
        """Match FAQ queries"""
        matches = sum(1 for regex in patterns if regex.search(message))

        if matches >= 2:
            return ToolIntentMatch(
                intent=DirectToolIntent.FAQ_QUERY,
                confidence=0.9,
                extracted_args={"query": message, "language": language},
                reasoning=f"Matched {matches} FAQ patterns in {language}",
                language=language
            )
        elif matches == 1:
            return ToolIntentMatch(
                intent=DirectToolIntent.FAQ_QUERY,
                confidence=0.6,
                extracted_args={"query": message, "language": language},
                reasoning=f"Matched 1 FAQ pattern in {language}",
                language=language
            )

        return None

    def _match_price(
        self,
        message: str,
        context: Optional[Dict],
        patterns: List,
        language: str
    ) -> Optional[ToolIntentMatch]:
        """Match price queries"""
        matches = sum(1 for regex in patterns if regex.search(message))

        if matches >= 1:
            # Extract service name (everything after price keyword)
            service_match = re.search(
                r"(?:cost|price|fee|charge|how much|cuánto cuesta|quanto custa).{0,5}(?:for|of|is|are|de)?\s+(.+)",
                message,
                re.IGNORECASE
            )
            service = service_match.group(1).strip() if service_match else message

            return ToolIntentMatch(
                intent=DirectToolIntent.PRICE_QUERY,
                confidence=0.85,
                extracted_args={"query": service, "language": language},
                reasoning=f"Matched {matches} price patterns in {language}",
                language=language
            )

        return None

    def _match_availability(
        self,
        message: str,
        context: Optional[Dict],
        patterns: List,
        language: str
    ) -> Optional[ToolIntentMatch]:
        """Match availability checks"""
        matches = sum(1 for regex in patterns if regex.search(message))

        if matches >= 1:
            # Extract date if present
            date_match = self._extract_date(message, language)

            return ToolIntentMatch(
                intent=DirectToolIntent.CHECK_AVAILABILITY,
                confidence=0.8 if date_match else 0.6,
                extracted_args={
                    "date": date_match,
                    "query": message,
                    "language": language
                },
                reasoning=f"Matched {matches} availability patterns in {language}",
                language=language
            )

        return None

    def _match_booking(
        self,
        message: str,
        context: Optional[Dict],
        patterns: List,
        language: str
    ) -> Optional[ToolIntentMatch]:
        """Match booking confirmations (context-aware)"""
        # Context-aware: only high confidence if we're in booking flow
        in_booking_flow = context and context.get("last_intent") == "check_availability"

        matches = sum(1 for regex in patterns if regex.search(message))

        if matches >= 1 and in_booking_flow:
            return ToolIntentMatch(
                intent=DirectToolIntent.BOOK_APPOINTMENT,
                confidence=0.9,
                extracted_args=self._extract_booking_args(message, context),
                reasoning="Booking confirmation in active flow",
                language=language
            )
        elif matches >= 1:
            return ToolIntentMatch(
                intent=DirectToolIntent.BOOK_APPOINTMENT,
                confidence=0.5,  # Lower without context
                extracted_args=self._extract_booking_args(message, context),
                reasoning="Booking pattern without active flow",
                language=language
            )

        return None

    def _extract_date(self, message: str, language: str) -> Optional[str]:
        """Extract date from message (YYYY-MM-DD format)"""
        from datetime import date, timedelta

        # Today/tomorrow (multilingual)
        today_keywords = {"en": "today", "es": "hoy", "pt": "hoje"}
        tomorrow_keywords = {"en": "tomorrow", "es": "mañana", "pt": "amanhã"}

        if today_keywords.get(language, "today") in message:
            return date.today().isoformat()
        if tomorrow_keywords.get(language, "tomorrow") in message:
            return (date.today() + timedelta(days=1)).isoformat()

        # Explicit date patterns
        date_patterns = [
            r"\d{4}-\d{2}-\d{2}",  # YYYY-MM-DD
            r"\d{1,2}/\d{1,2}/\d{4}",  # MM/DD/YYYY or DD/MM/YYYY
        ]

        for pattern in date_patterns:
            if match := re.search(pattern, message):
                return match.group(0)

        return None

    def _extract_booking_args(self, message: str, context: Optional[Dict]) -> Dict[str, Any]:
        """Extract booking arguments from message and context"""
        args = {}

        # Extract from context if available
        if context:
            args["selected_slot"] = context.get("selected_slot")
            args["doctor_id"] = context.get("doctor_id")
            args["service_id"] = context.get("service_id")

        # Extract from message
        time_match = re.search(r"\d{1,2}[:/]\d{2}\s*(am|pm)?", message, re.IGNORECASE)
        if time_match:
            args["time"] = time_match.group(0)

        return args
