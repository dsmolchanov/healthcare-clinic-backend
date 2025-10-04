"""
WhatsApp Confirmation Service for Appointments

This service handles sending appointment confirmations and reminders
via WhatsApp using the Evolution API integration.
"""

import logging
import asyncio
import aiohttp
from typing import Dict, Any, Optional, List
from datetime import datetime, timedelta
import json

from app.database import create_supabase_client
from app.services.redis_session_manager import RedisSessionManager

logger = logging.getLogger(__name__)


class WhatsAppConfirmationService:
    """
    Service for sending appointment confirmations via WhatsApp.
    Integrates with Evolution API for WhatsApp Business messaging.
    """

    def __init__(self, clinic_id: str):
        """
        Initialize the WhatsApp confirmation service.

        Args:
            clinic_id: ID of the clinic
        """
        self.clinic_id = clinic_id
        self.supabase = create_supabase_client()
        self.session_manager = RedisSessionManager()
        self.evolution_api_url = None
        self.evolution_api_key = None
        self.whatsapp_instance = None
        self._initialized = False

        logger.info(f"Initialized WhatsAppConfirmationService for clinic {clinic_id}")

    async def initialize(self):
        """Initialize the service and load configuration"""
        if self._initialized:
            return

        try:
            # Load clinic WhatsApp configuration
            result = self.supabase.table('clinics').select(
                'whatsapp_config'
            ).eq('id', self.clinic_id).execute()

            if result.data and result.data[0].get('whatsapp_config'):
                config = result.data[0]['whatsapp_config']
                self.evolution_api_url = config.get('evolution_api_url')
                self.evolution_api_key = config.get('evolution_api_key')
                self.whatsapp_instance = config.get('instance_name')
                self._initialized = True
                logger.info(f"WhatsApp configuration loaded for clinic {self.clinic_id}")
            else:
                logger.warning(f"No WhatsApp configuration found for clinic {self.clinic_id}")

        except Exception as e:
            logger.error(f"Error initializing WhatsApp service: {str(e)}")

    async def send_appointment_confirmation(
        self,
        appointment_id: str,
        patient_phone: str,
        channel_preference: str = "whatsapp"
    ) -> Dict[str, Any]:
        """
        Send appointment confirmation to patient.

        Args:
            appointment_id: ID of the appointment
            patient_phone: Patient's phone number
            channel_preference: Preferred communication channel

        Returns:
            Dictionary with sending result
        """
        try:
            await self.initialize()

            if channel_preference != "whatsapp" or not self._is_configured():
                logger.info(f"WhatsApp not configured or not preferred, skipping confirmation")
                return {
                    "success": False,
                    "reason": "WhatsApp not configured or not preferred channel"
                }

            # Get appointment details
            appointment = await self._get_appointment_details(appointment_id)
            if not appointment:
                return {
                    "success": False,
                    "error": "Appointment not found"
                }

            # Format confirmation message
            message = self._format_confirmation_message(appointment)

            # Send via Evolution API
            result = await self._send_whatsapp_message(
                phone=patient_phone,
                message=message,
                buttons=self._create_confirmation_buttons(appointment_id)
            )

            if result["success"]:
                # Log confirmation sent
                await self._log_confirmation_sent(
                    appointment_id=appointment_id,
                    channel="whatsapp",
                    message_id=result.get("message_id")
                )

            return result

        except Exception as e:
            logger.error(f"Error sending appointment confirmation: {str(e)}")
            return {
                "success": False,
                "error": str(e)
            }

    async def send_appointment_reminder(
        self,
        appointment_id: str,
        patient_phone: str,
        hours_before: int = 24
    ) -> Dict[str, Any]:
        """
        Send appointment reminder to patient.

        Args:
            appointment_id: ID of the appointment
            patient_phone: Patient's phone number
            hours_before: Hours before appointment to send reminder

        Returns:
            Dictionary with sending result
        """
        try:
            await self.initialize()

            if not self._is_configured():
                return {
                    "success": False,
                    "reason": "WhatsApp not configured"
                }

            # Get appointment details
            appointment = await self._get_appointment_details(appointment_id)
            if not appointment:
                return {
                    "success": False,
                    "error": "Appointment not found"
                }

            # Check if it's time to send reminder
            appointment_time = datetime.fromisoformat(appointment['scheduled_at'])
            reminder_time = appointment_time - timedelta(hours=hours_before)
            now = datetime.now()

            if now < reminder_time:
                return {
                    "success": False,
                    "reason": f"Too early for reminder, scheduled for {reminder_time.isoformat()}"
                }

            # Format reminder message
            message = self._format_reminder_message(appointment, hours_before)

            # Send via Evolution API
            result = await self._send_whatsapp_message(
                phone=patient_phone,
                message=message,
                buttons=self._create_reminder_buttons(appointment_id)
            )

            if result["success"]:
                # Log reminder sent
                await self._log_reminder_sent(
                    appointment_id=appointment_id,
                    hours_before=hours_before,
                    message_id=result.get("message_id")
                )

            return result

        except Exception as e:
            logger.error(f"Error sending appointment reminder: {str(e)}")
            return {
                "success": False,
                "error": str(e)
            }

    async def send_cancellation_confirmation(
        self,
        appointment_id: str,
        patient_phone: str,
        cancellation_reason: str
    ) -> Dict[str, Any]:
        """
        Send cancellation confirmation to patient.

        Args:
            appointment_id: ID of the cancelled appointment
            patient_phone: Patient's phone number
            cancellation_reason: Reason for cancellation

        Returns:
            Dictionary with sending result
        """
        try:
            await self.initialize()

            if not self._is_configured():
                return {
                    "success": False,
                    "reason": "WhatsApp not configured"
                }

            # Get appointment details
            appointment = await self._get_appointment_details(appointment_id)
            if not appointment:
                return {
                    "success": False,
                    "error": "Appointment not found"
                }

            # Format cancellation message
            message = self._format_cancellation_message(appointment, cancellation_reason)

            # Send via Evolution API
            result = await self._send_whatsapp_message(
                phone=patient_phone,
                message=message,
                buttons=[{
                    "id": "book_new",
                    "title": "Book New Appointment"
                }]
            )

            return result

        except Exception as e:
            logger.error(f"Error sending cancellation confirmation: {str(e)}")
            return {
                "success": False,
                "error": str(e)
            }

    async def send_rescheduling_confirmation(
        self,
        appointment_id: str,
        patient_phone: str,
        old_datetime: str,
        new_datetime: str
    ) -> Dict[str, Any]:
        """
        Send rescheduling confirmation to patient.

        Args:
            appointment_id: ID of the rescheduled appointment
            patient_phone: Patient's phone number
            old_datetime: Original appointment datetime
            new_datetime: New appointment datetime

        Returns:
            Dictionary with sending result
        """
        try:
            await self.initialize()

            if not self._is_configured():
                return {
                    "success": False,
                    "reason": "WhatsApp not configured"
                }

            # Get appointment details
            appointment = await self._get_appointment_details(appointment_id)
            if not appointment:
                return {
                    "success": False,
                    "error": "Appointment not found"
                }

            # Format rescheduling message
            message = self._format_rescheduling_message(appointment, old_datetime, new_datetime)

            # Send via Evolution API
            result = await self._send_whatsapp_message(
                phone=patient_phone,
                message=message,
                buttons=self._create_confirmation_buttons(appointment_id)
            )

            return result

        except Exception as e:
            logger.error(f"Error sending rescheduling confirmation: {str(e)}")
            return {
                "success": False,
                "error": str(e)
            }

    async def request_appointment_confirmation(
        self,
        hold_id: str,
        patient_phone: str,
        slot_details: Dict[str, Any],
        timeout_minutes: int = 15
    ) -> Dict[str, Any]:
        """
        Request confirmation for a held appointment slot.

        Args:
            hold_id: ID of the appointment hold
            patient_phone: Patient's phone number
            slot_details: Details of the held slot
            timeout_minutes: Time to wait for confirmation

        Returns:
            Dictionary with request result
        """
        try:
            await self.initialize()

            if not self._is_configured():
                return {
                    "success": False,
                    "reason": "WhatsApp not configured"
                }

            # Format confirmation request message
            message = self._format_confirmation_request(slot_details, timeout_minutes)

            # Create interactive buttons for yes/no
            buttons = [
                {
                    "id": f"confirm_{hold_id}",
                    "title": "✅ Confirm"
                },
                {
                    "id": f"cancel_{hold_id}",
                    "title": "❌ Cancel"
                }
            ]

            # Send via Evolution API
            result = await self._send_whatsapp_message(
                phone=patient_phone,
                message=message,
                buttons=buttons,
                interactive=True
            )

            if result["success"]:
                # Store confirmation request in session
                await self.session_manager.set_session_data(
                    session_id=f"confirm_{hold_id}",
                    data={
                        "hold_id": hold_id,
                        "patient_phone": patient_phone,
                        "slot_details": slot_details,
                        "requested_at": datetime.now().isoformat(),
                        "timeout_at": (datetime.now() + timedelta(minutes=timeout_minutes)).isoformat(),
                        "message_id": result.get("message_id")
                    },
                    ttl=timeout_minutes * 60
                )

            return result

        except Exception as e:
            logger.error(f"Error requesting appointment confirmation: {str(e)}")
            return {
                "success": False,
                "error": str(e)
            }

    # Private helper methods

    def _is_configured(self) -> bool:
        """Check if WhatsApp is properly configured"""
        return bool(
            self.evolution_api_url and
            self.evolution_api_key and
            self.whatsapp_instance
        )

    async def _get_appointment_details(self, appointment_id: str) -> Optional[Dict[str, Any]]:
        """Get appointment details from database"""
        try:
            result = self.supabase.table('healthcare.appointments').select('*').eq(
                'id', appointment_id
            ).execute()
            return result.data[0] if result.data else None
        except Exception as e:
            logger.error(f"Error getting appointment details: {str(e)}")
            return None

    async def _send_whatsapp_message(
        self,
        phone: str,
        message: str,
        buttons: Optional[List[Dict[str, str]]] = None,
        interactive: bool = False
    ) -> Dict[str, Any]:
        """Send WhatsApp message via Evolution API"""
        try:
            # Format phone number (remove + and add country code if needed)
            formatted_phone = phone.replace('+', '').replace('-', '').replace(' ', '')
            if not formatted_phone.startswith('1'):  # US country code
                formatted_phone = '1' + formatted_phone

            # Prepare message payload
            payload = {
                "number": formatted_phone + "@s.whatsapp.net",
                "text": message
            }

            # Add buttons if provided
            if buttons:
                if interactive:
                    # Interactive buttons
                    payload["buttons"] = buttons
                    payload["footer"] = "Please select an option"
                else:
                    # Quick reply buttons
                    payload["quickReplyButtons"] = buttons

            # Send via Evolution API
            url = f"{self.evolution_api_url}/message/sendText/{self.whatsapp_instance}"
            headers = {
                "apikey": self.evolution_api_key,
                "Content-Type": "application/json"
            }

            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, headers=headers) as response:
                    if response.status == 200:
                        data = await response.json()
                        return {
                            "success": True,
                            "message_id": data.get("key", {}).get("id"),
                            "status": data.get("status")
                        }
                    else:
                        error_text = await response.text()
                        logger.error(f"Evolution API error: {error_text}")
                        return {
                            "success": False,
                            "error": f"API error: {response.status}"
                        }

        except Exception as e:
            logger.error(f"Error sending WhatsApp message: {str(e)}")
            return {
                "success": False,
                "error": str(e)
            }

    def _format_confirmation_message(self, appointment: Dict[str, Any]) -> str:
        """Format appointment confirmation message"""
        dt = datetime.fromisoformat(appointment['scheduled_at'])

        message = f"🗓️ *Appointment Confirmed*\n\n"
        message += f"📍 *Service:* {appointment.get('service_name', 'Appointment')}\n"
        message += f"📅 *Date:* {dt.strftime('%A, %B %d, %Y')}\n"
        message += f"🕐 *Time:* {dt.strftime('%I:%M %p')}\n"

        if appointment.get('doctor_name'):
            message += f"👨‍⚕️ *Doctor:* Dr. {appointment['doctor_name']}\n"

        if appointment.get('duration_minutes'):
            message += f"⏱️ *Duration:* {appointment['duration_minutes']} minutes\n"

        message += f"\n📌 *Appointment ID:* {appointment['id'][:8]}...\n"
        message += "\nPlease arrive 10 minutes early for check-in."

        return message

    def _format_reminder_message(self, appointment: Dict[str, Any], hours_before: int) -> str:
        """Format appointment reminder message"""
        dt = datetime.fromisoformat(appointment['scheduled_at'])

        message = f"⏰ *Appointment Reminder*\n\n"
        message += f"You have an appointment in {hours_before} hours:\n\n"
        message += f"📍 *Service:* {appointment.get('service_name', 'Appointment')}\n"
        message += f"📅 *Date:* {dt.strftime('%A, %B %d, %Y')}\n"
        message += f"🕐 *Time:* {dt.strftime('%I:%M %p')}\n"

        if appointment.get('doctor_name'):
            message += f"👨‍⚕️ *Doctor:* Dr. {appointment['doctor_name']}\n"

        message += "\nPlease confirm your attendance or let us know if you need to reschedule."

        return message

    def _format_cancellation_message(
        self,
        appointment: Dict[str, Any],
        cancellation_reason: str
    ) -> str:
        """Format cancellation confirmation message"""
        dt = datetime.fromisoformat(appointment['scheduled_at'])

        message = f"❌ *Appointment Cancelled*\n\n"
        message += f"Your appointment has been cancelled:\n\n"
        message += f"📍 *Service:* {appointment.get('service_name', 'Appointment')}\n"
        message += f"📅 *Original Date:* {dt.strftime('%A, %B %d, %Y')}\n"
        message += f"🕐 *Original Time:* {dt.strftime('%I:%M %p')}\n"

        if cancellation_reason:
            message += f"📝 *Reason:* {cancellation_reason}\n"

        message += "\nWould you like to book a new appointment?"

        return message

    def _format_rescheduling_message(
        self,
        appointment: Dict[str, Any],
        old_datetime: str,
        new_datetime: str
    ) -> str:
        """Format rescheduling confirmation message"""
        old_dt = datetime.fromisoformat(old_datetime)
        new_dt = datetime.fromisoformat(new_datetime)

        message = f"🔄 *Appointment Rescheduled*\n\n"
        message += f"Your appointment has been rescheduled:\n\n"
        message += f"📍 *Service:* {appointment.get('service_name', 'Appointment')}\n"
        message += f"❌ *Old Time:* {old_dt.strftime('%A, %B %d at %I:%M %p')}\n"
        message += f"✅ *New Time:* {new_dt.strftime('%A, %B %d at %I:%M %p')}\n"

        if appointment.get('doctor_name'):
            message += f"👨‍⚕️ *Doctor:* Dr. {appointment['doctor_name']}\n"

        message += f"\n📌 *Appointment ID:* {appointment['id'][:8]}...\n"
        message += "\nPlease confirm the new time works for you."

        return message

    def _format_confirmation_request(
        self,
        slot_details: Dict[str, Any],
        timeout_minutes: int
    ) -> str:
        """Format appointment confirmation request message"""
        dt = datetime.fromisoformat(slot_details['datetime'])

        message = f"🔔 *Appointment Confirmation Request*\n\n"
        message += f"We're holding this appointment slot for you:\n\n"
        message += f"📍 *Service:* {slot_details.get('service_name', 'Appointment')}\n"
        message += f"📅 *Date:* {dt.strftime('%A, %B %d, %Y')}\n"
        message += f"🕐 *Time:* {dt.strftime('%I:%M %p')}\n"

        if slot_details.get('doctor_name'):
            message += f"👨‍⚕️ *Doctor:* Dr. {slot_details['doctor_name']}\n"

        if slot_details.get('duration_minutes'):
            message += f"⏱️ *Duration:* {slot_details['duration_minutes']} minutes\n"

        message += f"\n⚠️ *This slot will be held for {timeout_minutes} minutes.*\n"
        message += "\nPlease confirm or cancel below:"

        return message

    def _create_confirmation_buttons(self, appointment_id: str) -> List[Dict[str, str]]:
        """Create confirmation buttons for appointment"""
        return [
            {
                "id": f"confirm_{appointment_id}",
                "title": "✅ Confirm Attendance"
            },
            {
                "id": f"reschedule_{appointment_id}",
                "title": "🔄 Reschedule"
            },
            {
                "id": f"cancel_{appointment_id}",
                "title": "❌ Cancel"
            }
        ]

    def _create_reminder_buttons(self, appointment_id: str) -> List[Dict[str, str]]:
        """Create reminder buttons for appointment"""
        return [
            {
                "id": f"confirm_{appointment_id}",
                "title": "✅ I'll be there"
            },
            {
                "id": f"reschedule_{appointment_id}",
                "title": "🔄 Need to reschedule"
            }
        ]

    async def _log_confirmation_sent(
        self,
        appointment_id: str,
        channel: str,
        message_id: Optional[str] = None
    ):
        """Log that confirmation was sent"""
        try:
            self.supabase.table('appointment_confirmations').insert({
                "appointment_id": appointment_id,
                "channel": channel,
                "message_id": message_id,
                "sent_at": datetime.now().isoformat(),
                "type": "confirmation"
            }).execute()
        except Exception as e:
            logger.warning(f"Could not log confirmation: {str(e)}")

    async def _log_reminder_sent(
        self,
        appointment_id: str,
        hours_before: int,
        message_id: Optional[str] = None
    ):
        """Log that reminder was sent"""
        try:
            self.supabase.table('appointment_reminders').insert({
                "appointment_id": appointment_id,
                "hours_before": hours_before,
                "message_id": message_id,
                "sent_at": datetime.now().isoformat(),
                "channel": "whatsapp"
            }).execute()
        except Exception as e:
            logger.warning(f"Could not log reminder: {str(e)}")


class AppointmentReminderScheduler:
    """
    Scheduler for automated appointment reminders.
    """

    def __init__(self, clinic_id: str):
        """
        Initialize the reminder scheduler.

        Args:
            clinic_id: ID of the clinic
        """
        self.clinic_id = clinic_id
        self.confirmation_service = WhatsAppConfirmationService(clinic_id)
        self.supabase = create_supabase_client()
        self.is_running = False

    async def start(self):
        """Start the reminder scheduler"""
        if self.is_running:
            return

        self.is_running = True
        logger.info(f"Starting appointment reminder scheduler for clinic {self.clinic_id}")

        while self.is_running:
            try:
                await self._check_and_send_reminders()
                await asyncio.sleep(300)  # Check every 5 minutes
            except Exception as e:
                logger.error(f"Error in reminder scheduler: {str(e)}")
                await asyncio.sleep(60)  # Wait 1 minute on error

    async def stop(self):
        """Stop the reminder scheduler"""
        self.is_running = False
        logger.info(f"Stopping appointment reminder scheduler for clinic {self.clinic_id}")

    async def _check_and_send_reminders(self):
        """Check for appointments needing reminders and send them"""
        try:
            # Find appointments in the next 24-48 hours that haven't had reminders sent
            tomorrow = datetime.now() + timedelta(days=1)
            day_after = datetime.now() + timedelta(days=2)

            result = self.supabase.table('healthcare.appointments').select('*').eq(
                'clinic_id', self.clinic_id
            ).eq('status', 'scheduled').gte(
                'scheduled_at', tomorrow.isoformat()
            ).lt('scheduled_at', day_after.isoformat()).execute()

            if not result.data:
                return

            for appointment in result.data:
                # Check if reminder already sent
                reminder_check = self.supabase.table('appointment_reminders').select('id').eq(
                    'appointment_id', appointment['id']
                ).execute()

                if not reminder_check.data:
                    # Send reminder
                    await self.confirmation_service.send_appointment_reminder(
                        appointment_id=appointment['id'],
                        patient_phone=appointment['patient_phone'],
                        hours_before=24
                    )

                    logger.info(f"Sent reminder for appointment {appointment['id']}")

        except Exception as e:
            logger.error(f"Error checking for reminders: {str(e)}")