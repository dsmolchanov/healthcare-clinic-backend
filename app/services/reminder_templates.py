"""
SOTA Reminder Message Templates

Multi-language templates for:
- Immediate confirmation
- T-24h reminder (uses WhatsApp Template if outside 24h window)
- T-2h wayfinding reminder (uses WhatsApp Template if outside 24h window)

IMPORTANT: strftime('%B') outputs English month names regardless of system locale.
We use explicit month name dictionaries for proper localization.
"""
from typing import Dict, Any, Optional, Tuple
from datetime import datetime
from app.utils.i18n_helpers import get_translation


# Explicit month names for proper localization (strftime doesn't work cross-platform)
MONTHS = {
    'ru': ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня',
           'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря'],
    'es': ['enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio',
           'julio', 'agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre'],
    'en': ['January', 'February', 'March', 'April', 'May', 'June',
           'July', 'August', 'September', 'October', 'November', 'December']
}


def format_date_localized(dt: datetime, lang: str) -> str:
    """
    Format date according to locale using explicit month names.

    NOTE: We don't use strftime('%B') because it outputs English
    month names regardless of system locale setting.
    """
    months = MONTHS.get(lang, MONTHS['en'])
    month_name = months[dt.month - 1]

    if lang == 'ru':
        # Russian: 25 декабря в 14:00
        return f"{dt.day} {month_name} в {dt.strftime('%H:%M')}"
    elif lang == 'es':
        # Spanish: 25 de enero a las 14:00
        return f"{dt.day} de {month_name} a las {dt.strftime('%H:%M')}"
    else:
        # English: January 25 at 2:00 PM
        return f"{month_name} {dt.day} at {dt.strftime('%I:%M %p')}"


def get_entry_instructions(clinic: Dict[str, Any], lang: str) -> str:
    """
    Get entry instructions for clinic in specified language.

    FIX: The column is 'entry_instructions_i18n' but get_translation()
    was looking for 'entry_instructions' key.
    """
    # Try the i18n column directly
    i18n_data = clinic.get('entry_instructions_i18n', {}) or {}
    if isinstance(i18n_data, dict) and lang in i18n_data:
        return i18n_data[lang]
    # Fallback to English, then any available
    if isinstance(i18n_data, dict):
        return i18n_data.get('en', '') or next(iter(i18n_data.values()), '')
    return ''


def format_confirmation_message(
    appointment: Dict[str, Any],
    clinic: Dict[str, Any],
    lang: str = 'ru'
) -> str:
    """Format immediate booking confirmation with location."""
    scheduled_at = appointment.get('scheduled_at', '')
    if isinstance(scheduled_at, str):
        dt = datetime.fromisoformat(scheduled_at.replace('Z', '+00:00'))
    else:
        dt = scheduled_at

    formatted_date = format_date_localized(dt, lang)

    service_name = get_translation(appointment, 'service_name', lang) or appointment.get('service_name', '')
    doctor_name = appointment.get('doctor_name', '')
    clinic_name = clinic.get('name', '')
    address = clinic.get('address', '')

    # FIX: Use correct function for entry instructions
    entry_instructions = get_entry_instructions(clinic, lang)

    # Get directions URL from location_data
    location_data = clinic.get('location_data', {}) or {}
    directions_url = location_data.get('directions_url') or location_data.get('google_maps_uri', '')

    # Build entry instructions section (avoid nested f-strings)
    entry_section = ""
    if entry_instructions:
        if lang == 'ru':
            entry_section = f"\n🚪 *Как войти:*\n{entry_instructions}"
        elif lang == 'es':
            entry_section = f"\n🚪 *Instrucciones de entrada:*\n{entry_instructions}"
        else:
            entry_section = f"\n🚪 *Entry Instructions:*\n{entry_instructions}"

    templates = {
        'ru': f"""✅ *Запись подтверждена!*

📋 *Услуга:* {service_name}
👨‍⚕️ *Врач:* {doctor_name}
📅 *Дата:* {formatted_date}

━━━━━━━━━━
📍 *Адрес:*
{address}

🗺️ *Как добраться:*
{directions_url}{entry_section}
━━━━━━━━━━

Пожалуйста, приходите за 10 минут до начала.
Если нужно перенести или отменить — просто напишите.""",

        'en': f"""✅ *Appointment Confirmed!*

📋 *Service:* {service_name}
👨‍⚕️ *Doctor:* {doctor_name}
📅 *Date:* {formatted_date}

━━━━━━━━━━
📍 *Address:*
{address}

🗺️ *Directions:*
{directions_url}{entry_section}
━━━━━━━━━━

Please arrive 10 minutes early for check-in.
To reschedule or cancel, just reply to this message.""",

        'es': f"""✅ *Cita Confirmada!*

📋 *Servicio:* {service_name}
👨‍⚕️ *Doctor:* {doctor_name}
📅 *Fecha:* {formatted_date}

━━━━━━━━━━
📍 *Dirección:*
{address}

🗺️ *Cómo llegar:*
{directions_url}{entry_section}
━━━━━━━━━━

Por favor llegue 10 minutos antes.
Para reprogramar o cancelar, simplemente responda a este mensaje."""
    }

    return templates.get(lang, templates['en'])


def format_reminder_24h(
    appointment: Dict[str, Any],
    clinic: Dict[str, Any],
    lang: str = 'ru'
) -> str:
    """Format T-24h reminder with confirm/reschedule prompt."""
    scheduled_at = appointment.get('scheduled_at', '')
    if isinstance(scheduled_at, str):
        dt = datetime.fromisoformat(scheduled_at.replace('Z', '+00:00'))
    else:
        dt = scheduled_at

    formatted_date = format_date_localized(dt, lang)

    service_name = get_translation(appointment, 'service_name', lang) or appointment.get('service_name', '')
    doctor_name = appointment.get('doctor_name', '')

    templates = {
        'ru': f"""⏰ *Напоминание о записи*

Завтра у вас запись:

📋 *Услуга:* {service_name}
👨‍⚕️ *Врач:* {doctor_name}
📅 *Дата:* {formatted_date}

Вы придёте? Ответьте "да" для подтверждения или "перенести" если нужно изменить время.""",

        'en': f"""⏰ *Appointment Reminder*

You have an appointment tomorrow:

📋 *Service:* {service_name}
👨‍⚕️ *Doctor:* {doctor_name}
📅 *Date:* {formatted_date}

Will you be there? Reply "yes" to confirm or "reschedule" if you need to change the time.""",

        'es': f"""⏰ *Recordatorio de Cita*

Tiene una cita mañana:

📋 *Servicio:* {service_name}
👨‍⚕️ *Doctor:* {doctor_name}
📅 *Fecha:* {formatted_date}

Asistirá? Responda "sí" para confirmar o "reprogramar" si necesita cambiar la hora."""
    }

    return templates.get(lang, templates['en'])


def format_wayfinding_2h(
    appointment: Dict[str, Any],
    clinic: Dict[str, Any],
    lang: str = 'ru'
) -> str:
    """Format T-2h wayfinding reminder (sent before location card)."""
    scheduled_at = appointment.get('scheduled_at', '')
    if isinstance(scheduled_at, str):
        dt = datetime.fromisoformat(scheduled_at.replace('Z', '+00:00'))
    else:
        dt = scheduled_at

    clinic_name = clinic.get('name', '')
    # FIX: Use correct function for entry instructions
    entry_instructions = get_entry_instructions(clinic, lang)

    # Build entry section (avoid nested f-strings for cleaner output)
    entry_section = ""
    if entry_instructions:
        if lang == 'ru':
            entry_section = f"\n🚪 *Как войти:* {entry_instructions}\n"
        elif lang == 'es':
            entry_section = f"\n🚪 *Cómo entrar:* {entry_instructions}\n"
        else:
            entry_section = f"\n🚪 *How to enter:* {entry_instructions}\n"

    # Also get directions URL as fallback if location card fails
    location_data = clinic.get('location_data', {}) or {}
    directions_url = location_data.get('directions_url') or location_data.get('google_maps_uri', '')

    templates = {
        'ru': f"""🗺️ *Скоро ваш приём!*

Через 2 часа вас ждут в {clinic_name}.
{entry_section}
Вот ваша локация для навигации:""",

        'en': f"""🗺️ *Your appointment is coming up!*

{clinic_name} is expecting you in 2 hours.
{entry_section}
Here's your location for navigation:""",

        'es': f"""🗺️ *Tu cita es pronto!*

{clinic_name} te espera en 2 horas.
{entry_section}
Aquí está la ubicación para navegación:"""
    }

    return templates.get(lang, templates['en'])


def get_reminder_buttons(appointment_id: str, lang: str = 'ru') -> list:
    """Get interactive buttons for reminder messages."""
    buttons = {
        'ru': [
            {"id": f"confirm_{appointment_id}", "title": "✅ Приду"},
            {"id": f"reschedule_{appointment_id}", "title": "🔄 Перенести"}
        ],
        'en': [
            {"id": f"confirm_{appointment_id}", "title": "✅ I'll be there"},
            {"id": f"reschedule_{appointment_id}", "title": "🔄 Reschedule"}
        ],
        'es': [
            {"id": f"confirm_{appointment_id}", "title": "✅ Asistiré"},
            {"id": f"reschedule_{appointment_id}", "title": "🔄 Reprogramar"}
        ]
    }
    return buttons.get(lang, buttons['en'])
