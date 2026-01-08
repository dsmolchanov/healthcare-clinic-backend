# clinics/backend/app/services/escalation_handler.py

import logging
import asyncio
from typing import Dict, Any, Optional, List
from datetime import datetime, timezone
import os

logger = logging.getLogger(__name__)


class EscalationHandler:
    """Handles conversation escalation to human agents with HITL notifications"""

    HOLDING_MESSAGES = [
        "Thank you for your patience. We're working on your request and will have an answer for you shortly.",
        "We appreciate your patience as we're consulting with our team to provide you with the best response.",
        "Your request is being reviewed by our specialists. We'll get back to you as soon as possible.",
        "We're looking into this for you. Thank you for your understanding.",
    ]

    # Escalation reasons that trigger admin notifications
    NOTIFICATION_TRIGGERS = [
        'emergency',
        'handoff_requested',
        'scheduling_failed',
        'complex_medical',
        'repeated_failures',
        'user_requested'
    ]

    def __init__(self):
        self.supabase = None  # Will be injected

    async def escalate_conversation(
        self,
        session_id: str,
        reason: str,
        escalated_to_user_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        clinic_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Escalate a conversation to human agent with HITL control mode switch.

        Now includes:
        1. Setting control_mode='human' on the session
        2. Looking up clinic's hitl_admin_numbers
        3. Sending WhatsApp notification to admin numbers

        Returns:
            {
                'escalated': bool,
                'escalation_id': str,
                'assigned_to': Optional[str],
                'holding_message': str,
                'admin_notified': bool
            }
        """

        logger.warning(f"🚨 Escalating conversation {session_id}: {reason}")

        # Update session status with HITL control mode
        update_data = {
            'turn_status': 'escalated',
            'escalation_reason': reason,
            'escalated_to_user_id': escalated_to_user_id,
            'updated_at': datetime.now(timezone.utc).isoformat(),
            # HITL Phase 4: Set control mode when escalating
            'control_mode': 'human',
            'locked_at': datetime.now(timezone.utc).isoformat(),
            'lock_reason': reason,
            'lock_source': 'auto_escalation'
        }

        admin_notified = False

        try:
            from app.memory.conversation_memory import get_memory_manager
            manager = get_memory_manager()

            manager.supabase.table('conversation_sessions').update(
                update_data
            ).eq('id', session_id).execute()

            # Create escalation record (for tracking)
            escalation_record = {
                'session_id': session_id,
                'reason': reason,
                'assigned_to': escalated_to_user_id,
                'metadata': metadata or {},
                'created_at': datetime.now(timezone.utc).isoformat(),
                'status': 'pending'
            }

            # HITL Phase 4: Send WhatsApp notification to admin numbers
            if clinic_id:
                try:
                    admin_notified = await self._notify_admin_numbers(
                        clinic_id=clinic_id,
                        session_id=session_id,
                        reason=reason,
                        metadata=metadata
                    )
                except Exception as notify_error:
                    logger.error(f"Failed to notify admin numbers: {notify_error}")

            logger.info(f"✅ Conversation escalated successfully (admin_notified={admin_notified})")

            # Return holding message
            import random
            holding_message = random.choice(self.HOLDING_MESSAGES)

            return {
                'escalated': True,
                'escalation_id': session_id,
                'assigned_to': escalated_to_user_id,
                'holding_message': holding_message,
                'admin_notified': admin_notified
            }

        except Exception as e:
            logger.error(f"Failed to escalate conversation: {e}", exc_info=True)
            return {
                'escalated': False,
                'error': str(e),
                'holding_message': self.HOLDING_MESSAGES[0],  # Default message
                'admin_notified': False
            }

    async def _notify_admin_numbers(
        self,
        clinic_id: str,
        session_id: str,
        reason: str,
        metadata: Optional[Dict[str, Any]] = None
    ) -> bool:
        """
        Send WhatsApp notification to clinic's admin numbers.

        Args:
            clinic_id: UUID of the clinic
            session_id: Session being escalated
            reason: Escalation reason
            metadata: Additional context

        Returns:
            True if at least one admin was notified successfully
        """
        from app.db.supabase_client import get_supabase_client

        try:
            supabase = get_supabase_client()

            # Get clinic settings including admin numbers and notification preferences
            result = supabase.schema('healthcare').table('clinics').select(
                'hitl_admin_numbers, hitl_notify_on, name'
            ).eq('id', clinic_id).single().execute()

            if not result.data:
                logger.warning(f"No clinic found for ID {clinic_id}")
                return False

            clinic_data = result.data
            admin_numbers = clinic_data.get('hitl_admin_numbers') or []
            notify_on = clinic_data.get('hitl_notify_on') or self.NOTIFICATION_TRIGGERS
            clinic_name = clinic_data.get('name', 'Unknown Clinic')

            if not admin_numbers:
                logger.info(f"No admin numbers configured for clinic {clinic_id}")
                return False

            # Check if this reason should trigger a notification
            should_notify = self._should_notify_for_reason(reason, notify_on)
            if not should_notify:
                logger.info(f"Escalation reason '{reason}' not in notify_on list, skipping notification")
                return False

            # Get patient info for the notification
            patient_phone = metadata.get('from_phone', 'Unknown') if metadata else 'Unknown'
            patient_name = metadata.get('patient_name', patient_phone)

            # Build notification message
            notification_text = self._build_admin_notification(
                clinic_name=clinic_name,
                patient_name=patient_name,
                session_id=session_id,
                reason=reason
            )

            # Get instance name for sending
            instance_result = supabase.schema('healthcare').table('integrations').select(
                'config'
            ).eq('organization_id', clinic_id).eq('type', 'whatsapp').single().execute()

            instance_name = None
            if instance_result.data:
                instance_name = instance_result.data.get('config', {}).get('instance_name')

            if not instance_name:
                logger.warning(f"No WhatsApp instance found for clinic {clinic_id}")
                return False

            # Send notification to each admin number (in parallel)
            notify_tasks = []
            for admin_number in admin_numbers:
                notify_tasks.append(
                    self._send_admin_notification(
                        instance_name=instance_name,
                        admin_number=admin_number,
                        message=notification_text,
                        clinic_id=clinic_id
                    )
                )

            results = await asyncio.gather(*notify_tasks, return_exceptions=True)

            # Count successful notifications
            success_count = sum(1 for r in results if r is True)
            logger.info(
                f"Admin notifications sent: {success_count}/{len(admin_numbers)} successful"
            )

            return success_count > 0

        except Exception as e:
            logger.error(f"Error sending admin notifications: {e}", exc_info=True)
            return False

    def _should_notify_for_reason(self, reason: str, notify_on: List[str]) -> bool:
        """Check if the escalation reason matches any notification trigger."""
        reason_lower = reason.lower()

        for trigger in notify_on:
            trigger_lower = trigger.lower()
            if trigger_lower in reason_lower or reason_lower in trigger_lower:
                return True

        # Check for common keywords
        if 'emergency' in reason_lower or 'urgent' in reason_lower:
            return 'emergency' in [t.lower() for t in notify_on]

        if 'human' in reason_lower or 'agent' in reason_lower or 'handoff' in reason_lower:
            return 'handoff_requested' in [t.lower() for t in notify_on]

        return False

    def _build_admin_notification(
        self,
        clinic_name: str,
        patient_name: str,
        session_id: str,
        reason: str
    ) -> str:
        """Build the notification message for admin staff."""
        # Use emojis sparingly for clarity
        base_url = os.getenv('FRONTEND_URL', 'https://plaintalk.io')
        session_link = f"{base_url}/conversations/{session_id}"

        return f"""🚨 *Escalation Alert*

*Clinic:* {clinic_name}
*Patient:* {patient_name}
*Reason:* {reason}

The AI assistant has transferred this conversation to human control.

📱 Open conversation:
{session_link}

Reply to this message or open the link to take over the conversation."""

    async def _send_admin_notification(
        self,
        instance_name: str,
        admin_number: str,
        message: str,
        clinic_id: str
    ) -> bool:
        """Send a notification message to a single admin number."""
        try:
            from app.services.whatsapp_queue.evolution_client import send_text

            result = await send_text(
                instance=instance_name,
                to_number=admin_number,
                text=message
            )

            if isinstance(result, dict):
                return result.get('success', False)
            return bool(result)

        except Exception as e:
            logger.error(f"Failed to send notification to {admin_number}: {e}")
            return False

    async def check_if_should_escalate(
        self,
        conversation_context: str,
        user_message: str
    ) -> Dict[str, Any]:
        """
        Determine if conversation should be escalated

        Uses heuristics:
        - User explicitly asks for human
        - Repeated failed queries (detected in context)
        - Complex medical questions

        Returns:
            {
                'should_escalate': bool,
                'reason': str,
                'confidence': float
            }
        """

        # Simple keyword matching for now
        escalation_keywords = [
            'speak to human', 'talk to person', 'real person',
            'speak to agent', 'human agent', 'representative',
            'manager', 'supervisor',
            # Multilingual
            'hablar con humano', 'persona real',  # Spanish
            'לדבר עם אדם', 'נציג אמיתי'  # Hebrew
        ]

        user_lower = user_message.lower()

        for keyword in escalation_keywords:
            if keyword in user_lower:
                logger.warning(f"Escalation keyword detected: {keyword}")
                return {
                    'should_escalate': True,
                    'reason': f"User requested human agent (keyword: '{keyword}')",
                    'confidence': 0.95
                }

        # Check for repeated questions (TODO: implement with LLM)
        # Check for complex medical terms (TODO: implement with LLM)

        return {
            'should_escalate': False,
            'reason': None,
            'confidence': 0.0
        }

    async def get_holding_message(self, session_id: str) -> str:
        """Get an appropriate holding message for escalated conversation"""
        import random
        return random.choice(self.HOLDING_MESSAGES)
