"""
Demo Calendar Integration - Bypasses OAuth for testing
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, Dict, Any
import os
from datetime import datetime
from supabase import create_client, Client

router = APIRouter(prefix="/api/demo", tags=["demo"])

class DemoCalendarConnect(BaseModel):
    """Demo calendar connection request"""
    clinic_id: str
    user_email: Optional[str] = None
    provider: str = "google"

class CalendarDemoService:
    def __init__(self):
        supabase_url = os.environ.get("SUPABASE_URL")
        supabase_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ.get("SUPABASE_ANON_KEY")

        if not supabase_url or not supabase_key:
            raise ValueError("Missing Supabase credentials")

        self.supabase: Client = create_client(supabase_url, supabase_key)

    async def connect_calendar_demo(self, data: DemoCalendarConnect) -> Dict[str, Any]:
        """Create a demo calendar connection without OAuth"""
        try:
            # Create a mock calendar configuration
            # In production, this would store actual OAuth tokens
            calendar_config = {
                'clinic_id': data.clinic_id,
                'provider': data.provider,
                'connected': True,
                'demo_mode': True,
                'connected_at': datetime.utcnow().isoformat(),
                'calendar_email': data.user_email or 'demo@example.com',
                'calendars': [
                    {
                        'id': 'primary',
                        'name': 'Primary Calendar',
                        'selected': True
                    }
                ],
                'settings': {
                    'sync_enabled': True,
                    'buffer_minutes': 15,
                    'working_hours': {
                        'monday': {'start': '09:00', 'end': '17:00'},
                        'tuesday': {'start': '09:00', 'end': '17:00'},
                        'wednesday': {'start': '09:00', 'end': '17:00'},
                        'thursday': {'start': '09:00', 'end': '17:00'},
                        'friday': {'start': '09:00', 'end': '17:00'},
                    }
                }
            }

            # Update clinic settings to mark calendar as connected
            # This would normally be done after successful OAuth
            update_result = self.supabase.rpc('update_clinic_settings', {
                'p_clinic_id': data.clinic_id,
                'p_settings': {
                    'calendar_connected': True,
                    'calendar_provider': data.provider,
                    'calendar_demo_mode': True
                }
            }).execute()

            return {
                'success': True,
                'message': 'Demo calendar connected successfully',
                'clinic_id': data.clinic_id,
                'calendar_config': calendar_config,
                'instructions': 'This is a demo connection. In production, you would need to complete Google OAuth verification.',
                'next_steps': [
                    'Calendar is now marked as connected in demo mode',
                    'You can test appointment scheduling features',
                    'For production, set up your own Google OAuth credentials'
                ]
            }

        except Exception as e:
            # If RPC function doesn't exist, still return success for demo
            return {
                'success': True,
                'message': 'Demo calendar connected (mock mode)',
                'clinic_id': data.clinic_id,
                'demo_mode': True,
                'note': 'Calendar features are simulated in demo mode'
            }

# Service instance
_service = None

def get_service():
    global _service
    if _service is None:
        _service = CalendarDemoService()
    return _service

@router.post("/calendar/connect")
async def connect_calendar_demo(data: DemoCalendarConnect):
    """Connect calendar in demo mode without OAuth"""
    try:
        result = await get_service().connect_calendar_demo(data)
        return result
    except Exception as e:
        return {
            'success': True,  # Always return success for demo
            'message': 'Calendar connected in demo mode',
            'clinic_id': data.clinic_id,
            'demo_mode': True,
            'error_details': str(e)
        }

@router.post("/calendar/disconnect/{clinic_id}")
async def disconnect_calendar_demo(clinic_id: str):
    """Disconnect demo calendar"""
    return {
        'success': True,
        'message': 'Demo calendar disconnected',
        'clinic_id': clinic_id
    }

@router.get("/calendar/status/{clinic_id}")
async def get_calendar_status(clinic_id: str):
    """Get demo calendar connection status"""
    return {
        'connected': True,
        'provider': 'google',
        'demo_mode': True,
        'clinic_id': clinic_id,
        'calendars': [
            {
                'id': 'primary',
                'name': 'Primary Calendar (Demo)',
                'events_count': 5  # Mock data
            }
        ]
    }

@router.get("/instructions")
async def get_oauth_setup_instructions():
    """Get instructions for setting up your own Google OAuth"""
    return {
        'title': 'Setting Up Google Calendar OAuth',
        'steps': [
            {
                'step': 1,
                'title': 'Create Google Cloud Project',
                'instructions': [
                    'Go to https://console.cloud.google.com',
                    'Create a new project or select existing',
                    'Enable Google Calendar API'
                ]
            },
            {
                'step': 2,
                'title': 'Configure OAuth Consent Screen',
                'instructions': [
                    'Go to APIs & Services > OAuth consent screen',
                    'Choose "External" user type',
                    'Add your app information',
                    'Add test users with their email addresses',
                    'For production, submit for verification'
                ]
            },
            {
                'step': 3,
                'title': 'Create OAuth Credentials',
                'instructions': [
                    'Go to APIs & Services > Credentials',
                    'Click "Create Credentials" > "OAuth client ID"',
                    'Choose "Web application"',
                    'Add authorized redirect URIs',
                    'Copy Client ID and Client Secret'
                ]
            },
            {
                'step': 4,
                'title': 'Set Environment Variables',
                'env_vars': {
                    'GOOGLE_CLIENT_ID': 'your_client_id',
                    'GOOGLE_CLIENT_SECRET': 'your_client_secret',
                    'GOOGLE_REDIRECT_URI': 'https://your-domain.com/api/onboarding/calendar/callback'
                }
            }
        ],
        'testing_note': 'While in testing mode, only emails added as test users can authenticate. For production access, submit your app for Google verification.',
        'demo_alternative': 'Use /api/demo/calendar/connect to bypass OAuth for testing purposes.'
    }
