"""Pricing flow FSM - pure deterministic business logic.

This module implements the pricing flow as a pure function:
    step(state, event) -> (new_state, actions)

Pricing is a single-turn flow - query → tool call → response.
No state persistence needed across turns.
"""

import re
import logging
from typing import Tuple, List, Dict, Any, Optional
from dataclasses import replace

from .types import Action, CallTool, Respond, Event, UserEvent, ToolResultEvent
from .state import PricingState, PricingStage

logger = logging.getLogger(__name__)

# Minimum relevance score to accept search results
# Below this threshold, we treat results as "not found" to avoid returning irrelevant services
MIN_RELEVANCE_THRESHOLD = 0.3

# Russian service name aliases (for when router doesn't extract service_type)
SERVICE_ALIASES_RU = {
    'виниры': 'veneers',
    'винир': 'veneers',
    'импланты': 'implants',
    'имплант': 'implants',
    'чистка': 'cleaning',
    'чистку': 'cleaning',
    'отбеливание': 'whitening',
    'пломба': 'filling',
    'пломбу': 'filling',
    'коронка': 'crown',
    'коронку': 'crown',
    'удаление': 'extraction',
    'осмотр': 'checkup',
    'консультация': 'consultation',
    'консультацию': 'consultation',
}


def extract_service_keyword(text: str, language: str = 'ru') -> Optional[str]:
    """Extract service keyword from user text.

    Handles patterns like:
    - "сколько стоят виниры" → "veneers"
    - "цены на виниры" → "veneers"
    - "это на импланты, а я спрашивал про виниры" → "veneers"

    Args:
        text: Raw user message
        language: Language code

    Returns:
        Extracted service keyword (English) or None
    """
    if not text:
        return None

    text_lower = text.lower()

    # Pattern 1: Correction pattern "спрашивал про X" - extract the NEW target
    correction_match = re.search(r'спрашивал\s+(?:про|о|об)\s+(\w+)', text_lower)
    if correction_match:
        word = correction_match.group(1)
        if word in SERVICE_ALIASES_RU:
            logger.info(f"Extracted service from correction pattern: '{word}' → '{SERVICE_ALIASES_RU[word]}'")
            return SERVICE_ALIASES_RU[word]

    # Pattern 2: "цены/цена на X" or "стоимость X"
    price_pattern = re.search(r'(?:цен\w*|стоимость)\s+(?:на|)?\s*(\w+)', text_lower)
    if price_pattern:
        word = price_pattern.group(1)
        if word in SERVICE_ALIASES_RU:
            logger.info(f"Extracted service from price pattern: '{word}' → '{SERVICE_ALIASES_RU[word]}'")
            return SERVICE_ALIASES_RU[word]

    # Pattern 3: "сколько стоит/стоят X"
    cost_pattern = re.search(r'сколько\s+стои[тя]\w*\s+(\w+)', text_lower)
    if cost_pattern:
        word = cost_pattern.group(1)
        if word in SERVICE_ALIASES_RU:
            logger.info(f"Extracted service from cost pattern: '{word}' → '{SERVICE_ALIASES_RU[word]}'")
            return SERVICE_ALIASES_RU[word]

    # Pattern 4: Direct service name anywhere in text (last resort)
    for ru_word, en_word in SERVICE_ALIASES_RU.items():
        if ru_word in text_lower:
            logger.info(f"Extracted service from direct match: '{ru_word}' → '{en_word}'")
            return en_word

    return None


def get_clean_query(event: UserEvent) -> str:
    """Get clean service query from user event.

    Priority:
    1. Router-extracted service_type (most reliable)
    2. Extracted service keyword from text
    3. Raw text (fallback)

    Args:
        event: User event with text and router output

    Returns:
        Clean query string for price search
    """
    # Priority 1: Router extracted service_type
    if event.router and event.router.service_type:
        logger.info(f"Using router service_type: '{event.router.service_type}'")
        return event.router.service_type

    # Priority 2: Extract from text using patterns
    extracted = extract_service_keyword(event.text, event.language)
    if extracted:
        logger.info(f"Using extracted keyword: '{extracted}'")
        return extracted

    # Priority 3: Fallback to raw text (less ideal but works with FTS)
    logger.warning(f"No service keyword extracted, using raw text: '{event.text[:50]}...'")
    return event.text


def filter_relevant_results(results: List[Dict[str, Any]], query: str) -> List[Dict[str, Any]]:
    """Filter search results by relevance threshold.

    Rejects low-confidence matches that could be semantically unrelated
    (e.g., implants when user asked for veneers).

    Args:
        results: Raw search results
        query: Original query for logging

    Returns:
        Filtered results above threshold
    """
    if not results:
        return []

    filtered = []
    for r in results:
        score = r.get('relevance_score', 0)
        stage = r.get('search_stage', 'unknown')

        # Accept results above threshold OR from exact/alias matches
        if score >= MIN_RELEVANCE_THRESHOLD or stage in ('alias_exact', 'alias_trigram'):
            filtered.append(r)
        else:
            logger.warning(
                f"Rejecting low-relevance result: '{r.get('name')}' "
                f"(score={score:.2f}, stage={stage}) for query '{query}'"
            )

    if not filtered and results:
        logger.warning(
            f"All {len(results)} results rejected for query '{query}' "
            f"(best score: {results[0].get('relevance_score', 0):.2f})"
        )

    return filtered


def step(state: PricingState, event: Event) -> Tuple[PricingState, List[Action]]:
    """Pure pricing flow FSM - single-turn, no state persistence needed.

    Args:
        state: Current pricing state
        event: User message or tool result

    Returns:
        Tuple of (new_state, list_of_actions)
    """
    lang = state.language

    # Handle tool result
    if isinstance(event, ToolResultEvent):
        if event.tool_name == "query_service_prices":
            raw_results = event.result.get('results', []) if event.success else []

            # Filter out low-relevance results to avoid returning wrong services
            # (e.g., implants when user asked for veneers)
            results = filter_relevant_results(raw_results, state.query or "")
            state = replace(state, results=results, stage=PricingStage.RESPOND)

            if not results:
                msg = get_no_results_message(lang, state.query)
                return replace(state, stage=PricingStage.COMPLETE), [Respond(text=msg)]

            # Format pricing response
            response = format_pricing_response(results, lang)
            return replace(state, stage=PricingStage.COMPLETE), [Respond(text=response)]

        # Unknown tool result
        return state, [Respond(text=get_error_message(lang))]

    # Handle user query
    assert isinstance(event, UserEvent)

    # Extract clean service query (uses router.service_type if available)
    clean_query = get_clean_query(event)
    logger.info(f"Pricing query: raw='{event.text[:50]}...' → clean='{clean_query}'")

    state = replace(state, query=clean_query, language=event.language)
    lang = event.language

    if state.stage == PricingStage.QUERY:
        return replace(state, stage=PricingStage.RESPOND), [
            CallTool(name="query_service_prices", args={"query": clean_query})
        ]

    # Already completed - respond to follow-up
    return state, [Respond(text=get_anything_else_message(lang))]


def format_pricing_response(results: List[Dict[str, Any]], lang: str) -> str:
    """Format pricing results.

    Args:
        results: List of service pricing dictionaries
        lang: Language code

    Returns:
        Formatted pricing response string
    """
    lines = []
    for svc in results[:5]:
        name = svc.get('name', 'Service')
        price = svc.get('price', 'N/A')
        currency = svc.get('currency', 'USD')
        if price and price != 'N/A':
            lines.append(f"• {name}: {price} {currency}")
        else:
            lines.append(f"• {name}: Price available upon consultation")

    headers = {
        'en': "Here are our prices:\n",
        'ru': "Вот наши цены:\n",
        'es': "Estos son nuestros precios:\n",
    }
    footers = {
        'en': "\n\nWould you like to book an appointment for any of these services?",
        'ru': "\n\nХотите записаться на какую-либо из этих услуг?",
        'es': "\n\n¿Le gustaría agendar una cita para alguno de estos servicios?",
    }

    return headers.get(lang, headers['en']) + '\n'.join(lines) + footers.get(lang, footers['en'])


def get_no_results_message(lang: str, query: Optional[str] = None) -> str:
    """Get localized 'no results' message with helpful suggestion.

    Args:
        lang: Language code
        query: The service that wasn't found (for context)

    Returns:
        Helpful message suggesting consultation
    """
    # More helpful message that acknowledges the specific service
    if query:
        messages = {
            'en': f"I couldn't find exact pricing for '{query}' in our system. "
                  f"The cost typically depends on the specific treatment plan. "
                  f"I can schedule a consultation where the doctor will assess your needs and provide an accurate quote. Would you like to book one?",
            'ru': f"К сожалению, у меня нет точной цены на '{query}' в системе. "
                  f"Стоимость обычно зависит от конкретного плана лечения. "
                  f"Я могу записать вас на консультацию, где врач оценит ваши потребности и назовёт точную цену. Хотите записаться?",
            'es': f"No encontré el precio exacto para '{query}' en nuestro sistema. "
                  f"El costo generalmente depende del plan de tratamiento específico. "
                  f"Puedo programar una consulta donde el doctor evaluará sus necesidades y le dará un presupuesto exacto. ¿Le gustaría agendar una?",
        }
    else:
        messages = {
            'en': "I couldn't find pricing for that service. Could you try asking about a different service, or would you like to schedule a consultation?",
            'ru': "Не удалось найти информацию о ценах на эту услугу. Попробуйте спросить о другой услуге или хотите записаться на консультацию?",
            'es': "No pude encontrar precios para ese servicio. ¿Podría preguntar sobre otro servicio o le gustaría programar una consulta?",
        }
    return messages.get(lang, messages['en'])


def get_error_message(lang: str) -> str:
    """Get localized error message."""
    messages = {
        'en': "Something went wrong while looking up prices. Please try again.",
        'ru': "Произошла ошибка при поиске цен. Попробуйте ещё раз.",
        'es': "Algo salió mal al buscar precios. Por favor intente de nuevo.",
    }
    return messages.get(lang, messages['en'])


def get_anything_else_message(lang: str) -> str:
    """Get localized 'anything else' message."""
    messages = {
        'en': "Is there anything else you'd like to know about our prices?",
        'ru': "Есть ли что-то ещё, что вы хотели бы узнать о наших ценах?",
        'es': "¿Hay algo más que le gustaría saber sobre nuestros precios?",
    }
    return messages.get(lang, messages['en'])
