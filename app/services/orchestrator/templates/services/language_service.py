"""Language detection and localization service."""
from typing import Optional
import logging

logger = logging.getLogger(__name__)


def get_localized_field(service: dict, field: str, language: str) -> str:
    """Get localized field value with fallback."""
    # Try localized field first
    localized_key = f"{field}_{language}"
    value = service.get(localized_key)
    if value:
        return value

    # Fallback to default field
    value = service.get(field)
    if value:
        return value

    # Fallback to English
    return service.get(f"{field}_en", '')


def detect_language_from_message(
    message: str,
    default: str = 'en'
) -> str:
    """
    Detect language from message text.

    Uses character analysis for fast, reliable detection.
    """
    if not message or len(message.strip()) < 3:
        return default

    text_len = len(message)
    if text_len == 0:
        return default

    # Cyrillic → Russian
    cyrillic_chars = sum(1 for c in message if '\u0400' <= c <= '\u04FF')
    if cyrillic_chars / text_len > 0.3:
        return 'ru'

    # Hebrew
    hebrew_chars = sum(1 for c in message if '\u0590' <= c <= '\u05FF')
    if hebrew_chars / text_len > 0.3:
        return 'he'

    # Spanish indicators
    message_lower = message.lower()
    spanish_markers = ['hola', 'gracias', 'señor', 'está', 'qué', 'cómo', 'buenos', 'buenas']
    if any(m in message_lower for m in spanish_markers):
        return 'es'

    # Portuguese indicators
    portuguese_markers = ['olá', 'obrigado', 'você', 'não', 'bom dia']
    if any(m in message_lower for m in portuguese_markers):
        return 'pt'

    # Default to English
    return default
