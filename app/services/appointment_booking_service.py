"""
Appointment Booking Service with Calendar Integration
Handles appointment scheduling with idempotency and calendar sync

⚠️ DEPRECATION NOTICE:
This service is deprecated as of Phase C (2025-11-28).
All functionality has been merged into UnifiedAppointmentService.

New code should use:
    from app.services.unified_appointment_service import UnifiedAppointmentService

This class is maintained for backward compatibility only and will be removed in a future version.
"""

import os
import uuid
import hashlib
import logging
import warnings
from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime, timedelta, date, time
from supabase import create_client, Client
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
import aiohttp

from ..security.compliance_vault import ComplianceVault
from ..security.compliance_manager import ComplianceManager

logger = logging.getLogger(__name__)


class AppointmentBookingService:
    """
    ⚠️ DEPRECATED: Use UnifiedAppointmentService instead.

    Manages appointment booking with calendar synchronization.
    This class is maintained for backward compatibility only.

    Migration Guide:
    ----------------
    Old code:
        booking_service = AppointmentBookingService(supabase)
        result = await booking_service.book_appointment(phone, clinic_id, details, key)

    New code:
        from app.services.unified_appointment_service import UnifiedAppointmentService, AppointmentRequest, AppointmentType
        unified_service = UnifiedAppointmentService(supabase)
        request = AppointmentRequest(
            patient_id=patient_id,
            doctor_id=details['doctor_id'],
            clinic_id=clinic_id,
            start_time=datetime.fromisoformat(f"{details['date']} {details['time']}"),
            end_time=...,
            appointment_type=AppointmentType.CONSULTATION,
            patient_phone=phone
        )
        result = await unified_service.book_appointment(request, idempotency_key=key, source_channel='whatsapp')
    """

    def __init__(self, supabase_client: Client = None):
        warnings.warn(
            "AppointmentBookingService is deprecated. Use UnifiedAppointmentService instead.",
            DeprecationWarning,
            stacklevel=2
        )
        if supabase_client:
            self.supabase = supabase_client
        else:
            self.supabase: Client = create_client(
                os.environ.get("SUPABASE_URL"),
                os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
            )

        self.vault = ComplianceVault()
        self.compliance = ComplianceManager()

    async def book_appointment(
        self,
        patient_phone: str,
        clinic_id: str,
        appointment_details: Dict[str, Any],
        idempotency_key: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Book an appointment with full idempotency and calendar sync

        Args:
            patient_phone: Patient's phone number
            clinic_id: Clinic ID
            appointment_details: Appointment information
            idempotency_key: Optional idempotency key

        Returns:
            Booking result with confirmation details
        """
        try:
            # Generate idempotency key if not provided
            if not idempotency_key:
                idempotency_key = self._generate_idempotency_key(
                    patient_phone,
                    appointment_details
                )

            # Check for existing booking with same idempotency key
            existing = await self._check_idempotency(idempotency_key, clinic_id)
            if existing:
                logger.info(f"Returning existing booking for idempotency key: {idempotency_key}")
                return existing['response_payload']

            # Record idempotency attempt
            await self._record_idempotency_attempt(
                idempotency_key,
                clinic_id,
                appointment_details
            )

            # Get or create patient
            patient = await self._get_or_create_patient(patient_phone, clinic_id, appointment_details)

            # Validate appointment slot availability
            slot_available = await self._check_slot_availability(
                clinic_id,
                appointment_details['doctor_id'],
                appointment_details['date'],
                appointment_details['time']
            )

            if not slot_available:
                alternatives = await self._suggest_alternatives(
                    clinic_id,
                    appointment_details['doctor_id'],
                    appointment_details['date']
                )

                return {
                    'success': False,
                    'reason': 'slot_unavailable',
                    'message': 'The requested time slot is not available',
                    'alternatives': alternatives
                }

            # Create appointment hold
            hold_id = await self._create_appointment_hold(
                clinic_id,
                patient['id'],
                appointment_details,
                idempotency_key
            )

            try:
                # Create appointment in database
                appointment = await self._create_appointment_record(
                    clinic_id,
                    patient['id'],
                    appointment_details
                )

                # Sync to external calendar
                calendar_event = await self._sync_to_calendar(
                    clinic_id,
                    appointment_details['doctor_id'],
                    appointment,
                    patient
                )

                # Confirm the hold
                await self._confirm_appointment_hold(hold_id, appointment['id'])

                # Schedule reminders
                await self._schedule_reminders(appointment['id'], patient['phone'])

                # Update idempotency record with success
                result = {
                    'success': True,
                    'appointment_id': appointment['id'],
                    'confirmation_number': self._generate_confirmation_number(appointment['id']),
                    'appointment': {
                        'date': appointment['appointment_date'],
                        'time': appointment['start_time'],
                        'doctor': appointment_details.get('doctor_name'),
                        'service': appointment_details.get('service_name'),
                        'duration': appointment_details.get('duration_minutes', 30)
                    },
                    'calendar_synced': bool(calendar_event),
                    'reminder_scheduled': True,
                    'message': 'Your appointment has been successfully booked!'
                }

                await self._update_idempotency_success(idempotency_key, result)

                # Audit log
                await self.compliance.soc2_audit_trail(
                    operation='appointment_booked',
                    details={
                        'appointment_id': appointment['id'],
                        'patient_id': patient['id'],
                        'clinic_id': clinic_id,
                        'idempotency_key': idempotency_key
                    },
                    organization_id=clinic_id
                )

                return result

            except Exception as e:
                # Release the hold on error
                await self._cancel_appointment_hold(hold_id)
                raise

        except Exception as e:
            logger.error(f"Failed to book appointment: {str(e)}")

            # Update idempotency record with failure
            await self._update_idempotency_failure(idempotency_key, str(e))

            return {
                'success': False,
                'reason': 'booking_error',
                'message': 'Failed to book appointment. Please try again.',
                'error': str(e)
            }

    def _generate_idempotency_key(
        self,
        patient_phone: str,
        appointment_details: Dict[str, Any]
    ) -> str:
        """Generate deterministic idempotency key"""
        key_data = f"{patient_phone}:{appointment_details['date']}:{appointment_details['time']}:{appointment_details.get('doctor_id', '')}:{appointment_details.get('service_id', '')}"
        return hashlib.sha256(key_data.encode()).hexdigest()

    async def _check_idempotency(
        self,
        idempotency_key: str,
        clinic_id: str
    ) -> Optional[Dict]:
        """Check for existing booking with idempotency key"""

        # Get organization ID for clinic
        clinic_result = self.supabase.table('healthcare.clinics').select(
            'organization_id'
        ).eq('id', clinic_id).single().execute()

        if not clinic_result.data:
            return None

        result = self.supabase.table('healthcare.booking_idempotency').select('*').eq(
            'idempotency_key', idempotency_key
        ).eq(
            'organization_id', clinic_result.data['organization_id']
        ).eq(
            'status', 'completed'
        ).single().execute()

        return result.data if result.data else None

    async def _record_idempotency_attempt(
        self,
        idempotency_key: str,
        clinic_id: str,
        appointment_details: Dict[str, Any]
    ):
        """Record idempotency attempt"""

        # Get organization ID
        clinic_result = self.supabase.table('healthcare.clinics').select(
            'organization_id'
        ).eq('id', clinic_id).single().execute()

        if clinic_result.data:
            self.supabase.table('healthcare.booking_idempotency').insert({
                'idempotency_key': idempotency_key,
                'organization_id': clinic_result.data['organization_id'],
                'request_payload': appointment_details,
                'request_timestamp': datetime.utcnow().isoformat(),
                'request_source': 'whatsapp',
                'status': 'processing'
            }).execute()

    async def _get_or_create_patient(
        self,
        patient_phone: str,
        clinic_id: str,
        appointment_details: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Get existing patient or create new one"""

        # Clean phone number
        clean_phone = patient_phone.replace('whatsapp:', '').replace('+', '')

        # Check for existing patient
        result = self.supabase.table('healthcare.patients').select('*').eq(
            'phone', clean_phone
        ).eq('clinic_id', clinic_id).single().execute()

        if result.data:
            return result.data

        # Create new patient with provided details
        first_name = (appointment_details.get('first_name') or '').strip() or 'Pending'
        last_name = (appointment_details.get('last_name') or '').strip() or 'Registration'
        date_of_birth = (
            appointment_details.get('dob')
            or appointment_details.get('date_of_birth')
            or '2000-01-01'
        )

        patient_data = {
            'id': str(uuid.uuid4()),
            'clinic_id': clinic_id,
            'phone': clean_phone,
            'first_name': first_name,
            'last_name': last_name,
            'date_of_birth': date_of_birth,
            'email': appointment_details.get('email'),
            'preferred_contact_method': 'whatsapp',
            'registered_date': datetime.utcnow().date().isoformat()
        }

        result = self.supabase.table('healthcare.patients').insert(patient_data).execute()

        return result.data[0] if result.data else patient_data

    async def _check_slot_availability(
        self,
        clinic_id: str,
        doctor_id: str,
        appointment_date: str,
        appointment_time: str
    ) -> bool:
        """Check if appointment slot is available"""

        # Convert time string to proper format
        start_datetime = datetime.fromisoformat(f"{appointment_date}T{appointment_time}")
        end_datetime = start_datetime + timedelta(minutes=30)  # Default duration

        # Check for existing appointments at this time
        result = self.supabase.table('healthcare.appointments').select('id').eq(
            'doctor_id', doctor_id
        ).eq(
            'appointment_date', appointment_date
        ).gte(
            'start_time', start_datetime.time().isoformat()
        ).lt(
            'start_time', end_datetime.time().isoformat()
        ).eq(
            'status', 'scheduled'
        ).execute()

        if result.data:
            return False  # Slot is taken

        # Check calendar availability
        calendar_available = await self._check_calendar_availability(
            clinic_id,
            doctor_id,
            start_datetime,
            end_datetime
        )

        return calendar_available

    async def _check_calendar_availability(
        self,
        clinic_id: str,
        doctor_id: str,
        start_time: datetime,
        end_time: datetime
    ) -> bool:
        """Check external calendar for availability"""

        # Get calendar integration
        result = self.supabase.table('healthcare.calendar_integrations').select('*').eq(
            'doctor_id', doctor_id
        ).eq('sync_enabled', True).single().execute()

        if not result.data:
            return True  # No calendar integration, assume available

        integration = result.data

        try:
            # Retrieve credentials
            credentials = await self.vault.retrieve_calendar_credentials(
                organization_id=clinic_id,
                provider=integration['provider']
            )

            if integration['provider'] == 'google':
                return await self._check_google_calendar(
                    credentials,
                    integration['calendar_id'],
                    start_time,
                    end_time
                )
            elif integration['provider'] == 'outlook':
                return await self._check_outlook_calendar(
                    credentials,
                    integration['calendar_id'],
                    start_time,
                    end_time
                )

        except Exception as e:
            logger.error(f"Failed to check calendar availability: {str(e)}")

        return True  # Default to available if check fails

    async def _check_google_calendar(
        self,
        credentials: Dict,
        calendar_id: str,
        start_time: datetime,
        end_time: datetime
    ) -> bool:
        """Check Google Calendar for conflicts"""

        try:
            creds = Credentials(
                token=credentials['access_token'],
                refresh_token=credentials.get('refresh_token'),
                token_uri='https://oauth2.googleapis.com/token',
                client_id=os.environ.get('GOOGLE_CLIENT_ID'),
                client_secret=os.environ.get('GOOGLE_CLIENT_SECRET')
            )

            service = build('calendar', 'v3', credentials=creds, cache_discovery=False)

            # Query for events in the time range
            events_result = service.events().list(
                calendarId=calendar_id or 'primary',
                timeMin=start_time.isoformat() + 'Z',
                timeMax=end_time.isoformat() + 'Z',
                singleEvents=True,
                orderBy='startTime'
            ).execute()

            events = events_result.get('items', [])

            # If no events, slot is available
            return len(events) == 0

        except Exception as e:
            logger.error(f"Google Calendar check failed: {str(e)}")
            return True

    async def _check_outlook_calendar(
        self,
        credentials: Dict,
        calendar_id: str,
        start_time: datetime,
        end_time: datetime
    ) -> bool:
        """Check Outlook Calendar for conflicts"""

        try:
            async with aiohttp.ClientSession() as session:
                headers = {
                    'Authorization': f"Bearer {credentials['access_token']}",
                    'Content-Type': 'application/json'
                }

                # Query for busy times
                data = {
                    'schedules': [calendar_id],
                    'startTime': {
                        'dateTime': start_time.isoformat(),
                        'timeZone': 'UTC'
                    },
                    'endTime': {
                        'dateTime': end_time.isoformat(),
                        'timeZone': 'UTC'
                    }
                }

                async with session.post(
                    'https://graph.microsoft.com/v1.0/me/calendar/getSchedule',
                    headers=headers,
                    json=data
                ) as response:
                    if response.status == 200:
                        result = await response.json()
                        schedules = result.get('value', [])

                        if schedules:
                            # Check if any busy times overlap
                            busy_times = schedules[0].get('scheduleItems', [])
                            return len(busy_times) == 0

            return True

        except Exception as e:
            logger.error(f"Outlook Calendar check failed: {str(e)}")
            return True

    async def _suggest_alternatives(
        self,
        clinic_id: str,
        doctor_id: str,
        preferred_date: str
    ) -> List[Dict[str, str]]:
        """Suggest alternative appointment slots"""

        alternatives = []
        base_date = datetime.fromisoformat(preferred_date)

        # Check next 7 days
        for days_ahead in range(7):
            check_date = base_date + timedelta(days=days_ahead)

            # Skip weekends
            if check_date.weekday() >= 5:
                continue

            # Check standard appointment times
            for hour in [9, 10, 11, 14, 15, 16]:
                time_slot = time(hour, 0)

                available = await self._check_slot_availability(
                    clinic_id,
                    doctor_id,
                    check_date.date().isoformat(),
                    time_slot.isoformat()
                )

                if available:
                    alternatives.append({
                        'date': check_date.date().isoformat(),
                        'time': time_slot.isoformat(),
                        'display': f"{check_date.strftime('%A, %B %d')} at {time_slot.strftime('%I:%M %p')}"
                    })

                if len(alternatives) >= 5:
                    return alternatives

        return alternatives

    async def _create_appointment_hold(
        self,
        clinic_id: str,
        patient_id: str,
        appointment_details: Dict[str, Any],
        idempotency_key: str
    ) -> str:
        """Create temporary appointment hold"""

        # Get organization ID
        clinic_result = self.supabase.table('healthcare.clinics').select(
            'organization_id'
        ).eq('id', clinic_id).single().execute()

        if not clinic_result.data:
            raise ValueError("Clinic not found")

        hold_data = {
            'hold_id': str(uuid.uuid4()),
            'organization_id': clinic_result.data['organization_id'],
            'appointment_slot': appointment_details,
            'patient_identifier': patient_id,
            'idempotency_key': idempotency_key,
            'status': 'active',
            'expires_at': (datetime.utcnow() + timedelta(minutes=15)).isoformat()
        }

        result = self.supabase.table('healthcare.appointment_holds').insert(
            hold_data
        ).execute()

        return hold_data['hold_id']

    async def _create_appointment_record(
        self,
        clinic_id: str,
        patient_id: str,
        appointment_details: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Create appointment record in database"""

        appointment_data = {
            'id': str(uuid.uuid4()),
            'clinic_id': clinic_id,
            'patient_id': patient_id,
            'doctor_id': appointment_details['doctor_id'],
            'service_id': appointment_details.get('service_id'),
            'appointment_type': appointment_details.get('type', 'general'),
            'appointment_date': appointment_details['date'],
            'start_time': appointment_details['time'],
            'end_time': (
                datetime.fromisoformat(f"{appointment_details['date']}T{appointment_details['time']}") +
                timedelta(minutes=appointment_details.get('duration_minutes', 30))
            ).time().isoformat(),
            'duration_minutes': appointment_details.get('duration_minutes', 30),
            'status': 'scheduled',
            'reason_for_visit': appointment_details.get('reason'),
            'created_at': datetime.utcnow().isoformat()
        }

        result = self.supabase.table('healthcare.appointments').insert(
            appointment_data
        ).execute()

        return result.data[0] if result.data else appointment_data

    async def _sync_to_calendar(
        self,
        clinic_id: str,
        doctor_id: str,
        appointment: Dict[str, Any],
        patient: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Sync appointment to external calendar"""

        # Get calendar integration
        result = self.supabase.table('healthcare.calendar_integrations').select('*').eq(
            'doctor_id', doctor_id
        ).eq('sync_enabled', True).single().execute()

        if not result.data:
            return None

        integration = result.data

        try:
            # Retrieve credentials
            credentials = await self.vault.retrieve_calendar_credentials(
                organization_id=clinic_id,
                provider=integration['provider']
            )

            # Create calendar event
            event_data = {
                'summary': f"Appointment: {patient['first_name']} {patient['last_name']}",
                'description': f"Patient: {patient['first_name']} {patient['last_name']}\nPhone: {patient['phone']}\nType: {appointment['appointment_type']}",
                'start': f"{appointment['appointment_date']}T{appointment['start_time']}",
                'end': f"{appointment['appointment_date']}T{appointment['end_time']}",
                'reminders': {
                    'useDefault': False,
                    'overrides': [
                        {'method': 'popup', 'minutes': 10}
                    ]
                }
            }

            if integration['provider'] == 'google':
                calendar_event = await self._create_google_event(
                    credentials,
                    integration['calendar_id'],
                    event_data
                )
            elif integration['provider'] == 'outlook':
                calendar_event = await self._create_outlook_event(
                    credentials,
                    integration['calendar_id'],
                    event_data
                )
            else:
                return None

            # Log sync
            if calendar_event:
                self.supabase.table('healthcare.calendar_sync_log').insert({
                    'id': str(uuid.uuid4()),
                    'integration_id': integration['id'],
                    'appointment_id': appointment['id'],
                    'external_event_id': calendar_event.get('id'),
                    'sync_type': 'create',
                    'sync_direction': 'to_external',
                    'sync_status': 'success',
                    'local_data': appointment,
                    'external_data': calendar_event
                }).execute()

            return calendar_event

        except Exception as e:
            logger.error(f"Failed to sync to calendar: {str(e)}")

            # Log sync failure
            self.supabase.table('healthcare.calendar_sync_log').insert({
                'id': str(uuid.uuid4()),
                'integration_id': integration['id'],
                'appointment_id': appointment['id'],
                'sync_type': 'create',
                'sync_direction': 'to_external',
                'sync_status': 'failed',
                'error_message': str(e)
            }).execute()

            return None

    async def _create_google_event(
        self,
        credentials: Dict,
        calendar_id: str,
        event_data: Dict
    ) -> Optional[Dict]:
        """Create event in Google Calendar"""

        try:
            creds = Credentials(
                token=credentials['access_token'],
                refresh_token=credentials.get('refresh_token')
            )

            service = build('calendar', 'v3', credentials=creds, cache_discovery=False)

            event = {
                'summary': event_data['summary'],
                'description': event_data['description'],
                'start': {
                    'dateTime': event_data['start'] + ':00Z',
                    'timeZone': 'UTC'
                },
                'end': {
                    'dateTime': event_data['end'] + ':00Z',
                    'timeZone': 'UTC'
                },
                'reminders': event_data.get('reminders', {'useDefault': True})
            }

            result = service.events().insert(
                calendarId=calendar_id or 'primary',
                body=event
            ).execute()

            return result

        except Exception as e:
            logger.error(f"Failed to create Google event: {str(e)}")
            return None

    async def _create_outlook_event(
        self,
        credentials: Dict,
        calendar_id: str,
        event_data: Dict
    ) -> Optional[Dict]:
        """Create event in Outlook Calendar"""

        try:
            async with aiohttp.ClientSession() as session:
                headers = {
                    'Authorization': f"Bearer {credentials['access_token']}",
                    'Content-Type': 'application/json'
                }

                event = {
                    'subject': event_data['summary'],
                    'body': {
                        'contentType': 'Text',
                        'content': event_data['description']
                    },
                    'start': {
                        'dateTime': event_data['start'],
                        'timeZone': 'UTC'
                    },
                    'end': {
                        'dateTime': event_data['end'],
                        'timeZone': 'UTC'
                    },
                    'isReminderOn': True,
                    'reminderMinutesBeforeStart': 10
                }

                async with session.post(
                    f'https://graph.microsoft.com/v1.0/me/calendars/{calendar_id}/events',
                    headers=headers,
                    json=event
                ) as response:
                    if response.status == 201:
                        return await response.json()

            return None

        except Exception as e:
            logger.error(f"Failed to create Outlook event: {str(e)}")
            return None

    async def _confirm_appointment_hold(self, hold_id: str, appointment_id: str):
        """Confirm appointment hold"""
        self.supabase.table('healthcare.appointment_holds').update({
            'status': 'confirmed',
            'confirmed_appointment_id': appointment_id,
            'confirmed_at': datetime.utcnow().isoformat()
        }).eq('hold_id', hold_id).execute()

    async def _cancel_appointment_hold(self, hold_id: str):
        """Cancel appointment hold"""
        self.supabase.table('healthcare.appointment_holds').update({
            'status': 'cancelled',
            'cancelled_at': datetime.utcnow().isoformat()
        }).eq('hold_id', hold_id).execute()

    async def _schedule_reminders(self, appointment_id: str, patient_phone: str):
        """Schedule appointment reminders"""
        # This would integrate with a task queue or scheduling service
        # For now, we'll just log the intention
        logger.info(f"Scheduling reminders for appointment {appointment_id} to {patient_phone}")

    def _generate_confirmation_number(self, appointment_id: str) -> str:
        """Generate human-friendly confirmation number"""
        # Use last 8 characters of UUID
        return appointment_id.split('-')[-1].upper()

    async def _update_idempotency_success(self, idempotency_key: str, result: Dict):
        """Update idempotency record with success"""
        self.supabase.table('healthcare.booking_idempotency').update({
            'status': 'completed',
            'response_payload': result,
            'response_timestamp': datetime.utcnow().isoformat()
        }).eq('idempotency_key', idempotency_key).execute()

    async def _update_idempotency_failure(self, idempotency_key: str, error: str):
        """Update idempotency record with failure"""
        self.supabase.table('healthcare.booking_idempotency').update({
            'status': 'failed',
            'error_message': error,
            'response_timestamp': datetime.utcnow().isoformat()
        }).eq('idempotency_key', idempotency_key).execute()
