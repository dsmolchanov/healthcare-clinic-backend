"""Service catalog for pricing and service search."""
from typing import List, Dict, Optional
import logging
from .language_service import get_localized_field

logger = logging.getLogger(__name__)


def search_services_in_memory(
    services: list,
    query: str,
    language: str = 'en'
) -> list:
    """
    Search services in memory with multilingual support.

    Args:
        services: List of cached service dicts
        query: Search query (already cleaned)
        language: Language code for field priority

    Returns:
        List of matching services, sorted by relevance
    """
    if not query:
        # Return all services if no query
        return services[:10]

    query_lower = query.lower()
    query_words = query_lower.split()

    # Define field search priority by language
    name_fields = {
        'ru': ['name_ru', 'name', 'name_en'],
        'es': ['name_es', 'name', 'name_en'],
        'en': ['name_en', 'name'],
        'pt': ['name_pt', 'name', 'name_en'],
        'he': ['name_he', 'name', 'name_en'],
    }.get(language, ['name', 'name_en'])

    scored_results = []

    for service in services:
        score = 0
        matched_name = None

        # Check name fields
        for field in name_fields:
            value = service.get(field, '')
            if value:
                value_lower = value.lower()

                # Exact match
                if query_lower == value_lower:
                    score = 100
                    matched_name = value
                    break

                # Query contained in name
                if query_lower in value_lower:
                    score = max(score, 80)
                    matched_name = value

                # All query words found in name
                if all(word in value_lower for word in query_words):
                    score = max(score, 70)
                    matched_name = value

                # Any query word found
                word_matches = sum(1 for word in query_words if word in value_lower)
                if word_matches > 0:
                    word_score = 30 + (word_matches * 10)
                    if word_score > score:
                        score = word_score
                        matched_name = value

        # Also check category
        category = service.get('category', '').lower()
        if category and query_lower in category:
            score = max(score, 40)

        if score > 0:
            scored_results.append((score, service, matched_name))

    # Sort by score descending
    scored_results.sort(key=lambda x: x[0], reverse=True)

    return [s[1] for s in scored_results]


# Service patterns for extraction
SERVICE_PATTERNS = {
    'cleaning': ['cleaning', 'limpieza', 'чистка', 'clean'],
    'whitening': ['whitening', 'blanqueamiento', 'отбеливание', 'whiten'],
    'root_canal': ['root canal', 'endodoncia', 'удаление нерва', 'root-canal'],
    'filling': ['filling', 'empaste', 'пломба', 'cavity'],
    'checkup': ['checkup', 'exam', 'revisión', 'осмотр', 'check-up', 'examination'],
    'extraction': ['extraction', 'extracción', 'удаление', 'remove', 'pull'],
    'crown': ['crown', 'corona', 'коронка'],
    'implant': ['implant', 'implante', 'имплант'],
}


def extract_services_from_message(message: str) -> list:
    """
    Extract service types mentioned in user message.

    Used for direct query_prices calls when LLM fails.
    """
    found = []
    msg_lower = message.lower()
    for service, patterns in SERVICE_PATTERNS.items():
        if any(p in msg_lower for p in patterns):
            found.append(service)

    return found if found else ['general']


def format_price_response(
    tool_results: Dict,
    price_query: Dict,
    language: str = 'en'
) -> str:
    """
    Format tool results into a natural language response.

    This ensures dynamic_info_agent always completes with a response,
    never deferring to process_node for tool-based queries.

    Args:
        tool_results: Results from tool execution
        price_query: Results from price query context
        language: Response language

    Returns:
        Formatted response string
    """
    # Check both sources for price results
    results = (
        tool_results.get('query_service_prices', {}).get('results') or
        tool_results.get('query_service_prices') or
        price_query.get('results') or
        []
    )

    if isinstance(results, list) and results:
        price_lines = []
        for svc in results[:5]:
            name = svc.get('name', svc.get('service_name', 'Service'))
            price = svc.get('price') or svc.get('base_price')
            currency = svc.get('currency', 'USD')
            if price is not None:
                price_lines.append(f"- {name}: ${price} {currency}")
            else:
                price_lines.append(f"- {name}: price varies")

        if price_lines:
            templates = {
                'en': "Here are the prices:\n" + "\n".join(price_lines) + "\n\nWould you like to schedule a consultation?",
                'es': "Aquí están los precios:\n" + "\n".join(price_lines) + "\n\n¿Le gustaría programar una consulta?",
                'ru': "Вот цены:\n" + "\n".join(price_lines) + "\n\nХотите записаться на консультацию?",
            }
            return templates.get(language, templates['en'])

    # Fallback: no results found
    templates = {
        'en': "I couldn't find specific pricing information. Please call the clinic for accurate pricing, or I can help you schedule a consultation.",
        'es': "No encontré información de precios específica. Por favor llame a la clínica, o puedo ayudarle a programar una consulta.",
        'ru': "Я не нашёл конкретную информацию о ценах. Позвоните в клинику или я могу записать вас на консультацию.",
    }
    return templates.get(language, templates['en'])
