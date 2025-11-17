"""
Proactive Escalation for Failed Booking Confirmations
P1 Enhancement #2: Alert ops team when critical messages fail

Runs periodically to detect failed booking confirmation messages and
alert operations team for manual intervention.
"""

import asyncio
import logging
import os
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Optional

from app.db.supabase_client import get_supabase_client

logger = logging.getLogger(__name__)


class FailedConfirmationEscalation:
    """
    Monitors for failed booking confirmation messages
    Creates high-priority alerts for ops team
    """

    def __init__(self):
        self.supabase = get_supabase_client(schema='healthcare')
        self.check_interval_minutes = int(os.getenv('ESCALATION_CHECK_INTERVAL', '10'))
        self.failure_threshold_minutes = int(os.getenv('ESCALATION_FAILURE_THRESHOLD', '60'))
        self.min_retry_count = int(os.getenv('ESCALATION_MIN_RETRIES', '3'))
        self.slack_webhook_url = os.getenv('SLACK_OPS_WEBHOOK_URL')
        self.running = False

        logger.info(
            f"FailedConfirmationEscalation initialized: check_interval={self.check_interval_minutes}min, "
            f"failure_threshold={self.failure_threshold_minutes}min, min_retries={self.min_retry_count}"
        )

    async def start(self):
        """Start escalation monitoring loop"""
        self.running = True
        logger.info("FailedConfirmationEscalation started")

        while self.running:
            try:
                await self._check_and_escalate()

                # Wait for next check interval
                await asyncio.sleep(self.check_interval_minutes * 60)

            except Exception as e:
                logger.error(f"Escalation check error: {e}", exc_info=True)
                await asyncio.sleep(60)  # Brief pause on error

    async def stop(self):
        """Stop escalation monitoring"""
        self.running = False
        logger.info("FailedConfirmationEscalation stopped")

    async def _check_and_escalate(self):
        """
        Check for failed booking confirmations and escalate
        """
        try:
            # Find failed confirmations older than threshold
            threshold_time = datetime.now(timezone.utc) - timedelta(minutes=self.failure_threshold_minutes)

            result = self.supabase.table('outbound_messages').select(
                'id, conversation_id, clinic_id, to_number, message_text, failed_at, retry_count, created_at'
            ).eq(
                'delivery_status', 'failed'
            ).lt(
                'failed_at', threshold_time.isoformat()
            ).gte(
                'retry_count', self.min_retry_count
            ).execute()

            if not result.data:
                return

            # Filter to booking confirmations only
            failed_confirmations = self._filter_booking_confirmations(result.data)

            if not failed_confirmations:
                return

            logger.critical(
                f"Found {len(failed_confirmations)} failed booking confirmations requiring escalation"
            )

            # Create alerts for each failed confirmation
            for msg in failed_confirmations:
                await self._escalate_message(msg)

            logger.info(f"Escalated {len(failed_confirmations)} failed booking confirmations")

        except Exception as e:
            logger.error(f"Failed to check for escalations: {e}", exc_info=True)

    def _filter_booking_confirmations(self, messages: List[Dict]) -> List[Dict]:
        """
        Filter messages to only booking confirmations

        Identifies messages containing booking confirmation keywords
        """
        confirmation_keywords = [
            '✅ Запись создана',
            '✅ Appointment created',
            'подтверждена',
            'confirmed',
            'забронировал',
            'reserved'
        ]

        filtered = []
        for msg in messages:
            text = msg.get('message_text', '').lower()
            if any(keyword.lower() in text for keyword in confirmation_keywords):
                filtered.append(msg)

        return filtered

    async def _escalate_message(self, msg: Dict[str, Any]):
        """
        Create escalation alert for failed message

        Args:
            msg: Failed message dict
        """
        message_id = msg['id']
        patient_phone = msg['to_number']
        clinic_id = msg['clinic_id']
        failed_at = msg['failed_at']
        retry_count = msg['retry_count']
        message_preview = msg['message_text'][:100]

        logger.critical(
            f"🚨 CRITICAL: Failed booking confirmation for {patient_phone} "
            f"(clinic={clinic_id}, msg_id={message_id}, retries={retry_count}, "
            f"failed_at={failed_at})"
        )

        # Send to ops monitoring
        alert_data = {
            'severity': 'critical',
            'title': 'Failed Booking Confirmation',
            'details': {
                'patient_phone': patient_phone,
                'clinic_id': clinic_id,
                'message_id': message_id,
                'failed_at': failed_at,
                'retry_count': retry_count,
                'message_preview': message_preview
            },
            'action_required': 'Manually call patient to confirm appointment'
        }

        await self._send_ops_alert(alert_data)

    async def _send_ops_alert(self, alert_data: Dict[str, Any]):
        """
        Send alert to ops team (Slack, PagerDuty, etc.)

        Args:
            alert_data: Alert details dictionary
        """
        if not self.slack_webhook_url:
            logger.warning("SLACK_OPS_WEBHOOK_URL not configured, skipping alert")
            return

        try:
            import aiohttp

            slack_message = {
                "text": f"🚨 {alert_data['title']}",
                "blocks": [{
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": (
                            f"*{alert_data['title']}*\n\n"
                            f"Patient: `{alert_data['details']['patient_phone']}`\n"
                            f"Clinic: `{alert_data['details']['clinic_id']}`\n"
                            f"Failed: {alert_data['details']['failed_at']}\n"
                            f"Retries: {alert_data['details']['retry_count']}\n"
                            f"Message ID: `{alert_data['details']['message_id']}`\n\n"
                            f"*Action*: {alert_data['action_required']}"
                        )
                    }
                }]
            }

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self.slack_webhook_url,
                    json=slack_message,
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as response:
                    if response.status == 200:
                        logger.info(f"Slack alert sent successfully for message {alert_data['details']['message_id']}")
                    else:
                        logger.error(f"Slack alert failed: {response.status}")

        except Exception as e:
            logger.error(f"Failed to send Slack alert: {e}", exc_info=True)


# Singleton instance
_escalation: Optional['FailedConfirmationEscalation'] = None


def get_escalation_worker() -> FailedConfirmationEscalation:
    """Get or create singleton escalation worker instance"""
    global _escalation
    if _escalation is None:
        _escalation = FailedConfirmationEscalation()
    return _escalation


async def start_escalation_worker():
    """Start the escalation worker"""
    worker = get_escalation_worker()
    await worker.start()


async def stop_escalation_worker():
    """Stop the escalation worker"""
    worker = get_escalation_worker()
    await worker.stop()
