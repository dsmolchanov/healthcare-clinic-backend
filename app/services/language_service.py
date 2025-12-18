"""
Language Processing Service - SINGLE SOURCE OF TRUTH

This is the CANONICAL language detection service.
All components must use this service for language detection.

Features:
- Fast language detection (<10ms) with caching
- Synchronous detection for non-async contexts
- Character-based detection (Cyrillic, Hebrew, Spanish markers)
- ML-based detection via langdetect library
- Fuzzy service alias matching (rapidfuzz, threshold 0.88)
- I18N template rendering (Jinja2, ru/es/en/he/pt)
- Currency formatting (Babel)

Language support: Russian (ru), Spanish (es), English (en), Hebrew (he), Portuguese (pt)

IMPORTANT: Do NOT create separate language detection functions elsewhere.
Use LanguageService.detect_sync() for synchronous contexts.
"""

import logging
import time
from typing import Dict, Any, Optional, Tuple
from rapidfuzz import fuzz, process
import langdetect
from jinja2 import Environment, FileSystemLoader, select_autoescape
from babel.numbers import format_currency

logger = logging.getLogger(__name__)

# Disable langdetect's non-deterministic behavior for consistent results
langdetect.DetectorFactory.seed = 0


class LanguageService:
    """
    CANONICAL language processing service for multilingual support.

    This is the SINGLE SOURCE OF TRUTH for language detection.
    Do NOT create duplicate detection functions elsewhere.

    Features:
    - Language detection with Redis caching (async)
    - Synchronous detection for non-async contexts
    - Character-based fast detection (Cyrillic, Hebrew)
    - Keyword-based detection (Spanish, Portuguese markers)
    - ML-based fallback via langdetect library
    - Fuzzy alias matching for service names
    - I18N template rendering
    - Currency formatting per locale
    """

    # Supported languages (ISO 639-1 codes)
    SUPPORTED_LANGUAGES = {'en', 'es', 'ru', 'he', 'pt'}
    DEFAULT_LANGUAGE = 'es'

    def __init__(self, redis_client=None, templates_dir: str = "templates"):
        """
        Initialize language service

        Args:
            redis_client: Redis client for caching (optional for sync-only usage)
            templates_dir: Directory containing I18N templates
        """
        self.redis = redis_client
        self.supported_languages = self.SUPPORTED_LANGUAGES
        self.default_language = self.DEFAULT_LANGUAGE

        # Initialize Jinja2 for templates
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

        # Fuzzy matching configuration
        self.fuzzy_threshold = 88  # 0.88 * 100
        self.min_alias_length = 3

    def _make_language_cache_key(self, phone_hash: str) -> str:
        """Generate cache key for detected language"""
        return f"lang:{phone_hash}"

    def detect_sync(self, text: str) -> str:
        """
        Synchronous language detection without caching.

        Use this for non-async contexts (e.g., response formatting, intent routing).
        For async contexts with caching, use detect_and_cache().

        Args:
            text: Text to detect language from

        Returns:
            ISO language code (en, es, ru, he, pt)
        """
        if not text or not text.strip():
            return self.default_language

        # Fast character-based detection first
        char_result = self._detect_by_characters(text)
        if char_result and char_result != 'en':
            # Non-English detected with confidence (Cyrillic, Hebrew, etc.)
            return char_result

        # Fall back to langdetect for English/Spanish/Portuguese disambiguation
        return self._detect_by_langdetect(text)

    def _detect_by_characters(self, text: str) -> Optional[str]:
        """
        Fast Unicode character-based detection.

        This is the first pass - detects languages with distinctive scripts.
        Returns None for ambiguous cases (let langdetect handle).
        """
        if not text:
            return None

        text_len = len(text)
        if text_len == 0:
            return None

        # Count Cyrillic characters → Russian
        cyrillic = sum(1 for c in text if '\u0400' <= c <= '\u04FF')
        if cyrillic / text_len > 0.3:
            return 'ru'

        # Count Hebrew characters → Hebrew
        hebrew = sum(1 for c in text if '\u0590' <= c <= '\u05FF')
        if hebrew / text_len > 0.3:
            return 'he'

        # Check Portuguese FIRST (more unique markers like 'você')
        # Portuguese has distinct markers that don't overlap with Spanish
        text_lower = text.lower()
        # Note: 'quanto' is Portuguese (Spanish is 'cuánto' with accent)
        portuguese_markers = ['você', 'obrigado', 'olá', 'não', 'quanto']
        if any(m in text_lower for m in portuguese_markers):
            return 'pt'

        # Spanish indicators (keyword-based for Latin scripts)
        # Note: 'está' removed as it overlaps with Portuguese
        spanish_markers = ['hola', 'gracias', 'señor', 'qué', 'cómo', 'cuánto', 'cuándo', 'dónde']
        if any(m in text_lower for m in spanish_markers):
            return 'es'

        # Default to English for Latin script without markers
        return 'en'

    def _detect_by_langdetect(self, text: str) -> str:
        """
        ML-based detection via langdetect library.

        Used as fallback when character-based detection is ambiguous.
        """
        try:
            detected = langdetect.detect(text)
            return detected if detected in self.supported_languages else self.default_language
        except Exception:
            return self.default_language

    async def detect_and_cache(self, message: str, phone_hash: str) -> str:
        """
        Detect language with caching for performance.

        Async version with Redis caching. Use detect_sync() for non-async contexts.

        Args:
            message: Text message to detect language from
            phone_hash: Hashed phone number for cache key

        Returns:
            Language code (ru/es/en/he/pt)
        """
        # Check cache first
        if self.redis:
            cache_key = self._make_language_cache_key(phone_hash)
            cached_lang = self.redis.get(cache_key)

            if cached_lang:
                lang = cached_lang.decode() if isinstance(cached_lang, bytes) else cached_lang
                logger.debug(f"✅ Language cache HIT: {lang} for {phone_hash[:8]}...")
                return lang

        # Detect language using unified detection
        start_time = time.time()
        lang = self.detect_sync(message)
        detection_ms = (time.time() - start_time) * 1000
        logger.info(f"🌐 Detected language: {lang} in {detection_ms:.2f}ms")

        # Cache for 30 days (language preference rarely changes)
        if self.redis:
            cache_key = self._make_language_cache_key(phone_hash)
            self.redis.setex(cache_key, 30 * 24 * 3600, lang)

        return lang

    def normalize_text(self, text: str) -> str:
        """
        Normalize text for matching

        Args:
            text: Input text

        Returns:
            Normalized text (lowercase, stripped)
        """
        return text.lower().strip()

    def match_service_alias(
        self,
        message: str,
        alias_map: Dict[str, str],
        language: str
    ) -> Optional[Tuple[str, float]]:
        """
        Match service alias with fuzzy matching

        Performs:
        1. Exact match (O(1) lookup) - highest priority
        2. Fuzzy match (rapidfuzz) - threshold 0.88

        Args:
            message: User message
            alias_map: Dictionary of {alias: service_id}
            language: User's language

        Returns:
            Tuple of (service_id, confidence_score) or None
        """
        if not alias_map:
            return None

        normalized_message = self.normalize_text(message)

        # Filter out very short messages (likely not service names)
        if len(normalized_message) < self.min_alias_length:
            return None

        # Try exact match first (O(1))
        for alias, service_id in alias_map.items():
            if self.normalize_text(alias) == normalized_message:
                logger.info(f"✅ Exact alias match: '{alias}' -> {service_id}")
                return (service_id, 1.0)

        # Try fuzzy matching
        aliases = list(alias_map.keys())
        result = process.extractOne(
            normalized_message,
            aliases,
            scorer=fuzz.ratio,
            score_cutoff=self.fuzzy_threshold
        )

        if result:
            matched_alias, score, _ = result
            service_id = alias_map[matched_alias]
            confidence = score / 100.0
            logger.info(f"🔍 Fuzzy match: '{matched_alias}' -> {service_id} (confidence: {confidence:.2f})")
            return (service_id, confidence)

        logger.debug(f"❌ No alias match found for: '{normalized_message}'")
        return None

    def render_template(
        self,
        template_name: str,
        language: str,
        context: Dict[str, Any]
    ) -> str:
        """
        Render I18N template

        Args:
            template_name: Template filename (e.g., 'price_response')
            language: Language code (ru/es/en)
            context: Template context variables

        Returns:
            Rendered text
        """
        if not self.jinja_env:
            logger.warning("Templates not available, returning fallback")
            return self._fallback_template(template_name, context)

        # Construct template path with language
        template_path = f"{template_name}_{language}.txt"

        try:
            template = self.jinja_env.get_template(template_path)
            return template.render(**context)
        except Exception as e:
            logger.error(f"Template rendering failed for {template_path}: {e}")
            # Fallback to default language
            if language != self.default_language:
                try:
                    template_path = f"{template_name}_{self.default_language}.txt"
                    template = self.jinja_env.get_template(template_path)
                    return template.render(**context)
                except:
                    pass
            return self._fallback_template(template_name, context)

    def _fallback_template(self, template_name: str, context: Dict[str, Any]) -> str:
        """Fallback template when Jinja2 is unavailable"""
        if template_name == "price_response":
            first_name = context.get('first_name', 'Hola')
            service_name = context.get('service_name', 'servicio')
            price = context.get('price', 'N/A')
            return f"{first_name}, el precio de {service_name} es {price}."
        elif template_name == "service_info_ask_which_service":
            # P0: Clarifying prompt when service context is missing
            first_name = context.get('first_name', '')
            services = context.get('services', [])
            greeting = f"{first_name}, " if first_name else ""
            service_list = ", ".join(services[:3]) if services else "nuestros servicios"
            return f"{greeting}¿Sobre cuál servicio te gustaría saber más? Ofrecemos: {service_list}."
        elif template_name == "service_info_response":
            # P0: Service info response with duration/process/price
            first_name = context.get('first_name', '')
            service_name = context.get('service_name', 'este servicio')
            duration = context.get('duration_minutes', 30)
            price = context.get('price_display', 'N/A')
            greeting = f"{first_name}, " if first_name else ""

            response = f"{greeting}{service_name} dura aproximadamente {duration} minutos"
            if context.get('description'):
                response += f". {context['description']}"
            response += f". El precio es {price}."

            if context.get('preparation_notes'):
                response += f" {context['preparation_notes']}"

            if context.get('include_booking_prompt'):
                response += " ¿Te gustaría agendar una cita?"

            return response
        return "Template not available"

    def format_currency(
        self,
        amount: float,
        currency: str,
        language: str
    ) -> str:
        """
        Format currency using Babel for locale-aware formatting

        Args:
            amount: Monetary amount
            currency: Currency code (USD, RUB, EUR, MXN, etc.)
            language: Language code for locale

        Returns:
            Formatted currency string
        """
        locale = self.locale_map.get(language, 'es_ES')

        try:
            return format_currency(amount, currency, locale=locale)
        except Exception as e:
            logger.error(f"Currency formatting failed: {e}")
            # Fallback to simple formatting
            return f"{amount:.2f} {currency}"

    def get_greeting(self, language: str, first_name: Optional[str] = None) -> str:
        """
        Get localized greeting

        Args:
            language: Language code
            first_name: Optional first name to personalize

        Returns:
            Localized greeting
        """
        greetings = {
            'ru': 'Здравствуйте',
            'es': 'Hola',
            'en': 'Hello'
        }

        greeting = greetings.get(language, greetings['es'])

        if first_name:
            return f"{greeting}, {first_name}"
        return greeting

    def get_affirmative_patterns(self, language: str) -> set:
        """
        Get affirmative response patterns for language

        Args:
            language: Language code

        Returns:
            Set of affirmative words/phrases
        """
        patterns = {
            'ru': {'да', 'ага', 'ок', 'конечно', 'давай', 'хорошо', 'угу'},
            'es': {'sí', 'si', 'ok', 'claro', 'vale', 'bueno', 'de acuerdo'},
            'en': {'yes', 'yep', 'ok', 'sure', 'yeah', 'yup', 'alright'}
        }
        return patterns.get(language, patterns['es'])

    def get_negative_patterns(self, language: str) -> set:
        """
        Get negative response patterns for language

        Args:
            language: Language code

        Returns:
            Set of negative words/phrases
        """
        patterns = {
            'ru': {'нет', 'не', 'неа', 'не надо', 'не хочу'},
            'es': {'no', 'nada', 'nunca', 'tampoco'},
            'en': {'no', 'nope', 'nah', 'never', 'not'}
        }
        return patterns.get(language, patterns['es'])

    def is_affirmative(self, message: str, language: str) -> bool:
        """Check if message is affirmative"""
        normalized = self.normalize_text(message)
        return normalized in self.get_affirmative_patterns(language)

    def is_negative(self, message: str, language: str) -> bool:
        """Check if message is negative"""
        normalized = self.normalize_text(message)
        return normalized in self.get_negative_patterns(language)
