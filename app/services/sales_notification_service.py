"""
Sales Notification Service

Handles multi-channel notifications for sales discovery call bookings.
Sends notifications based on rep preferences:
- Email (always available)
- WhatsApp (if phone configured and enabled)
- Google Calendar (if connected and enabled)
"""

import os
import logging
import asyncio
from typing import Optional, Dict, Any
from datetime import datetime

from supabase import create_client, Client
from supabase.client import ClientOptions

logger = logging.getLogger(__name__)


class SalesNotificationService:
    """
    Service for sending multi-channel notifications to sales reps.
    """

    def __init__(self):
        # Initialize with sales schema
        sales_options = ClientOptions(schema='sales')
        supabase_url = os.environ.get("SUPABASE_URL", "")
        supabase_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")

        self.supabase: Client = create_client(
            supabase_url,
            supabase_key,
            options=sales_options
        )

        # Evolution API config for WhatsApp
        self.evolution_url = os.environ.get("EVOLUTION_API_URL", "")
        self.evolution_key = os.environ.get("EVOLUTION_API_KEY", "")

    async def notify_booking(
        self,
        discovery_call_id: str,
        rep_id: str,
        lead_name: str,
        lead_phone: str,
        company_name: Optional[str],
        scheduled_at: datetime,
        duration_minutes: int = 30,
        notes: Optional[str] = None,
        timezone: str = "Europe/Moscow"
    ) -> Dict[str, Any]:
        """
        Send notifications to sales rep based on their preferences.

        Args:
            discovery_call_id: UUID of the discovery call
            rep_id: Sales rep UUID
            lead_name: Lead's name
            lead_phone: Lead's phone number
            company_name: Lead's company name
            scheduled_at: Scheduled datetime
            duration_minutes: Call duration
            notes: Meeting notes
            timezone: Timezone for display

        Returns:
            Dict with results for each notification channel
        """
        results = {
            'email': {'sent': False, 'error': None},
            'whatsapp': {'sent': False, 'error': None},
            'google_calendar': {'sent': False, 'error': None}
        }

        try:
            # Get rep info with preferences
            rep_result = self.supabase.table('reps').select(
                'id, name, email, notification_preferences, whatsapp_phone, calendar_integration_id'
            ).eq('id', rep_id).single().execute()

            if not rep_result.data:
                logger.error(f"Rep not found: {rep_id}")
                return results

            rep = rep_result.data
            prefs = rep.get('notification_preferences', {})

            # Format display time
            import pytz
            tz = pytz.timezone(timezone)
            display_time = scheduled_at.astimezone(tz).strftime("%d.%m.%Y в %H:%M")
            day_names_ru = ['понедельник', 'вторник', 'среда', 'четверг', 'пятница', 'суббота', 'воскресенье']
            day_name = day_names_ru[scheduled_at.weekday()]

            # Send email notification
            if prefs.get('email', True) and rep.get('email'):
                try:
                    email_sent = await self._send_email_notification(
                        rep=rep,
                        lead_name=lead_name,
                        lead_phone=lead_phone,
                        company_name=company_name,
                        display_time=display_time,
                        day_name=day_name,
                        duration_minutes=duration_minutes,
                        notes=notes
                    )
                    results['email']['sent'] = email_sent
                except Exception as e:
                    logger.error(f"Email notification failed: {e}")
                    results['email']['error'] = str(e)

            # Send WhatsApp notification
            if prefs.get('whatsapp', False) and rep.get('whatsapp_phone'):
                try:
                    whatsapp_sent = await self._send_whatsapp_notification(
                        rep=rep,
                        lead_name=lead_name,
                        lead_phone=lead_phone,
                        company_name=company_name,
                        display_time=display_time,
                        day_name=day_name,
                        duration_minutes=duration_minutes
                    )
                    results['whatsapp']['sent'] = whatsapp_sent
                except Exception as e:
                    logger.error(f"WhatsApp notification failed: {e}")
                    results['whatsapp']['error'] = str(e)

            # Create Google Calendar event
            if prefs.get('google_calendar', False) and rep.get('calendar_integration_id'):
                try:
                    event_id = await self._create_calendar_event(
                        call_id=discovery_call_id,
                        rep_id=rep_id,
                        lead_name=lead_name,
                        lead_phone=lead_phone,
                        company_name=company_name,
                        scheduled_at=scheduled_at,
                        duration_minutes=duration_minutes,
                        notes=notes,
                        timezone=timezone
                    )
                    results['google_calendar']['sent'] = event_id is not None
                    if event_id:
                        results['google_calendar']['event_id'] = event_id
                except Exception as e:
                    logger.error(f"Calendar event creation failed: {e}")
                    results['google_calendar']['error'] = str(e)

            logger.info(f"Notifications sent for call {discovery_call_id}: {results}")
            return results

        except Exception as e:
            logger.error(f"notify_booking failed: {e}")
            return results

    async def _send_email_notification(
        self,
        rep: Dict[str, Any],
        lead_name: str,
        lead_phone: str,
        company_name: Optional[str],
        display_time: str,
        day_name: str,
        duration_minutes: int,
        notes: Optional[str]
    ) -> bool:
        """Send email notification using EmailService."""
        try:
            from app.services.email_service import get_email_service

            email_service = get_email_service()

            company_str = f" ({company_name})" if company_name else ""
            subject = f"Новый Discovery Call: {lead_name}{company_str}"

            html_content = f"""
            <html>
            <body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
                <h2 style="color: #10B981;">Новый Discovery Call запланирован!</h2>

                <div style="background: #F3F4F6; padding: 20px; border-radius: 8px; margin: 20px 0;">
                    <p style="margin: 5px 0;"><strong>Контакт:</strong> {lead_name}</p>
                    <p style="margin: 5px 0;"><strong>Компания:</strong> {company_name or 'Не указана'}</p>
                    <p style="margin: 5px 0;"><strong>Телефон:</strong> {lead_phone}</p>
                    <p style="margin: 5px 0;"><strong>Дата и время:</strong> {display_time} ({day_name})</p>
                    <p style="margin: 5px 0;"><strong>Длительность:</strong> {duration_minutes} минут</p>
                </div>

                {f'<p><strong>Заметки:</strong> {notes}</p>' if notes else ''}

                <p style="color: #666; font-size: 14px; margin-top: 30px;">
                    Это письмо отправлено автоматически системой PlainTalk Sales.
                </p>
            </body>
            </html>
            """

            text_content = f"""
            Новый Discovery Call запланирован!

            Контакт: {lead_name}
            Компания: {company_name or 'Не указана'}
            Телефон: {lead_phone}
            Дата и время: {display_time} ({day_name})
            Длительность: {duration_minutes} минут

            {f'Заметки: {notes}' if notes else ''}
            """

            return await email_service.send_email(
                to_email=rep['email'],
                subject=subject,
                html_content=html_content,
                text_content=text_content
            )

        except Exception as e:
            logger.error(f"Email send failed: {e}")
            return False

    async def _send_whatsapp_notification(
        self,
        rep: Dict[str, Any],
        lead_name: str,
        lead_phone: str,
        company_name: Optional[str],
        display_time: str,
        day_name: str,
        duration_minutes: int
    ) -> bool:
        """Send WhatsApp notification via Evolution API."""
        try:
            import aiohttp

            if not self.evolution_url or not self.evolution_key:
                logger.warning("Evolution API not configured")
                return False

            whatsapp_phone = rep.get('whatsapp_phone')
            if not whatsapp_phone:
                return False

            # Get instance name for the team
            team_result = self.supabase.table('reps').select(
                'team_id'
            ).eq('id', rep['id']).single().execute()

            if not team_result.data:
                return False

            team_id = team_result.data['team_id']

            integration_result = self.supabase.table('integrations').select(
                'instance_name'
            ).eq('team_id', team_id).eq('type', 'whatsapp').single().execute()

            instance_name = integration_result.data.get('instance_name') if integration_result.data else None

            if not instance_name:
                logger.warning("No WhatsApp instance found for team")
                return False

            company_str = f" ({company_name})" if company_name else ""
            message = f"""🗓️ *Новый Discovery Call!*

*Контакт:* {lead_name}{company_str}
*Телефон:* {lead_phone}
*Время:* {display_time} ({day_name})
*Длительность:* {duration_minutes} мин

_PlainTalk Sales_"""

            # Send via Evolution API
            async with aiohttp.ClientSession() as session:
                url = f"{self.evolution_url}/message/sendText/{instance_name}"
                payload = {
                    "number": whatsapp_phone,
                    "text": message
                }

                async with session.post(
                    url,
                    json=payload,
                    headers={
                        "apikey": self.evolution_key,
                        "Content-Type": "application/json"
                    }
                ) as response:
                    if response.status == 200 or response.status == 201:
                        logger.info(f"WhatsApp notification sent to {whatsapp_phone}")
                        return True
                    else:
                        error = await response.text()
                        logger.error(f"WhatsApp send failed: {error}")
                        return False

        except Exception as e:
            logger.error(f"WhatsApp notification failed: {e}")
            return False

    async def _create_calendar_event(
        self,
        call_id: str,
        rep_id: str,
        lead_name: str,
        lead_phone: str,
        company_name: Optional[str],
        scheduled_at: datetime,
        duration_minutes: int,
        notes: Optional[str],
        timezone: str
    ) -> Optional[str]:
        """Create Google Calendar event using SalesCalendarSync."""
        try:
            # Import here to avoid circular imports
            from claude_agent_services import sync_call_to_calendar

            event_id = await sync_call_to_calendar(
                call_id=call_id,
                rep_id=rep_id,
                lead_name=lead_name,
                lead_phone=lead_phone,
                company_name=company_name,
                scheduled_at=scheduled_at,
                duration_minutes=duration_minutes,
                notes=notes,
                timezone=timezone
            )

            return event_id

        except ImportError:
            # Fallback to local implementation if claude-agent not available
            logger.warning("SalesCalendarSync not available, trying local implementation")
            try:
                from app.services.sales_calendar_sync_local import sync_call_to_calendar as local_sync
                return await local_sync(
                    call_id=call_id,
                    rep_id=rep_id,
                    lead_name=lead_name,
                    lead_phone=lead_phone,
                    company_name=company_name,
                    scheduled_at=scheduled_at,
                    duration_minutes=duration_minutes,
                    notes=notes,
                    timezone=timezone
                )
            except ImportError:
                logger.error("No calendar sync implementation available")
                return None
        except Exception as e:
            logger.error(f"Calendar event creation failed: {e}")
            return None


# Singleton instance
_notification_service: Optional[SalesNotificationService] = None


def get_sales_notification_service() -> SalesNotificationService:
    """Get or create global SalesNotificationService."""
    global _notification_service
    if _notification_service is None:
        _notification_service = SalesNotificationService()
    return _notification_service


async def notify_discovery_call_booking(
    discovery_call_id: str,
    rep_id: str,
    lead_name: str,
    lead_phone: str,
    company_name: Optional[str],
    scheduled_at: datetime,
    duration_minutes: int = 30,
    notes: Optional[str] = None,
    timezone: str = "Europe/Moscow"
) -> Dict[str, Any]:
    """
    Convenience function to send notifications for a discovery call booking.

    Returns dict with results for each notification channel.
    """
    service = get_sales_notification_service()
    return await service.notify_booking(
        discovery_call_id=discovery_call_id,
        rep_id=rep_id,
        lead_name=lead_name,
        lead_phone=lead_phone,
        company_name=company_name,
        scheduled_at=scheduled_at,
        duration_minutes=duration_minutes,
        notes=notes,
        timezone=timezone
    )
