"""
Fast-Path Router Service

Classifies messages into lanes for optimal processing:
- FAQ lane: <400ms P50 (template-only, no LLM)
- PRICE lane: <600ms P50 (alias lookup + template)
- SCHEDULING lane: Context-aware booking
- COMPLEX lane: Full LangGraph orchestration

Target: >70% of messages routed to fast-path (FAQ/PRICE)
"""

import logging
from enum import Enum
from typing import Dict, Any, Optional, Tuple

logger = logging.getLogger(__name__)


class Lane(str, Enum):
    """Message routing lanes"""
    FAQ = "faq"
    PRICE = "price"
    SERVICE_INFO = "service_info"  # For "how long", "what's included" questions
    SCHEDULING = "scheduling"
    COMPLEX = "complex"


class RouterService:
    """
    Routes messages to appropriate processing lanes based on:
    1. Session context (pending actions)
    2. Service alias matching
    3. FAQ pattern matching
    4. Scheduling keywords
    5. Default to complex lane
    """

    def __init__(self, language_service, session_service):
        """
        Initialize router service

        Args:
            language_service: LanguageService for text processing
            session_service: SessionService for context management
        """
        self.language = language_service
        self.session = session_service

        # Scheduling keywords by language
        self.scheduling_keywords = {
            'ru': {
                'appointment': {'запись', 'записаться', 'назначить', 'время', 'когда'},
                'reschedule': {'перенести', 'изменить', 'перезаписаться'},
                'cancel': {'отменить', 'отмена'},
            },
            'es': {
                'appointment': {'cita', 'agendar', 'reservar', 'turno', 'cuando'},
                'reschedule': {'cambiar', 'reprogramar', 'mover'},
                'cancel': {'cancelar', 'anular'},
            },
            'en': {
                'appointment': {'appointment', 'book', 'schedule', 'when', 'time'},
                'reschedule': {'reschedule', 'change', 'move'},
                'cancel': {'cancel'},
            }
        }

        # FAQ keywords by language
        self.faq_keywords = {
            'ru': {
                'hours': {'часы', 'работаем', 'открыто', 'закрыто', 'график'},
                'location': {'адрес', 'где', 'находится', 'местоположение'},
                'insurance': {'страховка', 'страхование', 'полис'},
                'payment': {'оплата', 'платить', 'принимаете', 'картой'},
            },
            'es': {
                'hours': {'horario', 'horas', 'abierto', 'cerrado'},
                'location': {'dirección', 'donde', 'ubicación', 'está'},
                'insurance': {'seguro', 'póliza'},
                'payment': {'pago', 'pagar', 'aceptan', 'tarjeta'},
            },
            'en': {
                'hours': {'hours', 'open', 'closed', 'schedule'},
                'location': {'address', 'where', 'located', 'location'},
                'insurance': {'insurance', 'policy'},
                'payment': {'payment', 'pay', 'accept', 'card'},
            }
        }

        # Service info keywords by language
        self.service_info_keywords = {
            'ru': {
                'duration': {'сколько времени', 'как долго', 'длительность', 'продолжительность'},
                'process': {'как проходит', 'что входит', 'этапы', 'процесс'},
                'preparation': {'подготовка', 'что нужно', 'требования'},
            },
            'es': {
                'duration': {'cuánto tiempo', 'cuánto tarda', 'duración'},
                'process': {'cómo es', 'qué incluye', 'proceso', 'etapas'},
                'preparation': {'preparación', 'qué necesito', 'requisitos'},
            },
            'en': {
                'duration': {'how long', 'duration', 'time', 'takes'},
                'process': {'how does', 'what includes', 'process', 'steps'},
                'preparation': {'preparation', 'what do i need', 'requirements'},
            }
        }

    async def classify(
        self,
        message: str,
        context: Dict[str, Any]
    ) -> Tuple[Lane, Dict[str, Any]]:
        """
        Classify message into routing lane

        UPDATED Priority order:
        1. Pending action (highest priority)
        2. Service info match (NEW - before service alias)
        3. Service alias match
        4. FAQ match
        5. Scheduling keywords
        6. Complex (default)

        Args:
            message: User message
            context: Hydrated context with patient, clinic, session_state

        Returns:
            Tuple of (Lane, routing_metadata)
        """
        session_state = context.get('session_state', {})
        patient = context.get('patient', {})
        clinic = context.get('clinic', {})
        language = patient.get('preferred_language', 'es')

        metadata = {}

        # 1. Check pending action (highest priority)
        pending_action = session_state.get('pending_action')
        if pending_action == 'offer_booking':
            if self._is_affirmative(message, language):
                logger.info(f"🎯 Lane: SCHEDULING (affirmative response to booking offer)")
                metadata['reason'] = 'affirmative_to_booking_offer'
                return Lane.SCHEDULING, metadata

            if self._is_negative(message, language):
                logger.info(f"🎯 Lane: FAQ (negative response, clarify needs)")
                metadata['reason'] = 'negative_to_booking_offer'
                return Lane.FAQ, metadata

        # 2. NEW: Check service info keywords (BEFORE alias matching)
        if self._has_service_info_keywords(message, language):
            # Only route to SERVICE_INFO if we have service context
            last_service = session_state.get('last_service_mentioned')
            if last_service:
                logger.info(
                    f"🎯 Lane: SERVICE_INFO (duration/process question with service context: {last_service})"
                )
                metadata['reason'] = 'service_info_with_context'
                metadata['service_context'] = last_service
                return Lane.SERVICE_INFO, metadata
            else:
                logger.info(f"🎯 Lane: SERVICE_INFO (duration/process question, will ask which service)")
                metadata['reason'] = 'service_info_no_context'
                return Lane.SERVICE_INFO, metadata

        # 3. Try service alias match (for PRICE lane)
        alias_map = clinic.get('service_aliases', {})
        if alias_map:
            match = self.language.match_service_alias(
                message,
                alias_map,
                language
            )
            if match and match[1] > 0.90:  # High confidence threshold
                service_id, confidence = match
                logger.info(f"🎯 Lane: PRICE (alias match: {service_id}, confidence: {confidence:.2f})")
                metadata['reason'] = 'service_alias_match'
                metadata['service_id'] = service_id
                metadata['confidence'] = confidence
                return Lane.PRICE, metadata

        # 3. Try FAQ pattern match
        if self._has_faq_keywords(message, language):
            logger.info(f"🎯 Lane: FAQ (FAQ keywords detected)")
            metadata['reason'] = 'faq_keywords'
            return Lane.FAQ, metadata

        # 4. Check scheduling keywords
        if self._has_scheduling_keywords(message, language):
            # Only route to SCHEDULING if we have service context
            if session_state.get('last_service_mentioned'):
                logger.info(f"🎯 Lane: SCHEDULING (scheduling keywords + service context)")
                metadata['reason'] = 'scheduling_with_context'
                return Lane.SCHEDULING, metadata
            else:
                logger.info(f"🎯 Lane: COMPLEX (scheduling keywords but no service context)")
                metadata['reason'] = 'scheduling_no_context'
                return Lane.COMPLEX, metadata

        # 5. Default to complex lane (LangGraph)
        logger.info(f"🎯 Lane: COMPLEX (default, no fast-path match)")
        metadata['reason'] = 'default_complex'
        return Lane.COMPLEX, metadata

    def _is_affirmative(self, message: str, language: str) -> bool:
        """
        Check if message is affirmative

        Args:
            message: User message
            language: Language code

        Returns:
            True if affirmative
        """
        return self.language.is_affirmative(message, language)

    def _is_negative(self, message: str, language: str) -> bool:
        """
        Check if message is negative

        Args:
            message: User message
            language: Language code

        Returns:
            True if negative
        """
        return self.language.is_negative(message, language)

    def _has_faq_keywords(self, message: str, language: str) -> bool:
        """
        Check if message contains FAQ keywords

        Args:
            message: User message
            language: Language code

        Returns:
            True if FAQ keywords found
        """
        message_lower = message.lower()
        keywords = self.faq_keywords.get(language, {})

        for category, words in keywords.items():
            if any(word in message_lower for word in words):
                logger.debug(f"FAQ category matched: {category}")
                return True

        return False

    def _has_scheduling_keywords(self, message: str, language: str) -> bool:
        """
        Check if message contains scheduling keywords

        Args:
            message: User message
            language: Language code

        Returns:
            True if scheduling keywords found
        """
        message_lower = message.lower()
        keywords = self.scheduling_keywords.get(language, {})

        for category, words in keywords.items():
            if any(word in message_lower for word in words):
                logger.debug(f"Scheduling category matched: {category}")
                return True

        return False

    def _has_service_info_keywords(self, message: str, language: str) -> bool:
        """
        Check if message contains service info keywords

        Args:
            message: User message
            language: Language code

        Returns:
            True if service info keywords found
        """
        message_lower = message.lower()
        keywords = self.service_info_keywords.get(language, {})

        for category, words in keywords.items():
            if any(word in message_lower for word in words):
                logger.debug(f"Service info category matched: {category}")
                return True

        return False

    def get_lane_metrics(self) -> Dict[str, int]:
        """
        Get lane usage metrics (for coverage tracking)

        Returns:
            Dictionary of lane counts
        """
        # This would typically be tracked in Redis or session service
        # Placeholder for now
        return {
            'faq': 0,
            'price': 0,
            'service_info': 0,
            'scheduling': 0,
            'complex': 0
        }
