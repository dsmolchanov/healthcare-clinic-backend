"""
External Calendar Service
Implements ask-hold-reserve pattern for coordinating between internal database and external calendars
"""

import os
import json
import uuid
import logging
import asyncio
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass

from supabase import create_client, Client
from supabase.client import ClientOptions

logger = logging.getLogger(__name__)

# Optional calendar API imports - only load if available
try:
    from googleapiclient.discovery import build
    from google.oauth2.credentials import Credentials
    GOOGLE_AVAILABLE = True
except ImportError:
    GOOGLE_AVAILABLE = False
    logger.warning("Google Calendar API not available. Install google-api-python-client for Google Calendar support.")

try:
    from microsoft.graph import GraphServiceClient
    from azure.identity import ClientSecretCredential
    MICROSOFT_AVAILABLE = True
except ImportError:
    MICROSOFT_AVAILABLE = False
    logger.warning("Microsoft Graph API not available. Install msgraph-sdk for Outlook Calendar support.")

try:
    from ..security.compliance_manager import ComplianceManager
    COMPLIANCE_AVAILABLE = True
except ImportError:
    COMPLIANCE_AVAILABLE = False
    logger.warning("Compliance manager not available. Some security features may be limited.")

try:
    from ..security.compliance_vault import ComplianceVault
    VAULT_AVAILABLE = True
except ImportError:
    VAULT_AVAILABLE = False
    logger.warning("Compliance vault not available.")

@dataclass
class CalendarEvent:
    """External calendar event representation"""
    id: str
    provider: str
    start_time: datetime
    end_time: datetime
    title: str
    description: Optional[str] = None
    attendees: List[str] = None
    location: Optional[str] = None
    duration_minutes: Optional[int] = None

@dataclass
class HoldResult:
    """Result of hold operation"""
    success: bool
    reservation_id: Optional[str] = None
    error: Optional[str] = None
    external_event_ids: Dict[str, str] = None

class ExternalCalendarService:
    """Coordinates booking operations across multiple calendar sources"""

    def __init__(self, supabase: Client = None):
        if supabase:
            self.supabase = supabase
        else:
            # Configure client to use healthcare schema
            options = ClientOptions(
                schema='healthcare',
                auto_refresh_token=True,
                persist_session=False
            )
            self.supabase: Client = create_client(
                os.environ.get("SUPABASE_URL"),
                os.environ.get("SUPABASE_SERVICE_ROLE_KEY"),
                options=options
            )
        self.compliance = ComplianceManager() if COMPLIANCE_AVAILABLE else None
        self.vault = ComplianceVault() if VAULT_AVAILABLE else None
        self.hold_duration = timedelta(minutes=5)  # Calendar hold duration

    async def ask_hold_reserve(
        self,
        doctor_id: str,
        start_time: datetime,
        end_time: datetime,
        appointment_data: Dict
    ) -> Tuple[bool, Dict]:
        """
        Implement ask-hold-reserve pattern across all calendar sources

        Returns: (success, reservation_data)
        """
        reservation_id = f"hold_{doctor_id}_{start_time.isoformat()}_{uuid.uuid4().hex[:8]}"

        try:
            # Phase 1: ASK - Check availability across all sources
            logger.info(f"Phase 1: ASK - Checking availability for {doctor_id} at {start_time}")
            availability_checks = await asyncio.gather(
                self._check_internal_availability(doctor_id, start_time, end_time),
                self._check_google_calendar_availability(doctor_id, start_time, end_time),
                self._check_outlook_calendar_availability(doctor_id, start_time, end_time),
                return_exceptions=True
            )

            # Check if all sources are available
            for i, check in enumerate(availability_checks):
                if isinstance(check, Exception):
                    logger.error(f"Availability check {i} failed: {check}")
                    continue
                if not check.get('available', False):
                    logger.warning(f"Slot unavailable in source {i}: {check}")
                    return False, {'error': 'Slot unavailable in one or more calendars', 'details': check}

            # Phase 2: HOLD - Create temporary holds in all systems
            logger.info(f"Phase 2: HOLD - Creating holds for reservation {reservation_id}")
            hold_operations = await asyncio.gather(
                self._create_internal_hold(reservation_id, doctor_id, start_time, end_time),
                self._create_google_calendar_hold(reservation_id, doctor_id, start_time, end_time),
                self._create_outlook_calendar_hold(reservation_id, doctor_id, start_time, end_time),
                return_exceptions=True
            )

            # Check if any holds failed
            failed_holds = []
            for i, op in enumerate(hold_operations):
                if isinstance(op, Exception):
                    failed_holds.append(f"Hold {i}: {str(op)}")
                elif not op.get('success', False):
                    failed_holds.append(f"Hold {i}: {op.get('error', 'Unknown error')}")

            if failed_holds:
                logger.error(f"Hold operations failed: {failed_holds}")
                # Rollback holds on any failure
                await self._rollback_holds(reservation_id)
                return False, {'error': 'Failed to secure holds across all calendars', 'details': failed_holds}

            # Phase 3: RESERVE - Confirm in all systems or rollback
            logger.info(f"Phase 3: RESERVE - Confirming reservation {reservation_id}")
            try:
                reserve_operations = await asyncio.gather(
                    self._confirm_internal_appointment(reservation_id, appointment_data),
                    self._confirm_google_calendar_event(reservation_id, appointment_data),
                    self._confirm_outlook_calendar_event(reservation_id, appointment_data),
                    return_exceptions=True
                )

                # Check if any confirmations failed
                failed_confirmations = []
                successful_confirmations = 0
                appointment_id = None

                for i, op in enumerate(reserve_operations):
                    if isinstance(op, Exception):
                        failed_confirmations.append(f"Confirmation {i}: {str(op)}")
                    elif op.get('success', False):
                        successful_confirmations += 1
                        if op.get('appointment_id'):
                            appointment_id = op['appointment_id']
                    else:
                        failed_confirmations.append(f"Confirmation {i}: {op.get('error', 'Unknown error')}")

                if failed_confirmations:
                    logger.error(f"Some confirmations failed: {failed_confirmations}")
                    # If critical confirmations failed, rollback everything
                    if successful_confirmations == 0:
                        await self._rollback_reservations(reservation_id)
                        return False, {'error': 'All confirmation operations failed', 'details': failed_confirmations}

                return True, {
                    'reservation_id': reservation_id,
                    'confirmed_calendars': successful_confirmations,
                    'appointment_id': appointment_id,
                    'partial_failures': failed_confirmations if failed_confirmations else None
                }

            except Exception as e:
                logger.error(f"Reservation confirmation failed: {e}")
                # Rollback all operations on any failure
                await self._rollback_reservations(reservation_id)
                return False, {'error': f'Reservation failed: {str(e)}'}

        except Exception as e:
            logger.error(f"Ask-Hold-Reserve operation failed: {e}")
            return False, {'error': f'Operation failed: {str(e)}'}

    async def _check_internal_availability(
        self,
        doctor_id: str,
        start_time: datetime,
        end_time: datetime
    ) -> Dict[str, Any]:
        """Check availability in internal database"""
        try:
            # Check existing appointments
            result = self.supabase.table('appointments').select('*').eq(
                'doctor_id', doctor_id
            ).eq(
                'appointment_date', start_time.date()
            ).neq(
                'status', 'cancelled'
            ).execute()

            # Check for overlapping appointments
            for appointment in result.data:
                apt_start = datetime.combine(
                    appointment['appointment_date'],
                    appointment['start_time']
                )
                apt_end = datetime.combine(
                    appointment['appointment_date'],
                    appointment['end_time']
                )

                # Check for overlap
                if (start_time < apt_end and end_time > apt_start):
                    return {
                        'available': False,
                        'source': 'internal',
                        'conflict': {
                            'appointment_id': appointment['id'],
                            'start_time': apt_start.isoformat(),
                            'end_time': apt_end.isoformat()
                        }
                    }

            # Check existing holds
            holds_result = self.supabase.table('calendar_holds').select('*').eq(
                'doctor_id', doctor_id
            ).eq(
                'status', 'pending'
            ).gte(
                'expires_at', datetime.now()
            ).execute()

            for hold in holds_result.data:
                hold_start = hold['start_time']
                hold_end = hold['end_time']

                # Check for overlap with existing holds
                if isinstance(hold_start, str):
                    hold_start = datetime.fromisoformat(hold_start.replace('Z', '+00:00'))
                if isinstance(hold_end, str):
                    hold_end = datetime.fromisoformat(hold_end.replace('Z', '+00:00'))

                if (start_time < hold_end and end_time > hold_start):
                    return {
                        'available': False,
                        'source': 'internal_hold',
                        'conflict': {
                            'hold_id': hold['id'],
                            'reservation_id': hold['reservation_id'],
                            'start_time': hold_start.isoformat(),
                            'end_time': hold_end.isoformat()
                        }
                    }

            return {'available': True, 'source': 'internal'}

        except Exception as e:
            logger.error(f"Internal availability check failed: {e}")
            return {'available': False, 'source': 'internal', 'error': str(e)}

    async def _check_google_calendar_availability(
        self,
        doctor_id: str,
        start_time: datetime,
        end_time: datetime
    ) -> Dict[str, Any]:
        """Check availability in Google Calendar"""
        try:
            if not GOOGLE_AVAILABLE:
                return {'available': True, 'source': 'google', 'note': 'Google Calendar API not available'}

            # Get calendar credentials for doctor
            calendar_config = await self._get_calendar_config(doctor_id, 'google')
            if not calendar_config or not calendar_config.get('enabled'):
                return {'available': True, 'source': 'google', 'note': 'Not configured'}

            # Create Google Calendar service
            credentials = Credentials.from_authorized_user_info(
                calendar_config['credentials']
            )
            service = build('calendar', 'v3', credentials=credentials)

            # Query for events in the time range
            events_result = service.events().list(
                calendarId='primary',
                timeMin=start_time.isoformat(),
                timeMax=end_time.isoformat(),
                singleEvents=True,
                orderBy='startTime'
            ).execute()

            events = events_result.get('items', [])

            # Check for conflicts
            for event in events:
                event_start = datetime.fromisoformat(
                    event['start'].get('dateTime', event['start'].get('date'))
                )
                event_end = datetime.fromisoformat(
                    event['end'].get('dateTime', event['end'].get('date'))
                )

                # Check for overlap
                if (start_time < event_end and end_time > event_start):
                    return {
                        'available': False,
                        'source': 'google',
                        'conflict': {
                            'event_id': event['id'],
                            'title': event.get('summary', 'No title'),
                            'start_time': event_start.isoformat(),
                            'end_time': event_end.isoformat()
                        }
                    }

            return {'available': True, 'source': 'google'}

        except Exception as e:
            logger.error(f"Google Calendar availability check failed: {e}")
            # If calendar check fails, assume unavailable for safety
            return {'available': False, 'source': 'google', 'error': str(e)}

    async def _check_outlook_calendar_availability(
        self,
        doctor_id: str,
        start_time: datetime,
        end_time: datetime
    ) -> Dict[str, Any]:
        """Check availability in Outlook/Microsoft Graph Calendar"""
        try:
            if not MICROSOFT_AVAILABLE:
                return {'available': True, 'source': 'outlook', 'note': 'Microsoft Graph API not available'}

            # Get calendar credentials for doctor
            calendar_config = await self._get_calendar_config(doctor_id, 'outlook')
            if not calendar_config or not calendar_config.get('enabled'):
                return {'available': True, 'source': 'outlook', 'note': 'Not configured'}

            # Create Microsoft Graph client
            credential = ClientSecretCredential(
                tenant_id=calendar_config['tenant_id'],
                client_id=calendar_config['client_id'],
                client_secret=calendar_config['client_secret']
            )
            graph_client = GraphServiceClient(credential)

            # Query for events in the time range
            events = await graph_client.me.calendar.events.get(
                filter=f"start/dateTime ge '{start_time.isoformat()}' and end/dateTime le '{end_time.isoformat()}'"
            )

            # Check for conflicts
            if events and events.value:
                for event in events.value:
                    event_start = datetime.fromisoformat(event.start.date_time)
                    event_end = datetime.fromisoformat(event.end.date_time)

                    # Check for overlap
                    if (start_time < event_end and end_time > event_start):
                        return {
                            'available': False,
                            'source': 'outlook',
                            'conflict': {
                                'event_id': event.id,
                                'title': event.subject or 'No title',
                                'start_time': event_start.isoformat(),
                                'end_time': event_end.isoformat()
                            }
                        }

            return {'available': True, 'source': 'outlook'}

        except Exception as e:
            logger.error(f"Outlook Calendar availability check failed: {e}")
            # If calendar check fails, assume unavailable for safety
            return {'available': False, 'source': 'outlook', 'error': str(e)}

    async def _create_internal_hold(
        self,
        reservation_id: str,
        doctor_id: str,
        start_time: datetime,
        end_time: datetime
    ) -> Dict[str, Any]:
        """Create temporary hold in internal database"""
        try:
            expires_at = datetime.now() + self.hold_duration

            hold_data = {
                'reservation_id': reservation_id,
                'doctor_id': doctor_id,
                'start_time': start_time.isoformat(),
                'end_time': end_time.isoformat(),
                'status': 'pending',
                'expires_at': expires_at.isoformat(),
                'metadata': {
                    'created_by': 'external_calendar_service',
                    'hold_type': 'ask_hold_reserve'
                }
            }

            result = self.supabase.table('calendar_holds').insert(hold_data).execute()

            if result.data:
                return {
                    'success': True,
                    'hold_id': result.data[0]['id'],
                    'expires_at': expires_at.isoformat()
                }
            else:
                return {'success': False, 'error': 'Failed to create internal hold'}

        except Exception as e:
            logger.error(f"Failed to create internal hold: {e}")
            return {'success': False, 'error': str(e)}

    async def _create_google_calendar_hold(
        self,
        reservation_id: str,
        doctor_id: str,
        start_time: datetime,
        end_time: datetime
    ) -> Dict[str, Any]:
        """Create temporary event in Google Calendar with hold status"""
        try:
            # Get calendar credentials for doctor
            calendar_config = await self._get_calendar_config(doctor_id, 'google')
            if not calendar_config or not calendar_config.get('enabled'):
                return {'success': True, 'note': 'Google Calendar not configured'}

            # Create Google Calendar service
            credentials = Credentials.from_authorized_user_info(
                calendar_config['credentials']
            )
            service = build('calendar', 'v3', credentials=credentials)

            # Create hold event
            event = {
                'summary': f'[HOLD] Appointment Hold - {reservation_id}',
                'description': f'Temporary hold for appointment booking. Reservation ID: {reservation_id}',
                'start': {
                    'dateTime': start_time.isoformat(),
                    'timeZone': 'UTC',
                },
                'end': {
                    'dateTime': end_time.isoformat(),
                    'timeZone': 'UTC',
                },
                'colorId': '8',  # Gray color for holds
                'transparency': 'tentative',
                'extendedProperties': {
                    'private': {
                        'reservation_id': reservation_id,
                        'hold_type': 'ask_hold_reserve',
                        'expires_at': (datetime.now() + self.hold_duration).isoformat()
                    }
                }
            }

            created_event = service.events().insert(
                calendarId='primary',
                body=event
            ).execute()

            # Log the operation
            await self._log_calendar_operation(
                doctor_id, 'google', 'create_hold',
                external_event_id=created_event['id'],
                status='success',
                request_data=event
            )

            return {
                'success': True,
                'google_event_id': created_event['id'],
                'event_link': created_event.get('htmlLink')
            }

        except Exception as e:
            logger.error(f"Failed to create Google Calendar hold: {e}")
            # Log the failure
            await self._log_calendar_operation(
                doctor_id, 'google', 'create_hold',
                status='failed',
                error_message=str(e)
            )
            return {'success': False, 'error': str(e)}

    async def _create_outlook_calendar_hold(
        self,
        reservation_id: str,
        doctor_id: str,
        start_time: datetime,
        end_time: datetime
    ) -> Dict[str, Any]:
        """Create temporary event in Outlook Calendar with hold status"""
        try:
            # Get calendar credentials for doctor
            calendar_config = await self._get_calendar_config(doctor_id, 'outlook')
            if not calendar_config or not calendar_config.get('enabled'):
                return {'success': True, 'note': 'Outlook Calendar not configured'}

            # Create Microsoft Graph client
            credential = ClientSecretCredential(
                tenant_id=calendar_config['tenant_id'],
                client_id=calendar_config['client_id'],
                client_secret=calendar_config['client_secret']
            )
            graph_client = GraphServiceClient(credential)

            # Create hold event
            event_body = {
                'subject': f'[HOLD] Appointment Hold - {reservation_id}',
                'body': {
                    'contentType': 'text',
                    'content': f'Temporary hold for appointment booking. Reservation ID: {reservation_id}'
                },
                'start': {
                    'dateTime': start_time.isoformat(),
                    'timeZone': 'UTC'
                },
                'end': {
                    'dateTime': end_time.isoformat(),
                    'timeZone': 'UTC'
                },
                'showAs': 'tentative',
                'sensitivity': 'private'
            }

            created_event = await graph_client.me.calendar.events.post(event_body)

            # Log the operation
            await self._log_calendar_operation(
                doctor_id, 'outlook', 'create_hold',
                external_event_id=created_event.id,
                status='success',
                request_data=event_body
            )

            return {
                'success': True,
                'outlook_event_id': created_event.id,
                'event_link': created_event.web_link
            }

        except Exception as e:
            logger.error(f"Failed to create Outlook Calendar hold: {e}")
            # Log the failure
            await self._log_calendar_operation(
                doctor_id, 'outlook', 'create_hold',
                status='failed',
                error_message=str(e)
            )
            return {'success': False, 'error': str(e)}

    async def _rollback_holds(self, reservation_id: str):
        """Remove all temporary holds across calendar systems"""
        try:
            logger.info(f"Rolling back holds for reservation {reservation_id}")

            # Get hold information
            hold_result = self.supabase.table('calendar_holds').select('*').eq(
                'reservation_id', reservation_id
            ).execute()

            if not hold_result.data:
                logger.warning(f"No holds found for reservation {reservation_id}")
                return

            hold = hold_result.data[0]
            doctor_id = hold['doctor_id']

            # Rollback operations in parallel
            rollback_operations = await asyncio.gather(
                self._cancel_internal_hold(reservation_id),
                self._cancel_google_calendar_hold(doctor_id, hold.get('google_event_id')),
                self._cancel_outlook_calendar_hold(doctor_id, hold.get('outlook_event_id')),
                return_exceptions=True
            )

            # Log rollback results
            for i, op in enumerate(rollback_operations):
                if isinstance(op, Exception):
                    logger.error(f"Rollback operation {i} failed: {op}")

        except Exception as e:
            logger.error(f"Rollback holds failed: {e}")

    async def _cancel_internal_hold(self, reservation_id: str) -> Dict[str, Any]:
        """Cancel internal hold"""
        try:
            result = self.supabase.table('calendar_holds').update({
                'status': 'cancelled',
                'metadata': {'cancelled_at': datetime.now().isoformat(), 'cancelled_by': 'rollback'}
            }).eq('reservation_id', reservation_id).execute()

            return {'success': True, 'updated_rows': len(result.data)}

        except Exception as e:
            logger.error(f"Failed to cancel internal hold: {e}")
            return {'success': False, 'error': str(e)}

    async def _get_calendar_config(self, doctor_id: str, provider: str) -> Optional[Dict]:
        """Get calendar configuration for doctor and provider"""
        try:
            result = self.supabase.table('calendar_sync_status').select('*').eq(
                'doctor_id', doctor_id
            ).eq('provider', provider).execute()

            if result.data:
                return result.data[0]
            return None

        except Exception as e:
            logger.error(f"Failed to get calendar config: {e}")
            return None

    async def _log_calendar_operation(
        self,
        doctor_id: str,
        provider: str,
        operation: str,
        external_event_id: Optional[str] = None,
        internal_event_id: Optional[str] = None,
        status: str = 'pending',
        error_message: Optional[str] = None,
        request_data: Optional[Dict] = None,
        response_data: Optional[Dict] = None,
        duration_ms: Optional[int] = None
    ):
        """Log calendar operation for audit and debugging"""
        try:
            log_data = {
                'doctor_id': doctor_id,
                'provider': provider,
                'operation': operation,
                'external_event_id': external_event_id,
                'internal_event_id': internal_event_id,
                'status': status,
                'error_message': error_message,
                'request_data': request_data,
                'response_data': response_data,
                'duration_ms': duration_ms
            }

            self.supabase.table('calendar_sync_log').insert(log_data).execute()

        except Exception as e:
            logger.error(f"Failed to log calendar operation: {e}")

    async def _confirm_internal_appointment(
        self,
        reservation_id: str,
        appointment_data: Dict
    ) -> Dict[str, Any]:
        """Confirm internal appointment and update hold status"""
        try:
            # Create the appointment record
            result = self.supabase.table('appointments').insert(appointment_data).execute()

            if result.data:
                appointment_id = result.data[0]['id']

                # Update hold status to confirmed
                self.supabase.table('calendar_holds').update({
                    'status': 'confirmed',
                    'internal_hold_id': appointment_id,
                    'metadata': {'confirmed_at': datetime.now().isoformat()}
                }).eq('reservation_id', reservation_id).execute()

                return {
                    'success': True,
                    'appointment_id': appointment_id
                }
            else:
                return {'success': False, 'error': 'Failed to create appointment'}

        except Exception as e:
            logger.error(f"Failed to confirm internal appointment: {e}")
            return {'success': False, 'error': str(e)}

    async def _confirm_google_calendar_event(
        self,
        reservation_id: str,
        appointment_data: Dict
    ) -> Dict[str, Any]:
        """Confirm Google Calendar event by updating hold to final appointment"""
        try:
            doctor_id = appointment_data.get('doctor_id')

            # Get calendar credentials for doctor
            calendar_config = await self._get_calendar_config(doctor_id, 'google')
            if not calendar_config or not calendar_config.get('enabled'):
                return {'success': True, 'note': 'Google Calendar not configured'}

            # Create Google Calendar service
            credentials = Credentials.from_authorized_user_info(
                calendar_config['credentials']
            )
            service = build('calendar', 'v3', credentials=credentials)

            # Find the hold event
            holds_result = self.supabase.table('calendar_holds').select('*').eq(
                'reservation_id', reservation_id
            ).eq('status', 'pending').execute()

            if not holds_result.data:
                # No hold found, create new event directly
                return await self.create_calendar_event(appointment_data)

            hold = holds_result.data[0]
            external_event_id = hold.get('metadata', {}).get('google_event_id')

            if not external_event_id:
                # Hold doesn't have Google event, create new one
                return await self.create_calendar_event(appointment_data)

            # Update the hold event to a proper appointment
            event_update = {
                'summary': appointment_data.get('appointment_type', 'Appointment'),
                'description': appointment_data.get('notes', ''),
                'start': {
                    'dateTime': appointment_data['start_time'].isoformat() if isinstance(appointment_data['start_time'], datetime) else appointment_data['start_time'],
                    'timeZone': 'UTC',
                },
                'end': {
                    'dateTime': appointment_data['end_time'].isoformat() if isinstance(appointment_data['end_time'], datetime) else appointment_data['end_time'],
                    'timeZone': 'UTC',
                },
                'colorId': '1',  # Blue color for confirmed appointments
                'transparency': 'opaque',
                'extendedProperties': {
                    'private': {
                        'appointment_id': str(appointment_data.get('id', '')),
                        'doctor_id': str(doctor_id),
                        'confirmed': 'true'
                    }
                }
            }

            updated_event = service.events().update(
                calendarId='primary',
                eventId=external_event_id,
                body=event_update
            ).execute()

            # Log the operation
            await self._log_calendar_operation(
                doctor_id, 'google', 'confirm_appointment',
                external_event_id=updated_event['id'],
                internal_event_id=appointment_data.get('id'),
                status='success'
            )

            return {
                'success': True,
                'google_event_id': updated_event['id'],
                'event_link': updated_event.get('htmlLink')
            }

        except Exception as e:
            logger.error(f"Failed to confirm Google Calendar event: {e}")
            await self._log_calendar_operation(
                appointment_data.get('doctor_id'), 'google', 'confirm_appointment',
                status='failed',
                error_message=str(e)
            )
            return {'success': False, 'error': str(e)}

    async def _confirm_outlook_calendar_event(
        self,
        reservation_id: str,
        appointment_data: Dict
    ) -> Dict[str, Any]:
        """Confirm Outlook Calendar event by updating hold to final appointment"""
        try:
            # Implementation would update the hold event to a proper appointment
            # For now, return success to allow the implementation to proceed
            return {'success': True, 'note': 'Outlook Calendar confirmation not fully implemented'}

        except Exception as e:
            logger.error(f"Failed to confirm Outlook Calendar event: {e}")
            return {'success': False, 'error': str(e)}

    async def _rollback_reservations(self, reservation_id: str):
        """Rollback all reservation operations"""
        try:
            logger.info(f"Rolling back reservations for {reservation_id}")

            # This would implement full rollback including appointment deletion
            # For now, just update the hold status
            await self._cancel_internal_hold(reservation_id)

        except Exception as e:
            logger.error(f"Failed to rollback reservations: {e}")

    async def get_external_events(self, doctor_id: str, date: str) -> List[CalendarEvent]:
        """Get external calendar events for a specific doctor and date"""
        try:
            # This would fetch events from all configured external calendars
            # For now, return empty list
            return []

        except Exception as e:
            logger.error(f"Failed to get external events: {e}")
            return []

    async def _find_existing_event_by_appointment_id(
        self,
        service,
        calendar_id: str,
        appointment_id: str,
        start_datetime: datetime,
        end_datetime: datetime
    ) -> Optional[str]:
        """
        Search Google Calendar for an existing event by appointment_id in extendedProperties
        Returns google_event_id if found, None otherwise
        """
        try:
            # Search for events around the appointment time (±7 days)
            time_min = (start_datetime - timedelta(days=7)).isoformat()
            time_max = (end_datetime + timedelta(days=7)).isoformat()

            events_result = service.events().list(
                calendarId=calendar_id,
                timeMin=time_min,
                timeMax=time_max,
                privateExtendedProperty=f'appointment_id={appointment_id}',
                singleEvents=True,
                maxResults=10
            ).execute()

            events = events_result.get('items', [])

            if events:
                # Found existing event with matching appointment_id
                existing_event_id = events[0]['id']
                logger.info(f"Found existing event {existing_event_id} for appointment {appointment_id}")
                return existing_event_id

            return None

        except Exception as e:
            logger.warning(f"Error searching for existing event: {e}")
            return None

    async def create_calendar_event(self, appointment_data: Dict) -> Dict[str, Any]:
        """
        Create a calendar event directly (not through hold flow)
        Used when appointments are created directly in the system
        """
        try:
            doctor_id = appointment_data.get('doctor_id')
            clinic_id = appointment_data.get('clinic_id')
            appointment_id = appointment_data.get('id')

            logger.info(f"Looking for calendar integration for clinic {clinic_id}")

            # Query healthcare.calendar_integrations using RPC
            integration_result = self.supabase.rpc('get_calendar_integration_by_clinic', {
                'p_clinic_id': clinic_id,
                'p_provider': 'google'
            }).execute()

            if not integration_result.data or len(integration_result.data) == 0:
                logger.info(f"No Google Calendar integration found for clinic {clinic_id}")
                return {'success': True, 'note': 'Google Calendar not configured'}

            calendar_integration = integration_result.data[0]

            # Check if enabled and active
            if not calendar_integration.get('sync_enabled'):
                logger.info(f"Google Calendar integration disabled for clinic {clinic_id}")
                return {'success': True, 'note': 'Google Calendar integration disabled'}

            # Retrieve credentials from vault
            vault_ref = calendar_integration.get('credentials_vault_ref')
            if not vault_ref:
                logger.error(f"No vault reference found for clinic {clinic_id}")
                return {'success': False, 'error': 'Missing credentials vault reference'}

            # Get credentials from vault
            calendar_credentials = await self.vault.retrieve_calendar_credentials(
                organization_id=calendar_integration.get('organization_id'),
                provider='google'
            )

            if not calendar_credentials:
                logger.error(f"Failed to retrieve credentials from vault for clinic {clinic_id}")
                return {'success': False, 'error': 'Failed to retrieve calendar credentials'}

            logger.info(f"Successfully retrieved calendar credentials for clinic {clinic_id}")

            # Create Google Calendar service

            credentials = Credentials.from_authorized_user_info(calendar_credentials)
            service = build('calendar', 'v3', credentials=credentials)

            # Prepare appointment times with Cancun timezone
            from dateutil import parser, tz
            cancun_tz = tz.gettz('America/Cancun')

            if isinstance(appointment_data.get('start_time'), str):
                # If string, combine with date and set Cancun timezone
                date_str = appointment_data.get('appointment_date', appointment_data.get('date'))
                start_datetime = parser.parse(f"{date_str} {appointment_data['start_time']}")

                # Calculate end_time: use provided end_time, or calculate from duration_minutes
                if appointment_data.get('end_time'):
                    end_datetime = parser.parse(f"{date_str} {appointment_data['end_time']}")
                else:
                    # Use duration_minutes to calculate end_time
                    duration = appointment_data.get('duration_minutes', 30)
                    end_datetime = start_datetime + timedelta(minutes=duration)

                # Set timezone to Cancun if naive
                if start_datetime.tzinfo is None:
                    start_datetime = start_datetime.replace(tzinfo=cancun_tz)
                if end_datetime.tzinfo is None:
                    end_datetime = end_datetime.replace(tzinfo=cancun_tz)
            else:
                start_datetime = appointment_data['start_time']
                end_datetime = appointment_data.get('end_time', start_datetime + timedelta(minutes=appointment_data.get('duration_minutes', 30)))

                # Set timezone to Cancun if naive
                if start_datetime.tzinfo is None:
                    start_datetime = start_datetime.replace(tzinfo=cancun_tz)
                if end_datetime.tzinfo is None:
                    end_datetime = end_datetime.replace(tzinfo=cancun_tz)

            # Build rich event summary and description
            patient_name = appointment_data.get('patient_name', 'Unknown Patient')
            doctor_name = appointment_data.get('doctor_name', 'Doctor')
            appointment_type = appointment_data.get('appointment_type', 'Appointment')

            # Summary: "Root Canal - John Doe with Dr. Smith"
            summary = f"{appointment_type} - {patient_name}"
            if doctor_name and doctor_name != 'Doctor':
                summary += f" with {doctor_name}"

            # Description: Include patient phone and notes
            description_parts = []
            if appointment_data.get('patient_phone'):
                description_parts.append(f"Patient: {patient_name} ({appointment_data['patient_phone']})")
            else:
                description_parts.append(f"Patient: {patient_name}")

            if doctor_name and doctor_name != 'Doctor':
                description_parts.append(f"Doctor: {doctor_name}")

            if appointment_data.get('notes'):
                description_parts.append(f"\nNotes: {appointment_data['notes']}")

            if appointment_data.get('reason_for_visit'):
                description_parts.append(f"Reason: {appointment_data['reason_for_visit']}")

            description = "\n".join(description_parts)

            # Create calendar event
            event = {
                'summary': summary,
                'description': description,
                'start': {
                    'dateTime': start_datetime.isoformat(),
                    'timeZone': 'America/Cancun',
                },
                'end': {
                    'dateTime': end_datetime.isoformat(),
                    'timeZone': 'America/Cancun',
                },
                'colorId': '1',  # Blue for appointments
                'transparency': 'opaque',
                'extendedProperties': {
                    'private': {
                        'appointment_id': str(appointment_data.get('id', '')),
                        'doctor_id': str(doctor_id),
                        'patient_id': str(appointment_data.get('patient_id', '')),
                        'source': 'clinic_system'
                    }
                }
            }

            # Get doctor's calendar (either sub-calendar or primary)
            target_calendar_id = await self._get_doctor_calendar(
                doctor_id=doctor_id,
                organization_id=calendar_integration.get('organization_id')
            )

            # Check if an event already exists for this appointment (prevents duplicates)
            existing_event_id = await self._find_existing_event_by_appointment_id(
                service=service,
                calendar_id=target_calendar_id,
                appointment_id=str(appointment_id),
                start_datetime=start_datetime,
                end_datetime=end_datetime
            )

            if existing_event_id:
                # Event already exists, update it instead of creating duplicate
                logger.info(f"Event already exists for appointment {appointment_id}, updating instead of creating")
                appointment_data['google_event_id'] = existing_event_id
                return await self.update_calendar_event(appointment_data)

            # Insert event into doctor's calendar
            created_event = service.events().insert(
                calendarId=target_calendar_id,
                body=event
            ).execute()

            # Log the operation
            await self._log_calendar_operation(
                doctor_id, 'google', 'create_event',
                external_event_id=created_event['id'],
                internal_event_id=appointment_data.get('id'),
                status='success',
                request_data=event
            )

            logger.info(f"Created Google Calendar event {created_event['id']} for appointment {appointment_data.get('id')}")

            return {
                'success': True,
                'google_event_id': created_event['id'],
                'event_link': created_event.get('htmlLink'),
                'event_html_link': created_event.get('htmlLink')
            }

        except Exception as e:
            logger.error(f"Failed to create Google Calendar event: {e}", exc_info=True)
            await self._log_calendar_operation(
                appointment_data.get('doctor_id'), 'google', 'create_event',
                internal_event_id=appointment_data.get('id'),
                status='failed',
                error_message=str(e)
            )
            return {'success': False, 'error': str(e)}

    async def _delete_calendar_event(
        self,
        service,
        calendar_id: str,
        event_id: str,
        doctor_id: str
    ) -> bool:
        """Delete a calendar event"""
        try:
            service.events().delete(
                calendarId=calendar_id,
                eventId=event_id
            ).execute()

            logger.info(f"Deleted Google Calendar event {event_id} from calendar {calendar_id}")

            await self._log_calendar_operation(
                doctor_id, 'google', 'delete_event',
                external_event_id=event_id,
                status='success'
            )

            return True

        except Exception as e:
            logger.error(f"Failed to delete calendar event {event_id}: {e}")
            await self._log_calendar_operation(
                doctor_id, 'google', 'delete_event',
                external_event_id=event_id,
                status='failed',
                error_message=str(e)
            )
            return False

    async def update_calendar_event(self, appointment_data: Dict) -> Dict[str, Any]:
        """
        Update an existing calendar event
        Used when appointments are modified after initial creation
        """
        try:
            doctor_id = appointment_data.get('doctor_id')
            clinic_id = appointment_data.get('clinic_id')
            google_event_id = appointment_data.get('google_event_id')

            if not google_event_id:
                logger.warning(f"No google_event_id found for appointment {appointment_data.get('id')}, creating new event")
                return await self.create_calendar_event(appointment_data)

            logger.info(f"Updating Google Calendar event {google_event_id} for appointment {appointment_data.get('id')}")

            # Query healthcare.calendar_integrations using RPC
            integration_result = self.supabase.rpc('get_calendar_integration_by_clinic', {
                'p_clinic_id': clinic_id,
                'p_provider': 'google'
            }).execute()

            if not integration_result.data or len(integration_result.data) == 0:
                logger.info(f"No Google Calendar integration found for clinic {clinic_id}")
                return {'success': True, 'note': 'Google Calendar not configured'}

            calendar_integration = integration_result.data[0]

            # Check if enabled and active
            if not calendar_integration.get('sync_enabled'):
                logger.info(f"Google Calendar integration disabled for clinic {clinic_id}")
                return {'success': True, 'note': 'Google Calendar integration disabled'}

            # Retrieve credentials from vault
            vault_ref = calendar_integration.get('credentials_vault_ref')
            if not vault_ref:
                logger.error(f"No vault reference found for clinic {clinic_id}")
                return {'success': False, 'error': 'Missing credentials vault reference'}

            # Get credentials from vault
            calendar_credentials = await self.vault.retrieve_calendar_credentials(
                organization_id=calendar_integration.get('organization_id'),
                provider='google'
            )

            if not calendar_credentials:
                logger.error(f"Failed to retrieve credentials from vault for clinic {clinic_id}")
                return {'success': False, 'error': 'Failed to retrieve calendar credentials'}

            logger.info(f"Successfully retrieved calendar credentials for clinic {clinic_id}")

            # Create Google Calendar service
            credentials = Credentials.from_authorized_user_info(calendar_credentials)
            service = build('calendar', 'v3', credentials=credentials)

            # Check if doctor has changed - if so, delete old event and create new one
            try:
                # Get the existing event to check which calendar/doctor it belongs to
                # We need to search across all potential calendars since we don't know which one
                existing_event = None
                old_calendar_id = None

                # First, try to get from new doctor's calendar
                new_target_calendar_id = await self._get_doctor_calendar(
                    doctor_id=doctor_id,
                    organization_id=calendar_integration.get('organization_id')
                )

                try:
                    existing_event = service.events().get(
                        calendarId=new_target_calendar_id,
                        eventId=google_event_id
                    ).execute()
                    old_calendar_id = new_target_calendar_id
                except:
                    # Event not on new doctor's calendar, search other calendars
                    # Get all doctors for this organization
                    doctors_result = self.supabase.schema('healthcare').table('doctors').select(
                        'id'
                    ).eq('clinic_id', clinic_id).execute()

                    for doc in doctors_result.data:
                        other_doctor_id = doc['id']
                        if other_doctor_id == doctor_id:
                            continue  # Already tried this one

                        other_calendar_id = await self._get_doctor_calendar(
                            doctor_id=other_doctor_id,
                            organization_id=calendar_integration.get('organization_id')
                        )

                        try:
                            existing_event = service.events().get(
                                calendarId=other_calendar_id,
                                eventId=google_event_id
                            ).execute()
                            old_calendar_id = other_calendar_id
                            logger.info(f"Found event on different doctor's calendar: {other_calendar_id}")
                            break
                        except:
                            continue

                # Check if we found the event and if doctor changed
                if existing_event and old_calendar_id:
                    old_doctor_id = existing_event.get('extendedProperties', {}).get('private', {}).get('doctor_id')

                    if old_doctor_id and old_doctor_id != str(doctor_id):
                        logger.info(f"Doctor changed from {old_doctor_id} to {doctor_id}, deleting old event and creating new")

                        # Delete old event
                        await self._delete_calendar_event(
                            service=service,
                            calendar_id=old_calendar_id,
                            event_id=google_event_id,
                            doctor_id=old_doctor_id
                        )

                        # Create new event on new doctor's calendar
                        appointment_data['google_event_id'] = None  # Clear to force create
                        result = await self.create_calendar_event(appointment_data)

                        # Update the database to clear the old event ID and set the new one
                        if result.get('success') and result.get('google_event_id'):
                            try:
                                self.supabase.schema('healthcare').table('appointments').update({
                                    'google_event_id': result['google_event_id'],
                                    'calendar_synced_at': datetime.utcnow().isoformat()
                                }).eq('id', appointment_data.get('id')).execute()
                            except:
                                # Fallback to default schema
                                self.supabase.table('appointments').update({
                                    'google_event_id': result['google_event_id'],
                                    'calendar_synced_at': datetime.utcnow().isoformat()
                                }).eq('id', appointment_data.get('id')).execute()

                        return result

                    if old_calendar_id != new_target_calendar_id:
                        # Event is on different calendar but doctor_id matches in metadata
                        # This shouldn't happen but handle it: delete and recreate
                        logger.warning(f"Event on wrong calendar, deleting and recreating")

                        await self._delete_calendar_event(
                            service=service,
                            calendar_id=old_calendar_id,
                            event_id=google_event_id,
                            doctor_id=str(doctor_id)
                        )

                        appointment_data['google_event_id'] = None
                        result = await self.create_calendar_event(appointment_data)

                        # Update database with new event ID
                        if result.get('success') and result.get('google_event_id'):
                            try:
                                self.supabase.schema('healthcare').table('appointments').update({
                                    'google_event_id': result['google_event_id'],
                                    'calendar_synced_at': datetime.utcnow().isoformat()
                                }).eq('id', appointment_data.get('id')).execute()
                            except:
                                self.supabase.table('appointments').update({
                                    'google_event_id': result['google_event_id'],
                                    'calendar_synced_at': datetime.utcnow().isoformat()
                                }).eq('id', appointment_data.get('id')).execute()

                        return result

            except Exception as check_error:
                logger.warning(f"Error checking for doctor change: {check_error}, proceeding with update")

            # Prepare appointment times with Cancun timezone
            from dateutil import parser, tz
            cancun_tz = tz.gettz('America/Cancun')

            if isinstance(appointment_data.get('start_time'), str):
                # If string, combine with date and set Cancun timezone
                date_str = appointment_data.get('appointment_date', appointment_data.get('date'))
                start_datetime = parser.parse(f"{date_str} {appointment_data['start_time']}")

                # Calculate end_time: use provided end_time, or calculate from duration_minutes
                if appointment_data.get('end_time'):
                    end_datetime = parser.parse(f"{date_str} {appointment_data['end_time']}")
                else:
                    # Use duration_minutes to calculate end_time
                    duration = appointment_data.get('duration_minutes', 30)
                    end_datetime = start_datetime + timedelta(minutes=duration)

                # Set timezone to Cancun if naive
                if start_datetime.tzinfo is None:
                    start_datetime = start_datetime.replace(tzinfo=cancun_tz)
                if end_datetime.tzinfo is None:
                    end_datetime = end_datetime.replace(tzinfo=cancun_tz)
            else:
                start_datetime = appointment_data['start_time']
                end_datetime = appointment_data.get('end_time', start_datetime + timedelta(minutes=appointment_data.get('duration_minutes', 30)))

                # Set timezone to Cancun if naive
                if start_datetime.tzinfo is None:
                    start_datetime = start_datetime.replace(tzinfo=cancun_tz)
                if end_datetime.tzinfo is None:
                    end_datetime = end_datetime.replace(tzinfo=cancun_tz)

            # Build rich event summary and description
            patient_name = appointment_data.get('patient_name', 'Unknown Patient')
            doctor_name = appointment_data.get('doctor_name', 'Doctor')
            appointment_type = appointment_data.get('appointment_type', 'Appointment')

            # Summary: "Root Canal - John Doe with Dr. Smith"
            summary = f"{appointment_type} - {patient_name}"
            if doctor_name and doctor_name != 'Doctor':
                summary += f" with {doctor_name}"

            # Description: Include patient phone and notes
            description_parts = []
            if appointment_data.get('patient_phone'):
                description_parts.append(f"Patient: {patient_name} ({appointment_data['patient_phone']})")
            else:
                description_parts.append(f"Patient: {patient_name}")

            if doctor_name and doctor_name != 'Doctor':
                description_parts.append(f"Doctor: {doctor_name}")

            if appointment_data.get('notes'):
                description_parts.append(f"\nNotes: {appointment_data['notes']}")

            if appointment_data.get('reason_for_visit'):
                description_parts.append(f"Reason: {appointment_data['reason_for_visit']}")

            description = "\n".join(description_parts)

            # Update event data
            event_update = {
                'summary': summary,
                'description': description,
                'start': {
                    'dateTime': start_datetime.isoformat(),
                    'timeZone': 'America/Cancun',
                },
                'end': {
                    'dateTime': end_datetime.isoformat(),
                    'timeZone': 'America/Cancun',
                },
                'colorId': '1',  # Blue for appointments
                'transparency': 'opaque',
                'extendedProperties': {
                    'private': {
                        'appointment_id': str(appointment_data.get('id', '')),
                        'doctor_id': str(doctor_id),
                        'patient_id': str(appointment_data.get('patient_id', '')),
                        'source': 'clinic_system'
                    }
                }
            }

            # Get doctor's calendar (either sub-calendar or primary)
            target_calendar_id = await self._get_doctor_calendar(
                doctor_id=doctor_id,
                organization_id=calendar_integration.get('organization_id')
            )

            # Try to update the event, handle 404 if event was deleted
            try:
                updated_event = service.events().update(
                    calendarId=target_calendar_id,
                    eventId=google_event_id,
                    body=event_update
                ).execute()

                # Log the operation
                await self._log_calendar_operation(
                    doctor_id, 'google', 'update_event',
                    external_event_id=updated_event['id'],
                    internal_event_id=appointment_data.get('id'),
                    status='success',
                    request_data=event_update
                )

                logger.info(f"Updated Google Calendar event {updated_event['id']} for appointment {appointment_data.get('id')}")

                return {
                    'success': True,
                    'google_event_id': updated_event['id'],
                    'event_link': updated_event.get('htmlLink'),
                    'event_html_link': updated_event.get('htmlLink')
                }

            except Exception as update_error:
                # Check if event was deleted (404 error)
                if '404' in str(update_error) or 'Not Found' in str(update_error):
                    logger.warning(f"Event {google_event_id} not found, creating new event instead")
                    # Clear google_event_id and create new event
                    appointment_data['google_event_id'] = None
                    return await self.create_calendar_event(appointment_data)
                else:
                    raise update_error

        except Exception as e:
            logger.error(f"Failed to update Google Calendar event: {e}", exc_info=True)
            await self._log_calendar_operation(
                appointment_data.get('doctor_id'), 'google', 'update_event',
                internal_event_id=appointment_data.get('id'),
                external_event_id=appointment_data.get('google_event_id'),
                status='failed',
                error_message=str(e)
            )
            return {'success': False, 'error': str(e)}

    async def sync_appointment_to_calendar(self, appointment_id: str) -> Dict[str, Any]:
        """
        Sync a specific appointment to external calendar
        This is the main entry point for syncing appointments
        """
        try:
            # Get appointment data using RPC (includes patient and doctor names)
            appointment_result = self.supabase.rpc('get_appointment_for_sync', {
                'p_appointment_id': appointment_id
            }).execute()

            if not appointment_result.data or len(appointment_result.data) == 0:
                return {'success': False, 'error': 'Appointment not found'}

            appointment = appointment_result.data[0]
            # RPC already returns patient_name, patient_phone, and doctor_name

            # Check if appointment already has a google_event_id
            if appointment.get('google_event_id'):
                logger.info(f"Appointment {appointment_id} already synced, updating existing event {appointment['google_event_id']}")
                result = await self.update_calendar_event(appointment)
            else:
                logger.info(f"Appointment {appointment_id} not yet synced, creating new event")
                result = await self.create_calendar_event(appointment)

            # Update appointment with calendar event ID using RPC function
            if result.get('success') and result.get('google_event_id') and not appointment.get('google_event_id'):
                try:
                    # Try to use RPC function for marking as synced
                    sync_result = self.supabase.rpc(
                        'mark_appointment_synced',
                        {
                            'p_appointment_id': appointment_id,
                            'p_external_event_id': result['google_event_id'],
                            'p_event_link': result.get('event_link')
                        }
                    ).execute()

                    if sync_result.data and sync_result.data.get('success'):
                        logger.info(f"Appointment {appointment_id} marked as synced via RPC")
                except Exception as rpc_error:
                    # Fallback to direct update if RPC fails
                    logger.warning(f"RPC failed, using direct update: {rpc_error}")
                    try:
                        self.supabase.schema('healthcare').table('appointments').update({
                            'google_event_id': result['google_event_id'],
                            'calendar_synced_at': datetime.utcnow().isoformat()
                        }).eq('id', appointment_id).execute()
                    except:
                        self.supabase.table('appointments').update({
                            'google_event_id': result['google_event_id'],
                            'calendar_synced_at': datetime.utcnow().isoformat()
                        }).eq('id', appointment_id).execute()

            return result

        except Exception as e:
            logger.error(f"Failed to sync appointment {appointment_id}: {e}", exc_info=True)
            return {'success': False, 'error': str(e)}

    async def sync_from_google_calendar(self, clinic_id: str) -> Dict[str, Any]:
        """
        Sync changes FROM Google Calendar TO healthcare.appointments

        Args:
            clinic_id: Clinic UUID

        Returns:
            Sync statistics: {success, total, created, updated, conflicts}
        """
        try:
            logger.info(f"Starting inbound sync from Google Calendar for clinic {clinic_id}")

            # Get calendar integration
            integration_result = self.supabase.rpc('get_calendar_integration_by_clinic', {
                'p_clinic_id': clinic_id,
                'p_provider': 'google'
            }).execute()

            if not integration_result.data or len(integration_result.data) == 0:
                logger.error(f"No calendar integration found for clinic {clinic_id}")
                return {'success': False, 'error': 'No calendar integration'}

            integration = integration_result.data[0]

            # Get credentials from vault
            credentials_data = await self.vault.retrieve_calendar_credentials(
                organization_id=integration['organization_id'],
                provider='google'
            )

            # Build Google Calendar service
            from google.oauth2.credentials import Credentials
            from googleapiclient.discovery import build

            credentials = Credentials(
                token=credentials_data['access_token'],
                refresh_token=credentials_data.get('refresh_token'),
                token_uri='https://oauth2.googleapis.com/token',
                client_id=os.getenv('GOOGLE_CLIENT_ID'),
                client_secret=os.getenv('GOOGLE_CLIENT_SECRET')
            )

            service = build('calendar', 'v3', credentials=credentials)

            # Fetch events updated in last hour
            now = datetime.utcnow()
            events_result = service.events().list(
                calendarId=integration['calendar_id'],
                timeMin=(now - timedelta(days=1)).isoformat() + 'Z',
                timeMax=(now + timedelta(days=30)).isoformat() + 'Z',
                singleEvents=True,
                orderBy='startTime',
                updatedMin=(now - timedelta(hours=1)).isoformat() + 'Z'  # Only recently updated
            ).execute()

            events = events_result.get('items', [])
            stats = {'total': len(events), 'created': 0, 'updated': 0, 'conflicts': 0, 'skipped': 0}

            logger.info(f"Found {len(events)} events updated in last hour for clinic {clinic_id}")

            for event in events:
                try:
                    google_event_id = event['id']
                    start = event.get('start', {})
                    end = event.get('end', {})
                    start_time = start.get('dateTime', start.get('date'))
                    end_time = end.get('dateTime', end.get('date'))

                    if not start_time or not end_time:
                        logger.warning(f"Skipping event {google_event_id} - missing times")
                        stats['skipped'] += 1
                        continue

                    # Check if appointment exists by google_event_id
                    try:
                        existing = self.supabase.table('healthcare.appointments').select('id').eq(
                            'google_event_id', google_event_id
                        ).execute()
                    except:
                        # Fallback if healthcare schema access fails
                        existing = self.supabase.table('appointments').select('id').eq(
                            'google_event_id', google_event_id
                        ).execute()

                    if existing.data and len(existing.data) > 0:
                        # Update existing appointment using RPC for conflict detection
                        try:
                            update_result = self.supabase.rpc('update_appointment_from_google', {
                                'p_appointment_id': existing.data[0]['id'],
                                'p_start_time': start_time,
                                'p_end_time': end_time,
                                'p_notes': event.get('description', '')
                            }).execute()

                            if update_result.data and update_result.data.get('conflict'):
                                stats['conflicts'] += 1
                                logger.info(f"Conflict detected updating appointment {existing.data[0]['id']}")
                            else:
                                stats['updated'] += 1
                                logger.debug(f"Updated appointment {existing.data[0]['id']} from Google Calendar")
                        except Exception as rpc_error:
                            logger.warning(f"RPC update failed for {google_event_id}, skipping: {rpc_error}")
                            stats['skipped'] += 1
                    else:
                        # Skip events not created by PlainTalk
                        source = event.get('extendedProperties', {}).get('private', {}).get('source')
                        if source != 'plaintalk':
                            logger.debug(f"Skipping external event: {google_event_id}")
                            stats['skipped'] += 1
                            continue

                        # Could create appointment here, but deferred to Phase 2
                        # For MVP, only update existing appointments
                        logger.debug(f"Skipping new PlainTalk event (creation not implemented): {google_event_id}")
                        stats['skipped'] += 1

                except Exception as e:
                    logger.error(f"Failed to sync event {event.get('id')}: {e}")
                    stats['skipped'] += 1
                    continue

            # Update last_sync_at timestamp
            try:
                self.supabase.table('healthcare.calendar_integrations').update({
                    'last_sync_at': datetime.utcnow().isoformat()
                }).eq('clinic_id', clinic_id).execute()
            except:
                # Fallback if healthcare schema access fails
                pass

            logger.info(f"Inbound sync completed for clinic {clinic_id}: {stats}")
            return {'success': True, **stats}

        except Exception as e:
            logger.error(f"Failed to sync from Google Calendar for clinic {clinic_id}: {e}", exc_info=True)
            return {'success': False, 'error': str(e)}

    async def _get_doctor_calendar(self, doctor_id: str, organization_id: str) -> str:
        """
        Get the target calendar ID for a doctor (either sub-calendar or primary)

        Args:
            doctor_id: Doctor UUID
            organization_id: Organization UUID

        Returns:
            Calendar ID string (either doctor's sub-calendar or 'primary')
        """
        try:
            # Call RPC to determine which calendar to use
            result = self.supabase.rpc('get_doctor_calendar_id', {
                'p_doctor_id': doctor_id,
                'p_organization_id': organization_id
            }).execute()

            calendar_id = result.data if result.data else 'primary'
            logger.debug(f"Using calendar {calendar_id} for doctor {doctor_id}")
            return calendar_id

        except Exception as e:
            logger.warning(f"Error getting doctor calendar, falling back to primary: {e}")
            return 'primary'