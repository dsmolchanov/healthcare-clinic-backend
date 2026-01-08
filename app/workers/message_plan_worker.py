"""
SOTA Communication Plan Worker (Phase 6)

Polls appointment_message_plan table and sends due messages.
Features:
- Atomic claiming with 'processing' status to prevent double-sends
- All messages sent through outbox for reliability
- 24h rule compliance with template detection
- Stuck message recovery
"""
import asyncio
import os
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any
from uuid import uuid4

from app.db.supabase_client import get_supabase_client
from app.services.reminder_templates import (
    format_confirmation_message,
    format_reminder_24h,
    format_wayfinding_2h,
    format_date_localized,
)
from app.services.outbox_service import (
    write_to_outbox,
    write_location_to_outbox,
    write_template_to_outbox,
)

logger = logging.getLogger(__name__)

# How long before a 'processing' message is considered stuck
STUCK_THRESHOLD_MINUTES = 5

# WhatsApp 24h session window
SESSION_WINDOW_HOURS = 24


def should_use_template(last_message_at: Optional[str]) -> bool:
    """
    Determine if we need to use WhatsApp Templates (outside 24h session window).

    WhatsApp Business API requires templates for proactive messages
    sent outside the 24-hour customer service window.

    Args:
        last_message_at: ISO timestamp of last patient message, or None

    Returns:
        True if templates required (outside session window)
    """
    if not last_message_at:
        # No previous message = proactive outreach = needs template
        return True

    try:
        if isinstance(last_message_at, str):
            last_msg_time = datetime.fromisoformat(last_message_at.replace('Z', '+00:00'))
        else:
            last_msg_time = last_message_at

        now = datetime.now(timezone.utc)
        time_since_last_msg = now - last_msg_time

        # Outside 24h window = needs template
        return time_since_last_msg > timedelta(hours=SESSION_WINDOW_HOURS)
    except Exception:
        # If we can't parse, assume we need templates (safer)
        return True


class MessagePlanWorker:
    """
    Worker that processes scheduled messages from appointment_message_plan.

    Features:
    - Atomic claiming with 'processing' status prevents double-sends
    - All messages go through outbox (text, location, buttons, templates)
    - 24h rule: Uses templates for proactive messages outside session window
    - Recovery of stuck 'processing' messages after timeout
    """

    def __init__(self):
        self.poll_interval = float(os.getenv('MESSAGE_PLAN_POLL_INTERVAL', '30'))
        self.batch_size = int(os.getenv('MESSAGE_PLAN_BATCH_SIZE', '20'))
        self.is_running = False
        self.supabase = get_supabase_client()

    async def start(self):
        """Start the message plan worker loop."""
        self.is_running = True
        logger.info("MessagePlanWorker started")

        while self.is_running:
            try:
                # First, recover any stuck processing messages
                await self._recover_stuck_messages()

                # Then process due messages
                await self._process_due_messages()

            except Exception as e:
                logger.error(f"MessagePlanWorker error: {e}")

            await asyncio.sleep(self.poll_interval)

    async def stop(self):
        """Stop the worker."""
        self.is_running = False
        logger.info("MessagePlanWorker stopped")

    async def _recover_stuck_messages(self):
        """
        Reset messages stuck in 'processing' status.

        If a worker crashes while processing, the message stays stuck.
        Reset to 'scheduled' if stuck for more than STUCK_THRESHOLD_MINUTES.
        """
        threshold = datetime.now(timezone.utc) - timedelta(minutes=STUCK_THRESHOLD_MINUTES)

        try:
            result = self.supabase.schema('healthcare').table('appointment_message_plan').update({
                'status': 'scheduled'
            }).eq('status', 'processing').lt(
                'updated_at', threshold.isoformat()
            ).execute()

            if result.data:
                logger.warning(f"Recovered {len(result.data)} stuck messages")
        except Exception as e:
            logger.error(f"Failed to recover stuck messages: {e}")

    async def _process_due_messages(self):
        """Find and process messages that are due to be sent."""
        now = datetime.now(timezone.utc)

        # Fetch due messages (only 'scheduled', not 'processing')
        result = self.supabase.schema('healthcare').table('appointment_message_plan').select(
            'id'
        ).eq('status', 'scheduled').lte(
            'scheduled_at', now.isoformat()
        ).order('scheduled_at').limit(self.batch_size).execute()

        message_ids = [m['id'] for m in (result.data or [])]

        for msg_id in message_ids:
            # CRITICAL: Atomic claim - only process if we successfully claim it
            if await self._claim_message(msg_id):
                await self._process_message(msg_id)

    async def _claim_message(self, msg_id: str) -> bool:
        """
        Atomically claim a message for processing.

        Uses conditional UPDATE to ensure only one worker can claim each message.
        Returns True if we successfully claimed it, False if another worker got it.
        """
        try:
            result = self.supabase.schema('healthcare').table('appointment_message_plan').update({
                'status': 'processing',
                'updated_at': datetime.now(timezone.utc).isoformat()
            }).eq('id', msg_id).eq('status', 'scheduled').execute()

            # If we updated exactly one row, we claimed it
            return len(result.data or []) == 1
        except Exception as e:
            logger.error(f"Failed to claim message {msg_id}: {e}")
            return False

    async def _process_message(self, msg_id: str):
        """Process a single claimed message."""
        try:
            # Fetch full message data now that we've claimed it
            result = self.supabase.schema('healthcare').table('appointment_message_plan').select(
                '*'
            ).eq('id', msg_id).execute()

            if not result.data:
                return

            msg = result.data[0]
            appointment_id = msg['appointment_id']
            message_type = msg['message_type']
            template_key = msg['template_key']

            # Get appointment and clinic data
            appointment = await self._get_appointment(appointment_id)
            if not appointment:
                await self._update_status(msg_id, 'failed', 'Appointment not found')
                return

            # Check if appointment was cancelled
            if appointment.get('status') == 'cancelled':
                await self._update_status(msg_id, 'cancelled', 'Appointment cancelled')
                return

            clinic = await self._get_clinic(appointment['clinic_id'])
            if not clinic:
                await self._update_status(msg_id, 'failed', 'Clinic not found')
                return

            patient_phone = appointment.get('patient_phone')
            lang = appointment.get('language', 'ru')
            last_message_at = appointment.get('last_patient_message_at')

            if not patient_phone:
                await self._update_status(msg_id, 'failed', 'No patient phone')
                return

            # Get WhatsApp instance for this clinic
            instance_name = await self._get_instance_for_clinic(clinic['id'])
            if not instance_name:
                await self._update_status(msg_id, 'failed', 'No WhatsApp instance')
                return

            # Determine if we need to use templates (24h rule)
            use_template = should_use_template(last_message_at)

            # Generate message content
            message_text = self._generate_message(
                template_key, appointment, clinic, lang
            )

            # ALL messages go through outbox for reliability
            outbox_msg_id = str(uuid4())
            conversation_id = appointment.get('conversation_id', '')
            success = False

            if use_template and message_type in ('reminder_24h', 'reminder_2h'):
                # Use WhatsApp Template for proactive messages outside session
                template_data = self._build_template_data(
                    message_type, appointment, clinic, lang
                )
                success = await write_template_to_outbox(
                    instance_name=instance_name,
                    to_number=patient_phone,
                    template_name=template_data.get('name', 'appointment_reminder'),
                    conversation_id=conversation_id,
                    clinic_id=clinic['id'],
                    language=template_data.get('language', 'en'),
                    components=template_data.get('components'),
                    message_id=outbox_msg_id
                )
            else:
                # Use session message (within 24h window or confirmation)
                success = await write_to_outbox(
                    instance_name=instance_name,
                    to_number=patient_phone,
                    message_text=message_text,
                    conversation_id=conversation_id,
                    clinic_id=clinic['id'],
                    message_id=outbox_msg_id
                )

            if success:
                # For wayfinding, also send location card via outbox
                if message_type == 'reminder_2h':
                    location_data = clinic.get('location_data', {}) or {}
                    if location_data.get('lat') and location_data.get('lng'):
                        loc_msg_id = str(uuid4())
                        await write_location_to_outbox(
                            instance_name=instance_name,
                            to_number=patient_phone,
                            lat=location_data['lat'],
                            lng=location_data['lng'],
                            name=clinic.get('name', ''),
                            address=clinic.get('address', ''),
                            conversation_id=conversation_id,
                            clinic_id=clinic['id'],
                            message_id=loc_msg_id
                        )

                await self._update_status(msg_id, 'sent', provider_message_id=outbox_msg_id)

                # Log to appointment_reminders for backward compatibility
                await self._log_reminder(appointment_id, message_type, outbox_msg_id)
            else:
                # Increment retry count and reset to scheduled if under max
                retry_count = msg.get('retry_count', 0)
                max_retries = msg.get('max_retries', 3)
                if retry_count < max_retries:
                    await self._retry_message(msg_id, retry_count)
                else:
                    await self._update_status(msg_id, 'failed', 'Max retries exceeded')

        except Exception as e:
            logger.error(f"Failed to process message {msg_id}: {e}")
            await self._update_status(msg_id, 'failed', str(e))

    def _build_template_data(
        self,
        message_type: str,
        appointment: Dict,
        clinic: Dict,
        lang: str
    ) -> Dict:
        """Build template data for WhatsApp Template messages."""
        scheduled_at = appointment.get('scheduled_at', '')
        if isinstance(scheduled_at, str):
            dt = datetime.fromisoformat(scheduled_at.replace('Z', '+00:00'))
        else:
            dt = scheduled_at

        formatted_date = format_date_localized(dt, lang)

        if message_type == 'reminder_24h':
            return {
                'name': 'appointment_reminder_24h',
                'language': lang,
                'components': [{
                    'type': 'body',
                    'parameters': [
                        {'type': 'text', 'text': appointment.get('patient_name', '')},
                        {'type': 'text', 'text': formatted_date},
                        {'type': 'text', 'text': clinic.get('name', '')}
                    ]
                }]
            }
        elif message_type == 'reminder_2h':
            return {
                'name': 'wayfinding_2h',
                'language': lang,
                'components': [{
                    'type': 'body',
                    'parameters': [
                        {'type': 'text', 'text': clinic.get('name', '')},
                        {'type': 'text', 'text': clinic.get('address', '')}
                    ]
                }]
            }
        return {}

    async def _retry_message(self, msg_id: str, current_retry: int):
        """Reset message to scheduled with incremented retry count."""
        self.supabase.schema('healthcare').table('appointment_message_plan').update({
            'status': 'scheduled',
            'retry_count': current_retry + 1,
            'updated_at': datetime.now(timezone.utc).isoformat()
        }).eq('id', msg_id).execute()

    def _generate_message(
        self,
        template_key: str,
        appointment: Dict,
        clinic: Dict,
        lang: str
    ) -> str:
        """Generate message content from template."""
        if template_key == 'booking_confirmation':
            return format_confirmation_message(appointment, clinic, lang)
        elif template_key == 'reminder_24h':
            return format_reminder_24h(appointment, clinic, lang)
        elif template_key == 'wayfinding_2h':
            return format_wayfinding_2h(appointment, clinic, lang)
        else:
            return f"Reminder for your appointment"

    async def _update_status(
        self,
        msg_id: str,
        status: str,
        error_message: Optional[str] = None,
        provider_message_id: Optional[str] = None
    ):
        """Update message plan status with optional provider message ID."""
        update_data = {
            'status': status,
            'updated_at': datetime.now(timezone.utc).isoformat()
        }
        if status == 'sent':
            update_data['sent_at'] = datetime.now(timezone.utc).isoformat()
        if error_message:
            update_data['error_message'] = error_message
        if provider_message_id:
            update_data['provider_message_id'] = provider_message_id

        self.supabase.schema('healthcare').table('appointment_message_plan').update(
            update_data
        ).eq('id', msg_id).execute()

    async def _log_reminder(
        self,
        appointment_id: str,
        reminder_type: str,
        message_id: str
    ):
        """Log to appointment_reminders for deduplication."""
        try:
            self.supabase.schema('healthcare').table('appointment_reminders').upsert({
                'appointment_id': appointment_id,
                'reminder_type': reminder_type,
                'message_id': message_id,
                'channel': 'whatsapp',
                'sent_at': datetime.now(timezone.utc).isoformat(),
                'status': 'sent'
            }, on_conflict='appointment_id,reminder_type').execute()
        except Exception as e:
            logger.warning(f"Failed to log reminder: {e}")

    async def _get_appointment(self, appointment_id: str) -> Optional[Dict]:
        """Fetch appointment with patient details."""
        result = self.supabase.schema('healthcare').table('appointments').select(
            '*, patients(phone, language, communication_preferences)'
        ).eq('id', appointment_id).limit(1).execute()

        if result.data:
            appt = result.data[0]
            patient = appt.get('patients', {}) or {}
            appt['patient_phone'] = patient.get('phone') or appt.get('patient_phone')
            appt['language'] = patient.get('language', 'ru')
            return appt
        return None

    async def _get_clinic(self, clinic_id: str) -> Optional[Dict]:
        """Fetch clinic with location data."""
        result = self.supabase.schema('healthcare').table('clinics').select(
            'id, name, address, city, state, location_data, entry_instructions_i18n'
        ).eq('id', clinic_id).limit(1).execute()

        return result.data[0] if result.data else None

    async def _get_instance_for_clinic(self, clinic_id: str) -> Optional[str]:
        """Get WhatsApp instance name for clinic."""
        result = self.supabase.schema('healthcare').table('integrations').select(
            'instance_name'
        ).eq('clinic_id', clinic_id).eq('channel', 'whatsapp').eq(
            'status', 'connected'
        ).limit(1).execute()

        return result.data[0]['instance_name'] if result.data else None
