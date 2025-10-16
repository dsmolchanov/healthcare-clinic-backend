"""
Fast-Path Service

Template-based handlers for FAQ and PRICE lanes
Bypasses LLM for deterministic responses with <600ms P50 latency

Performance Targets:
- FAQ Handler: <400ms P50
- PRICE Handler: <600ms P50
"""

import logging
import time
from typing import Dict, Any, Optional
from jinja2 import Environment, FileSystemLoader, select_autoescape
from babel.numbers import format_currency

logger = logging.getLogger(__name__)


class FastPathService:
    """
    Fast-path service for template-based message handling

    Handles:
    - Price queries with service alias lookup
    - FAQ queries with template responses
    - Session state management
    - Performance tracking
    """

    def __init__(
        self,
        language_service,
        session_service,
        templates_dir: str = "templates"
    ):
        """
        Initialize fast-path service

        Args:
            language_service: LanguageService for text processing
            session_service: SessionService for state management
            templates_dir: Directory containing Jinja2 templates
        """
        self.language = language_service
        self.session = session_service

        # Initialize Jinja2
        try:
            self.jinja_env = Environment(
                loader=FileSystemLoader(templates_dir),
                autoescape=select_autoescape(['html', 'xml']),
                trim_blocks=True,
                lstrip_blocks=True
            )
        except Exception as e:
            logger.warning(f"Could not load templates from {templates_dir}: {e}")
            self.jinja_env = None

        # Locale mapping for Babel
        self.locale_map = {
            'ru': 'ru_RU',
            'es': 'es_ES',
            'en': 'en_US'
        }

    async def handle_price_query(
        self,
        message: str,
        context: Dict[str, Any],
        service_id: str,
        confidence: float
    ) -> Dict[str, Any]:
        """
        Handle price query with template response

        Target: <600ms P50 latency

        Args:
            message: User message
            context: Hydrated context (patient, clinic, session_state)
            service_id: Service ID from alias match
            confidence: Match confidence score

        Returns:
            Response dictionary with reply text and metadata
        """
        start_time = time.time()

        try:
            # Get service details from clinic context
            clinic = context.get('clinic', {})
            services = clinic.get('services', [])
            service = next((s for s in services if s.get('id') == service_id), None)

            if not service:
                logger.warning(f"Service {service_id} not found in clinic services")
                return await self._fallback_to_complex(message, context, "service_not_found")

            # Get patient info
            patient = context.get('patient', {})
            language = patient.get('preferred_language', 'es')
            first_name = patient.get('first_name')

            # Format price
            price = service.get('price', 0)
            currency = service.get('currency', 'MXN')
            formatted_price = self._format_currency(price, currency, language)

            # Get service name in patient's language
            service_name_i18n = service.get('name_i18n', {})
            service_name = service_name_i18n.get(language, service_name_i18n.get('es', service.get('name', 'servicio')))

            # Render template
            template_vars = {
                'first_name': first_name,
                'service_name': service_name,
                'price': formatted_price,
                'duration': service.get('duration_minutes', 30)
            }

            reply = self.language.render_template(
                'price_response',
                language,
                template_vars
            )

            # Update session state
            session_state = context.get('session_state', {})
            session_id = session_state.get('session_id')

            if session_id and self.session:
                await self.session.update_state(
                    session_id,
                    current_intent="service_inquiry",
                    last_service_mentioned=service_id,
                    pending_action="offer_booking"
                )

            # Track performance
            latency_ms = (time.time() - start_time) * 1000
            logger.info(f"⚡ PRICE handler: {latency_ms:.2f}ms (target: <600ms)")

            return {
                'reply': reply,
                'lane': 'price',
                'service_id': service_id,
                'confidence': confidence,
                'latency_ms': latency_ms,
                'next_action': 'offer_booking'
            }

        except Exception as e:
            logger.error(f"Error in handle_price_query: {e}", exc_info=True)
            return await self._fallback_to_complex(message, context, f"error: {str(e)}")

    async def handle_faq_query(
        self,
        message: str,
        context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Handle FAQ query with template response

        Target: <400ms P50 latency

        Args:
            message: User message
            context: Hydrated context

        Returns:
            Response dictionary with reply text and metadata
        """
        start_time = time.time()

        try:
            patient = context.get('patient', {})
            clinic = context.get('clinic', {})
            language = patient.get('preferred_language', 'es')
            first_name = patient.get('first_name')

            # Determine FAQ category (simplified for now)
            category = self._detect_faq_category(message, language)

            # Get FAQ response from clinic config or use template
            faq_responses = clinic.get('faq_responses', {})
            reply = faq_responses.get(category, {}).get(language)

            if not reply:
                # Use generic greeting template as fallback
                reply = self.language.render_template(
                    'greeting',
                    language,
                    {'first_name': first_name}
                )
                reply += " " + self._get_generic_faq_response(category, language)

            # Track performance
            latency_ms = (time.time() - start_time) * 1000
            logger.info(f"⚡ FAQ handler: {latency_ms:.2f}ms (target: <400ms)")

            return {
                'reply': reply,
                'lane': 'faq',
                'category': category,
                'latency_ms': latency_ms
            }

        except Exception as e:
            logger.error(f"Error in handle_faq_query: {e}", exc_info=True)
            return await self._fallback_to_complex(message, context, f"error: {str(e)}")

    def _format_currency(
        self,
        amount: float,
        currency: str,
        language: str
    ) -> str:
        """
        Format currency using Babel

        Args:
            amount: Monetary amount
            currency: Currency code (USD, MXN, RUB, etc.)
            language: Language code for locale

        Returns:
            Formatted currency string
        """
        locale = self.locale_map.get(language, 'es_ES')

        try:
            return format_currency(amount, currency, locale=locale)
        except Exception as e:
            logger.error(f"Currency formatting failed: {e}")
            return f"{amount:.2f} {currency}"

    def _detect_faq_category(self, message: str, language: str) -> str:
        """
        Detect FAQ category from message

        Args:
            message: User message
            language: Language code

        Returns:
            FAQ category
        """
        message_lower = message.lower()

        # Simple keyword matching (can be enhanced with semantic search)
        if language == 'ru':
            if any(word in message_lower for word in ['часы', 'работаем', 'график']):
                return 'hours'
            if any(word in message_lower for word in ['адрес', 'где', 'находится']):
                return 'location'
            if any(word in message_lower for word in ['страховка', 'полис']):
                return 'insurance'
            if any(word in message_lower for word in ['оплата', 'платить', 'картой']):
                return 'payment'

        elif language == 'es':
            if any(word in message_lower for word in ['horario', 'horas', 'abierto']):
                return 'hours'
            if any(word in message_lower for word in ['dirección', 'donde', 'ubicación']):
                return 'location'
            if any(word in message_lower for word in ['seguro', 'póliza']):
                return 'insurance'
            if any(word in message_lower for word in ['pago', 'aceptan', 'tarjeta']):
                return 'payment'

        elif language == 'en':
            if any(word in message_lower for word in ['hours', 'open', 'schedule']):
                return 'hours'
            if any(word in message_lower for word in ['address', 'where', 'location']):
                return 'location'
            if any(word in message_lower for word in ['insurance', 'policy']):
                return 'insurance'
            if any(word in message_lower for word in ['payment', 'pay', 'card']):
                return 'payment'

        return 'general'

    def _get_generic_faq_response(self, category: str, language: str) -> str:
        """
        Get generic FAQ response

        Args:
            category: FAQ category
            language: Language code

        Returns:
            Generic response text
        """
        responses = {
            'ru': {
                'hours': 'Мы работаем с понедельника по пятницу с 9:00 до 18:00.',
                'location': 'Наш адрес можно найти на нашем сайте.',
                'insurance': 'Мы принимаем большинство страховых полисов.',
                'payment': 'Мы принимаем наличные, карты и банковские переводы.',
                'general': 'Чем я могу помочь вам сегодня?'
            },
            'es': {
                'hours': 'Estamos abiertos de lunes a viernes de 9:00 a 18:00.',
                'location': 'Puedes encontrar nuestra dirección en nuestro sitio web.',
                'insurance': 'Aceptamos la mayoría de seguros.',
                'payment': 'Aceptamos efectivo, tarjetas y transferencias bancarias.',
                'general': '¿En qué puedo ayudarte hoy?'
            },
            'en': {
                'hours': 'We are open Monday to Friday from 9:00 AM to 6:00 PM.',
                'location': 'You can find our address on our website.',
                'insurance': 'We accept most insurance policies.',
                'payment': 'We accept cash, cards, and bank transfers.',
                'general': 'How can I help you today?'
            }
        }

        return responses.get(language, responses['es']).get(category, responses[language]['general'])

    async def _fallback_to_complex(
        self,
        message: str,
        context: Dict[str, Any],
        reason: str
    ) -> Dict[str, Any]:
        """
        Fallback to complex lane

        Args:
            message: User message
            context: Context
            reason: Reason for fallback

        Returns:
            Fallback response indicating complex lane routing
        """
        logger.info(f"⚠️ Fast-path fallback to COMPLEX: {reason}")

        return {
            'reply': None,  # Signal to route to complex lane
            'lane': 'complex',
            'fallback_reason': reason,
            'original_message': message
        }
