"""Booking extraction and helper utilities."""
import re
import logging
from typing import Optional, Dict, Any, List, Tuple
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

# Tool argument schemas for validation
TOOL_SCHEMAS = {
    'check_availability': {
        'required': ['date'],
        'optional': ['doctor_id', 'appointment_type', 'duration_minutes'],
    },
    'book_appointment': {
        'required': ['datetime_str', 'appointment_type'],
        'optional': ['patient_identifier', 'patient_name', 'patient_phone', 'doctor_id', 'duration_minutes', 'patient_id'],
    },
    'query_prices': {
        'required': [],
        'optional': ['service_type', 'services'],
    },
    'cancel_appointment': {
        'required': ['patient_id'],
        'optional': ['appointment_id'],
    },
}


def fallback_booking_extraction(message: str) -> dict:
    """Regex-based fallback for booking info extraction."""
    extracted = {}
    message_lower = message.lower()

    # Intent detection
    if any(w in message_lower for w in ['book', 'schedule', 'appointment', 'запис']):
        extracted['intent'] = 'book'
    elif any(w in message_lower for w in ['cancel', 'отмен']):
        extracted['intent'] = 'cancel'
    elif any(w in message_lower for w in ['reschedule', 'перенес']):
        extracted['intent'] = 'reschedule'

    # Service type
    services = ['cleaning', 'checkup', 'exam', 'filling', 'root canal', 'whitening',
                'чистка', 'осмотр', 'пломба', 'limpieza', 'examen']
    for service in services:
        if service in message_lower:
            extracted['service_type'] = service
            break

    # Phone number (any 10+ digit sequence)
    phone_match = re.search(r'[\d\-\(\)\s]{10,}', message)
    if phone_match:
        extracted['patient_phone'] = re.sub(r'[^\d]', '', phone_match.group())

    # Name after "my name is" or similar
    name_patterns = [
        r"my name is\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)",
        r"i'm\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)",
        r"меня зовут\s+([А-ЯЁ][а-яё]+(?:\s+[А-ЯЁ][а-яё]+)?)",
        r"mi nombre es\s+([A-Z][a-záéíóúñ]+(?:\s+[A-Z][a-záéíóúñ]+)?)",
    ]
    for pattern in name_patterns:
        match = re.search(pattern, message, re.IGNORECASE)
        if match:
            extracted['patient_name'] = match.group(1)
            break

    # Time patterns
    time_patterns = [
        r'(\d{1,2}(?::\d{2})?\s*(?:am|pm))',
        r'(\d{1,2}:\d{2})',
        r'(morning|afternoon|evening)',
    ]
    for pattern in time_patterns:
        match = re.search(pattern, message_lower)
        if match:
            extracted['requested_time'] = match.group(1)
            break

    # Date patterns
    if 'tomorrow' in message_lower or 'завтра' in message_lower or 'mañana' in message_lower:
        extracted['requested_date'] = 'tomorrow'
    elif 'today' in message_lower or 'сегодня' in message_lower or 'hoy' in message_lower:
        extracted['requested_date'] = 'today'

    return extracted


def resolve_doctor_id_from_list(
    doctor_name: str,
    doctors: List[Dict[str, Any]]
) -> Optional[str]:
    """
    Resolve doctor name to UUID from a list of doctors.

    Per Opinion 4: "Dr. Smith" -> "doc-123-uuid"
    If multiple matches (Dr. John Smith vs Dr. Jane Smith), returns None
    and planner should ask user to clarify.
    """
    if not doctor_name or not doctors:
        return None

    # Normalize search term
    search_term = doctor_name.lower().replace('dr.', '').replace('dr', '').strip()

    matches = []
    for doc in doctors:
        doc_name = doc.get('name', '').lower()
        if search_term in doc_name or doc_name in search_term:
            matches.append(doc)

    if len(matches) == 1:
        return matches[0].get('id')
    elif len(matches) > 1:
        # Multiple matches - ambiguous
        logger.warning(f"[planner] Ambiguous doctor: '{doctor_name}' matches {[m.get('name') for m in matches]}")
        return None
    else:
        # No matches
        logger.warning(f"[planner] No doctor match for: '{doctor_name}'")
        return None


def generate_booking_summary(
    adapted_args: dict,
    patient_name: str,
    service_type: str,
    datetime_str: str,
    doctor_name: Optional[str] = None,
    availability_verified: bool = False,
    language: str = 'en'
) -> str:
    """
    Generate informative human_summary for ActionProposal.

    Example output:
    - "Book dental cleaning for John Smith on Dec 27 at 10:00 AM"
    - "Записать Марию на чистку зубов на 27 декабря в 10:00"
    """
    # Add availability verification note if we verified
    availability_prefix = ""
    if availability_verified:
        availability_prefix = {
            'en': "I've checked availability and ",
            'es': "He verificado la disponibilidad y ",
            'ru': "Я проверил(а) доступность и ",
        }.get(language, "I've checked availability and ")

    if language == 'ru':
        parts = [f"{availability_prefix}Записать {patient_name}"]
        if service_type and service_type != 'appointment':
            service_ru = {
                'cleaning': 'на чистку зубов',
                'dental_cleaning': 'на чистку зубов',
                'checkup': 'на осмотр',
                'exam': 'на обследование',
                'filling': 'на пломбирование',
                'root canal': 'на лечение корневого канала',
                'whitening': 'на отбеливание',
                'general': 'на прием',
                'consultation': 'на консультацию',
            }.get(service_type.lower(), f'на {service_type}')
            parts.append(service_ru)
        if doctor_name:
            parts.append(f"к {doctor_name}")
        if datetime_str:
            parts.append(f"на {datetime_str}")
        return ' '.join(parts)

    elif language == 'es':
        parts = [f"{availability_prefix}Reservar {service_type} para {patient_name}"]
        if doctor_name:
            parts.append(f"con {doctor_name}")
        if datetime_str:
            parts.append(f"el {datetime_str}")
        return ' '.join(parts)

    else:  # English default
        parts = [f"{availability_prefix}Book {service_type} for {patient_name}"]
        if doctor_name:
            parts.append(f"with {doctor_name}")
        if datetime_str:
            parts.append(f"on {datetime_str}")
        return ' '.join(parts)


async def resolve_datetime_for_tool(
    natural_date: str,
    clinic_timezone: str
) -> Optional[str]:
    """
    Convert natural language date to ISO format for tool arguments.

    Handles: "tomorrow", "next Tuesday", "January 15th", etc.
    Returns None if parsing fails (caller should ask for clarification).
    """
    if not natural_date:
        return None

    try:
        from dateparser import parse as dateparser_parse
    except ImportError:
        logger.warning("[datetime_resolver] dateparser not installed, falling back to basic parsing")
        dateparser_parse = None

    try:
        # Get timezone object
        try:
            from zoneinfo import ZoneInfo
            tz = ZoneInfo(clinic_timezone)
        except ImportError:
            import pytz
            tz = pytz.timezone(clinic_timezone)

        now = datetime.now(tz)

        if dateparser_parse:
            # Parse the natural language date with dateparser
            parsed = dateparser_parse(
                natural_date,
                settings={
                    'PREFER_DATES_FROM': 'future',
                    'RELATIVE_BASE': now.replace(tzinfo=None),  # dateparser expects naive datetime
                    'TIMEZONE': clinic_timezone,
                    'RETURN_AS_TIMEZONE_AWARE': True,
                }
            )

            if parsed:
                iso_str = parsed.isoformat()
                logger.info(f"[datetime_resolver] '{natural_date}' -> {iso_str}")
                return iso_str
            else:
                logger.warning(f"[datetime_resolver] Could not parse: '{natural_date}'")
                return None
        else:
            # Basic fallback parsing without dateparser
            date_lower = natural_date.lower().strip()
            if 'tomorrow' in date_lower:
                target = now + timedelta(days=1)
                return target.replace(hour=9, minute=0, second=0, microsecond=0).isoformat()
            elif 'today' in date_lower:
                return now.replace(minute=0, second=0, microsecond=0).isoformat()
            else:
                logger.warning(f"[datetime_resolver] No dateparser, cannot parse: '{natural_date}'")
                return None

    except Exception as e:
        logger.error(f"[datetime_resolver] Error parsing '{natural_date}': {e}")
        return None


def validate_tool_arguments(tool_name: str, arguments: dict) -> Tuple[bool, Optional[str]]:
    """
    Validate that arguments match the expected tool signature.

    Returns (is_valid: bool, error_message: Optional[str]).
    """
    schema = TOOL_SCHEMAS.get(tool_name)
    if not schema:
        return True, None  # Unknown tool, skip validation

    missing = []
    for field in schema['required']:
        if not arguments.get(field):
            missing.append(field)

    if missing:
        return False, f"Missing required fields for {tool_name}: {missing}"

    return True, None
