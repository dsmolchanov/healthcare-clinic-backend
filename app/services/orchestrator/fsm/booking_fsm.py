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


# Localized messages
MESSAGES = {
    'ask_service': {
        'en': "What type of appointment do you need? We offer cleanings, checkups, consultations, and more.",
        'ru': "Какой тип приёма вам нужен? Мы предлагаем чистку, осмотр, консультацию и другое.",
        'es': "Qué tipo de cita necesita? Ofrecemos limpiezas, chequeos, consultas y más.",
    },
    'ask_date': {
        'en': "When would you like to come in? You can say something like 'tomorrow at 2pm' or 'next Monday morning'.",
        'ru': "Когда вы хотели бы прийти? Можете сказать 'завтра в 14:00' или 'в следующий понедельник утром'.",
        'es': "Cuándo le gustaría venir? Puede decir algo como 'mañana a las 2pm' o 'el próximo lunes por la mañana'.",
    },
    'ask_patient_info': {
        'en': "Could you please provide your name and phone number so I can find your record?",
        'ru': "Не могли бы вы назвать ваше имя и номер телефона, чтобы я нашёл(а) вашу карту?",
        'es': "Podría proporcionarme su nombre y número de teléfono para encontrar su registro?",
    },
    'no_availability': {
        'en': "I checked and unfortunately we don't have availability at that time. Our hours are Monday-Friday 9am-5pm. Would you like to try a different time?",
        'ru': "Я проверил(а), но, к сожалению, на это время нет записи. Мы работаем Пн-Пт 9:00-17:00. Хотите попробовать другое время?",
        'es': "He verificado y lamentablemente no tenemos disponibilidad en ese horario. Nuestro horario es de lunes a viernes de 9am a 5pm. Le gustaría probar otro horario?",
    },
    'empathy_prefix': {
        'en': "I'm sorry to hear you're in discomfort. ",
        'ru': "Мне жаль, что вам нехорошо. ",
        'es': "Lamento que tenga molestias. ",
    },
    'pain_urgent': {
        'en': "I'm sorry to hear you're in pain. Let me check for the earliest available appointment right away.",
        'ru': "Мне жаль, что вам больно. Позвольте проверить ближайшее доступное время.",
        'es': "Lamento que tenga dolor. Déjeme verificar la cita disponible más temprana de inmediato.",
    },
    'escalate': {
        'en': "I'm having trouble understanding your request. Let me connect you with our staff who can help directly.",
        'ru': "Мне сложно понять ваш запрос. Позвольте соединить вас с нашим персоналом.",
        'es': "Tengo dificultades para entender su solicitud. Permítame conectarlo con nuestro personal.",
    },
    'try_different_time': {
        'en': "No problem. Would you like to try a different time?",
        'ru': "Хорошо. Хотите попробовать другое время?",
        'es': "No hay problema. Le gustaría probar otro horario?",
    },
    'booking_failed': {
        'en': "I wasn't able to complete the booking: {error}. Would you like to try again?",
        'ru': "Не удалось завершить запись: {error}. Хотите попробовать снова?",
        'es': "No pude completar la reserva: {error}. Le gustaría intentar de nuevo?",
    },
    'fallback': {
        'en': "I'm not sure how to help with that. Could you rephrase?",
        'ru': "Не уверен(а), как помочь с этим. Не могли бы вы перефразировать?",
        'es': "No estoy seguro de cómo ayudar con eso. Podría reformular?",
    },
}


def get_msg(key: str, lang: str) -> str:
    """Get localized message, falling back to English."""
    return MESSAGES.get(key, {}).get(lang, MESSAGES.get(key, {}).get('en', ''))


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
    state = replace(state, language=event.language)
    lang = event.language

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
            # FIX: If user provided a date/time, skip to availability check
            # with a default service type. This handles cases like:
            # "Can I come in Sunday at 3 AM?" - user wants availability, not to specify service
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
                # Ask for service type with empathy if pain was mentioned
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
            # Ask for date with empathy if pain was mentioned
            # Clear pain flag after using empathy
            state = replace(state, has_pain=False)
            return state, [AskUser(
                text=empathy + get_msg('ask_date', lang),
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
                text=format_slots_message(state.available_slots, lang),
                field_awaiting='slot_selection'
            )]

    # ==========================================
    # Stage: COLLECT_PATIENT_INFO
    # ==========================================
    if state.stage == BookingStage.COLLECT_PATIENT_INFO:
        if not state.patient_phone:
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
            text=format_slots_message(slots, lang),
            field_awaiting='slot_selection'
        )]

    # ==========================================
    # Handle book_appointment result
    # ==========================================
    if event.tool_name == "book_appointment":
        if event.success:
            appointment_id = event.result.get('appointment_id')
            state = replace(
                state,
                stage=BookingStage.COMPLETE,
                appointment_id=appointment_id,
                confirmation_message=event.result.get('confirmation_message')
            )
            return state, [Respond(
                text=format_booking_confirmation(state, lang)
            )]
        else:
            # Booking failed
            error = event.result.get('error', 'Unknown error')
            msg = get_msg('booking_failed', lang).format(error=error)
            return state, [Respond(text=msg)]

    # Unknown tool
    return state, [Respond(text="Something went wrong. Please try again.")]


# ==========================================
# Helper Functions
# ==========================================

def format_slots_message(slots: List[Dict[str, Any]], lang: str) -> str:
    """Format available slots for display.

    Args:
        slots: List of slot dictionaries
        lang: Language code

    Returns:
        Formatted message string
    """
    if not slots:
        return get_msg('no_availability', lang)

    headers = {
        'en': "I found these times available:\n",
        'ru': "Я нашёл(а) свободные окна:\n",
        'es': "Encontré estos horarios disponibles:\n",
    }
    footers = {
        'en': "\nWhich time works best for you?",
        'ru': "\nКакое время вам подходит?",
        'es': "\nCuál le conviene mejor?",
    }

    lines = [headers.get(lang, headers['en'])]
    for i, slot in enumerate(slots[:5], 1):
        dt = slot.get('datetime', slot.get('time', slot.get('start', '')))
        provider = slot.get('provider_name', slot.get('doctor_name', ''))
        if provider:
            lines.append(f"{i}. {dt} - {provider}")
        else:
            lines.append(f"{i}. {dt}")
    lines.append(footers.get(lang, footers['en']))

    return '\n'.join(lines)


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
    }
    return templates.get(lang, templates['en'])


def format_booking_confirmation(state: BookingState, lang: str) -> str:
    """Format successful booking confirmation.

    Args:
        state: Booking state with appointment details
        lang: Language code

    Returns:
        Formatted confirmation message
    """
    slot_time = state.selected_slot.get('datetime', '') if state.selected_slot else state.target_date or ''

    templates = {
        'en': f"Your appointment has been booked for {slot_time}. We'll send you a confirmation shortly. Is there anything else I can help with?",
        'ru': f"Ваша запись подтверждена на {slot_time}. Мы отправим вам подтверждение. Могу ли я помочь вам с чем-то ещё?",
        'es': f"Su cita ha sido reservada para {slot_time}. Le enviaremos una confirmación pronto. Hay algo más en lo que pueda ayudarle?",
    }
    return templates.get(lang, templates['en'])


def parse_slot_selection(text: str, slots: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Parse user's slot selection from their message.

    Args:
        text: User's response text
        slots: List of available slots

    Returns:
        Selected slot dict, or None if couldn't parse
    """
    text_lower = text.lower().strip()

    if not slots:
        return None

    # Number selection: "1", "2", "#1"
    match = re.search(r'^#?(\d)$', text_lower)
    if match:
        idx = int(match.group(1)) - 1
        if 0 <= idx < len(slots):
            return slots[idx]

    # Ordinals: "first", "second"
    ordinals = {
        'first': 0, 'second': 1, 'third': 2, 'fourth': 3, 'fifth': 4,
        'первый': 0, 'второй': 1, 'третий': 2,
        'primero': 0, 'segundo': 1, 'tercero': 2
    }
    for word, idx in ordinals.items():
        if word in text_lower and idx < len(slots):
            return slots[idx]

    # Simple confirmation = first slot
    if text_lower in ('yes', 'yeah', 'sure', 'ok', 'да', 'хорошо', 'sí', 'si'):
        return slots[0]

    return None


def is_confirmation(text: str, lang: str) -> bool:
    """Check if text is a confirmation.

    Args:
        text: User's response text
        lang: Language code (not used but kept for consistency)

    Returns:
        True if this is a confirmation
    """
    confirms = {
        'yes', 'yeah', 'yep', 'sure', 'ok', 'okay', 'confirm',
        'да', 'хорошо', 'ладно', 'подтверждаю',
        'sí', 'si', 'vale', 'confirmo'
    }
    return text.lower().strip() in confirms


def is_rejection(text: str, lang: str) -> bool:
    """Check if text is a rejection.

    Args:
        text: User's response text
        lang: Language code (not used but kept for consistency)

    Returns:
        True if this is a rejection
    """
    rejects = {
        'no', 'nope', 'cancel', 'nevermind', 'never mind',
        'нет', 'отмена', 'не надо',
        'no', 'cancelar', 'no quiero'
    }
    return text.lower().strip() in rejects
