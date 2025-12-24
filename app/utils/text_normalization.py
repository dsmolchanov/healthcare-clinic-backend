"""
Russian Text Normalization

This module provides:
1. Text normalization (NFC Unicode, lowercase, punctuation removal)
2. Query normalization for search matching

Note: Synonym expansion has been migrated to the database (healthcare.service_aliases table).
The expand_synonyms() function is deprecated and returns only the normalized query.
"""

import re
from unicodedata import normalize
from typing import List, Set

# Russian punctuation patterns
RUS_PUNCT = r"[?!.,;:()\[\]«»\"'…/\\-]+"


def normalize_query(query: str) -> str:
    """
    Normalize text for search matching

    Steps:
    1. Unicode NFC normalization
    2. Lowercase conversion
    3. Punctuation removal
    4. Whitespace normalization

    Args:
        query: Raw search query

    Returns:
        Normalized query string

    Example:
        >>> normalize_query("Смолы?!")
        "смолы"
        >>> normalize_query("  композитная   пломба  ")
        "композитная пломба"
    """
    if not query:
        return ""

    # Unicode normalization (NFC form)
    q = normalize("NFC", query)

    # Strip and lowercase
    q = q.strip().lower()

    # Remove punctuation
    q = re.sub(RUS_PUNCT, " ", q)

    # Normalize whitespace
    q = re.sub(r"\s+", " ", q).strip()

    return q


def expand_synonyms(query: str) -> List[str]:
    """DEPRECATED: Synonyms now handled by service_aliases table in database.

    This function returns only the normalized query.
    Vector search + alias table now handles synonym matching.

    Args:
        query: Search query

    Returns:
        List containing only the normalized query
    """
    normalized = normalize_query(query)
    return [normalized] if normalized else []


def get_all_searchable_terms(query: str) -> Set[str]:
    """Get searchable terms without synonym expansion.

    Synonym expansion now handled by database alias table.

    Args:
        query: Raw search query

    Returns:
        Set of normalized query and individual words
    """
    if not query:
        return set()

    terms: Set[str] = set()
    normalized = normalize_query(query)

    if normalized:
        terms.add(normalized)
        # Add individual words >= 3 chars
        words = normalized.split()
        terms.update(w for w in words if len(w) >= 3)

    return terms


def format_price_reply(
    service: dict,
    language: str = "ru",
    unit: str = "per surface"
) -> str:
    """
    Format deterministic price reply using database i18n values.

    Uses get_translation() helper to access service name from name_i18n JSONB.
    Removes hardcoded service_names_ru dictionary.

    Args:
        service: Service dict with name_i18n, base_price, currency fields
        language: Language code (ru, en, es, pt, he)
        unit: Unit description

    Returns:
        Formatted price message with CTA

    Example:
        >>> service = {'name': 'Composite filling', 'name_i18n': {'ru': 'Композитная пломба'}, 'base_price': 80.0, 'currency': 'USD'}
        >>> format_price_reply(service, "ru")
        "Композитная пломба: $80.00 за одну поверхность. Записать вас на удобное время?"
    """
    from app.utils.i18n_helpers import get_translation

    # Get translated name from database JSONB field
    service_name = get_translation(service, 'name', language, fallback_languages=['en'])

    # Fallback to base name if no translation
    if not service_name:
        service_name = service.get('name', 'Service')

    # Get price and currency from service dict
    # Support both 'price' (from RPC results) and 'base_price' (from raw table)
    price = service.get('price') or service.get('base_price', 0) or 0
    currency = service.get('currency', 'USD') or 'USD'

    # Currency symbols
    currency_symbols = {
        "USD": "$",
        "EUR": "€",
        "MXN": "$",
        "RUB": "₽",
        "GBP": "£",
        "ILS": "₪"
    }
    symbol = currency_symbols.get(currency, f"{currency} ")

    # Localized units
    units_by_language = {
        "ru": {
            "per surface": "за одну поверхность",
            "per tooth": "за зуб",
            "per visit": "за визит",
            "per procedure": "за процедуру"
        },
        "es": {
            "per surface": "por superficie",
            "per tooth": "por diente",
            "per visit": "por visita",
            "per procedure": "por procedimiento"
        },
        "en": {
            "per surface": "per surface",
            "per tooth": "per tooth",
            "per visit": "per visit",
            "per procedure": "per procedure"
        }
    }

    # CTA messages by language
    cta_by_language = {
        "ru": "Записать вас на удобное время?",
        "es": "¿Le gustaría agendar una cita?",
        "pt": "Gostaria de agendar uma consulta?",
        "he": "?האם תרצה לקבוע תור",
        "en": "Would you like to book an appointment?"
    }

    # Get language-specific values with English fallback
    lang_key = language[:2] if language else 'en'
    units = units_by_language.get(lang_key, units_by_language['en'])
    unit_text = units.get(unit, unit)
    cta = cta_by_language.get(lang_key, cta_by_language['en'])

    return f"{service_name}: {symbol}{price:.2f} {unit_text}. {cta}"


def quick_reply(language: str = "ru") -> str:
    """
    Quick reply when search exceeds budget or fails

    Args:
        language: Language code (ru, en)

    Returns:
        Canned response with generic price range and CTA
    """
    if language.startswith("ru"):
        return (
            "У нас широкий спектр услуг от $50 до $500. "
            "Могу уточнить цену на конкретную процедуру. "
            "Записать вас на консультацию?"
        )
    else:
        return (
            "We offer a wide range of services from $50 to $500. "
            "I can provide specific pricing. "
            "Would you like to book a consultation?"
        )
