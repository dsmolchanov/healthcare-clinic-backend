"""Security guardrails for healthcare conversations."""
from typing import Dict, Any, List, Tuple, Optional
from datetime import datetime
import re
import logging

logger = logging.getLogger(__name__)

# Emergency patterns (multilingual)
EMERGENCY_PATTERNS: List[str] = [
    # English
    '911', 'emergency', 'heart attack', 'cant breathe', "can't breathe",
    'severe bleeding', 'suicidal', 'overdose', 'dying', 'severe pain',
    # Russian
    'помогите', 'умираю', 'острая боль', 'сильная боль', 'скорая', 'очень больно', 'нестерпимая боль',
    # Spanish
    'emergencia', 'no puedo respirar', 'dolor severo', 'urgente', 'dolor agudo',
    # Portuguese
    'emergência', 'dor forte', 'não consigo respirar',
    # Hebrew
    'חירום', 'כאב חזק',
]

# PHI patterns (SSN, etc.)
PHI_SSN_PATTERNS: List[str] = [
    r'\b\d{3}-\d{2}-\d{4}\b',  # SSN (XXX-XX-XXXX)
    r'\b\d{9}\b',  # 9-digit number (possible SSN without dashes)
]

# All available tools
ALL_TOOLS: List[str] = [
    'check_availability', 'book_appointment', 'cancel_appointment',
    'query_prices', 'query_services', 'query_doctors'
]

# Tools blocked in escalated state
ESCALATED_BLOCKED_TOOLS: List[str] = [
    'book_appointment', 'cancel_appointment', 'reschedule_appointment'
]


def detect_emergency(message: str) -> Tuple[bool, Optional[str]]:
    """
    Detect emergency patterns in message.

    Returns:
        Tuple of (is_emergency, matched_pattern)
    """
    message_lower = message.lower()
    for pattern in EMERGENCY_PATTERNS:
        if pattern in message_lower:
            return True, pattern
    return False, None


def detect_phi_ssn(message: str) -> bool:
    """
    Detect SSN/PII patterns in message.

    Returns:
        True if SSN pattern detected
    """
    return any(re.search(p, message) for p in PHI_SSN_PATTERNS)


def get_emergency_response_by_language(language: str) -> str:
    """Get localized emergency response."""
    emergency_responses = {
        'en': "I understand you're experiencing a medical emergency. Please call 911 immediately or go to your nearest emergency room. Your health is our priority, and emergency services are best equipped to help you right now.",
        'ru': "Я понимаю, что у вас неотложная медицинская ситуация. Пожалуйста, немедленно позвоните 911 или обратитесь в ближайшую скорую помощь. Ваше здоровье — наш приоритет, и экстренные службы лучше всего оснащены, чтобы помочь вам прямо сейчас.",
        'es': "Entiendo que está experimentando una emergencia médica. Por favor llame al 911 inmediatamente o vaya a la sala de emergencias más cercana. Su salud es nuestra prioridad.",
        'pt': "Entendo que você está passando por uma emergência médica. Por favor, ligue para o 192 imediatamente ou vá ao pronto-socorro mais próximo.",
        'he': "אני מבין שאתה חווה מצב חירום רפואי. אנא התקשר למד״א 101 מיד או גש לחדר מיון הקרוב אליך.",
    }
    return emergency_responses.get(language, emergency_responses['en'])


def get_pii_response_by_language(language: str) -> str:
    """Get localized PII/privacy response."""
    pii_responses = {
        'en': "For privacy and security, I can't access or verify patient records using sensitive information like Social Security numbers. I can help you schedule an appointment or direct you to our front desk for record-related questions. How can I assist you today?",
        'es': "Por privacidad y seguridad, no puedo acceder a registros de pacientes usando información sensible como números de seguro social. Puedo ayudarle a programar una cita o dirigirle a recepción para preguntas sobre registros. ¿Cómo puedo asistirle hoy?",
        'ru': "В целях конфиденциальности я не могу получить доступ к записям пациентов по номеру социального страхования. Я могу помочь вам записаться на прием или связаться с регистратурой для вопросов о записях. Как я могу вам помочь?",
    }
    return pii_responses.get(language, pii_responses['en'])


def calculate_allowed_tools(
    blocked_tools: List[str],
    all_tools: Optional[List[str]] = None
) -> List[str]:
    """Calculate allowed tools by removing blocked ones."""
    tools = all_tools or ALL_TOOLS
    return [t for t in tools if t not in blocked_tools]


def get_blocked_tools_for_state(flow_state: str) -> List[str]:
    """Get list of tools blocked for a given flow state."""
    if flow_state == 'escalated':
        return ESCALATED_BLOCKED_TOOLS.copy()
    return []


def create_guardrail_audit_entry(
    action: str,
    is_emergency: bool = False,
    phi_detected: bool = False,
    blocked_tools: Optional[List[str]] = None,
    reason: Optional[str] = None,
) -> Dict[str, Any]:
    """Create audit trail entry for guardrail check."""
    entry = {
        "node": "guardrail",
        "timestamp": datetime.utcnow().isoformat(),
        "action": action,
        "is_emergency": is_emergency,
        "phi_detected": phi_detected,
    }
    if blocked_tools:
        entry["blocked_tools"] = blocked_tools
    if reason:
        entry["reason"] = reason
    return entry


def route_by_guardrail_action(action: str, next_agent: Optional[str] = None) -> str:
    """
    Determine route based on guardrail action.

    Returns:
        'escalate', 'exit', or 'continue'
    """
    if action == 'escalate':
        return 'escalate'
    if next_agent == 'pii_detected':
        return 'exit'
    return 'continue'
