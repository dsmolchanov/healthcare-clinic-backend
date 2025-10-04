"""
WhatsApp message handler base class.
"""

from typing import Optional, Dict, Any, List
from datetime import datetime
import re

class WhatsAppHandler:
    """Base WhatsApp message handler"""

    def __init__(self, twilio_client=None):
        self.twilio_client = twilio_client

    async def process_message(
        self,
        message: str,
        from_number: str,
        clinic_id: str
    ) -> Dict[str, Any]:
        """
        Process incoming WhatsApp message.

        Args:
            message: Message text
            from_number: Sender's phone number
            clinic_id: Clinic identifier

        Returns:
            Response dictionary
        """
        # Extract appointment details from message
        appointment_info = self.extract_appointment_details(message)

        if appointment_info:
            # Get available slots (mock data for testing)
            available_slots = await self.get_available_slots(
                clinic_id,
                appointment_info.get('date')
            )

            return {
                'appointments': available_slots,
                'intent': 'appointment',
                'extracted_info': appointment_info
            }

        return {
            'intent': 'unknown',
            'message': message
        }

    def extract_appointment_details(self, message: str) -> Optional[Dict[str, Any]]:
        """Extract appointment details from message text"""
        details = {}

        # Date patterns
        date_patterns = [
            r'(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})',
            r'(mañana|tomorrow)',
            r'(hoy|today)',
            r'(lunes|martes|miércoles|jueves|viernes|sábado|domingo)',
            r'(monday|tuesday|wednesday|thursday|friday|saturday|sunday)'
        ]

        for pattern in date_patterns:
            match = re.search(pattern, message.lower())
            if match:
                details['date'] = match.group(0)
                break

        # Time patterns
        time_patterns = [
            r'(\d{1,2}):(\d{2})\s*(am|pm)?',
            r'(\d{1,2})\s*(am|pm)',
            r'(mañana|tarde|noche|morning|afternoon|evening)'
        ]

        for pattern in time_patterns:
            match = re.search(pattern, message.lower())
            if match:
                details['time'] = match.group(0)
                break

        return details if details else None

    async def get_available_slots(
        self,
        clinic_id: str,
        date: Optional[str] = None
    ) -> List[Dict[str, str]]:
        """Get available appointment slots"""
        # Mock data for testing
        return [
            {'date': '2024-12-28', 'time': '10:00 AM'},
            {'date': '2024-12-28', 'time': '2:00 PM'},
            {'date': '2024-12-28', 'time': '4:00 PM'}
        ]

    async def send_message(self, to_number: str, message: str) -> bool:
        """Send WhatsApp message"""
        if self.twilio_client:
            try:
                self.twilio_client.messages.create(
                    body=message,
                    from_='whatsapp:+14155238886',  # Twilio sandbox number
                    to=f'whatsapp:{to_number}'
                )
                return True
            except Exception:
                return False
        return True  # Mock success for testing

    async def handle_media(self, media_url: str, media_type: str) -> Dict[str, Any]:
        """Handle media messages"""
        return {
            'media_url': media_url,
            'media_type': media_type,
            'processed': True
        }
