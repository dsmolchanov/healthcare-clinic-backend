"""
Language-aware WhatsApp message handler.
Automatically detects language and responds appropriately.
"""

from typing import Optional, Dict, Any
from app.services.language_detection_service import LanguageDetectionService
from app.whatsapp_handler import WhatsAppHandler

class LanguageAwareWhatsAppHandler:
    """WhatsApp handler with automatic language detection"""

    def __init__(self, twilio_client=None):
        self.base_handler = WhatsAppHandler(twilio_client)
        self.language_detector = LanguageDetectionService()

        # Response templates by language
        self.templates = {
            'es': {
                'greeting': '¡Hola! Bienvenido a Clínica Dental Sonrisa. ¿En qué puedo ayudarle hoy?',
                'appointment_confirm': 'Su cita ha sido confirmada para {date} a las {time}.',
                'appointment_options': 'Tenemos disponibilidad en los siguientes horarios:\n{options}',
                'no_availability': 'Lo siento, no tenemos disponibilidad para esa fecha. ¿Le gustaría probar otro día?',
                'confirmation_request': '¿Desea confirmar esta cita?',
                'thank_you': 'Gracias por elegir Clínica Dental Sonrisa.',
                'help': 'Puede escribir:\n- "Agendar cita" para hacer una reservación\n- "Cancelar cita" para cancelar\n- "Información" para conocer nuestros servicios'
            },
            'en': {
                'greeting': 'Hello! Welcome to Sonrisa Dental Clinic. How can I help you today?',
                'appointment_confirm': 'Your appointment has been confirmed for {date} at {time}.',
                'appointment_options': 'We have availability at the following times:\n{options}',
                'no_availability': 'Sorry, we don\'t have availability for that date. Would you like to try another day?',
                'confirmation_request': 'Would you like to confirm this appointment?',
                'thank_you': 'Thank you for choosing Sonrisa Dental Clinic.',
                'help': 'You can write:\n- "Book appointment" to make a reservation\n- "Cancel appointment" to cancel\n- "Information" for our services'
            }
        }

    async def process_message(self, message: str, from_number: str, clinic_id: str) -> Dict[str, Any]:
        """
        Process an incoming WhatsApp message with language detection.

        Args:
            message: The message text
            from_number: Sender's phone number
            clinic_id: Clinic identifier

        Returns:
            Response dictionary with message and metadata
        """
        # Detect language
        language = await self.language_detector.detect_language(message)

        # Extract intent
        intent = await self.language_detector.extract_intent(message)

        # Check if it's a greeting
        is_greeting = await self.language_detector.is_greeting(message)

        # Process based on intent
        if is_greeting:
            response_text = self.templates[language]['greeting']
        elif intent == 'appointment':
            # Process appointment request
            response = await self.base_handler.process_message(message, from_number, clinic_id)
            # Wrap response in appropriate language
            if response.get('appointments'):
                options = '\n'.join([
                    f"• {slot['date']} - {slot['time']}"
                    for slot in response['appointments']
                ])
                response_text = self.templates[language]['appointment_options'].format(
                    options=options
                )
            else:
                response_text = self.templates[language]['no_availability']
        elif intent == 'information':
            response_text = self.templates[language]['help']
        else:
            # Default help message
            response_text = self.templates[language]['help']

        return {
            'response': response_text,
            'language': language,
            'intent': intent,
            'is_greeting': is_greeting
        }

    async def send_confirmation(
        self,
        to_number: str,
        appointment_date: str,
        appointment_time: str,
        language: str = 'es'
    ) -> bool:
        """
        Send appointment confirmation in the appropriate language.

        Args:
            to_number: Recipient's phone number
            appointment_date: Date of appointment
            appointment_time: Time of appointment
            language: Language code

        Returns:
            Success status
        """
        message = self.templates[language]['appointment_confirm'].format(
            date=appointment_date,
            time=appointment_time
        )

        return await self.base_handler.send_message(to_number, message)

    async def send_reminder(
        self,
        to_number: str,
        appointment_date: str,
        appointment_time: str,
        language: str = 'es'
    ) -> bool:
        """
        Send appointment reminder in the appropriate language.

        Args:
            to_number: Recipient's phone number
            appointment_date: Date of appointment
            appointment_time: Time of appointment
            language: Language code

        Returns:
            Success status
        """
        if language == 'es':
            message = f"Recordatorio: Tiene una cita mañana {appointment_date} a las {appointment_time} en Clínica Dental Sonrisa."
        else:
            message = f"Reminder: You have an appointment tomorrow {appointment_date} at {appointment_time} at Sonrisa Dental Clinic."

        return await self.base_handler.send_message(to_number, message)

    def get_language_preferences(self, phone_number: str) -> str:
        """
        Get stored language preference for a phone number.
        In production, this would check a database.
        """
        # Default to Spanish for Mexican deployment
        return 'es'

    async def handle_media(self, media_url: str, media_type: str) -> Dict[str, Any]:
        """Handle media messages (images, voice notes, etc.)"""
        return await self.base_handler.handle_media(media_url, media_type)
