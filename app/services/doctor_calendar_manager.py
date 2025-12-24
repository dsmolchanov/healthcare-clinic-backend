"""
Doctor Calendar Manager
Manages individual Google Calendar sub-calendars for each doctor
"""
import os
import logging
from typing import Dict, Any, Optional
from datetime import datetime

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from app.db.supabase_client import get_supabase_client

logger = logging.getLogger(__name__)

# Color IDs for Google Calendar (11 standard colors)
DOCTOR_COLORS = {
    1: {'name': 'Lavender', 'hex': '#7986CB'},
    2: {'name': 'Sage', 'hex': '#33B679'},
    3: {'name': 'Grape', 'hex': '#8E24AA'},
    4: {'name': 'Flamingo', 'hex': '#E67C73'},
    5: {'name': 'Banana', 'hex': '#F6BF26'},
    6: {'name': 'Tangerine', 'hex': '#F4511E'},
    7: {'name': 'Peacock', 'hex': '#039BE5'},
    8: {'name': 'Graphite', 'hex': '#616161'},
    9: {'name': 'Blueberry', 'hex': '#3F51B5'},
    10: {'name': 'Basil', 'hex': '#0B8043'},
    11: {'name': 'Tomato', 'hex': '#D50000'}
}


class DoctorCalendarManager:
    """Manages individual Google Calendar sub-calendars for doctors"""

    def __init__(self):
        self.supabase = get_supabase_client()

    async def create_doctor_calendar(
        self,
        doctor_id: str,
        doctor_name: str,
        credentials: Dict[str, Any],
        color_id: int = None
    ) -> Dict[str, Any]:
        """
        Create a secondary Google Calendar for a specific doctor

        Args:
            doctor_id: Doctor UUID
            doctor_name: Doctor's full name
            credentials: Google OAuth credentials dict
            color_id: Optional color ID (1-11), auto-assigned if None

        Returns:
            Dict with calendar_id, calendar_url, color_id
        """
        try:
            # Build Google Calendar service
            creds = Credentials(
                token=credentials['access_token'],
                refresh_token=credentials.get('refresh_token'),
                token_uri='https://oauth2.googleapis.com/token',
                client_id=os.getenv('GOOGLE_CLIENT_ID'),
                client_secret=os.getenv('GOOGLE_CLIENT_SECRET')
            )
            service = build('calendar', 'v3', credentials=creds, cache_discovery=False)

            # Auto-assign color if not provided
            if color_id is None:
                color_id = await self._get_next_available_color(doctor_id)

            # Create secondary calendar
            calendar = {
                'summary': f"Dr. {doctor_name}",
                'description': f"Appointment calendar for Dr. {doctor_name}",
                'timeZone': 'America/Cancun'
            }

            created_calendar = service.calendars().insert(body=calendar).execute()
            calendar_id = created_calendar['id']

            # Set calendar color and ensure it's visible
            try:
                calendar_list_entry = service.calendarList().get(calendarId=calendar_id).execute()
                calendar_list_entry['colorId'] = str(color_id)
                calendar_list_entry['selected'] = True  # Ensure it's visible in the sidebar
                calendar_list_entry['hidden'] = False   # Make sure it's not hidden
                service.calendarList().update(
                    calendarId=calendar_id,
                    body=calendar_list_entry
                ).execute()
                logger.info(f"Set calendar visibility and color for Dr. {doctor_name}")
            except Exception as color_error:
                logger.warning(f"Failed to set calendar color/visibility: {color_error}")

            # Store calendar ID in database
            self.supabase.from_('doctors').update({
                'google_calendar_id': calendar_id,
                'google_calendar_color_id': str(color_id),
                'google_calendar_created_at': datetime.utcnow().isoformat()
            }).eq('id', doctor_id).execute()

            logger.info(f"Created calendar for Dr. {doctor_name}: {calendar_id} (color: {DOCTOR_COLORS[color_id]['name']})")

            return {
                'success': True,
                'calendar_id': calendar_id,
                'calendar_url': f"https://calendar.google.com/calendar/embed?src={calendar_id}",
                'color_id': color_id,
                'color_name': DOCTOR_COLORS[color_id]['name']
            }

        except Exception as e:
            logger.error(f"Failed to create doctor calendar: {e}", exc_info=True)
            return {'success': False, 'error': str(e)}

    async def setup_multi_doctor_calendars(
        self,
        organization_id: str,
        credentials: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Setup sub-calendars for all doctors in an organization

        Args:
            organization_id: Organization UUID
            credentials: Google OAuth credentials

        Returns:
            Statistics about calendar creation
        """
        try:
            logger.info(f"Setting up multi-doctor calendars for org {organization_id}")

            # Get all doctors in the organization
            # First get clinics for this organization
            clinics = self.supabase.from_('clinics').select('id').eq('organization_id', organization_id).execute()
            clinic_ids = [c['id'] for c in (clinics.data or [])]

            if not clinic_ids:
                return {'success': True, 'message': 'No clinics found for organization', 'created': 0}

            # Get doctors for these clinics
            doctors = self.supabase.from_('doctors').select('id, first_name, last_name, google_calendar_id').in_(
                'clinic_id', clinic_ids
            ).execute()

            if not doctors.data:
                return {'success': True, 'message': 'No doctors found', 'created': 0}

            stats = {'total': len(doctors.data), 'created': 0, 'skipped': 0, 'failed': 0}

            for idx, doctor in enumerate(doctors.data):
                # Skip if doctor already has a calendar
                if doctor.get('google_calendar_id'):
                    logger.info(f"Doctor {doctor['first_name']} {doctor['last_name']} already has calendar")
                    stats['skipped'] += 1
                    continue

                doctor_name = f"{doctor['first_name']} {doctor['last_name']}"
                color_id = (idx % 11) + 1  # Cycle through 11 colors

                result = await self.create_doctor_calendar(
                    doctor_id=doctor['id'],
                    doctor_name=doctor_name,
                    credentials=credentials,
                    color_id=color_id
                )

                if result.get('success'):
                    stats['created'] += 1
                else:
                    stats['failed'] += 1

            # Enable multi-doctor mode for this organization
            self.supabase.from_('calendar_integrations').update({
                'multi_doctor_mode': True
            }).eq('organization_id', organization_id).eq('provider', 'google').execute()

            logger.info(f"Multi-doctor calendar setup complete: {stats}")

            return {'success': True, **stats}

        except Exception as e:
            logger.error(f"Failed to setup multi-doctor calendars: {e}", exc_info=True)
            return {'success': False, 'error': str(e)}

    async def _get_next_available_color(self, doctor_id: str) -> int:
        """Get the next available color ID for a doctor"""
        # Simple rotation: count doctors in org and cycle through colors
        result = self.supabase.from_('doctors').select('id').execute()
        count = len(result.data) if result.data else 0
        return (count % 11) + 1

    async def get_doctor_calendar_id(
        self,
        doctor_id: str,
        organization_id: str
    ) -> Optional[str]:
        """
        Get the calendar ID for a doctor (either their sub-calendar or primary)

        Args:
            doctor_id: Doctor UUID
            organization_id: Organization UUID

        Returns:
            Calendar ID or None
        """
        try:
            # Use RPC function to determine which calendar to use
            result = self.supabase.rpc('get_doctor_calendar_id', {
                'p_doctor_id': doctor_id,
                'p_organization_id': organization_id
            }).execute()

            return result.data if result.data else 'primary'

        except Exception as e:
            logger.error(f"Error getting doctor calendar ID: {e}")
            return 'primary'  # Fallback to primary calendar
