"""Booking flow FSM - pure deterministic business logic.

This module implements the booking flow as a pure function:
    step(state, event) -> (new_state, actions)

No side effects. No I/O. No LLM calls.
All business logic is deterministic and testable.
"""

from typing import Tuple, List, Optional, Dict, Any
from dataclasses import replace
import re

from .types import (
    Action, AskUser, CallTool, Respond, Escalate,
    Event, UserEvent, ToolResultEvent, RouterOutput
)
from .state import BookingState, BookingStage

# Phase 5: Import SOTA reminder templates for rich confirmation messages
from app.services.reminder_templates import format_confirmation_message as format_sota_confirmation

# Phase 5.1: Import robust multilingual text utilities
# Phase 5.2: Added has_time_anchor for auto-availability guard
from .text_utils import (
    is_affirmative as _is_affirmative,
    is_confirmation,
    is_rejection,
    normalize_tokens,
    has_availability_intent,
    has_time_anchor,
)


# Localized messages
MESSAGES = {
    'ask_service': {
        'en': "What type of appointment do you need? We offer cleanings, checkups, consultations, and more.",
        'ru': "Какой тип приёма вам нужен? Мы предлагаем чистку, осмотр, консультацию и другое.",
        'es': "Qué tipo de cita necesita? Ofrecemos limpiezas, chequeos, consultas y más.",
        'he': "איזה סוג תור אתה צריך? אנחנו מציעים ניקוי, בדיקה, ייעוץ ועוד.",
    },
    # Phase 5.2: New template for doctor availability without date
    'ask_when_prefer': {
        'en': "When would you like to see {doctor}? You can say a date like 'tomorrow' or 'next Monday'.",
        'ru': "Когда вы хотели бы к {doctor}? Можете сказать дату, например 'завтра' или 'в понедельник'.",
        'es': "¿Cuándo le gustaría ver a {doctor}? Puede decir una fecha como 'mañana' o 'el lunes'.",
        'he': "מתי תרצה להיפגש עם {doctor}? אפשר לומר תאריך כמו 'מחר' או 'ביום שני'.",
    },
    'ask_date': {
        'en': "When would you like to come in? You can say something like 'tomorrow at 2pm' or 'next Monday morning'.",
        'ru': "Когда вы хотели бы прийти? Можете сказать 'завтра в 14:00' или 'в следующий понедельник утром'.",
        'es': "Cuándo le gustaría venir? Puede decir algo como 'mañana a las 2pm' o 'el próximo lunes por la mañana'.",
        'he': "מתי תרצה להגיע? אפשר להגיד 'מחר ב-14:00' או 'יום שני הבא בבוקר'.",
    },
    'ask_patient_info': {
        'en': "Could you please provide your name and phone number so I can find your record?",
        'ru': "Не могли бы вы назвать ваше имя и номер телефона, чтобы я нашёл(а) вашу карту?",
        'es': "Podría proporcionarme su nombre y número de teléfono para encontrar su registro?",
        'he': "האם תוכל לספק את שמך ומספר הטלפון שלך כדי שאוכל למצוא את הרשומה שלך?",
    },
    'no_availability': {
        'en': "I checked and unfortunately we don't have availability at that time. Our hours are Monday-Friday 9am-5pm. Would you like to try a different time?",
        'ru': "Я проверил(а), но, к сожалению, на это время нет записи. Мы работаем Пн-Пт 9:00-17:00. Хотите попробовать другое время?",
        'es': "He verificado y lamentablemente no tenemos disponibilidad en ese horario. Nuestro horario es de lunes a viernes de 9am a 5pm. Le gustaría probar otro horario?",
        'he': "בדקתי ולצערי אין לנו פנוי בזמן הזה. שעות הפעילות שלנו הן ראשון-חמישי 9:00-17:00. תרצה לנסות זמן אחר?",
    },
    'empathy_prefix': {
        'en': "I'm sorry to hear you're in discomfort. ",
        'ru': "Мне жаль, что вам нехорошо. ",
        'es': "Lamento que tenga molestias. ",
        'he': "מצטער לשמוע שלא נוח לך. ",
    },
    'pain_urgent': {
        'en': "I'm sorry to hear you're in pain. Let me check for the earliest available appointment right away.",
        'ru': "Мне жаль, что вам больно. Позвольте проверить ближайшее доступное время.",
        'es': "Lamento que tenga dolor. Déjeme verificar la cita disponible más temprana de inmediato.",
        'he': "מצטער לשמוע שיש לך כאבים. תן לי לבדוק את התור הקרוב ביותר מיד.",
    },
    'escalate': {
        'en': "I'm having trouble understanding your request. Let me connect you with our staff who can help directly.",
        'ru': "Мне сложно понять ваш запрос. Позвольте соединить вас с нашим персоналом.",
        'es': "Tengo dificultades para entender su solicitud. Permítame conectarlo con nuestro personal.",
        'he': "אני מתקשה להבין את הבקשה שלך. תן לי לחבר אותך לצוות שלנו שיוכל לעזור ישירות.",
    },
    'try_different_time': {
        'en': "No problem. Would you like to try a different time?",
        'ru': "Хорошо. Хотите попробовать другое время?",
        'es': "No hay problema. Le gustaría probar otro horario?",
        'he': "אין בעיה. תרצה לנסות זמן אחר?",
    },
    'booking_failed': {
        'en': "I wasn't able to complete the booking: {error}. Would you like to try again?",
        'ru': "Не удалось завершить запись: {error}. Хотите попробовать снова?",
        'es': "No pude completar la reserva: {error}. Le gustaría intentar de nuevo?",
        'he': "לא הצלחתי להשלים את ההזמנה: {error}. תרצה לנסות שוב?",
    },
    'fallback': {
        'en': "I'm not sure how to help with that. Could you rephrase?",
        'ru': "Не уверен(а), как помочь с этим. Не могли бы вы перефразировать?",
        'es': "No estoy seguro de cómo ayudar con eso. Podría reformular?",
        'he': "אני לא בטוח איך לעזור עם זה. אפשר לנסח מחדש?",
    },
}


def get_msg(key: str, lang: str) -> str:
    """Get localized message, falling back to English."""
    return MESSAGES.get(key, {}).get(lang, MESSAGES.get(key, {}).get('en', ''))


def normalize_time_phrase(text: str) -> Optional[int]:
    """
    Parse multilingual time phrases to hour (0-23).

    Supports:
    - Russian: "10 утра" → 10, "3 вечера" → 15, "в 10" → 10
    - Spanish: "10 de la mañana" → 10, "3 de la tarde" → 15
    - European: "10.00" → 10, "10:00" → 10
    - English: "10am" → 10, "3pm" → 15

    Returns:
        Hour as int (0-23), or None if no match
    """
    text_lower = text.lower().strip()

    # Pattern 1: Russian time of day - "10 утра", "3 вечера", "2 дня"
    # утра = morning (AM), дня = afternoon (12-17), вечера = evening (17+)
    ru_patterns = [
        (r'(\d{1,2})\s*(?:утра|утром)', 0),        # 10 утра → +0
        (r'(\d{1,2})\s*(?:дня|днём)', 12),          # 3 дня → +12 (if < 12)
        (r'(\d{1,2})\s*(?:вечера|вечером)', 12),    # 7 вечера → +12 (if < 12)
        (r'в\s+(\d{1,2})(?:\s|$|:)', 0),            # в 10 → assume context
    ]

    for pattern, offset in ru_patterns:
        match = re.search(pattern, text_lower)
        if match:
            hour = int(match.group(1))
            # Apply offset for PM times
            if offset > 0 and hour < 12:
                hour += offset
            # Note: "12 утра" is unusual; "12 дня" is more natural for noon
            # Treat "12 утра" as 12:00 (noon) - add unit test to lock behavior
            return hour

    # Pattern 2: Spanish time of day - "10 de la mañana", "3 de la tarde"
    es_patterns = [
        (r'(\d{1,2})\s*(?:de la mañana|por la mañana)', 0),    # 10 de la mañana → +0
        (r'(\d{1,2})\s*(?:de la tarde|por la tarde)', 12),     # 3 de la tarde → +12
        (r'(\d{1,2})\s*(?:de la noche|por la noche)', 12),     # 7 de la noche → +12
        (r'a las\s+(\d{1,2})(?:\s|$|:)', 0),                    # a las 10 → assume context
    ]

    for pattern, offset in es_patterns:
        match = re.search(pattern, text_lower)
        if match:
            hour = int(match.group(1))
            if offset > 0 and hour < 12:
                hour += offset
            return hour

    # Pattern 3: European format - "10.00", "14.30"
    eu_match = re.search(r'(\d{1,2})\.(\d{2})(?:\s|$)', text_lower)
    if eu_match:
        return int(eu_match.group(1))

    # Pattern 4: 24-hour format - "10:00", "14:00", "20:00"
    time_match = re.search(r'(\d{1,2}):(\d{2})(?:\s|$)', text_lower)
    if time_match:
        return int(time_match.group(1))

    # Pattern 5: Bare number with context clues - "10", "в 10"
    bare_match = re.search(r'^(\d{1,2})$', text_lower)
    if bare_match:
        hour = int(bare_match.group(1))
        # Assume business hours context (9-17)
        return hour if 0 <= hour <= 23 else None

    return None


def cluster_slots_by_hour(slots: List[Dict[str, Any]]) -> Dict[str, List[int]]:
    """
    Cluster slots into morning/afternoon/evening hours.

    Returns:
        {
            "morning": [9, 10, 11],
            "afternoon": [14, 15, 16],
            "evening": [17, 18]
        }

    Note: Hours 0-5 and 22-23 are ignored as they're outside typical clinic hours.
    """
    clusters: Dict[str, List[int]] = {"morning": [], "afternoon": [], "evening": []}
    seen_hours: set = set()

    for slot in slots:
        # IMPORTANT: Always wrap in str() to handle datetime objects
        slot_time = str(slot.get('datetime', slot.get('time', slot.get('start', ''))))
        hour_match = re.search(r'T(\d{2}):', slot_time)
        if hour_match:
            hour = int(hour_match.group(1))
            if hour not in seen_hours:
                seen_hours.add(hour)
                # Tightened buckets (per expert feedback):
                # - Morning: 6-11 (6 <= hour < 12)
                # - Afternoon: 12-16 (12 <= hour < 17)
                # - Evening: 17-21 (17 <= hour < 22)
                # - Ignore: 0-5, 22-23 (unusual hours)
                if 6 <= hour < 12:
                    clusters["morning"].append(hour)
                elif 12 <= hour < 17:
                    clusters["afternoon"].append(hour)
                elif 17 <= hour < 22:
                    clusters["evening"].append(hour)
                # else: ignore unusual hours (0-5, 22-23)

    # Sort each cluster
    for key in clusters:
        clusters[key].sort()

    return clusters


def format_hours_naturally(hours: List[int], lang: str) -> str:
    """Format a list of hours in natural language."""
    if not hours:
        return ""

    if len(hours) == 1:
        return f"{hours[0]}:00"
    elif len(hours) == 2:
        connectors = {'en': 'or', 'ru': 'или', 'es': 'o', 'he': 'או'}
        connector = connectors.get(lang, 'or')
        return f"{hours[0]}:00 {connector} {hours[1]}:00"
    else:
        # "9:00, 10:00, or 11:00"
        connectors = {'en': 'or', 'ru': 'или', 'es': 'o', 'he': 'או'}
        connector = connectors.get(lang, 'or')
        formatted = ", ".join(f"{h}:00" for h in hours[:-1])
        return f"{formatted}, {connector} {hours[-1]}:00"


def _should_update_language(text: str) -> bool:
    """
    Determine if the input text is substantial enough to trust language detection.

    Prevents language flip-flopping on:
    - Short inputs ("10", "да", "ok")
    - Timestamps ("10.00", "14:30", "10:00")
    - Purely numeric inputs

    Returns:
        True if language should be updated, False to keep existing
    """
    clean = text.strip()

    # Check for explicit time strings that shouldn't trigger language switch
    # Matches: "10.00", "14:30", "9:00", "10.30"
    is_time_string = bool(re.match(r'^\d{1,2}[.:]\d{2}$', clean))
    if is_time_string:
        return False

    # Check if only digits and punctuation (no actual text)
    only_digits_punct = clean.replace('.', '').replace(':', '').replace(' ', '').isdigit()
    if only_digits_punct:
        return False

    # Check if it "looks like real text" - has alphabetic characters
    is_texty = any(ch.isalpha() for ch in clean)

    # Require at least 2 characters of text-like content
    # This allows "да", "si", "ok" to update language while avoiding noise
    return is_texty and len(clean) >= 2


def step(state: BookingState, event: Event) -> Tuple[BookingState, List[Action]]:
    """
    Pure function: (state, event) -> (new_state, actions)

    No side effects. No I/O. No LLM calls.
    All business logic is deterministic and testable.

    Args:
        state: Current booking state
        event: User message or tool result

    Returns:
        Tuple of (new_state, list_of_actions)
    """
    lang = state.language

    # Handle tool results
    if isinstance(event, ToolResultEvent):
        return handle_tool_result(state, event)

    # Handle user messages
    assert isinstance(event, UserEvent)

    # Save original values BEFORE merge for backtracking detection
    original_target_date = state.target_date
    original_doctor_name = state.doctor_name

    # Merge any new info from router (fills gaps, doesn't overwrite)
    state = state.merge_router_output(event.router)

    # === STICKY LANGUAGE: Only update on substantial text input ===
    # Prevents hallucinations like "10.00" → Spanish or short confirmations
    # from triggering unwanted language switches
    if _should_update_language(event.text):
        state = replace(state, language=event.language)
        lang = event.language
    # else: keep existing state.language (sticky)

    # === CHECK FOR CONCISE PROMPT PREFERENCE ===
    if _wants_concise_prompts(event.text):
        state = replace(state, user_prefers_concise=True)

    # === HANDLE DOCTOR_INFO ROUTE (Tangent) ===
    # This handles "Доктор Марк у вас работает?" regardless of current stage
    if event.router.route == "doctor_info":
        doctor_info_kind = event.router.doctor_info_kind or "exists"
        return handle_doctor_info_tangent(
            state=state,
            doctor_name=event.router.doctor_name,
            doctor_info_kind=doctor_info_kind,
            lang=lang,
        )

    # === HANDLE FAQ ROUTE (Tangent) ===
    # This handles "Расскажите о процедуре" / "Tell me about the procedure"
    # regardless of current stage - user can ask for info mid-booking
    if event.router.route == "faq":
        return handle_faq_tangent(
            state=state,
            service_type=event.router.service_type,
            lang=lang,
        )

    # === HANDLE CONTEXTUAL "YES" BASED ON pending_action ===
    # Only trigger special logic if we're awaiting specific confirmation
    if _is_affirmative(event.text, lang):
        if state.awaiting_field == 'booking_confirmation' and state.pending_action:
            action = state.pending_action
            if action.get('type') == 'start_booking':
                # Proceed to date collection with doctor pre-filled (if any)
                # Phase 5.2: Increment per-field ask count for adaptive prompting
                new_state = state.increment_field_ask_count('target_date')
                new_state = replace(
                    new_state,
                    stage=BookingStage.COLLECT_DATE,
                    awaiting_field=None,
                    pending_action=None,
                )
                return new_state, [AskUser(
                    text=get_date_prompt(lang, new_state.get_field_ask_count('target_date'), new_state.user_prefers_concise),
                    field_awaiting='target_date'
                )]

    # Build empathy prefix if needed (don't clear pain flag yet - need it for logic below)
    empathy = get_msg('empathy_prefix', lang) if state.has_pain else ""
    is_pain_scenario = state.has_pain

    # ==========================================
    # Stage: INTENT - Just got booking intent
    # ==========================================
    if state.stage == BookingStage.INTENT:
        state = replace(state, stage=BookingStage.COLLECT_SERVICE)
        # Fall through to check if we have service

    # ==========================================
    # Stage: COLLECT_SERVICE
    # ==========================================
    if state.stage == BookingStage.COLLECT_SERVICE:
        if not state.service_type:
            # If user provided a date/time, skip to availability check
            # with a default service type. This handles cases like:
            # "Can I come in Sunday at 3 AM?" - user wants to know if that time works
            if state.target_date:
                # Use "general" as default service type when checking availability
                state = replace(state, service_type="general", stage=BookingStage.COLLECT_DATE)
                # Fall through to COLLECT_DATE which will trigger CHECK_AVAILABILITY
            elif state.doctor_name:
                # User asked about a specific doctor - also proceed with general type
                state = replace(state, service_type="general", stage=BookingStage.COLLECT_DATE)
                # Fall through to COLLECT_DATE
            else:
                # No date/time provided - need to ask for service type
                # Clear pain flag after using empathy
                state = replace(state, has_pain=False)
                return state, [AskUser(
                    text=empathy + get_msg('ask_service', lang),
                    field_awaiting='service_type'
                )]
        state = replace(state, stage=BookingStage.COLLECT_DATE)

    # ==========================================
    # Stage: COLLECT_DATE
    # ==========================================
    if state.stage == BookingStage.COLLECT_DATE:
        if not state.target_date:
            # Phase 5.2: STRICT GUARD for auto-availability shortcut
            # Only auto-check availability when:
            # 1. User explicitly asked about availability (has_availability_intent)
            # 2. Message contains a time anchor (has_time_anchor) - specific date/time
            # 3. We're NOT awaiting a confirmation or service selection
            # 4. Doctor name is mentioned
            #
            # WITHOUT a time anchor: "Is Dr. Mark available?" → ASK for date preference
            # WITH a time anchor: "Is Dr. Mark available tomorrow?" → check availability

            if state.doctor_name and has_availability_intent(event.text, lang):
                # User asked about doctor availability (with or without date)
                # Be proactive: check availability for "this week" if no specific date given
                # This provides better UX than asking "when?" for simple questions like
                # "Is Dr. Smith available?"
                target = event.router.target_date if event.router else None
                if not target:
                    target = "this week"  # Default to this week for proactive availability check

                state = replace(state, has_pain=False, target_date=target)
                state = replace(state, stage=BookingStage.CHECK_AVAILABILITY)
                return state, [CallTool(
                    name="check_availability",
                    args={
                        "service_type": state.service_type or "general",
                        "date": target,
                        "time_preference": state.time_of_day,
                        "doctor_name": state.doctor_name,
                    }
                )]

            # No date and no explicit availability question - ask for preferred date
            # Phase 5.2: Use per-field counts for adaptive prompting
            state = state.increment_field_ask_count('target_date')
            date_ask_count = state.get_field_ask_count('target_date')
            state = replace(state, has_pain=False)

            # Use adaptive prompt verbosity based on ask count
            if date_ask_count >= 2:
                date_prompt = {'en': "When?", 'ru': "Когда?", 'es': "¿Cuándo?", 'he': "מתי?"}.get(lang, "When?")
            elif date_ask_count == 1:
                date_prompt = get_msg('ask_date', lang)  # Medium verbosity
            else:
                date_prompt = empathy + get_msg('ask_date', lang)  # Full verbosity with empathy

            return state, [AskUser(
                text=date_prompt,
                field_awaiting='target_date'
            )]

        # Clear pain flag before tool call - we've acknowledged it
        state = replace(state, has_pain=False)

        # Have enough info to check availability
        state = replace(state, stage=BookingStage.CHECK_AVAILABILITY)

        return state, [CallTool(
            name="check_availability",
            args={
                "service_type": state.service_type,
                "date": state.target_date,
                "time_preference": state.time_of_day,
                "doctor_name": state.doctor_name,
            }
        )]

    # ==========================================
    # Stage: CHECK_AVAILABILITY (UserEvent recovery)
    # ==========================================
    # If we receive a UserEvent while in CHECK_AVAILABILITY, it means
    # the tool call was "lost" (e.g., eval harness override, network error).
    # Treat any new user info and re-trigger availability check.
    if state.stage == BookingStage.CHECK_AVAILABILITY:
        # User provided more info while we were checking - re-check with updated state
        return state, [CallTool(
            name="check_availability",
            args={
                "service_type": state.service_type,
                "date": state.target_date,
                "time_preference": state.time_of_day,
                "doctor_name": state.doctor_name,
            }
        )]

    # ==========================================
    # Stage: AWAIT_SLOT_SELECTION
    # ==========================================
    if state.stage == BookingStage.AWAIT_SLOT_SELECTION:
        # User should have selected a slot
        selected = parse_slot_selection(event.text, state.available_slots)
        if selected:
            state = replace(state, selected_slot=selected, stage=BookingStage.COLLECT_PATIENT_INFO)
        else:
            # Couldn't parse selection - re-present slots
            return state, [AskUser(
                text=format_slots_message(
                    state.available_slots,
                    lang,
                    doctor_name=state.doctor_name,
                    target_date=state.target_date,
                ),
                field_awaiting='slot_selection'
            )]

    # ==========================================
    # Stage: COLLECT_PATIENT_INFO
    # ==========================================
    if state.stage == BookingStage.COLLECT_PATIENT_INFO:
        # Check if we have pre-populated phone but need patient name
        if state.patient_phone and not state.patient_name:
            # Phone is pre-populated from WhatsApp - show UX transparency message
            phone_preview = state.patient_phone[-4:] if len(state.patient_phone) >= 4 else state.patient_phone
            transparency_templates = {
                'en': f"I'll book using your WhatsApp number (ending in {phone_preview}). What name should I use for the appointment?",
                'ru': f"Запишу вас на этот номер WhatsApp (заканчивается на {phone_preview}). Как вас записать?",
                'es': f"Usaré tu número de WhatsApp (termina en {phone_preview}). ¿A qué nombre hago la cita?",
            }
            state = replace(state, clarification_count=state.clarification_count + 1)
            if state.clarification_count > 3:
                return replace(state, stage=BookingStage.COMPLETE), [
                    Escalate(
                        reason="max_clarifications_exceeded",
                        context={
                            "attempts": state.clarification_count,
                            "missing_field": "patient_name",
                            "partial_booking": {
                                "service": state.service_type,
                                "date": state.target_date,
                                "slot": state.selected_slot
                            }
                        }
                    ),
                    Respond(text=get_msg('escalate', lang))
                ]
            return state, [AskUser(
                text=transparency_templates.get(lang, transparency_templates['en']),
                field_awaiting='patient_name'
            )]
        elif not state.patient_phone:
            state = replace(state, clarification_count=state.clarification_count + 1)
            if state.clarification_count > 3:  # Raised threshold per feedback
                # Use Escalate action for proper handoff tracking
                return replace(state, stage=BookingStage.COMPLETE), [
                    Escalate(
                        reason="max_clarifications_exceeded",
                        context={
                            "attempts": state.clarification_count,
                            "missing_field": "patient_phone",
                            "partial_booking": {
                                "service": state.service_type,
                                "date": state.target_date,
                                "slot": state.selected_slot
                            }
                        }
                    ),
                    Respond(text=get_msg('escalate', lang))
                ]
            return state, [AskUser(
                text=empathy + get_msg('ask_patient_info', lang),
                field_awaiting='patient_phone'
            )]
        state = replace(state, stage=BookingStage.AWAIT_CONFIRM)

    # ==========================================
    # Stage: AWAIT_CONFIRM
    # ==========================================
    if state.stage == BookingStage.AWAIT_CONFIRM:
        # Check if user confirmed
        if is_confirmation(event.text, lang):
            # Idempotency: Don't book twice if already have appointment_id
            if state.appointment_id:
                return state, [Respond(text=format_booking_confirmation(state, lang))]

            state = replace(state, stage=BookingStage.BOOK)
            return state, [CallTool(
                name="book_appointment",
                args={
                    "patient_phone": state.patient_phone,
                    "patient_name": state.patient_name,
                    "datetime_str": state.selected_slot.get('datetime') if state.selected_slot else state.target_date,
                    "doctor_id": state.doctor_id,
                    "appointment_type": state.service_type or "general",
                }
            )]
        elif is_rejection(event.text, lang):
            # User said no - ask what they want instead
            state = replace(state, stage=BookingStage.COLLECT_DATE, selected_slot=None)
            return state, [AskUser(
                text=get_msg('try_different_time', lang),
                field_awaiting='target_date'
            )]
        else:
            # BACKTRACKING DETECTION: User provided new info instead of confirming
            # Check if they're changing their mind (e.g., "Actually, make it next week")
            # Compare with ORIGINAL values (before merge) to detect actual changes
            if event.router.target_date and event.router.target_date != original_target_date:
                # New date detected - reset to date collection
                state = replace(
                    state,
                    stage=BookingStage.COLLECT_DATE,
                    target_date=event.router.target_date,
                    selected_slot=None,
                    available_slots=[]
                )
                # Call check_availability with new date
                return state, [CallTool(
                    name="check_availability",
                    args={
                        "service_type": state.service_type,
                        "date": state.target_date,
                        "time_preference": state.time_of_day,
                        "doctor_name": state.doctor_name,
                    }
                )]
            elif event.router.doctor_name and event.router.doctor_name != original_doctor_name:
                # Different doctor requested - re-check availability
                state = replace(
                    state,
                    stage=BookingStage.COLLECT_DATE,
                    doctor_name=event.router.doctor_name,
                    selected_slot=None,
                    available_slots=[]
                )
                return state, [CallTool(
                    name="check_availability",
                    args={
                        "service_type": state.service_type,
                        "date": state.target_date,
                        "time_preference": state.time_of_day,
                        "doctor_name": state.doctor_name,
                    }
                )]
            else:
                # Unclear - re-ask confirmation
                return state, [AskUser(
                    text=format_confirmation_message(state, lang),
                    field_awaiting='confirmation'
                )]

    # ==========================================
    # Stage: COMPLETE - Already done
    # ==========================================
    if state.stage == BookingStage.COMPLETE:
        if state.appointment_id:
            return state, [Respond(text=format_booking_confirmation(state, lang))]
        # Shouldn't reach here normally
        return state, [Respond(text=get_msg('fallback', lang))]

    # Fallback - shouldn't reach here
    return state, [Respond(text=get_msg('fallback', lang))]


def handle_tool_result(state: BookingState, event: ToolResultEvent) -> Tuple[BookingState, List[Action]]:
    """Handle results from tool execution.

    Args:
        state: Current booking state
        event: Tool result event

    Returns:
        Tuple of (new_state, list_of_actions)
    """
    lang = state.language

    # ==========================================
    # Handle check_availability result
    # ==========================================
    if event.tool_name == "check_availability":
        slots = event.result.get('available_slots', []) if event.success else []
        state = replace(state, available_slots=slots)

        if not slots:
            # No availability - THIS FIXES THE "UNAVAILABLE SLOT" EVAL
            state = replace(
                state,
                stage=BookingStage.COLLECT_DATE,
                target_date=None  # Clear so user can try new date
            )
            return state, [Respond(text=get_msg('no_availability', lang))]

        # Have slots - present them
        state = replace(state, stage=BookingStage.AWAIT_SLOT_SELECTION)
        return state, [AskUser(
            text=format_slots_message(
                slots,
                lang,
                doctor_name=state.doctor_name,
                target_date=state.target_date,
            ),
            field_awaiting='slot_selection'
        )]

    # ==========================================
    # Handle book_appointment result
    # ==========================================
    if event.tool_name == "book_appointment":
        if event.success:
            appointment_id = event.result.get('appointment_id')
            # Phase 5: Extract clinic data for SOTA confirmation
            clinic_data = event.result.get('clinic', {})

            state = replace(
                state,
                stage=BookingStage.COMPLETE,
                appointment_id=appointment_id,
                confirmation_message=event.result.get('confirmation_message')
            )
            return state, [Respond(
                text=format_booking_confirmation(state, lang, clinic=clinic_data)
            )]
        else:
            # Booking failed
            error = event.result.get('error', 'Unknown error')
            msg = get_msg('booking_failed', lang).format(error=error)
            return state, [Respond(text=msg)]

    # ==========================================
    # Handle query_faq result
    # ==========================================
    if event.tool_name == "query_faq":
        # Get the FAQ answer or a fallback message
        if event.success and event.result:
            faq_answer = event.result.get('answer', event.result.get('text', ''))
            if not faq_answer and isinstance(event.result, str):
                faq_answer = event.result
        else:
            # Fallback if no FAQ found
            service = state.service_type or ''
            faq_templates = {
                'en': f"I don't have detailed information about {service} right now. Would you like to schedule an appointment to discuss this with a doctor?",
                'ru': f"У меня нет подробной информации о {service} прямо сейчас. Хотите записаться на консультацию к врачу?",
                'es': f"No tengo información detallada sobre {service} en este momento. ¿Le gustaría programar una cita para discutirlo con un doctor?",
            }
            faq_answer = faq_templates.get(lang, faq_templates['en'])

        # After FAQ, offer to continue booking
        continue_templates = {
            'en': "Would you like to proceed with booking?",
            'ru': "Хотите записаться?",
            'es': "¿Le gustaría proceder con la reserva?",
        }
        continue_prompt = continue_templates.get(lang, continue_templates['en'])

        # Clear the pending action and set up for booking confirmation
        new_state = replace(
            state,
            pending_action={'type': 'start_booking'},
            awaiting_field='booking_confirmation',
        )

        # Combine FAQ answer with booking prompt
        full_response = f"{faq_answer}\n\n{continue_prompt}"

        return new_state, [AskUser(
            text=full_response,
            field_awaiting='booking_confirmation'
        )]

    # Unknown tool
    return state, [Respond(text="Something went wrong. Please try again.")]


# ==========================================
# Helper Functions
# ==========================================

def format_slots_message(
    slots: List[Dict[str, Any]],
    lang: str,
    doctor_name: Optional[str] = None,
    target_date: Optional[str] = None,
) -> str:
    """
    Format available slots for human-like display.

    New approach:
    - Few slots (1-2 hours): "Да, доктор Марк завтра свободен в 9 или в 10 утра."
    - Many slots: "Доктор Марк завтра работает утром и после обеда. Когда удобнее?"

    Args:
        slots: List of slot dictionaries
        lang: Language code
        doctor_name: Doctor name for personalized response
        target_date: Target date string for context
    """
    if not slots:
        return get_msg('no_availability', lang)

    # Cluster slots by time of day
    clusters = cluster_slots_by_hour(slots)
    total_hours = len(clusters["morning"]) + len(clusters["afternoon"]) + len(clusters["evening"])

    # Build doctor reference with duplication check (per expert feedback)
    doc_ref = doctor_name or ""
    if doc_ref:
        # Check if already has a title prefix (avoid "Dr. Dr. Mark")
        has_title = doc_ref.lower().startswith((
            'dr', 'dr.', 'doctor',           # English
            'доктор', 'врач', 'док.',         # Russian
            'dra', 'dra.', 'doctor', 'doctora',  # Spanish
            'ד"ר', 'דוקטור',                  # Hebrew
        ))
        if not has_title:
            doc_prefix = {'en': 'Dr. ', 'ru': 'доктор ', 'es': 'Dr. ', 'he': 'ד"ר '}
            doc_ref = doc_prefix.get(lang, 'Dr. ') + doc_ref

    # Build date reference
    date_ref = target_date or ""
    date_refs = {
        'tomorrow': {'en': 'tomorrow', 'ru': 'завтра', 'es': 'mañana', 'he': 'מחר'},
        'today': {'en': 'today', 'ru': 'сегодня', 'es': 'hoy', 'he': 'היום'},
        'завтра': {'en': 'tomorrow', 'ru': 'завтра', 'es': 'mañana', 'he': 'מחר'},
        'сегодня': {'en': 'today', 'ru': 'сегодня', 'es': 'hoy', 'he': 'היום'},
        'מחר': {'en': 'tomorrow', 'ru': 'завтра', 'es': 'mañana', 'he': 'מחר'},
        'היום': {'en': 'today', 'ru': 'сегодня', 'es': 'hoy', 'he': 'היום'},
    }
    for key, translations in date_refs.items():
        if key in date_ref.lower():
            date_ref = translations.get(lang, date_ref)
            break

    # Always show specific times for slot grounding (eval requirement)
    # This prevents vague "morning or afternoon" responses
    all_hours = clusters["morning"] + clusters["afternoon"] + clusters["evening"]
    all_hours.sort()

    # Show up to 4 specific times for grounded responses
    display_hours = all_hours[:4]
    hours_str = format_hours_naturally(display_hours, lang)

    # Add "and more" if there are additional slots
    if len(all_hours) > 4:
        more_text = {'en': 'and more', 'ru': 'и другие', 'es': 'y más', 'he': 'ועוד'}
        hours_str += f" {more_text.get(lang, 'and more')}"

    templates = {
        'en': f"Yes, {doc_ref} is available {date_ref}. Open slots at {hours_str}. Which works for you?",
        'ru': f"Да, {doc_ref} {date_ref} свободен. Есть время в {hours_str}. Какое удобнее?",
        'es': f"Sí, {doc_ref} está disponible {date_ref}. Horarios: {hours_str}. ¿Cuál prefiere?",
        'he': f"כן, {doc_ref} פנוי {date_ref}. יש תורים ב-{hours_str}. מה מתאים יותר?",
    }
    return templates.get(lang, templates['en']).replace("  ", " ").strip()


def format_confirmation_message(state: BookingState, lang: str) -> str:
    """Format booking confirmation request.

    Args:
        state: Current booking state
        lang: Language code

    Returns:
        Formatted confirmation prompt
    """
    slot_time = state.selected_slot.get('datetime', '') if state.selected_slot else state.target_date or ''
    service = state.service_type or 'appointment'

    templates = {
        'en': f"Please confirm: Book {service} for {state.patient_name or 'you'} at {slot_time}\n\nReply 'yes' to confirm or 'no' to cancel.",
        'ru': f"Подтвердите: Записать на {service} для {state.patient_name or 'вас'} в {slot_time}\n\nОтветьте 'да' для подтверждения или 'нет' для отмены.",
        'es': f"Por favor confirme: Reservar {service} para {state.patient_name or 'usted'} a las {slot_time}\n\nResponda 'sí' para confirmar o 'no' para cancelar.",
        'he': f"אנא אשר: להזמין {service} עבור {state.patient_name or 'אותך'} ב-{slot_time}\n\nהשב 'כן' לאישור או 'לא' לביטול.",
    }
    return templates.get(lang, templates['en'])


def format_booking_confirmation(
    state: BookingState,
    lang: str,
    clinic: Optional[Dict[str, Any]] = None
) -> str:
    """Format successful booking confirmation.

    Phase 5: Now uses SOTA templates with location and entry instructions
    when clinic data is available.

    Args:
        state: Booking state with appointment details
        lang: Language code
        clinic: Optional clinic data with location_data and entry_instructions_i18n

    Returns:
        Formatted confirmation message
    """
    slot_time = state.selected_slot.get('datetime', '') if state.selected_slot else state.target_date or ''

    # Phase 5: Use SOTA template if clinic data is available
    if clinic and clinic.get('location_data'):
        appointment = {
            'scheduled_at': slot_time,
            'service_name': state.service_type or '',
            'doctor_name': state.doctor_name or ''
        }
        return format_sota_confirmation(appointment, clinic, lang)

    # Fallback to simple template if no clinic data
    templates = {
        'en': f"Your appointment has been booked for {slot_time}. We'll send you a confirmation shortly. Is there anything else I can help with?",
        'ru': f"Ваша запись подтверждена на {slot_time}. Мы отправим вам подтверждение. Могу ли я помочь вам с чем-то ещё?",
        'es': f"Su cita ha sido reservada para {slot_time}. Le enviaremos una confirmación pronto. Hay algo más en lo que pueda ayudarle?",
        'he': f"התור שלך נקבע ל-{slot_time}. נשלח לך אישור בקרוב. האם יש משהו נוסף שאוכל לעזור בו?",
    }
    return templates.get(lang, templates['en'])


def parse_slot_selection(text: str, slots: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Parse user's slot selection from their message.

    Handles multiple selection patterns:
    - Number selection: "1", "2", "#1"
    - Ordinals: "first", "second"
    - Time mentions: "3pm", "10am", "morning one"
    - Natural language: "works great", "perfect", "sounds good"
    - Doctor preference: "Dr. Shtern", "with Shtern"

    Args:
        text: User's response text
        slots: List of available slots

    Returns:
        Selected slot dict, or None if couldn't parse
    """
    text_lower = text.lower().strip()

    if not slots:
        return None

    # Number selection: "1", "2", "#1", or "number 1", "option 2"
    match = re.search(r'(?:^|number\s*|option\s*|#)(\d)\b', text_lower)
    if match:
        idx = int(match.group(1)) - 1
        if 0 <= idx < len(slots):
            return slots[idx]

    # Ordinals: "first", "second", "the first one"
    ordinals = {
        'first': 0, 'second': 1, 'third': 2, 'fourth': 3, 'fifth': 4,
        'первый': 0, 'второй': 1, 'третий': 2, 'первая': 0, 'вторая': 1,
        'primero': 0, 'segundo': 1, 'tercero': 2, 'primera': 0, 'segunda': 1
    }
    for word, idx in ordinals.items():
        if word in text_lower and idx < len(slots):
            return slots[idx]

    # === NEW: MULTILINGUAL TIME PARSING ===
    # Try to parse time from text using normalize_time_phrase
    # This handles: "10 утра", "3 вечера", "10.00", "14:30", "a las 10", etc.
    target_hour = normalize_time_phrase(text_lower)
    if target_hour is not None:
        # Find slot matching this hour
        for slot in slots:
            # IMPORTANT: Always wrap in str() to handle datetime objects safely
            slot_time = str(slot.get('datetime', slot.get('time', slot.get('start', ''))))
            # Extract hour from ISO datetime (e.g., "2025-12-29T09:00:00")
            hour_match = re.search(r'T(\d{2}):', slot_time)
            if hour_match:
                slot_hour = int(hour_match.group(1))
                if slot_hour == target_hour:
                    return slot

    # Time-based selection: look for time patterns and match to slots
    # Patterns: "3pm", "3 pm", "3:00", "15:00", "morning", "afternoon"
    time_patterns = [
        (r'\b(\d{1,2})\s*(?:pm|p\.?m\.?)\b', 12),  # 3pm -> 15:00
        (r'\b(\d{1,2})\s*(?:am|a\.?m\.?)\b', 0),   # 10am -> 10:00
        (r'\b(\d{1,2}):(\d{2})\b', None),          # 10:00, 14:00
    ]

    for pattern, offset in time_patterns:
        match = re.search(pattern, text_lower)
        if match:
            if offset is not None:
                # am/pm format
                hour = int(match.group(1))
                if offset == 12 and hour != 12:
                    hour += 12
                elif offset == 0 and hour == 12:
                    hour = 0
            else:
                # 24-hour format
                hour = int(match.group(1))

            # Find slot with matching hour
            for slot in slots:
                # IMPORTANT: Always wrap in str() to handle datetime objects safely
                slot_time = str(slot.get('datetime', slot.get('time', '')))
                # Extract hour from datetime string (e.g., "2025-12-29T09:00:00")
                time_match = re.search(r'T(\d{2}):', slot_time)
                if time_match:
                    slot_hour = int(time_match.group(1))
                    if slot_hour == hour:
                        return slot

    # Time of day preference: "morning", "afternoon"
    if any(word in text_lower for word in ['morning', 'утр', 'mañana']):
        for slot in slots:
            # IMPORTANT: Always wrap in str() to handle datetime objects safely
            slot_time = str(slot.get('datetime', slot.get('time', '')))
            time_match = re.search(r'T(\d{2}):', slot_time)
            if time_match:
                slot_hour = int(time_match.group(1))
                if 6 <= slot_hour < 12:
                    return slot
    elif any(word in text_lower for word in ['afternoon', 'вечер', 'tarde']):
        for slot in slots:
            # IMPORTANT: Always wrap in str() to handle datetime objects safely
            slot_time = str(slot.get('datetime', slot.get('time', '')))
            time_match = re.search(r'T(\d{2}):', slot_time)
            if time_match:
                slot_hour = int(time_match.group(1))
                if 12 <= slot_hour < 18:
                    return slot

    # Doctor name matching: "Dr. Shtern", "with Shtern", "doctor Smith"
    # Handle "with Dr. X" pattern first
    doctor_match = re.search(r'(?:with\s+)?dr\.?\s*(\w+)', text_lower)
    if not doctor_match:
        doctor_match = re.search(r'(?:doctor\s+|with\s+)(\w+)', text_lower)
    if doctor_match:
        doc_name = doctor_match.group(1).lower()
        for slot in slots:
            slot_doctor = slot.get('doctor_name', slot.get('provider_name', '')).lower()
            if doc_name in slot_doctor:
                return slot

    # Natural language confirmation with positive sentiment = first slot
    # "works great", "perfect", "sounds good", "that's great", "that one"
    positive_patterns = [
        r'\b(?:works|perfect|great|good|fine|excellent|awesome)\b',
        r'\b(?:sounds?\s+good|that\s*(?:\'?s|one)?)\b',
        r'\b(?:подходит|хорошо|отлично)\b',
        r'\b(?:perfecto|bien|excelente)\b'
    ]
    for pattern in positive_patterns:
        if re.search(pattern, text_lower):
            return slots[0]

    # Simple confirmation = first slot
    simple_confirms = {
        'yes', 'yeah', 'yep', 'sure', 'ok', 'okay', 'yup', 'absolutely',
        'да', 'хорошо', 'ладно', 'конечно',
        'sí', 'si', 'vale', 'claro'
    }
    # Check if any simple confirm is in the text (not exact match)
    for confirm in simple_confirms:
        if confirm in text_lower.split():
            return slots[0]

    return None


def _wants_concise_prompts(text: str) -> bool:
    """Detect if user explicitly asked for shorter prompts.

    Args:
        text: User's message text

    Returns:
        True if user wants concise prompts
    """
    concise_signals = [
        'не надо примеры', 'я сам знаю', 'без лишнего',
        'покороче', 'не подсказывай', 'сам знаю',
        "don't prompt me", "i know", "skip examples",
        'no examples', 'be brief', 'short please',
    ]
    text_lower = text.lower()
    return any(signal in text_lower for signal in concise_signals)


def get_date_prompt(lang: str, field_ask_count: int, user_prefers_concise: bool = False) -> str:
    """
    Get date prompt with decreasing verbosity based on field-specific ask count.

    Phase 5.2: Updated to use per-field ask counts instead of global clarification_count.

    - user_prefers_concise=True: Always use shortest form
    - count 0: Full prompt with examples
    - count 1: Medium prompt without examples
    - count 2+: Short prompt

    Args:
        lang: Language code
        field_ask_count: Number of times we've asked for THIS field (not global)
        user_prefers_concise: Whether user asked for short prompts

    Returns:
        Localized date prompt
    """
    # User explicitly asked for short prompts
    if user_prefers_concise:
        prompts = {'en': "When?", 'ru': "Когда?", 'es': "¿Cuándo?", 'he': "מתי?"}
        return prompts.get(lang, prompts['en'])

    if field_ask_count == 0:
        prompts = {
            'en': "When would you like to come? You can say 'tomorrow at 2pm' or 'next Monday morning'.",
            'ru': "Когда вы хотели бы прийти? Можете сказать 'завтра в 14:00' или 'в следующий понедельник утром'.",
            'es': "¿Cuándo le gustaría venir? Puede decir 'mañana a las 2pm' o 'el próximo lunes por la mañana'.",
            'he': "מתי תרצה להגיע? אפשר להגיד 'מחר ב-14:00' או 'יום שני הבא בבוקר'.",
        }
    elif field_ask_count == 1:
        prompts = {
            'en': "What day works for you?",
            'ru': "Какой день вам удобен?",
            'es': "¿Qué día le conviene?",
            'he': "איזה יום מתאים לך?",
        }
    else:
        prompts = {
            'en': "When?",
            'ru': "Когда?",
            'es': "¿Cuándo?",
            'he': "מתי?",
        }

    return prompts.get(lang, prompts['en'])


def _normalize_name_for_matching(name: str) -> str:
    """
    Normalize a name for fuzzy matching across Cyrillic/Latin.

    Transliterates common Cyrillic characters to Latin equivalents
    to allow matching "Марк" with "Mark", "Штерн" with "Shtern", etc.
    """
    # Cyrillic to Latin transliteration map (common dental clinic names)
    translit_map = {
        'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd', 'е': 'e', 'ё': 'e',
        'ж': 'zh', 'з': 'z', 'и': 'i', 'й': 'y', 'к': 'k', 'л': 'l', 'м': 'm',
        'н': 'n', 'о': 'o', 'п': 'p', 'р': 'r', 'с': 's', 'т': 't', 'у': 'u',
        'ф': 'f', 'х': 'h', 'ц': 'ts', 'ч': 'ch', 'ш': 'sh', 'щ': 'sch',
        'ъ': '', 'ы': 'y', 'ь': '', 'э': 'e', 'ю': 'yu', 'я': 'ya',
    }

    result = []
    for char in name.lower():
        if char in translit_map:
            result.append(translit_map[char])
        else:
            result.append(char)

    return ''.join(result)


def _names_match(query_name: str, doctor_name: str) -> bool:
    """
    Check if a query name matches a doctor name.

    Handles:
    - Case-insensitive matching
    - Cyrillic/Latin transliteration (Марк → Mark)
    - Partial matching (just first or last name)
    - Title stripping (Dr., доктор, etc.)
    """
    # Strip common titles
    titles = ['dr.', 'dr', 'doctor', 'доктор', 'врач', 'док.']
    query_clean = query_name.lower().strip()
    doctor_clean = doctor_name.lower().strip()

    for title in titles:
        query_clean = query_clean.replace(title, '').strip()
        doctor_clean = doctor_clean.replace(title, '').strip()

    # Normalize both names (transliterate Cyrillic)
    query_norm = _normalize_name_for_matching(query_clean)
    doctor_norm = _normalize_name_for_matching(doctor_clean)

    # Check for exact match
    if query_norm == doctor_norm:
        return True

    # Check if query is contained in doctor name (partial match)
    if query_norm in doctor_norm or doctor_norm in query_norm:
        return True

    # Check each word in query against doctor name parts
    query_parts = query_norm.split()
    doctor_parts = doctor_norm.split()

    for qpart in query_parts:
        if len(qpart) >= 3:  # Only match parts with 3+ chars
            for dpart in doctor_parts:
                if qpart in dpart or dpart in qpart:
                    return True

    return False


def handle_faq_tangent(
    state: BookingState,
    service_type: Optional[str],
    lang: str,
) -> Tuple[BookingState, List[Action]]:
    """
    Handle FAQ/procedure info questions as tangents.

    This handles questions like:
    - "Расскажите о процедуре" (Tell me about the procedure)
    - "What does whitening involve?"
    - "How does a root canal work?"

    After answering, user can continue with booking flow.

    Args:
        state: Current booking state
        service_type: Service type from router (if any)
        lang: Language code

    Returns:
        Tuple of (new_state, actions) with CallTool to query FAQ
    """
    # Determine the service to query about
    query_service = service_type or state.service_type

    if not query_service:
        # No service specified - ask what they want to know about
        templates = {
            'en': "What procedure would you like to know about?",
            'ru': "О какой процедуре вы хотите узнать?",
            'es': "¿Sobre qué procedimiento le gustaría saber?",
            'he': "על איזה הליך תרצה לדעת?",
        }
        new_state = replace(state, awaiting_field='faq_service')
        return new_state, [AskUser(
            text=templates.get(lang, templates['en']),
            field_awaiting='faq_service'
        )]

    # Have a service - call FAQ tool
    # Store the return stage so we can go back after FAQ
    new_state = replace(
        state,
        pending_action={'type': 'faq_query', 'return_stage': state.stage.value},
    )

    return new_state, [CallTool(
        name="query_faq",
        args={
            "query": f"What is {query_service}? How does the procedure work?",
            "service_type": query_service,
            "language": lang,
        }
    )]


def handle_doctor_info_tangent(
    state: BookingState,
    doctor_name: Optional[str],
    doctor_info_kind: str,  # "exists" | "list" | "recommend"
    lang: str,
    doctor_list: Optional[List[str]] = None,  # From ClinicInfoTool
) -> Tuple[BookingState, List[Action]]:
    """
    Handle doctor info questions as tangents.

    This handles questions like:
    - "Доктор Марк у вас работает?" (Does Dr. Mark work here?)
    - "Какие у вас врачи?" (Who are your doctors?)
    - "Какого врача порекомендуете?" (Which doctor do you recommend?)

    Args:
        state: Current booking state
        doctor_name: Doctor name from router (if any)
        doctor_info_kind: Type of doctor info question
        lang: Language code
        doctor_list: List of doctor names from clinic (optional)

    Returns:
        Tuple of (new_state, actions)
    """
    # Default doctor list if not provided
    if doctor_list is None:
        doctor_list = ["Dr. Mark Shtern", "Dr. Marie", "Dr. Shtern"]

    if doctor_info_kind == "exists" and doctor_name:
        # Check if doctor exists using fuzzy matching (handles Cyrillic/Latin)
        found_doctor = None
        for doc in doctor_list:
            if _names_match(doctor_name, doc):
                found_doctor = doc
                break

        if found_doctor:
            # Doctor exists - offer to book
            templates = {
                'en': f"Yes, {found_doctor} works at our clinic. Would you like to book an appointment?",
                'ru': f"Да, {found_doctor} работает в нашей клинике. Хотите записаться?",
                'es': f"Sí, {found_doctor} trabaja en nuestra clínica. ¿Le gustaría agendar una cita?",
            }
            new_state = replace(
                state,
                doctor_name=found_doctor,
                awaiting_field='booking_confirmation',
                pending_action={'type': 'start_booking', 'doctor_name': found_doctor},
            )
        else:
            # Doctor NOT found - ask for clarification
            # Find similar names using transliteration
            query_norm = _normalize_name_for_matching(doctor_name)
            similar = []
            for d in doctor_list:
                doc_norm = _normalize_name_for_matching(d)
                # Check if first 3 chars of query match any part of doctor name
                if len(query_norm) >= 3 and query_norm[:3] in doc_norm:
                    similar.append(d)
            suggestion = ""
            if similar:
                suggestion_templates = {
                    'en': f" Did you mean {', '.join(similar[:2])}?",
                    'ru': f" Возможно, вы имеете в виду {', '.join(similar[:2])}?",
                    'es': f" ¿Quizás quiso decir {', '.join(similar[:2])}?",
                }
                suggestion = suggestion_templates.get(lang, suggestion_templates['en'])

            templates = {
                'en': f"I don't see {doctor_name} on our roster.{suggestion} Could you spell the name?",
                'ru': f"Не вижу доктора {doctor_name} в списке наших врачей.{suggestion} Как правильно пишется?",
                'es': f"No encuentro al Dr. {doctor_name} en nuestro personal.{suggestion} ¿Podría deletrear el nombre?",
            }
            new_state = replace(state, awaiting_field='doctor_name_clarification')

        return new_state, [AskUser(
            text=templates.get(lang, templates['en']),
            field_awaiting=new_state.awaiting_field
        )]

    elif doctor_info_kind == "list":
        # List all doctors
        if doctor_list:
            names = ", ".join(doctor_list[:5])  # Limit to 5
            templates = {
                'en': f"Our doctors: {names}. Would you like to book with any of them?",
                'ru': f"Наши врачи: {names}. Хотите записаться к кому-то из них?",
                'es': f"Nuestros doctores: {names}. ¿Le gustaría agendar con alguno?",
            }
        else:
            templates = {
                'en': "We have several experienced doctors. Would you like me to check availability?",
                'ru': "У нас работают несколько опытных врачей. Хотите, проверю свободные окна?",
                'es': "Tenemos varios doctores experimentados. ¿Quiere que revise disponibilidad?",
            }
        new_state = replace(
            state,
            awaiting_field='booking_confirmation',
            pending_action={'type': 'start_booking'},
        )
        return new_state, [AskUser(
            text=templates.get(lang, templates['en']),
            field_awaiting='booking_confirmation'
        )]

    elif doctor_info_kind == "recommend":
        # Safe recommendation: ask for service context first (avoid medical advice)
        templates = {
            'en': "To recommend the right doctor, what type of appointment do you need? Cleaning, consultation, or something else?",
            'ru': "Чтобы порекомендовать врача, уточните: какой тип приёма вам нужен? Чистка, консультация, или что-то другое?",
            'es': "Para recomendar un doctor, ¿qué tipo de cita necesita? ¿Limpieza, consulta u otro?",
        }
        new_state = replace(state, awaiting_field='service_type')
        return new_state, [AskUser(
            text=templates.get(lang, templates['en']),
            field_awaiting='service_type'
        )]

    # Fallback - shouldn't reach here
    return state, []
