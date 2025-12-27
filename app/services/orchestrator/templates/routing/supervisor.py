"""Supervisor routing logic for healthcare conversations."""
from typing import Literal, List, Optional
from ..classifiers.intent_classifier import LANE_ALLOWED_TOOLS, classify_intent, ClassifiedIntent
import logging

logger = logging.getLogger(__name__)

# Lane types
LaneType = Literal["scheduling", "dynamic_info", "static_info", "exit", "out_of_scope", "time_query", "pii_detected"]

# Confirmation words (multilingual)
CONFIRMATION_WORDS: List[str] = [
    'да', 'yes', 'ok', 'okay', 'sure', 'sí', 'si',
    'хорошо', 'ладно', 'давай', 'конечно', 'угу'
]

# Pain/symptom keywords
PAIN_KEYWORDS: List[str] = ['pain', 'hurts', 'ache', 'болит', 'боль', 'dolor', 'duele']

# Routing keywords (used when no LLM available)
SCHEDULING_KEYWORDS_ROUTING: List[str] = [
    'book', 'appointment', 'schedule', 'reschedule', 'cancel', 'pain', 'hurts', 'болит'
]
PRICE_KEYWORDS_ROUTING: List[str] = [
    'price', 'cost', 'how much', 'cuanto', 'стоимость', 'сколько', 'fee', 'charge'
]
AVAILABILITY_KEYWORDS: List[str] = [
    'available', 'availability', 'slot', 'opening', 'free', 'when can'
]
STATIC_KEYWORDS: List[str] = [
    'hours', 'location', 'address', 'phone', 'parking', 'где', 'адрес', 'часы'
]
EXIT_KEYWORDS: List[str] = [
    'bye', 'goodbye', 'до свидания', 'thanks bye', 'adios'
]

# All tools available
ALL_TOOLS: List[str] = [
    'check_availability', 'book_appointment', 'cancel_appointment',
    'reschedule_appointment', 'query_service_prices'
]


def determine_lane(intent: ClassifiedIntent) -> LaneType:
    """
    Map classified intent to lane.

    This is deterministic - no LLM involved.
    """
    intent_to_lane = {
        "scheduling": "scheduling",
        "pricing": "dynamic_info",
        "availability": "scheduling",
        "static_info": "static_info",
        "out_of_scope": "out_of_scope",
        "time_query": "time_query",
        "greeting": "static_info",
        "unknown": "dynamic_info",
    }
    return intent_to_lane.get(intent.intent, "dynamic_info")


def get_allowed_tools_for_lane(lane: LaneType) -> List[str]:
    """Get tools allowed for a lane."""
    return LANE_ALLOWED_TOOLS.get(lane, [])


def get_blocked_tools_for_lane(lane: LaneType) -> List[str]:
    """Get tools blocked for a lane."""
    allowed = get_allowed_tools_for_lane(lane)
    return [t for t in ALL_TOOLS if t not in allowed]


def is_short_confirmation(message: str) -> bool:
    """Check if message is a short confirmation word."""
    message_lower = message.lower().strip()
    return message_lower in CONFIRMATION_WORDS or len(message_lower) <= 5


def has_pain_keywords(message: str) -> bool:
    """Check if message contains pain/symptom keywords."""
    message_lower = message.lower()
    return any(kw in message_lower for kw in PAIN_KEYWORDS)


def get_out_of_scope_response(language: str) -> str:
    """Get localized out-of-scope refusal response."""
    refusals = {
        'en': "I'm here to help with dental appointments and clinic information. I can't answer general knowledge questions, but I'd be happy to help you book an appointment or answer questions about our services.",
        'es': "Estoy aquí para ayudarle con citas dentales e información de la clínica. No puedo responder preguntas generales, pero con gusto le ayudo a agendar una cita.",
        'ru': "Я здесь, чтобы помочь с записью к стоматологу и информацией о клинике. Я не могу отвечать на общие вопросы, но с радостью помогу записаться на приём.",
    }
    return refusals.get(language, refusals['en'])


def get_time_query_response(language: str, business_hours: str) -> str:
    """Get localized time query response."""
    time_responses = {
        'en': f"I don't have access to a real-time clock. Our clinic hours are: {business_hours}. Is there anything else I can help you with?",
        'es': f"No tengo acceso al reloj en tiempo real. Nuestro horario es: {business_hours}. ¿Hay algo más en lo que pueda ayudarle?",
        'ru': f"У меня нет доступа к часам реального времени. Наш график работы: {business_hours}. Могу ли я помочь вам с чем-то ещё?",
    }
    return time_responses.get(language, time_responses['en'])


def route_by_keywords(message: str) -> LaneType:
    """
    Route message based on keywords (fallback when no LLM).

    Returns:
        Lane type based on keyword matching
    """
    message_lower = message.lower()

    if any(word in message_lower for word in SCHEDULING_KEYWORDS_ROUTING):
        return "scheduling"
    elif any(word in message_lower for word in PRICE_KEYWORDS_ROUTING + AVAILABILITY_KEYWORDS):
        return "dynamic_info"
    elif any(word in message_lower for word in STATIC_KEYWORDS):
        return "static_info"
    elif any(word in message_lower for word in EXIT_KEYWORDS):
        return "exit"
    else:
        return "dynamic_info"


def should_route_to_exit(next_agent: Optional[str]) -> bool:
    """Check if routing should go to exit based on next_agent."""
    return next_agent in ("out_of_scope", "time_query", "pii_detected")
