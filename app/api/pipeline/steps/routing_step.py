"""
RoutingStep - Route message to appropriate lane and handle fast-path queries.

Extracted from process_message() lines 489-580.

Phase 2A of the Agentic Flow Architecture Refactor.
"""

import logging
import re
from typing import Tuple

from ..base import PipelineStep
from ..context import PipelineContext

logger = logging.getLogger(__name__)


class RoutingStep(PipelineStep):
    """
    Route message to appropriate lane and handle fast-path queries.

    Responsibilities:
    1. Detect language from user message
    2. Extract name from message if present
    3. Classify message into lane (FAQ, PRICE, SERVICE_INFO, SCHEDULING, COMPLEX)
    4. Handle fast-path lanes (FAQ, PRICE, SERVICE_INFO) directly
    5. If fast-path handled, stop pipeline; otherwise continue to LLM
    """

    def __init__(
        self,
        language_service=None,
        router_service=None,
        fast_path_service=None,
        memory_manager=None
    ):
        """
        Initialize with routing services.

        Args:
            language_service: LanguageService for language detection
            router_service: RouterService for lane classification
            fast_path_service: FastPathService for handling FAQ/PRICE/SERVICE_INFO
            memory_manager: ConversationMemory for storing fast-path responses
        """
        self._language_service = language_service
        self._router_service = router_service
        self._fast_path_service = fast_path_service
        self._memory_manager = memory_manager

    @property
    def name(self) -> str:
        return "routing"

    async def execute(self, ctx: PipelineContext) -> Tuple[PipelineContext, bool]:
        """
        Execute routing step.

        Sets on context:
        - detected_language
        - extracted_first_name, extracted_last_name
        - lane, lane_metadata
        - If fast-path handled: response, fast_path_handled=True, returns False

        Returns:
        - (ctx, False) if fast-path handled response
        - (ctx, True) to continue to LLM generation
        """
        # 1. Detect language
        if self._language_service:
            ctx.detected_language = self._language_service.detect_sync(ctx.message)
        else:
            ctx.detected_language = self._detect_language_fallback(ctx.message)

        # 2. Extract name from message
        ctx.extracted_first_name, ctx.extracted_last_name = self._extract_name_from_message(ctx.message)

        # 3. Check for meta-reset command FIRST (before routing)
        if self._is_meta_reset(ctx.message, ctx.detected_language):
            ctx.is_meta_reset = True
            # Meta-reset handling is done in ConstraintEnforcementStep
            # Just flag it here and continue
            return ctx, True

        # 4. Build router context
        router_context = self._build_router_context(ctx)

        # 5. Classify message into lane
        if self._router_service:
            from app.services.router_service import Lane
            lane, metadata = await self._router_service.classify(ctx.message, router_context)
            ctx.lane = lane
            ctx.lane_metadata = metadata
        else:
            # Default to COMPLEX lane if no router
            ctx.lane = "COMPLEX"
            ctx.lane_metadata = {}

        logger.info(
            f"🚦 Route: lane={ctx.lane}, "
            f"confidence={ctx.lane_metadata.get('confidence', 0):.2f}, "
            f"language={ctx.detected_language}"
        )

        # 6. Handle fast-path lanes (FAQ, PRICE, SERVICE_INFO)
        if self._fast_path_service and ctx.lane:
            from app.services.router_service import Lane

            fast_path_lanes = [Lane.FAQ, Lane.PRICE, Lane.SERVICE_INFO]
            if ctx.lane in fast_path_lanes:
                result = await self._handle_fast_path(ctx, router_context)

                if result and not result.get('fallback_to_complex'):
                    # Fast-path succeeded
                    ctx.response = result.get('reply', '')
                    ctx.detected_language = result.get('language', ctx.detected_language)
                    ctx.fast_path_handled = True
                    ctx.response_metadata = {
                        'lane': str(ctx.lane),
                        'fast_path': True,
                        'latency_ms': result.get('latency_ms', 0)
                    }

                    # Store response
                    if self._memory_manager:
                        await self._memory_manager.store_message(
                            session_id=ctx.session_id,
                            role='assistant',
                            content=ctx.response,
                            phone_number=ctx.from_phone,
                            metadata=ctx.response_metadata
                        )

                    logger.info(f"⚡ Fast-path handled in {result.get('latency_ms', 0):.0f}ms")

                    # Stop pipeline - response ready
                    return ctx, False
                else:
                    logger.info("Fast-path failed or requested fallback, proceeding to LLM")

        # Continue to LLM generation
        return ctx, True

    async def _handle_fast_path(self, ctx: PipelineContext, router_context: dict) -> dict | None:
        """Handle fast-path routing for FAQ/PRICE/SERVICE_INFO lanes."""
        from app.services.router_service import Lane

        if ctx.lane == Lane.FAQ:
            return await self._fast_path_service.handle_faq_query(
                ctx.message, router_context
            )
        elif ctx.lane == Lane.PRICE:
            service_id = ctx.lane_metadata.get('service_id')
            confidence = ctx.lane_metadata.get('confidence', 0)
            return await self._fast_path_service.handle_price_query(
                ctx.message, router_context, service_id, confidence
            )
        elif ctx.lane == Lane.SERVICE_INFO:
            service_context = ctx.lane_metadata.get('service_context')
            return await self._fast_path_service.handle_service_info_query(
                ctx.message, router_context, service_context
            )

        return None

    def _build_router_context(self, ctx: PipelineContext) -> dict:
        """Build context dictionary for router service."""
        return {
            'patient': {
                'id': ctx.patient_id,
                'name': ctx.patient_name,
                'phone': ctx.from_phone
            },
            'clinic': {
                'id': ctx.effective_clinic_id,
                'name': ctx.clinic_name,
                'services': ctx.clinic_services,
                'doctors': ctx.clinic_doctors,
                'faqs': ctx.clinic_faqs
            },
            'session_state': {
                'turn_status': ctx.turn_status,
                'last_agent_action': ctx.last_agent_action
            },
            'history': ctx.session_messages,
            'profile': ctx.profile,
            'conversation_state': ctx.conversation_state,
            'preferences': ctx.user_preferences
        }

    def _detect_language_fallback(self, text: str) -> str:
        """Fallback language detection using character analysis."""
        if not text:
            return 'es'

        text_len = len(text)
        if text_len == 0:
            return 'es'

        # Cyrillic → Russian
        cyrillic = sum(1 for c in text if '\u0400' <= c <= '\u04FF')
        if cyrillic / text_len > 0.3:
            return 'ru'

        # Hebrew
        hebrew = sum(1 for c in text if '\u0590' <= c <= '\u05FF')
        if hebrew / text_len > 0.3:
            return 'he'

        # Spanish indicators
        text_lower = text.lower()
        spanish_markers = ['hola', 'gracias', 'señor', 'está', 'qué', 'cómo']
        if any(m in text_lower for m in spanish_markers):
            return 'es'

        # Portuguese indicators
        portuguese_markers = ['olá', 'obrigado', 'você', 'não']
        if any(m in text_lower for m in portuguese_markers):
            return 'pt'

        return 'en'

    def _is_meta_reset(self, message: str, language: str) -> bool:
        """Detect meta-reset command in message."""
        message_lower = message.lower().strip()

        # Reset patterns by language
        reset_patterns = {
            'en': ['start over', 'reset', 'new conversation', 'forget everything', 'clear chat'],
            'es': ['empezar de nuevo', 'reiniciar', 'nueva conversación', 'olvidar todo'],
            'ru': ['начать заново', 'сброс', 'новый разговор', 'забыть всё', 'забыть все'],
            'he': ['להתחיל מחדש', 'איפוס', 'שיחה חדשה'],
            'pt': ['começar de novo', 'reiniciar', 'nova conversa', 'esquecer tudo']
        }

        patterns = reset_patterns.get(language, reset_patterns['en'])
        return any(pattern in message_lower for pattern in patterns)

    def _extract_name_from_message(self, message: str) -> tuple[str | None, str | None]:
        """Extract first and last name from user message."""
        patterns = [
            # Spanish
            r'(?:me llamo|mi nombre es|soy)\s+([A-ZÁ-Ü][a-zá-ü]+(?:\s+[A-ZÁ-Ü][a-zá-ü]+)+)',
            # English
            r'(?:my name is|i\'m|i am|call me)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)',
            # Portuguese
            r'(?:meu nome é|eu sou)\s+([A-ZÁ-Ü][a-zá-ü]+(?:\s+[A-ZÁ-Ü][a-zá-ü]+)+)',
            # Hebrew
            r'(?:שמי|קוראים לי)\s+([א-ת]+(?:\s+[א-ת]+)+)',
            # Russian
            r'(?:меня зовут|я)\s+([А-ЯЁ][а-яё]+(?:\s+[А-ЯЁ][а-яё]+)+)',
        ]

        for pattern in patterns:
            match = re.search(pattern, message, re.IGNORECASE)
            if match:
                full_name = match.group(1).strip()
                parts = full_name.split()
                if len(parts) >= 2:
                    first_name = parts[0]
                    last_name = ' '.join(parts[1:])
                    logger.info(f"📝 Extracted name: {first_name} {last_name}")
                    return first_name, last_name
                elif len(parts) == 1:
                    return parts[0], None

        return None, None
