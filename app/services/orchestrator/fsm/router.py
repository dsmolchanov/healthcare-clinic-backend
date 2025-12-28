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

# Service keyword mappings (multilingual)
SERVICE_KEYWORDS = {
    'cleaning': ['cleaning', 'clean', 'чистк', 'чистку', 'limpieza'],
    'checkup': ['checkup', 'check-up', 'exam', 'осмотр', 'revisión'],
    'consultation': ['consultation', 'consult', 'консультаци', 'consulta'],
    'filling': ['filling', 'cavity', 'пломб', 'relleno', 'кариес'],
    'extraction': ['extraction', 'pull', 'remove tooth', 'удален', 'extracción'],
    'whitening': ['whitening', 'bleach', 'отбеливан', 'blanqueamiento'],
    'veneers': ['veneers', 'veneer', 'виниры', 'винир', 'carillas', 'carilla'],
    'implants': ['implants', 'implant', 'импланты', 'имплант', 'implantes'],
    'crown': ['crown', 'crowns', 'коронка', 'коронку', 'corona', 'coronas'],
    'root canal': ['root canal', 'endodontic', 'канал', 'эндодонт', 'endodoncia'],
}

# Reverse mapping: keyword → service name
KEYWORD_TO_SERVICE = {}
for service, keywords in SERVICE_KEYWORDS.items():
    for kw in keywords:
        KEYWORD_TO_SERVICE[kw.lower()] = service


def _extract_service_from_keyword(word: str) -> str | None:
    """Extract service type from a single keyword.

    Args:
        word: Single word to match

    Returns:
        Service name or the original word if no match
    """
    word_lower = word.lower()

    # Direct lookup
    if word_lower in KEYWORD_TO_SERVICE:
        return KEYWORD_TO_SERVICE[word_lower]

    # Partial match for Russian suffixes (чистк → cleaning)
    for kw, service in KEYWORD_TO_SERVICE.items():
        if kw in word_lower or word_lower in kw:
            return service

    # Return original word if no match
    return word


def _extract_service_from_message(message: str) -> str | None:
    """Extract service type from message using keyword matching.

    Args:
        message: Full message text (lowercase)

    Returns:
        Service type or None
    """
    for service, keywords in SERVICE_KEYWORDS.items():
        for kw in keywords:
            if kw in message:
                return service
    return None


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

User: "сколько стоят виниры"
→ {"route": "pricing", "service_type": "veneers"}

User: "Это на импланты, а я спрашивал про виниры"
→ {"route": "pricing", "service_type": "veneers"}

User: "No, I meant cleaning, not whitening"
→ {"route": "pricing", "service_type": "cleaning"}

User: "No, I meant Monday, not Tuesday"
→ {"route": "scheduling", "target_date": "Monday"}

User: "I meant at Monday or Tuesday and doctor Mark"
→ {"route": "scheduling", "target_date": "Monday or Tuesday", "doctor_name": "Dr. Mark"}

User: "I meant Dr. Mark, not Dr. Marie"
→ {"route": "scheduling", "doctor_name": "Dr. Mark"}

User: "а сегодня работает доктор Марк?"
→ {"route": "scheduling", "doctor_name": "доктор Марк", "target_date": "сегодня"}

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

    # Scheduling-related keywords that indicate a scheduling correction, not pricing
    scheduling_indicators = [
        'monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday',
        'понедельник', 'вторник', 'среда', 'четверг', 'пятница', 'суббота', 'воскресенье',
        'lunes', 'martes', 'miércoles', 'jueves', 'viernes', 'sábado', 'domingo',
        'doctor', 'dr.', 'dr ', 'доктор', 'врач',
        'tomorrow', 'today', 'next week', 'завтра', 'сегодня', 'mañana', 'hoy',
    ]

    # Check for correction patterns
    is_correction = bool(re.search(r'(?:meant|спрашивал|asked|хотел|имел\s+в\s+виду)', m))

    if is_correction:
        # Determine if this is a scheduling or pricing correction
        has_scheduling_context = any(kw in m for kw in scheduling_indicators)

        if has_scheduling_context:
            # This is a scheduling correction - extract doctor and date
            logger.info(f"Scheduling correction detected: '{m[:50]}...'")
            # Extract doctor name if present
            doctor_name = None
            doctor_match = re.search(r'(?:dr\.?|doctor|доктор|врач)\s+([a-zA-Zа-яА-ЯёЁ]+)', m, re.IGNORECASE)
            if doctor_match:
                doctor_name = doctor_match.group(1).title()

            # Extract date/day
            target_date = None
            for day in ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']:
                if day in m:
                    target_date = day
                    break
            if not target_date:
                for day_ru in ['понедельник', 'вторник', 'среда', 'четверг', 'пятница', 'суббота', 'воскресенье']:
                    if day_ru in m:
                        target_date = day_ru
                        break

            return RouterOutput(
                route='scheduling',
                doctor_name=doctor_name,
                target_date=target_date,
                language=language
            )
        else:
            # Pricing correction - extract service
            correction_match = re.search(r'(?:спрашивал|asked|meant)\s+(?:про|о|об|about|for)\s+(\w+)', m)
            if correction_match:
                extracted_service = _extract_service_from_keyword(correction_match.group(1))
                logger.info(f"Pricing correction detected: routing to pricing with service '{extracted_service}'")
                return RouterOutput(route='pricing', service_type=extracted_service, language=language)

            # Pattern: "No, I meant X" for services - check if X is a known service
            meant_match = re.search(r'(?:meant|хотел|имел\s+в\s+виду)\s+(\w+)', m)
            if meant_match:
                word = meant_match.group(1).lower()
                # Check if word is a known service (exists in KEYWORD_TO_SERVICE)
                if word in KEYWORD_TO_SERVICE or any(kw in word for kw in KEYWORD_TO_SERVICE.keys()):
                    extracted_service = _extract_service_from_keyword(word)
                    logger.info(f"'Meant' pattern detected: routing to pricing with service '{extracted_service}'")
                    return RouterOutput(route='pricing', service_type=extracted_service, language=language)

    # Pricing keywords (multilingual) - expanded Russian list
    pricing_keywords = [
        'price', 'cost', 'how much', 'fee',
        'сколько стоит', 'сколько стоят', 'сколько',  # how much (various forms)
        'цена', 'цены', 'цену',  # price (various cases)
        'стоимость',  # cost
        'precio', 'cuanto', 'cuesta', 'cuestan',  # Spanish
    ]
    if any(kw in m for kw in pricing_keywords):
        # Try to extract service type for pricing
        service_type = _extract_service_from_message(m)
        return RouterOutput(route='pricing', service_type=service_type, language=language)

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

    # FIX: Extract doctor name if mentioned (supports Latin and Cyrillic)
    doctor_name = None
    # Pattern matches: "dr.", "doctor", "доктор", "врач" followed by a name
    doctor_match = re.search(r'(?:dr\.?|doctor|доктор|врач)\s+([a-zA-Zа-яА-ЯёЁ]+)', m, re.IGNORECASE)
    if doctor_match:
        doctor_name = doctor_match.group(1).title()  # Capitalize first letter

    # Try to extract service type from common keywords
    service_type = _extract_service_from_message(m)

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
