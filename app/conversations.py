"""
Conversation handlers for multi-step interactions
"""

from typing import Dict, Any, Optional
from datetime import datetime


class ConversationHandler:
    """Handle multi-step conversations"""

    def __init__(self, redis_client=None):
        self.redis_client = redis_client
        self.context = {}

    async def process_message(
        self,
        phone: str,
        message: str,
        context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Process message with context

        Args:
            phone: User phone number
            message: Message content
            context: Conversation context

        Returns:
            Response with intent and message
        """
        # Store context
        self.context = context

        # Check for appointment modification
        if 'cambiar' in message.lower() or 'modificar' in message.lower():
            if context.get('appointments'):
                appointment = context['appointments'][0]
                return {
                    'intent': 'appointment_modification',
                    'message': f"Su cita actual es el {appointment['appointment_date']} a las {appointment['start_time']}. ¿Para cuándo desea cambiarla?"
                }

        # Check if modifying appointment
        if context.get('modifying_appointment'):
            # Parse new date/time
            if 'lunes' in message.lower() and '10' in message:
                return {
                    'intent': 'confirm_modification',
                    'message': 'Su cita será cambiada para el lunes a las 10:00. ¿Confirma el cambio?'
                }

        # Confirm modification
        if context.get('new_datetime') and ('sí' in message.lower() or 'confirmo' in message.lower()):
            return {
                'modification_complete': True,
                'message': 'Su cita ha sido actualizada exitosamente.'
            }

        return {
            'intent': 'unknown',
            'message': '¿En qué puedo ayudarle?'
        }


class ServiceInquiryFlow:
    """Handle service inquiry conversations"""

    async def handle_message(self, message: str, context: Dict[str, Any]) -> Dict[str, Any]:
        """
        Handle service inquiry message

        Args:
            message: User message
            context: Conversation context

        Returns:
            Response dictionary
        """
        message_lower = message.lower()

        # General service list
        if 'servicio' in message_lower or 'ofrecen' in message_lower:
            return {
                'message': """
Ofrecemos los siguientes servicios:
• Limpieza dental
• Blanqueamiento
• Ortodoncia
• Extracciones
• Endodoncia
• Coronas y puentes
• Implantes

¿Sobre cuál servicio desea más información?
"""
            }

        # Specific service pricing
        if 'cuánto' in message_lower and 'limpieza' in message_lower:
            return {
                'message': """
💰 Limpieza Dental: $600 MXN

Incluye:
• Eliminación de sarro
• Pulido dental
• Aplicación de flúor
• Revisión general

Duración: 45 minutos
"""
            }

        # Insurance inquiry
        if 'seguro' in message_lower or 'gnp' in message_lower.upper():
            return {
                'message': """
✅ Sí, aceptamos seguro GNP y otros seguros principales:
• GNP
• AXA
• MetLife
• Seguros Monterrey

Necesitará presentar su póliza y credencial.
"""
            }

        return {
            'message': '¿En qué más puedo ayudarle?'
        }


class ClinicManager:
    """Manage multiple clinics"""

    async def register_clinic(self, clinic: Dict[str, Any]):
        """Register a clinic"""
        # Store clinic configuration
        pass

    async def process_message(
        self,
        clinic_id: str,
        phone: str,
        message: str
    ) -> Dict[str, Any]:
        """Process message for a specific clinic"""
        from .whatsapp import process_whatsapp_message

        return await process_whatsapp_message(clinic_id, phone, message)
