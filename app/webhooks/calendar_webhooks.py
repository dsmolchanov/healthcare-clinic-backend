"""
Calendar Webhook Handlers
Handle external calendar change notifications for Google Calendar and Outlook
"""

import os
import json
import hmac
import hashlib
import logging
from datetime import datetime
from typing import Dict, Any, Optional

from fastapi import APIRouter, Request, HTTPException, BackgroundTasks
from supabase import create_client, Client
from supabase.client import ClientOptions

from ..services.external_calendar_service import ExternalCalendarService
from ..services.websocket_manager import websocket_manager, NotificationType
from ..services.realtime_conflict_detector import conflict_detector

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks/calendar", tags=["Calendar Webhooks"])

# Initialize services with healthcare schema
options = ClientOptions(schema='healthcare')
supabase: Client = create_client(
    os.environ.get("SUPABASE_URL"),
    os.environ.get("SUPABASE_SERVICE_ROLE_KEY"),
    options=options
)
calendar_service = ExternalCalendarService()

@router.post("/google")
async def google_calendar_webhook(
    request: Request,
    x_goog_channel_id: Optional[str] = None,
    x_goog_resource_state: Optional[str] = None,
    x_goog_resource_id: Optional[str] = None
):
    """
    Handle Google Calendar push notifications for external changes

    States:
    - sync: Initial verification (respond with 200)
    - exists: Resource exists (trigger sync)
    - not_exists: Resource deleted (ignore for now)
    """
    try:
        # Get headers
        headers = dict(request.headers)

        # Extract Google webhook headers (case-insensitive)
        channel_id = x_goog_channel_id or headers.get('x-goog-channel-id')
        resource_state = x_goog_resource_state or headers.get('x-goog-resource-state')
        resource_id = x_goog_resource_id or headers.get('x-goog-resource-id')

        logger.info(f"Received Google Calendar webhook - channel: {channel_id}, state: {resource_state}")

        # Verify it's a sync message (initial verification)
        if resource_state == 'sync':
            logger.info(f"Webhook sync verification for channel {channel_id}")
            return {"status": "verified"}

        # Only process actual changes
        if resource_state not in ['exists', 'not_exists']:
            logger.debug(f"Ignoring webhook state: {resource_state}")
            return {"status": "ignored"}

        # Lookup clinic from channel_id (don't use .single() to avoid exceptions)
        try:
            channel = supabase.table('webhook_channels').select('clinic_id').eq(
                'channel_id', channel_id
            ).execute()

            # Check if channel exists
            if not channel.data or len(channel.data) == 0:
                logger.info(f"Ignoring webhook for unknown/expired channel: {channel_id}")
                return {"status": "ignored", "reason": "channel_not_found"}

        except Exception as e:
            # Database error - ignore gracefully
            logger.info(f"Database error looking up channel {channel_id}: {e}")
            return {"status": "ignored", "reason": "database_error"}

        clinic_id = channel.data[0]['clinic_id']

        # Trigger incremental sync for this clinic
        sync_result = await calendar_service.sync_from_google_calendar(clinic_id)
        logger.info(f"Webhook triggered sync for clinic {clinic_id}: {sync_result}")
        return {"status": "synced", "result": sync_result}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Google Calendar webhook processing failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/outlook")
async def outlook_calendar_webhook(
    request: Request,
    background_tasks: BackgroundTasks
):
    """
    Handle Microsoft Graph webhook notifications for Outlook Calendar

    Microsoft Graph sends notifications when calendar events change
    """
    try:
        # Get validation token for subscription validation
        validation_token = request.query_params.get('validationToken')
        if validation_token:
            # This is a subscription validation request
            logger.info("Outlook webhook validation request received")
            return {"content": validation_token, "media_type": "text/plain"}

        # Get headers and body
        headers = dict(request.headers)
        webhook_data = await request.json()

        logger.info(f"Received Outlook Calendar webhook: {webhook_data}")

        # Verify webhook signature if configured
        webhook_secret = os.environ.get("OUTLOOK_CALENDAR_WEBHOOK_SECRET")
        if webhook_secret and not verify_outlook_webhook_signature(headers, webhook_data, webhook_secret):
            logger.warning("Invalid Outlook Calendar webhook signature")
            raise HTTPException(status_code=401, detail="Invalid webhook signature")

        # Process each notification
        notifications = webhook_data.get('value', [])
        for notification in notifications:
            # Process the change in background
            background_tasks.add_task(
                handle_outlook_calendar_change,
                notification
            )

        return {"status": "processed", "notifications_count": len(notifications)}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Outlook Calendar webhook processing failed: {e}")
        raise HTTPException(status_code=500, detail="Webhook processing failed")

@router.post("/test")
async def test_calendar_webhook(webhook_data: Dict[str, Any]):
    """Test endpoint for calendar webhook processing"""
    try:
        logger.info(f"Test webhook received: {webhook_data}")

        # Process test webhook
        await handle_external_calendar_change(
            provider=webhook_data.get('provider', 'test'),
            event_data=webhook_data
        )

        return {"status": "processed", "test_data": webhook_data}

    except Exception as e:
        logger.error(f"Test webhook processing failed: {e}")
        raise HTTPException(status_code=500, detail="Test webhook processing failed")

def verify_google_webhook_signature(
    headers: Dict[str, str],
    body: bytes,
    secret: str
) -> bool:
    """Verify Google Calendar webhook signature"""
    try:
        # Google Calendar doesn't use HMAC signatures by default
        # This is a placeholder for custom verification if needed
        return True

    except Exception as e:
        logger.error(f"Google webhook signature verification failed: {e}")
        return False

def verify_outlook_webhook_signature(
    headers: Dict[str, str],
    webhook_data: Dict[str, Any],
    secret: str
) -> bool:
    """Verify Outlook Calendar webhook signature"""
    try:
        # Microsoft Graph doesn't use HMAC signatures by default
        # This is a placeholder for custom verification if needed
        return True

    except Exception as e:
        logger.error(f"Outlook webhook signature verification failed: {e}")
        return False

async def handle_google_calendar_change(notification_data: Dict[str, Any]):
    """Process Google Calendar change notification"""
    try:
        logger.info(f"Processing Google Calendar change: {notification_data}")

        # Extract change information
        resource_state = notification_data.get('resource_state')
        resource_id = notification_data.get('resource_id')
        channel_id = notification_data.get('channel_id')

        if resource_state in ['exists', 'updated']:
            # Find the doctor associated with this calendar
            doctor_id = await find_doctor_by_calendar_channel(channel_id, 'google')

            if doctor_id:
                # Process the calendar change
                await handle_external_calendar_change(
                    provider='google',
                    event_data={
                        'change_type': resource_state,
                        'resource_id': resource_id,
                        'channel_id': channel_id,
                        'doctor_id': doctor_id,
                        'timestamp': datetime.now().isoformat()
                    }
                )
            else:
                logger.warning(f"No doctor found for Google Calendar channel {channel_id}")

    except Exception as e:
        logger.error(f"Failed to handle Google Calendar change: {e}")

async def handle_outlook_calendar_change(notification: Dict[str, Any]):
    """Process Outlook Calendar change notification"""
    try:
        logger.info(f"Processing Outlook Calendar change: {notification}")

        # Extract change information
        change_type = notification.get('changeType')
        resource = notification.get('resource')
        resource_data = notification.get('resourceData', {})

        # Find the doctor associated with this calendar
        subscription_id = notification.get('subscriptionId')
        doctor_id = await find_doctor_by_calendar_subscription(subscription_id, 'outlook')

        if doctor_id:
            # Process the calendar change
            await handle_external_calendar_change(
                provider='outlook',
                event_data={
                    'change_type': change_type,
                    'resource': resource,
                    'resource_data': resource_data,
                    'subscription_id': subscription_id,
                    'doctor_id': doctor_id,
                    'timestamp': datetime.now().isoformat()
                }
            )
        else:
            logger.warning(f"No doctor found for Outlook subscription {subscription_id}")

    except Exception as e:
        logger.error(f"Failed to handle Outlook Calendar change: {e}")

async def handle_external_calendar_change(provider: str, event_data: Dict[str, Any]):
    """
    Process external calendar changes and sync with internal system

    This function:
    1. Updates internal records based on external changes
    2. Notifies connected WebSocket clients
    3. Checks for conflicts with pending holds
    """
    try:
        logger.info(f"Handling {provider} calendar change: {event_data}")

        doctor_id = event_data.get('doctor_id')
        change_type = event_data.get('change_type')

        if not doctor_id:
            logger.warning("No doctor_id in calendar change event")
            return

        # Log the calendar change
        await calendar_service._log_calendar_operation(
            doctor_id=doctor_id,
            provider=provider,
            operation='webhook',
            status='received',
            request_data=event_data
        )

        # Update calendar sync status
        await update_calendar_sync_status(doctor_id, provider, event_data)

        # Check for conflicts with pending holds
        await check_calendar_conflicts(doctor_id, provider, event_data)

        # Notify WebSocket clients with real-time broadcasting
        await notify_calendar_change(doctor_id, provider, event_data)

        # Detect and handle conflicts in real-time
        await conflict_detector.detect_calendar_change_conflicts(
            doctor_id=doctor_id,
            provider=provider,
            change_data=event_data
        )

        # Broadcast external calendar change to connected clients
        await websocket_manager.broadcast_external_calendar_change(
            doctor_id=doctor_id,
            provider=provider,
            change_data=event_data
        )

        logger.info(f"Successfully processed {provider} calendar change for doctor {doctor_id}")

    except Exception as e:
        logger.error(f"Failed to handle external calendar change: {e}")
        # Log the error
        if event_data.get('doctor_id'):
            await calendar_service._log_calendar_operation(
                doctor_id=event_data['doctor_id'],
                provider=provider,
                operation='webhook',
                status='failed',
                error_message=str(e),
                request_data=event_data
            )

async def find_doctor_by_calendar_channel(
    channel_id: str,
    provider: str
) -> Optional[str]:
    """Find doctor ID by calendar webhook channel ID"""
    try:
        result = supabase.table('healthcare.calendar_sync_status').select('doctor_id').eq(
            'provider', provider
        ).contains(
            'sync_token', {'channel_id': channel_id}
        ).execute()

        if result.data:
            return result.data[0]['doctor_id']
        return None

    except Exception as e:
        logger.error(f"Failed to find doctor by calendar channel: {e}")
        return None

async def find_doctor_by_calendar_subscription(
    subscription_id: str,
    provider: str
) -> Optional[str]:
    """Find doctor ID by calendar webhook subscription ID"""
    try:
        result = supabase.table('healthcare.calendar_sync_status').select('doctor_id').eq(
            'provider', provider
        ).contains(
            'sync_token', {'subscription_id': subscription_id}
        ).execute()

        if result.data:
            return result.data[0]['doctor_id']
        return None

    except Exception as e:
        logger.error(f"Failed to find doctor by calendar subscription: {e}")
        return None

async def update_calendar_sync_status(
    doctor_id: str,
    provider: str,
    event_data: Dict[str, Any]
):
    """Update calendar sync status based on webhook notification"""
    try:
        update_data = {
            'last_sync_at': datetime.now().isoformat(),
            'updated_at': datetime.now().isoformat()
        }

        # Reset error count on successful webhook
        if event_data.get('change_type') != 'error':
            update_data['error_count'] = 0
            update_data['last_error'] = None

        supabase.table('healthcare.calendar_sync_status').update(update_data).eq(
            'doctor_id', doctor_id
        ).eq(
            'provider', provider
        ).execute()

    except Exception as e:
        logger.error(f"Failed to update calendar sync status: {e}")

async def check_calendar_conflicts(
    doctor_id: str,
    provider: str,
    event_data: Dict[str, Any]
):
    """Check for conflicts between external calendar changes and pending holds"""
    try:
        change_type = event_data.get('change_type')

        # Only check conflicts for event creation/updates
        if change_type not in ['created', 'updated', 'exists']:
            return

        # Get pending holds for this doctor
        holds_result = supabase.table('healthcare.calendar_holds').select('*').eq(
            'doctor_id', doctor_id
        ).eq(
            'status', 'pending'
        ).gte(
            'expires_at', datetime.now()
        ).execute()

        if not holds_result.data:
            return

        # Check each hold for potential conflicts
        for hold in holds_result.data:
            # This would implement conflict detection logic
            # For now, just log potential conflicts
            logger.info(f"Checking conflict for hold {hold['reservation_id']} against {provider} change")

    except Exception as e:
        logger.error(f"Failed to check calendar conflicts: {e}")

async def notify_calendar_change(
    doctor_id: str,
    provider: str,
    event_data: Dict[str, Any]
):
    """Notify WebSocket clients about calendar changes"""
    try:
        # This will be implemented in Phase 3: Real-Time Multi-Source Updates
        # For now, just log the notification
        logger.info(f"Would notify WebSocket clients about {provider} calendar change for doctor {doctor_id}")

    except Exception as e:
        logger.error(f"Failed to notify calendar change: {e}")