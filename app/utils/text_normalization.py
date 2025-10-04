"""
Russian Text Normalization and Synonym Expansion

This module provides:
1. Text normalization (NFC Unicode, lowercase, punctuation removal)
2. Multilingual synonym expansion for common medical terms
3. Query expansion for robust search matching
"""

import re
from unicodedata import normalize
from typing import List, Dict, Set

# Russian punctuation patterns
RUS_PUNCT = r"[?!.,;:()\[\]«»\"'…/\\-]+"

# Service synonyms map (canonical → variants)
# Includes Russian and English terms, lemmas, and colloquialisms
SERVICE_SYNONYMS: Dict[str, List[str]] = {
    # Composite filling (пломба)
    "пломба": [
        "пломба", "пломбирование", "пломбу", "пломбы",
        "композит", "композитная", "композитную", "композитной",
        "из смолы", "смола", "смолы", "смолой", "смолу",
        "светоотверждаемая", "фотополимер", "фотополимерная"
    ],
    "composite filling": [
        "composite", "resin", "filling", "tooth-colored",
        "light-cured", "photopolymer", "bonded filling"
    ],

    # Teeth whitening
    "отбеливание": [
        "отбеливание", "отбелить", "отбелить зубы",
        "белые зубы", "осветление", "осветлить"
    ],
    "whitening": [
        "whitening", "bleaching", "teeth whitening",
        "tooth whitening", "brightening"
    ],

    # Cleaning
    "чистка": [
        "чистка", "чистка зубов", "профчистка",
        "гигиена", "гигиеническая чистка", "профессиональная чистка"
    ],
    "cleaning": [
        "cleaning", "prophylaxis", "professional cleaning",
        "dental cleaning", "hygiene"
    ],

    # Consultation
    "консультация": [
        "консультация", "прием", "осмотр", "консультацию",
        "первичный прием", "первичная консультация"
    ],
    "consultation": [
        "consultation", "exam", "checkup", "visit",
        "initial consultation", "first visit"
    ],

    # X-ray
    "рентген": [
        "рентген", "снимок", "рентгеновский снимок",
        "панорамный снимок", "ортопантомограмма", "опг"
    ],
    "x-ray": [
        "x-ray", "xray", "radiograph", "panoramic",
        "panoramic x-ray", "dental x-ray"
    ],

    # Implant
    "имплант": [
        "имплант", "имплантация", "имплантат",
        "имплантанты", "импланты", "зубной имплант"
    ],
    "implant": [
        "implant", "implantation", "dental implant",
        "tooth implant", "implants"
    ],

    # Crown
    "коронка": [
        "коронка", "коронку", "зубная коронка",
        "металлокерамическая", "керамическая"
    ],
    "crown": [
        "crown", "dental crown", "tooth crown",
        "porcelain crown", "ceramic crown"
    ],

    # Extraction
    "удаление": [
        "удаление", "удалить зуб", "вырвать зуб",
        "экстракция", "удаление зуба"
    ],
    "extraction": [
        "extraction", "tooth extraction", "removal",
        "tooth removal", "pull tooth"
    ],

    # Root canal
    "каналы": [
        "каналы", "лечение каналов", "чистка каналов",
        "пломбирование каналов", "эндодонтия"
    ],
    "root canal": [
        "root canal", "endodontic", "endodontics",
        "root canal treatment", "rct"
    ]
}


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
    """
    Expand query to include all known synonyms

    Strategy:
    1. Normalize input query
    2. Check if query matches any synonym variant
    3. If match found, return all variants for that canonical term
    4. If no match, return normalized query

    Args:
        query: Search query (possibly colloquial or inflected)

    Returns:
        List of search terms including all synonyms

    Example:
        >>> expand_synonyms("смолы?")
        ["пломба", "пломбирование", "композит", "из смолы", "смола", "смолы"]
        >>> expand_synonyms("composite")
        ["composite", "resin", "filling", "tooth-colored"]
    """
    if not query:
        return []

    normalized = normalize_query(query)

    # Check each synonym group
    for canonical, variants in SERVICE_SYNONYMS.items():
        # Check if normalized query matches any variant in this group
        for variant in variants:
            if normalized == normalize_query(variant):
                # Return all normalized variants from this group
                return list({normalize_query(v) for v in variants + [canonical]})

    # No synonym match found, return normalized query
    return [normalized]


def get_all_searchable_terms(query: str) -> Set[str]:
    """
    Get all searchable terms including partial matches

    This is useful for fuzzy matching where you want to try:
    - Full query
    - Individual words
    - Synonym expansions

    Args:
        query: Raw search query

    Returns:
        Set of all searchable terms

    Example:
        >>> get_all_searchable_terms("композитная пломба")
        {"композитная пломба", "композитная", "пломба", "пломбирование", ...}
    """
    terms: Set[str] = set()

    # Add normalized full query
    normalized = normalize_query(query)
    if normalized:
        terms.add(normalized)

    # Add individual words
    words = normalized.split()
    for word in words:
        if word and len(word) >= 3:  # Skip very short words
            terms.add(word)

            # Expand synonyms for each word
            synonyms = expand_synonyms(word)
            terms.update(synonyms)

    # Expand synonyms for full query
    full_synonyms = expand_synonyms(query)
    terms.update(full_synonyms)

    return terms


def format_price_reply(
    service_name: str,
    price: float,
    currency: str = "USD",
    language: str = "ru",
    unit: str = "per surface"
) -> str:
    """
    Format deterministic price reply without LLM

    Args:
        service_name: Service name (will be localized if needed)
        price: Price value
        currency: Currency code (USD, EUR, RUB, MXN)
        language: Language code (ru, en)
        unit: Unit description

    Returns:
        Formatted price message with CTA

    Example:
        >>> format_price_reply("Composite filling", 80.0, "USD", "ru")
        "Композитная пломба: $80.00 за одну поверхность. Записать вас на удобное время?"
    """
    # Currency symbols
    currency_symbols = {
        "USD": "$",
        "EUR": "€",
        "MXN": "$",
        "RUB": "₽",
        "GBP": "£"
    }
    symbol = currency_symbols.get(currency, f"{currency} ")

    # Localize service name if needed
    service_names_ru = {
        "composite filling": "Композитная пломба",
        "teeth whitening": "Отбеливание зубов",
        "dental cleaning": "Профессиональная чистка",
        "consultation": "Консультация",
        "x-ray": "Рентген",
        "implant": "Имплантация",
        "crown": "Коронка",
        "extraction": "Удаление зуба",
        "root canal": "Лечение каналов"
    }

    # Localized units
    units_ru = {
        "per surface": "за одну поверхность",
        "per tooth": "за зуб",
        "per visit": "за визит",
        "per procedure": "за процедуру"
    }

    units_en = {
        "per surface": "per surface",
        "per tooth": "per tooth",
        "per visit": "per visit",
        "per procedure": "per procedure"
    }

    if language.startswith("ru"):
        # Russian response
        name = service_names_ru.get(service_name.lower(), service_name)
        unit_text = units_ru.get(unit, unit)
        cta = "Записать вас на удобное время?"

        return f"{name}: {symbol}{price:.2f} {unit_text}. {cta}"
    else:
        # English response
        unit_text = units_en.get(unit, unit)
        cta = "Would you like to book an appointment?"

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
