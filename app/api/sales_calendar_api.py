"""
Sales Calendar OAuth API

Endpoints for Google Calendar OAuth integration for sales reps.
"""

import logging
from typing import Optional
from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel

from app.calendar.sales_oauth_manager import get_sales_calendar_oauth_manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/sales/calendar", tags=["sales-calendar"])


class CalendarConnectRequest(BaseModel):
    """Request to initiate calendar OAuth."""
    rep_id: str
    organization_id: str
    user_id: Optional[str] = None


class CalendarConnectResponse(BaseModel):
    """Response with OAuth authorization URL."""
    auth_url: str
    provider: str = "google"


class NotificationPreferencesRequest(BaseModel):
    """Request to update notification preferences."""
    email: bool = True
    whatsapp: bool = False
    google_calendar: bool = False
    whatsapp_phone: Optional[str] = None


class NotificationPreferencesResponse(BaseModel):
    """Response with current notification preferences."""
    email: bool
    whatsapp: bool
    google_calendar: bool
    whatsapp_phone: Optional[str] = None


@router.post("/connect", response_model=CalendarConnectResponse)
async def connect_calendar(request: CalendarConnectRequest):
    """
    Initiate Google Calendar OAuth flow for a sales rep.

    Returns an authorization URL to redirect the user to Google's consent page.
    """
    try:
        oauth_manager = get_sales_calendar_oauth_manager()

        auth_url = await oauth_manager.initiate_google_oauth(
            rep_id=request.rep_id,
            organization_id=request.organization_id,
            user_id=request.user_id
        )

        return CalendarConnectResponse(auth_url=auth_url, provider="google")

    except Exception as e:
        logger.error(f"Failed to initiate calendar OAuth: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/callback")
async def calendar_oauth_callback(
    code: str = Query(..., description="Authorization code from Google"),
    state: str = Query(..., description="State parameter for verification")
):
    """
    Handle Google OAuth callback.

    This endpoint is called by Google after user grants calendar access.
    Returns a redirect or HTML page to close the popup window.
    """
    try:
        oauth_manager = get_sales_calendar_oauth_manager()

        result = await oauth_manager.handle_google_callback(code=code, state=state)

        if result.get('success'):
            # Return HTML that closes the popup and notifies the parent
            html_content = """
            <!DOCTYPE html>
            <html>
            <head>
                <title>Calendar Connected</title>
                <style>
                    body { font-family: Arial, sans-serif; text-align: center; padding: 50px; }
                    .success { color: #10B981; font-size: 24px; }
                    .message { color: #666; margin-top: 20px; }
                </style>
            </head>
            <body>
                <div class="success">✓ Calendar Connected Successfully!</div>
                <div class="message">You can close this window.</div>
                <script>
                    // Notify parent window and close
                    if (window.opener) {
                        window.opener.postMessage({ type: 'CALENDAR_CONNECTED', success: true }, '*');
                    }
                    setTimeout(() => window.close(), 2000);
                </script>
            </body>
            </html>
            """
            return Response(content=html_content, media_type="text/html")
        else:
            raise HTTPException(status_code=400, detail="Calendar connection failed")

    except Exception as e:
        logger.error(f"Calendar OAuth callback failed: {e}")
        # Return error HTML
        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>Connection Failed</title>
            <style>
                body {{ font-family: Arial, sans-serif; text-align: center; padding: 50px; }}
                .error {{ color: #EF4444; font-size: 24px; }}
                .message {{ color: #666; margin-top: 20px; }}
            </style>
        </head>
        <body>
            <div class="error">✗ Calendar Connection Failed</div>
            <div class="message">{str(e)}</div>
            <script>
                if (window.opener) {{
                    window.opener.postMessage({{ type: 'CALENDAR_CONNECTED', success: false, error: '{str(e)}' }}, '*');
                }}
                setTimeout(() => window.close(), 3000);
            </script>
        </body>
        </html>
        """
        return Response(content=html_content, media_type="text/html", status_code=400)


@router.get("/status/{rep_id}")
async def get_calendar_status(rep_id: str):
    """
    Get calendar connection status for a sales rep.
    """
    try:
        oauth_manager = get_sales_calendar_oauth_manager()
        status = await oauth_manager.get_connection_status(rep_id)
        return status

    except Exception as e:
        logger.error(f"Failed to get calendar status: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/disconnect/{rep_id}")
async def disconnect_calendar(rep_id: str):
    """
    Disconnect calendar integration for a sales rep.
    """
    try:
        oauth_manager = get_sales_calendar_oauth_manager()
        result = await oauth_manager.disconnect_calendar(rep_id)

        if not result.get('success'):
            raise HTTPException(status_code=400, detail=result.get('message'))

        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to disconnect calendar: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/preferences/{rep_id}", response_model=NotificationPreferencesResponse)
async def get_notification_preferences(rep_id: str):
    """
    Get notification preferences for a sales rep.
    """
    try:
        from supabase import create_client
        from supabase.client import ClientOptions
        import os

        sales_options = ClientOptions(schema='sales')
        supabase = create_client(
            os.environ.get("SUPABASE_URL", ""),
            os.environ.get("SUPABASE_SERVICE_ROLE_KEY", ""),
            options=sales_options
        )

        result = supabase.table('reps').select(
            'notification_preferences, whatsapp_phone'
        ).eq('id', rep_id).single().execute()

        if not result.data:
            raise HTTPException(status_code=404, detail="Rep not found")

        prefs = result.data.get('notification_preferences', {})
        whatsapp_phone = result.data.get('whatsapp_phone')

        return NotificationPreferencesResponse(
            email=prefs.get('email', True),
            whatsapp=prefs.get('whatsapp', False),
            google_calendar=prefs.get('google_calendar', False),
            whatsapp_phone=whatsapp_phone
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get notification preferences: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/preferences/{rep_id}", response_model=NotificationPreferencesResponse)
async def update_notification_preferences(rep_id: str, request: NotificationPreferencesRequest):
    """
    Update notification preferences for a sales rep.
    """
    try:
        from supabase import create_client
        from supabase.client import ClientOptions
        import os

        sales_options = ClientOptions(schema='sales')
        supabase = create_client(
            os.environ.get("SUPABASE_URL", ""),
            os.environ.get("SUPABASE_SERVICE_ROLE_KEY", ""),
            options=sales_options
        )

        # Check if rep exists
        rep_result = supabase.table('reps').select('id').eq('id', rep_id).single().execute()
        if not rep_result.data:
            raise HTTPException(status_code=404, detail="Rep not found")

        # Prepare update data
        update_data = {
            'notification_preferences': {
                'email': request.email,
                'whatsapp': request.whatsapp,
                'google_calendar': request.google_calendar
            }
        }

        # Update whatsapp_phone if provided
        if request.whatsapp_phone is not None:
            update_data['whatsapp_phone'] = request.whatsapp_phone

        # Validate: can't enable whatsapp without phone number
        if request.whatsapp and not request.whatsapp_phone:
            # Check if phone already exists
            existing = supabase.table('reps').select('whatsapp_phone').eq('id', rep_id).single().execute()
            if not existing.data.get('whatsapp_phone'):
                raise HTTPException(
                    status_code=400,
                    detail="WhatsApp phone number required to enable WhatsApp notifications"
                )

        # Validate: can't enable google_calendar without calendar integration
        if request.google_calendar:
            cal_result = supabase.table('calendar_integrations').select(
                'id'
            ).eq('rep_id', rep_id).execute()
            if not cal_result.data:
                raise HTTPException(
                    status_code=400,
                    detail="Connect Google Calendar first to enable calendar notifications"
                )

        # Update preferences
        supabase.table('reps').update(update_data).eq('id', rep_id).execute()

        return NotificationPreferencesResponse(
            email=request.email,
            whatsapp=request.whatsapp,
            google_calendar=request.google_calendar,
            whatsapp_phone=request.whatsapp_phone
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update notification preferences: {e}")
        raise HTTPException(status_code=500, detail=str(e))


class BookingNotificationRequest(BaseModel):
    """Request to send booking notifications."""
    discovery_call_id: str
    rep_id: str
    lead_name: str
    lead_phone: str
    company_name: Optional[str] = None
    scheduled_at: str  # ISO datetime string
    duration_minutes: int = 30
    notes: Optional[str] = None
    timezone: str = "Europe/Moscow"


@router.post("/notifications/booking")
async def send_booking_notifications(request: BookingNotificationRequest):
    """
    Send notifications to sales rep for a new discovery call booking.

    Sends notifications based on rep's preferences:
    - Email (if enabled)
    - WhatsApp (if enabled and phone configured)
    - Google Calendar event (if calendar connected and enabled)
    """
    try:
        from datetime import datetime
        from app.services.sales_notification_service import get_sales_notification_service

        # Parse the scheduled_at datetime
        scheduled_at = datetime.fromisoformat(request.scheduled_at.replace('Z', '+00:00'))

        notification_service = get_sales_notification_service()

        results = await notification_service.notify_booking(
            discovery_call_id=request.discovery_call_id,
            rep_id=request.rep_id,
            lead_name=request.lead_name,
            lead_phone=request.lead_phone,
            company_name=request.company_name,
            scheduled_at=scheduled_at,
            duration_minutes=request.duration_minutes,
            notes=request.notes,
            timezone=request.timezone
        )

        return results

    except Exception as e:
        logger.error(f"Failed to send booking notifications: {e}")
        raise HTTPException(status_code=500, detail=str(e))
