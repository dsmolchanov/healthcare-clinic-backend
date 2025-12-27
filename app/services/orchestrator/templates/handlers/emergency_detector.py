"""Emergency detection for healthcare conversations."""
from typing import List, Dict, Any
import logging

logger = logging.getLogger(__name__)

# Emergency keywords that require immediate attention
EMERGENCY_KEYWORDS: List[str] = [
    'emergency', 'urgent', 'severe pain', 'bleeding',
    'chest pain', 'difficulty breathing', '911'
]


def is_emergency_message(message: str) -> bool:
    """
    Check if message contains emergency keywords.

    Args:
        message: User message to check

    Returns:
        True if message contains emergency keywords
    """
    message_lower = message.lower()
    return any(keyword in message_lower for keyword in EMERGENCY_KEYWORDS)


def get_emergency_response(language: str = 'en') -> str:
    """
    Get localized emergency response message.

    Args:
        language: Language code

    Returns:
        Emergency response text
    """
    responses = {
        'en': (
            "This seems to be an emergency situation. "
            "Please call 911 or go to your nearest emergency room immediately. "
            "For immediate dental emergencies, call our emergency line: 1-800-URGENT-DENTAL"
        ),
        'es': (
            "Esta parece ser una situación de emergencia. "
            "Por favor llame al 911 o vaya a la sala de emergencias más cercana inmediatamente. "
            "Para emergencias dentales, llame a nuestra línea de emergencia: 1-800-URGENT-DENTAL"
        ),
        'ru': (
            "Это похоже на экстренную ситуацию. "
            "Пожалуйста, позвоните в 911 или немедленно обратитесь в ближайшую скорую помощь. "
            "Для срочных стоматологических проблем звоните: 1-800-URGENT-DENTAL"
        ),
    }
    return responses.get(language, responses['en'])


def check_audit_trail_for_emergency(audit_trail: List[Dict[str, Any]]) -> bool:
    """
    Check audit trail for emergency detection.

    Args:
        audit_trail: List of audit trail entries

    Returns:
        True if emergency was detected in audit trail
    """
    for entry in audit_trail:
        if entry.get('node') == 'emergency_check' and entry.get('is_emergency'):
            return True
    return False
