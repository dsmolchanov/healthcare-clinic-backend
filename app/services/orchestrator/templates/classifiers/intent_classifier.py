"""
Intent classification for healthcare conversations.

⚠️ DEPRECATED: This module is deprecated as of Phase 6 (2025-12-28).
The FSM orchestrator uses app.services.orchestrator.fsm.router for intent routing.
This file is kept as a fallback for LangGraph orchestrator.
"""

import warnings
warnings.warn(
    "intent_classifier.py is deprecated. FSM uses fsm.router instead.",
    DeprecationWarning,
    stacklevel=2
)

from typing import Literal, List
from dataclasses import dataclass

# Scheduling keywords (multilingual)
SCHEDULING_KEYWORDS: List[str] = [
    # English - action verbs
    'book', 'appointment', 'schedule', 'reschedule', 'cancel',
    'reserve', 'visit', 'see doctor', 'see the doctor',
    # FIX: Add "come in" patterns for scheduling
    'come in', 'stop by', 'drop by', 'swing by',
    # Symptoms/urgency
    'pain', 'hurts', 'ache', 'emergency', 'urgent',
    # Services
    'cleaning', 'checkup', 'exam', 'filling', 'root canal', 'whitening',
    # Contact info submission patterns
    'my phone', 'my number', 'you can reach me', 'contact me at',
    'my name is', 'i am', 'call me at',
    # Spanish
    'cita', 'reservar', 'programar', 'dolor', 'urgente',
    'mi teléfono', 'mi nombre es', 'me llamo',
    'puedo ir', 'puedo pasar', 'venir',  # FIX: Spanish "can I come"
    # Russian
    'записаться', 'запись', 'записать', 'болит', 'боль', 'срочно',
    'мой телефон', 'меня зовут', 'мой номер',
    'прийти', 'зайти', 'приехать', 'могу прийти',  # FIX: Russian "come in"
    # Portuguese
    'agendar', 'consulta', 'marcar', 'dor', 'urgente',
]

# Day/time patterns that indicate scheduling intent
# FIX: "Can I come in Sunday at 3 AM" should route to scheduling
SCHEDULING_DAY_PATTERNS: List[str] = [
    # English days
    'monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday',
    'tomorrow', 'today', 'next week', 'this week',
    # Spanish days
    'lunes', 'martes', 'miércoles', 'jueves', 'viernes', 'sábado', 'domingo',
    'mañana', 'hoy', 'próxima semana',
    # Russian days
    'понедельник', 'вторник', 'среда', 'четверг', 'пятница', 'суббота', 'воскресенье',
    'завтра', 'сегодня', 'на следующей неделе',
]

# Phrases that combined with day/time indicate scheduling
SCHEDULING_INTENT_PHRASES: List[str] = [
    'can i', 'could i', 'may i', 'do you have', 'is there', 'any slots',
    'available', 'availability', 'free', 'open',
    # Spanish
    'puedo', 'podría', 'tienen', 'hay',
    # Russian
    'можно', 'могу', 'есть ли', 'свободно',
]

PRICING_KEYWORDS: List[str] = [
    # English
    'price', 'prices', 'cost', 'costs', 'how much', 'fee', 'fees',
    'charge', 'charges', 'rate', 'rates', 'expensive', 'cheap',
    'affordable', 'compare', 'comparison', 'vs', 'versus',
    # Spanish
    'precio', 'precios', 'costo', 'costos', 'cuánto', 'cuanto cuesta',
    'tarifa', 'tarifas', 'comparar',
    # Russian
    'цена', 'цены', 'стоимость', 'сколько стоит', 'сколько',
    'тариф', 'сравнить',
    # Portuguese
    'preço', 'preços', 'custo', 'custos', 'quanto custa',
]

LANE_ALLOWED_TOOLS = {
    "scheduling": ["check_availability", "book_appointment", "cancel_appointment", "reschedule_appointment"],
    "dynamic_info": ["query_service_prices"],
    "static_info": ["get_clinic_info"],
    "out_of_scope": [],
    "time_query": [],
    "pii_detected": [],
}

OUT_OF_SCOPE_PATTERNS: List[str] = [
    'capital of', 'president of', 'what is the', 'who is', 'when did',
    'history of', 'tell me about', 'explain', 'how does', 'why is',
    'weather', 'sports', 'movie', 'music', 'recipe',
]

TIME_QUERY_PATTERNS: List[str] = [
    'what time is it', 'current time', 'time right now', 'time there',
    'qué hora es', 'сколько сейчас времени', 'который час',
]

CONTACT_INFO_PATTERNS: List[str] = [
    'my phone', 'my number', 'you can reach me', 'my name is',
    'mi teléfono', 'mi nombre', 'me llamo',
    'мой телефон', 'меня зовут', 'мой номер',
    'meu telefone', 'meu nome',
]

# Intent types - explicit, not inferred
IntentType = Literal[
    "scheduling", "pricing", "availability", "static_info",
    "out_of_scope", "time_query", "greeting", "unknown"
]


@dataclass
class ClassifiedIntent:
    """Result of intent classification."""
    intent: IntentType
    confidence: float
    requires_tools: bool  # Whether this intent requires tool calls


def classify_intent(message: str) -> ClassifiedIntent:
    """
    Classify message intent with explicit tool requirements.

    This is the SINGLE SOURCE OF TRUTH for intent classification.
    Downstream nodes should NOT re-interpret intent.
    """
    m = message.lower()

    # Priority order matters
    if looks_like_out_of_scope(m):
        return ClassifiedIntent(intent="out_of_scope", confidence=0.9, requires_tools=False)

    if looks_like_time_query(m):
        return ClassifiedIntent(intent="time_query", confidence=0.9, requires_tools=False)

    if looks_like_pricing(m):
        return ClassifiedIntent(intent="pricing", confidence=0.8, requires_tools=True)  # MUST use tools

    if looks_like_scheduling(m):
        return ClassifiedIntent(intent="scheduling", confidence=0.8, requires_tools=True)

    # Static info (hours, location, phone)
    static_keywords = ['hours', 'open', 'close', 'where', 'address', 'phone', 'часы', 'где', 'адрес']
    if any(kw in m for kw in static_keywords):
        return ClassifiedIntent(intent="static_info", confidence=0.7, requires_tools=False)

    # Greetings
    greeting_keywords = ['hi', 'hello', 'hey', 'привет', 'hola']
    if any(kw in m for kw in greeting_keywords):
        return ClassifiedIntent(intent="greeting", confidence=0.9, requires_tools=False)

    return ClassifiedIntent(intent="unknown", confidence=0.3, requires_tools=False)


def looks_like_scheduling(message: str) -> bool:
    """Check if message has scheduling intent."""
    m = message.lower() if isinstance(message, str) else message
    # Pricing takes precedence
    if looks_like_pricing(m):
        return False

    # Direct keyword match
    if any(k in m for k in SCHEDULING_KEYWORDS):
        return True

    # FIX: Check for day/time + intent phrase combination
    # e.g., "Can I come in Sunday at 3 AM?" has day pattern + intent phrase
    has_day = any(day in m for day in SCHEDULING_DAY_PATTERNS)
    has_intent_phrase = any(phrase in m for phrase in SCHEDULING_INTENT_PHRASES)

    if has_day and has_intent_phrase:
        return True

    # FIX: Check for time patterns (AM/PM, o'clock) with intent phrases
    import re
    has_time = bool(re.search(r'\d{1,2}\s*(?:am|pm|o\'clock|:)', m, re.IGNORECASE))
    if has_time and has_intent_phrase:
        return True

    return False


def looks_like_pricing(message: str) -> bool:
    """Check if message is a pricing query."""
    m = message.lower() if isinstance(message, str) else message
    return any(k in m for k in PRICING_KEYWORDS)


def looks_like_out_of_scope(message: str) -> bool:
    """Detect non-dental general knowledge questions."""
    m = message.lower() if isinstance(message, str) else message
    if looks_like_scheduling(m) or looks_like_pricing(m):
        return False
    return any(p in m for p in OUT_OF_SCOPE_PATTERNS)


def looks_like_time_query(message: str) -> bool:
    """Detect time-related queries."""
    m = message.lower() if isinstance(message, str) else message
    return any(p in m for p in TIME_QUERY_PATTERNS)


def is_contact_info_submission(message: str) -> bool:
    """Check if user is providing their contact info."""
    m = message.lower() if isinstance(message, str) else message
    return any(p in m for p in CONTACT_INFO_PATTERNS)
