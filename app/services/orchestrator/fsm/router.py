"""One-shot router for FSM orchestration.

This module implements the ONLY LLM call per turn - parsing user messages
into structured RouterOutput. All subsequent logic is pure Python.

Key principles:
- Use proper system+user role separation
- Return raw date strings (not ISO) - LLMs are bad at calendar math
- Fall back to keyword-based routing on failure
"""

import json
import logging
import re
from typing import Any

from .types import RouterOutput

logger = logging.getLogger(__name__)


# System prompt - kept separate from user message for proper role handling
ROUTER_SYSTEM_PROMPT = """You are a medical clinic receptionist assistant router. Parse user messages and extract structured information.

ROUTING RULES:
- "scheduling": Booking, rescheduling, visiting, "come in", availability requests, pain/symptoms
- "pricing": Cost, price, how much, fee questions
- "cancel": Cancel, void, remove appointment
- "info": Hours, location, address, phone, parking, current time questions, timezone questions, "what time is it"
- "exit": Goodbye, thanks bye, done
- "irrelevant": General knowledge (history, geography, math), politics, jokes, non-clinic topics, anything NOT about dental/medical appointments

IMPORTANT:
- Questions about current time ("what time is it", "what's the time there") are INFO queries, NOT scheduling.
- Questions like "What is the capital of France?" are IRRELEVANT - do NOT route to scheduling or info.

CRITICAL - DATE/TIME EXTRACTION:
Return the EXACT STRING the user said for dates and times. DO NOT calculate ISO dates.
- User says "next tuesday" → target_date: "next tuesday" (NOT "2025-01-02")
- User says "tomorrow at 2pm" → target_date: "tomorrow", time_of_day: "2pm"
- User says "in 3 days" → target_date: "in 3 days" (NOT calculated date)
Python tools will resolve these to actual datetimes. LLMs are bad at calendar math.

EXAMPLES:
User: "I'd like to book a cleaning for tomorrow"
→ {"route": "scheduling", "service_type": "cleaning", "target_date": "tomorrow"}

User: "How much is a root canal?"
→ {"route": "pricing", "service_type": "root canal"}

User: "Can I come in this Sunday at 3 AM?"
→ {"route": "scheduling", "target_date": "this Sunday", "time_of_day": "3 AM"}

User: "Is Dr. Smith available next week?"
→ {"route": "scheduling", "doctor_name": "Dr. Smith", "target_date": "next week"}

User: "My tooth hurts really bad"
→ {"route": "scheduling", "has_pain": true}

User: "I need to cancel my appointment"
→ {"route": "cancel"}

User: "What are your hours?"
→ {"route": "info"}

User: "What time is it there right now?"
→ {"route": "info"}

User: "What is the capital of France?"
→ {"route": "irrelevant"}

User: "Tell me a joke"
→ {"route": "irrelevant"}

Respond with JSON only. Include all fields you can extract."""


async def route_message(
    message: str,
    llm_factory: Any,
    language: str = "en",
) -> RouterOutput:
    """
    One-shot router: parse user message into structured output.

    This is the ONLY LLM call per turn for routing.
    Uses proper system+user role separation for better results.

    Args:
        message: User's message text
        llm_factory: LLMFactory instance for generating responses
        language: Detected language code

    Returns:
        RouterOutput with extracted entities and route
    """
    try:
        # Import here to avoid circular imports
        from app.services.llm.tiers import ModelTier

        # Proper role separation: system prompt + user message
        response = await llm_factory.generate_for_tier(
            tier=ModelTier.ROUTING,
            messages=[
                {"role": "system", "content": ROUTER_SYSTEM_PROMPT},
                {"role": "user", "content": message},
            ],
            temperature=0.0,  # Deterministic for consistent routing
            max_tokens=200,
            response_format={"type": "json_object"},
        )

        # Handle response.content which may be string or need extraction
        content = response.content
        if hasattr(response, 'choices') and response.choices:
            content = response.choices[0].message.content

        data = json.loads(content)

        return RouterOutput(
            route=data.get('route', 'scheduling'),
            service_type=data.get('service_type'),
            target_date=data.get('target_date'),
            time_of_day=data.get('time_of_day'),
            doctor_name=data.get('doctor_name'),
            patient_name=data.get('patient_name'),
            patient_phone=data.get('patient_phone'),
            has_pain=data.get('has_pain', False),
            cancel_intent=data.get('route') == 'cancel',
            confidence=data.get('confidence', 1.0),
            language=language,
        )
    except Exception as e:
        logger.warning(f"Router LLM failed, using fallback: {e}")
        # Fallback to keyword-based routing
        return fallback_router(message, language)


def fallback_router(message: str, language: str = "en") -> RouterOutput:
    """Keyword-based fallback when LLM fails.

    Includes multilingual keywords for Russian, Spanish, etc.

    Args:
        message: User's message text
        language: Language code

    Returns:
        RouterOutput based on keyword matching
    """
    m = message.lower()

    # Pricing keywords (multilingual)
    if any(kw in m for kw in ['price', 'cost', 'how much', 'fee', 'сколько стоит', 'precio', 'cuanto', 'цена']):
        return RouterOutput(route='pricing', language=language)

    # Cancel keywords (multilingual)
    if any(kw in m for kw in ['cancel', 'отменить', 'cancelar', 'отмена']):
        return RouterOutput(route='cancel', cancel_intent=True, language=language)

    # Info keywords (multilingual) - includes time/timezone queries
    if any(kw in m for kw in ['hours', 'address', 'location', 'located', 'phone', 'parking',
                               'what time', 'current time', 'time is it', 'timezone',
                               'адрес', 'часы', 'horario', 'direccion', 'где', 'ubicación',
                               'который час', 'que hora', 'qué hora']):
        return RouterOutput(route='info', language=language)

    # Exit keywords (multilingual)
    if any(kw in m for kw in ['bye', 'goodbye', 'thanks bye', 'до свидания', 'adios', 'chao', 'пока']):
        return RouterOutput(route='exit', language=language)

    # Irrelevant/out-of-scope detection
    # Check for general knowledge question patterns
    irrelevant_patterns = [
        'capital of', 'president of', 'who is', 'what year', 'how old is',
        'tell me a joke', 'sing a song', 'write a poem', 'столица', 'президент',
        'quien es', 'cuál es la capital', 'dime un chiste'
    ]
    if any(kw in m for kw in irrelevant_patterns):
        return RouterOutput(route='irrelevant', language=language)

    # Pain keywords (multilingual)
    has_pain = any(kw in m for kw in ['pain', 'hurt', 'ache', 'болит', 'dolor', 'duele', 'больно'])

    # FIX: Extract doctor name if mentioned
    doctor_name = None
    doctor_match = re.search(r'(?:dr\.?|doctor)\s+([a-zA-Z]+)', m, re.IGNORECASE)
    if doctor_match:
        doctor_name = doctor_match.group(1).title()  # Capitalize first letter

    # Try to extract service type from common keywords
    service_type = None
    service_keywords = {
        'cleaning': ['cleaning', 'clean', 'чистк', 'limpieza'],  # чистк catches чистка, чистку, etc.
        'checkup': ['checkup', 'check-up', 'exam', 'осмотр', 'revisión'],
        'consultation': ['consultation', 'consult', 'консультаци', 'consulta'],  # catches all cases
        'filling': ['filling', 'cavity', 'пломб', 'relleno', 'кариес'],  # catches all cases
        'extraction': ['extraction', 'pull', 'remove tooth', 'удален', 'extracción'],  # catches all cases
        'whitening': ['whitening', 'bleach', 'отбеливан', 'blanqueamiento'],  # catches all cases
    }
    for svc, keywords in service_keywords.items():
        if any(kw in m for kw in keywords):
            service_type = svc
            break

    # Try to extract simple date references
    target_date = None
    date_keywords = {
        'tomorrow': ['tomorrow', 'завтра', 'mañana'],
        'today': ['today', 'сегодня', 'hoy', 'para hoy'],  # FIX: Add "para hoy"
        'next week': ['next week', 'на следующей неделе', 'próxima semana'],
        # FIX: Add specific day patterns
        'next monday': ['next monday', 'следующий понедельник', 'el próximo lunes'],
        'next tuesday': ['next tuesday', 'следующий вторник', 'el próximo martes'],
        'next wednesday': ['next wednesday', 'следующая среда', 'el próximo miércoles'],
        'next thursday': ['next thursday', 'следующий четверг', 'el próximo jueves'],
        'next friday': ['next friday', 'следующая пятница', 'el próximo viernes'],
        # "This Sunday", "this Monday", etc.
        'this sunday': ['this sunday', 'в это воскресенье', 'este domingo'],
        'this saturday': ['this saturday', 'в эту субботу', 'este sábado'],
    }
    for date_str, keywords in date_keywords.items():
        if any(kw in m for kw in keywords):
            target_date = date_str
            break

    # FIX: If still no date found, try to extract day names with "next" prefix
    if not target_date:
        day_match = re.search(r'next\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday)', m)
        if day_match:
            target_date = f"next {day_match.group(1)}"

    # FIX: Check for "this <day>" pattern
    if not target_date:
        this_day_match = re.search(r'this\s+(sunday|saturday|monday|tuesday|wednesday|thursday|friday)', m)
        if this_day_match:
            target_date = f"this {this_day_match.group(1)}"

    # FIX: Check for time patterns like "at 3 AM" which imply scheduling
    if not target_date:
        time_match = re.search(r'(?:at\s+)?(\d{1,2})\s*(am|pm|AM|PM)', m)
        if time_match:
            # If there's a day mentioned anywhere, use it
            for day in ['sunday', 'monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday',
                       'воскресенье', 'понедельник', 'domingo', 'lunes']:
                if day in m:
                    target_date = f"this {day}" if 'this' in m or 'this' not in m else day
                    break

    # Default to scheduling
    return RouterOutput(
        route='scheduling',
        service_type=service_type,
        target_date=target_date,
        doctor_name=doctor_name,
        has_pain=has_pain,
        language=language
    )
