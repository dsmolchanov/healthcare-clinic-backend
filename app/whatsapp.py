"""
WhatsApp integration via Twilio
"""

import os
import re
import asyncio
from typing import Dict, Any, Optional, List
from datetime import datetime
from twilio.rest import Client
from twilio.base.exceptions import TwilioRestException
import logging

logger = logging.getLogger(__name__)


async def send_whatsapp_message(to_phone: str, message: str) -> Dict[str, Any]:
    """
    Send WhatsApp message via Twilio

    Args:
        to_phone: Recipient phone number
        message: Message content

    Returns:
        Result dictionary
    """
    try:
        account_sid = os.environ.get('TWILIO_ACCOUNT_SID')
        auth_token = os.environ.get('TWILIO_AUTH_TOKEN')
        from_number = os.environ.get('WHATSAPP_NUMBER', '+14155238886')

        client = Client(account_sid, auth_token)

        # Ensure phone numbers have whatsapp: prefix
        if not to_phone.startswith('whatsapp:'):
            to_phone = f'whatsapp:{to_phone}'
        if not from_number.startswith('whatsapp:'):
            from_number = f'whatsapp:{from_number}'

        message_obj = client.messages.create(
            body=message,
            from_=from_number,
            to=to_phone
        )

        return {
            'success': True,
            'message_sid': message_obj.sid
        }

    except TwilioRestException as e:
        return {
            'success': False,
            'error': str(e)
        }
    except Exception as e:
        return {
            'success': False,
            'error': str(e)
        }


async def handle_whatsapp_webhook(organization_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Handle incoming WhatsApp webhook from Twilio

    Args:
        organization_id: Clinic/organization ID
        payload: Webhook payload from Twilio

    Returns:
        Processing result
    """
    # Extract message details
    from_number = payload.get('From', '').replace('whatsapp:', '')
    message_body = payload.get('Body', '')
    message_sid = payload.get('MessageSid', '')

    # Process the message
    result = await process_whatsapp_message(
        organization_id,
        from_number,
        message_body
    )

    return result


async def process_whatsapp_message(
    clinic_id: str,
    phone: str,
    message: str
) -> Dict[str, Any]:
    """
    Process incoming WhatsApp message

    Args:
        clinic_id: Clinic identifier
        phone: Sender phone number
        message: Message content

    Returns:
        Processing result
    """
    from .session_manager import WhatsAppSessionManager
    from .privacy import check_consent, handle_consent_response

    # Get or create session
    session_manager = WhatsAppSessionManager()
    session = await session_manager.get_or_create_session(phone, clinic_id)

    # Check consent
    if not session.get('consent_given'):
        # Handle consent response
        consent_result = await handle_consent_response(phone, clinic_id, message)

        if consent_result['consent_given']:
            await session_manager.mark_consent_given(phone, clinic_id)
            await send_whatsapp_message(phone, consent_result['message'])
            return {'handled': True}
        elif consent_result['status'] == 'rejected':
            await send_whatsapp_message(phone, consent_result['message'])
            return {'handled': True}
        else:
            # Still waiting for valid consent response
            await send_whatsapp_message(phone, consent_result['message'])
            return {'handled': True}

    # Process message intent
    intent = await recognize_intent(message)

    # Handle based on intent
    if intent['type'] == 'appointment_booking':
        from .appointments import SimpleAppointmentBooking
        booking = SimpleAppointmentBooking()

        # Extract details
        details = await extract_appointment_details(message)

        if details.get('date') and details.get('time'):
            result = await booking.book_appointment(
                clinic_id=clinic_id,
                patient_phone=phone,
                requested_date=details['date'],
                requested_time=details['time']
            )

            await send_whatsapp_message(phone, result['message'])
        else:
            await send_whatsapp_message(
                phone,
                "¿Para qué fecha y hora le gustaría agendar su cita?"
            )

    elif intent['type'] == 'appointment_cancellation':
        await send_whatsapp_message(
            phone,
            "Para cancelar su cita, por favor proporcione el número de confirmación."
        )

    else:
        # Handle other intents or general queries
        handled = await handle_common_queries(phone, message, clinic_id)
        if not handled['handled']:
            await send_whatsapp_message(
                phone,
                "¿En qué puedo ayudarle? Puede agendar una cita o solicitar información."
            )

    return {'processed': True}


async def handle_common_queries(
    phone: str,
    message: str,
    clinic_id: str
) -> Dict[str, bool]:
    """
    Handle common queries like hours, location, etc.

    Args:
        phone: User phone number
        message: Message content
        clinic_id: Clinic identifier

    Returns:
        Dictionary with 'handled' flag
    """
    message_lower = message.lower()

    if any(word in message_lower for word in ['horario', 'hora', 'abierto', 'cerrado']):
        response = """
🕐 *Nuestros Horarios*

Lunes a Viernes: 9:00 AM - 6:00 PM
Sábado: 9:00 AM - 2:00 PM
Domingo: Cerrado

Para agendar una cita, escriba "Quiero una cita".
"""
        await send_whatsapp_message(phone, response)
        return {'handled': True}

    elif any(word in message_lower for word in ['ubicación', 'dirección', 'donde', 'están']):
        response = """
📍 *Nuestra Ubicación*

Av. Principal #123
Col. Centro, CP 12345
Ciudad de México

🗺️ Ver en Google Maps: https://maps.google.com
"""
        await send_whatsapp_message(phone, response)
        return {'handled': True}

    elif any(word in message_lower for word in ['precio', 'costo', 'cuánto']):
        response = """
💰 *Nuestros Precios*

• Consulta General: $400 MXN
• Limpieza Dental: $600 MXN
• Extracción Simple: $800 MXN
• Resina: desde $700 MXN

Aceptamos efectivo, tarjeta y transferencia.
"""
        await send_whatsapp_message(phone, response)
        return {'handled': True}

    return {'handled': False}


async def detect_language(message: str) -> str:
    """
    Detect language of the message

    Args:
        message: Message text

    Returns:
        Language code ('es' or 'en')
    """
    # Simple detection based on common words
    spanish_words = ['hola', 'quiero', 'necesito', 'cita', 'cuando', 'donde', 'gracias']
    english_words = ['hello', 'want', 'need', 'appointment', 'when', 'where', 'thanks']

    message_lower = message.lower()

    spanish_count = sum(1 for word in spanish_words if word in message_lower)
    english_count = sum(1 for word in english_words if word in message_lower)

    if english_count > spanish_count:
        return 'en'

    # Default to Spanish for Mexican market
    return 'es'


async def recognize_intent(message: str) -> Dict[str, str]:
    """
    Recognize intent from message

    Args:
        message: Message text

    Returns:
        Intent dictionary with type
    """
    message_lower = message.lower()

    # Appointment intents
    if any(word in message_lower for word in ['cita', 'agendar', 'appointment', 'consulta', 'reservar']):
        if any(word in message_lower for word in ['cancelar', 'cancel']):
            return {'type': 'appointment_cancellation'}
        elif any(word in message_lower for word in ['cambiar', 'modificar', 'change']):
            return {'type': 'appointment_modification'}
        elif any(word in message_lower for word in ['disponible', 'disponibilidad', 'available']):
            return {'type': 'appointment_availability'}
        else:
            return {'type': 'appointment_booking'}

    # Information intents
    elif any(word in message_lower for word in ['horario', 'hora', 'abierto', 'open']):
        return {'type': 'hours_inquiry'}
    elif any(word in message_lower for word in ['ubicación', 'dirección', 'donde', 'location']):
        return {'type': 'location_inquiry'}
    elif any(word in message_lower for word in ['precio', 'costo', 'cuánto', 'price', 'cost']):
        return {'type': 'price_inquiry'}
    elif any(word in message_lower for word in ['seguro', 'insurance', 'cobertura']):
        return {'type': 'insurance_inquiry'}

    # Default
    return {'type': 'general_inquiry'}


async def extract_appointment_details(message: str) -> Dict[str, Any]:
    """
    Extract appointment details from message

    Args:
        message: Message text

    Returns:
        Dictionary with extracted details
    """
    details = {}
    message_lower = message.lower()

    # Extract service type
    if 'limpieza' in message_lower or 'cleaning' in message_lower:
        details['service'] = 'limpieza dental'
    elif 'extracción' in message_lower or 'extraction' in message_lower:
        details['service'] = 'extracción'
    elif 'dolor' in message_lower or 'pain' in message_lower:
        details['service'] = 'emergencia'

    # Extract day
    days = {
        'lunes': 'monday',
        'martes': 'tuesday',
        'miércoles': 'wednesday',
        'jueves': 'thursday',
        'viernes': 'friday',
        'sábado': 'saturday',
        'domingo': 'sunday'
    }

    for spanish_day, english_day in days.items():
        if spanish_day in message_lower or english_day.lower() in message_lower:
            details['day'] = english_day
            # Calculate date from day (simplified)
            details['date'] = '2024-12-20'  # Mock date
            break

    # Extract time
    time_patterns = [
        r'(\d{1,2})\s*:\s*(\d{2})',  # 14:30
        r'(\d{1,2})\s*(am|pm)',  # 3pm
        r'(\d{1,2})\s*de la (mañana|tarde|noche)',  # 3 de la tarde
    ]

    for pattern in time_patterns:
        match = re.search(pattern, message_lower)
        if match:
            # Convert to 24hr format
            if 'tarde' in message_lower or 'pm' in message_lower:
                hour = int(match.group(1))
                if hour < 12:
                    hour += 12
                details['time'] = f'{hour:02d}:00'
            else:
                details['time'] = f'{match.group(1):0>2}:00'
            break

    # Handle specific time mentions
    if '3' in message and ('tarde' in message_lower or 'pm' in message_lower):
        details['time'] = '15:00'

    return details


async def send_appointment_template(
    to_phone: str,
    appointment: Dict[str, Any],
    template_type: str
) -> Dict[str, Any]:
    """Send appointment template message"""
    templates = {
        'confirmation': """
✅ *Confirmación de Cita*

Fecha: {appointment_date}
Hora: {start_time}
Servicio: {service}

Por favor llegue 10 minutos antes.
""",
        'reminder': """
🔔 *Recordatorio de Cita*

Su cita es mañana:
{appointment_date} a las {start_time}
""",
        'cancellation': """
❌ *Cita Cancelada*

Su cita del {appointment_date} ha sido cancelada.
"""
    }

    template = templates.get(template_type, templates['confirmation'])
    message = template.format(**appointment)

    return await send_whatsapp_message(to_phone, message)


async def send_whatsapp_media(
    to_phone: str,
    media_url: str,
    caption: str = ''
) -> Dict[str, Any]:
    """Send media message via WhatsApp"""
    try:
        account_sid = os.environ.get('TWILIO_ACCOUNT_SID')
        auth_token = os.environ.get('TWILIO_AUTH_TOKEN')
        from_number = os.environ.get('WHATSAPP_NUMBER', '+14155238886')

        client = Client(account_sid, auth_token)

        # Ensure phone numbers have whatsapp: prefix
        if not to_phone.startswith('whatsapp:'):
            to_phone = f'whatsapp:{to_phone}'
        if not from_number.startswith('whatsapp:'):
            from_number = f'whatsapp:{from_number}'

        message_obj = client.messages.create(
            body=caption,
            from_=from_number,
            to=to_phone,
            media_url=media_url
        )

        return {
            'success': True,
            'message_sid': message_obj.sid
        }

    except Exception as e:
        return {
            'success': False,
            'error': str(e)
        }


async def handle_status_callback(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Handle Twilio status callback"""
    return {
        'status': payload.get('MessageStatus'),
        'message_sid': payload.get('MessageSid')
    }


async def send_with_retry(
    to_phone: str,
    message: str,
    max_retries: int = 3
) -> Dict[str, Any]:
    """Send message with retry logic"""
    for attempt in range(max_retries):
        try:
            result = await send_whatsapp_message(to_phone, message)
            if result['success']:
                return result
        except TwilioRestException as e:
            if e.status == 429 and attempt < max_retries - 1:
                # Rate limited, wait and retry
                await asyncio.sleep(2 ** attempt)
                continue
            raise

    return {'success': False, 'error': 'Max retries exceeded'}


async def get_fallback_response(error_type: str) -> str:
    """Get fallback response for errors"""
    responses = {
        'processing_error': 'Disculpe, hubo un error procesando su mensaje. Por favor intente de nuevo.',
        'unavailable': 'El sistema no está disponible en este momento. Por favor intente más tarde.',
        'invalid_input': 'No entendí su mensaje. ¿Puede reformularlo?'
    }

    return responses.get(error_type, responses['processing_error'])


async def handle_media_message(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Handle media messages from WhatsApp"""
    num_media = int(payload.get('NumMedia', 0))

    if num_media == 0:
        return {'processed': False, 'reason': 'No media'}

    media_url = payload.get('MediaUrl0')
    media_type = payload.get('MediaContentType0')

    # Process based on media type
    if media_type and 'image' in media_type:
        # Handle image
        return {
            'processed': True,
            'media_type': media_type,
            'action': 'image_received'
        }
    elif media_type and 'pdf' in media_type:
        # Handle PDF document
        return {
            'processed': True,
            'media_type': media_type,
            'action': 'document_received'
        }

    return {
        'processed': True,
        'media_type': media_type
    }


async def validate_media_size(size_bytes: int) -> bool:
    """Validate media file size"""
    MAX_SIZE = 16 * 1024 * 1024  # 16MB WhatsApp limit
    return size_bytes <= MAX_SIZE


class MessageProcessor:
    """Process WhatsApp messages"""

    async def process_message(self, message_data: Dict[str, Any]) -> Dict[str, Any]:
        """Process a single message"""
        # Extract data
        phone = message_data['phone']
        message = message_data['message']

        # Detect intent
        intent = await recognize_intent(message)

        # Process based on intent
        response = await self._generate_response(intent, message)

        # Send response
        await send_whatsapp_message(phone, response)

        return {
            'processed': True,
            'intent': intent['type']
        }

    async def _generate_response(self, intent: Dict[str, str], message: str) -> str:
        """Generate response based on intent"""
        responses = {
            'appointment_booking': '¿Para qué fecha le gustaría agendar su cita?',
            'hours_inquiry': 'Nuestros horarios son L-V 9AM-6PM, Sáb 9AM-2PM',
            'price_inquiry': 'Consulta: $400, Limpieza: $600',
            'general_inquiry': '¿En qué puedo ayudarle?'
        }

        return responses.get(intent['type'], responses['general_inquiry'])
