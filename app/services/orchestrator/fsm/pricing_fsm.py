"""Pricing flow FSM - pure deterministic business logic.

This module implements the pricing flow as a pure function:
    step(state, event) -> (new_state, actions)

Pricing is a single-turn flow - query → tool call → response.
No state persistence needed across turns.
"""

from typing import Tuple, List, Dict, Any
from dataclasses import replace

from .types import Action, CallTool, Respond, Event, UserEvent, ToolResultEvent
from .state import PricingState, PricingStage


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
            results = event.result.get('results', []) if event.success else []
            state = replace(state, results=results, stage=PricingStage.RESPOND)

            if not results:
                msg = get_no_results_message(lang)
                return replace(state, stage=PricingStage.COMPLETE), [Respond(text=msg)]

            # Format pricing response
            response = format_pricing_response(results, lang)
            return replace(state, stage=PricingStage.COMPLETE), [Respond(text=response)]

        # Unknown tool result
        return state, [Respond(text=get_error_message(lang))]

    # Handle user query
    assert isinstance(event, UserEvent)
    state = replace(state, query=event.text, language=event.language)
    lang = event.language

    if state.stage == PricingStage.QUERY:
        return replace(state, stage=PricingStage.RESPOND), [
            CallTool(name="query_service_prices", args={"query": event.text})
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


def get_no_results_message(lang: str) -> str:
    """Get localized 'no results' message."""
    messages = {
        'en': "I couldn't find pricing for that service. Could you try asking about a different service?",
        'ru': "Не удалось найти информацию о ценах на эту услугу. Попробуйте спросить о другой услуге?",
        'es': "No pude encontrar precios para ese servicio. ¿Podría preguntar sobre otro servicio?",
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
