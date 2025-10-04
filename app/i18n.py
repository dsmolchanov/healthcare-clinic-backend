"""
Internationalization support
"""


async def get_message(key: str, lang: str = 'es') -> str:
    """
    Get localized message

    Args:
        key: Message key
        lang: Language code

    Returns:
        Localized message
    """
    messages = {
        'es': {
            'appointment_confirmed': 'Su cita ha sido confirmada',
            'appointment_cancelled': 'Su cita ha sido cancelada',
            'welcome': 'Bienvenido a nuestra clínica dental'
        },
        'en': {
            'appointment_confirmed': 'Your appointment has been confirmed',
            'appointment_cancelled': 'Your appointment has been cancelled',
            'welcome': 'Welcome to our dental clinic'
        }
    }

    return messages.get(lang, {}).get(key, key)
