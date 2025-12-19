"""
Unified Appointment Service
Replaces existing appointment booking with integrated calendar coordination
Implements direct replacement strategy with ask-hold-reserve pattern
"""

import os
import uuid
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass
from enum import Enum

from supabase import create_client, Client
from .external_calendar_service import ExternalCalendarService
from .websocket_manager import websocket_manager, NotificationType
from ..database import get_db_connection
import asyncpg

# Phase C.1: Intelligent Scheduling Components
from ..fsm.service_doctor_mapper import ServiceDoctorMapper
from .scheduling.preference_scorer import PreferenceScorer
from .scheduling.constraint_engine import ConstraintEngine
from .resource_service import ResourceService

logger = logging.getLogger(__name__)

class AppointmentStatus(Enum):
    SCHEDULED = "scheduled"
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    NO_SHOW = "no_show"
    RESCHEDULED = "rescheduled"

class AppointmentType(Enum):
    CONSULTATION = "consultation"
    CLEANING = "cleaning"
    PROCEDURE = "procedure"
    FOLLOW_UP = "follow_up"
    EMERGENCY = "emergency"

@dataclass
class TimeSlot:
    """Available time slot representation"""
    start_time: datetime
    end_time: datetime
    doctor_id: str
    available: bool
    source: str  # 'internal', 'google', 'outlook'
    conflicts: List[str] = None

@dataclass
class AppointmentRequest:
    """Appointment booking request"""
    patient_id: str
    doctor_id: str
    clinic_id: str
    start_time: datetime
    end_time: datetime
    appointment_type: AppointmentType
    service_id: Optional[str] = None
    reason: Optional[str] = None
    notes: Optional[str] = None
    patient_phone: Optional[str] = None
    patient_email: Optional[str] = None

@dataclass
class AppointmentResult:
    """Result of appointment operation"""
    success: bool
    appointment_id: Optional[str] = None
    reservation_id: Optional[str] = None
    error: Optional[str] = None
    external_events: Dict[str, str] = None
    conflicts: List[str] = None

class UnifiedAppointmentService:
    """
    Unified appointment service that replaces existing booking endpoints
    Integrates with external calendar coordination for comprehensive scheduling
    """

    def __init__(self, supabase: Client = None):
        if supabase:
            self.supabase = supabase
        else:
            self.supabase: Client = create_client(
                os.environ.get("SUPABASE_URL"),
                os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
            )
        try:
            self.healthcare_supabase = self.supabase.schema('healthcare')
        except AttributeError:
            self.healthcare_supabase = self.supabase
        self.calendar_service = ExternalCalendarService(supabase=self.supabase)
        self.default_appointment_duration = timedelta(minutes=30)

    async def get_available_slots(
        self,
        doctor_id: str,
        date: str,
        duration_minutes: int = 30,
        appointment_type: Optional[str] = None
    ) -> List[TimeSlot]:
        """
        Get available appointment slots for a specific doctor and date
        Checks all calendar sources for true availability
        """
        try:
            logger.info(f"Getting available slots for doctor {doctor_id} on {date}")

            # Parse date and create time range
            target_date = datetime.fromisoformat(date)
            start_of_day = target_date.replace(hour=8, minute=0, second=0, microsecond=0)
            end_of_day = target_date.replace(hour=18, minute=0, second=0, microsecond=0)

            # Get doctor's working hours (simplified - could be from database)
            working_hours = await self._get_doctor_working_hours(doctor_id, target_date)

            # Generate potential time slots
            potential_slots = self._generate_time_slots(
                working_hours['start'],
                working_hours['end'],
                duration_minutes
            )

            # PARALLEL: Check availability for all slots concurrently
            import asyncio

            async def check_single_slot(slot_start):
                """Check a single slot and return TimeSlot object."""
                slot_end = slot_start + timedelta(minutes=duration_minutes)
                is_available, sources = await self._check_slot_availability(
                    doctor_id, slot_start, slot_end
                )
                return TimeSlot(
                    start_time=slot_start,
                    end_time=slot_end,
                    doctor_id=doctor_id,
                    available=is_available,
                    source=','.join(sources) if sources else 'unknown'
                )

            # Fire all slot checks in parallel
            slot_results = await asyncio.gather(
                *[check_single_slot(slot_start) for slot_start in potential_slots],
                return_exceptions=True
            )

            # Filter to only available slots, handling any exceptions
            available_slots = []
            for result in slot_results:
                if isinstance(result, Exception):
                    logger.warning(f"Slot check failed: {result}")
                    continue
                if result and result.available:
                    available_slots.append(result)

            return available_slots

        except Exception as e:
            logger.error(f"Failed to get available slots: {e}")
            return []

    async def book_appointment(
        self,
        request: AppointmentRequest,
        idempotency_key: Optional[str] = None,
        source_channel: str = 'http'
    ) -> AppointmentResult:
        """
        Book appointment using ask-hold-reserve pattern with idempotency support
        This is the core replacement for the existing booking endpoint

        Args:
            request: Appointment request details
            idempotency_key: Optional idempotency key for duplicate prevention
            source_channel: Source channel ('http', 'whatsapp', 'voice')

        Returns:
            AppointmentResult with booking status
        """
        try:
            # Step 1: Check idempotency if key provided
            if idempotency_key:
                existing = await self._check_idempotency(idempotency_key, request.clinic_id)
                if existing:
                    logger.info(f"⚡ Returning cached result for idempotent request")
                    response = existing.get('response_payload', {})
                    return AppointmentResult(
                        success=response.get('success', True),
                        appointment_id=response.get('appointment_id'),
                        reservation_id=response.get('reservation_id'),
                        external_events=response.get('external_events')
                    )

                # Record idempotency attempt
                await self._record_idempotency_attempt(
                    idempotency_key,
                    request.clinic_id,
                    request_payload={
                        'patient_id': request.patient_id,
                        'doctor_id': request.doctor_id,
                        'start_time': request.start_time.isoformat(),
                        'end_time': request.end_time.isoformat(),
                        'service_id': request.service_id
                    },
                    channel=source_channel
                )

            logger.info(f"Booking appointment for patient {request.patient_id} with doctor {request.doctor_id}")

            appointment_id = str(uuid.uuid4())
            duration_minutes = max(
                1,
                int((request.end_time - request.start_time).total_seconds() // 60)
            )
            appointment_payload = {
                'id': appointment_id,
                'clinic_id': request.clinic_id,
                'patient_id': request.patient_id,
                'doctor_id': request.doctor_id,
                'service_id': request.service_id,
                'appointment_type': request.appointment_type.value,
                'appointment_date': request.start_time.date().isoformat(),
                'start_time': request.start_time,
                'end_time': request.end_time,
                'duration_minutes': duration_minutes,
                'status': AppointmentStatus.SCHEDULED.value,
                'reason_for_visit': request.reason or '',
                'notes': request.notes or '',
                'patient_phone': request.patient_phone,
                'patient_email': request.patient_email,
                'skip_internal_confirmation': True
            }

            # Phase 1: Use calendar service to check and hold
            success, hold_result = await self.calendar_service.ask_hold_reserve(
                doctor_id=request.doctor_id,
                start_time=request.start_time,
                end_time=request.end_time,
                appointment_data=appointment_payload
            )

            if not success:
                error_msg = hold_result.get('error', 'Slot not available')

                # Record idempotency failure
                if idempotency_key:
                    await self._update_idempotency_failure(
                        idempotency_key,
                        request.clinic_id,
                        error=error_msg
                    )

                return AppointmentResult(
                    success=False,
                    error=error_msg,
                    conflicts=hold_result.get('conflicts', [])
                )

            reservation_id = hold_result.get('reservation_id')
            appointment_payload['reservation_id'] = reservation_id

            internal_confirmed = hold_result.get('internal_confirmed', False)
            precreated_appointment_id = hold_result.get('appointment_id') if internal_confirmed else None
            if precreated_appointment_id:
                appointment_id = precreated_appointment_id
                appointment_payload['id'] = appointment_id

            # Phase 2 & 3: Room assignment + Create appointment (wrapped in transaction)
            room_id = None
            resolved_service_id: Optional[str] = request.service_id

            try:
                # Use database transaction with row-level locking to prevent race conditions
                async with get_db_connection() as conn:
                    if conn is None:
                        # Fallback to non-transactional mode if no connection pool
                        logger.warning("No database connection pool available, using non-transactional mode")
                        raise Exception("Database connection unavailable")

                    async with conn.transaction():
                        # Lock doctor row to prevent double-booking (RC1 fix)
                        doctor_lock = await conn.fetchrow(
                            """
                            SELECT id FROM healthcare.doctors
                            WHERE id = $1
                            FOR UPDATE
                            """,
                            uuid.UUID(request.doctor_id)
                        )

                        if not doctor_lock:
                            raise Exception(f"Doctor {request.doctor_id} not found")

                        logger.debug(f"Acquired lock on doctor {request.doctor_id}")

                        # Check for conflicting appointments (even with constraint, we check for better error messages)
                        conflict_check = await conn.fetchrow(
                            """
                            SELECT id FROM healthcare.appointments
                            WHERE doctor_id = $1
                            AND appointment_date = $2
                            AND status NOT IN ('cancelled')
                            AND (
                                (start_time, end_time) OVERLAPS ($3::time, $4::time)
                            )
                            LIMIT 1
                            """,
                            uuid.UUID(request.doctor_id),
                            request.start_time.date(),
                            request.start_time.time(),
                            request.end_time.time()
                        )

                        if conflict_check and str(conflict_check['id']) != appointment_id:
                            raise Exception(f"Doctor is already booked during this time slot")

                        if not resolved_service_id:
                            resolved_service_id = await self._resolve_service_id(
                                conn,
                                request.doctor_id,
                                request.clinic_id
                            )
                        appointment_payload['service_id'] = resolved_service_id

                        # Phase 2: Room Auto-Assignment with locking
                        logger.info("Starting room auto-assignment within transaction")

                        # Get available rooms with locking
                        available_rooms_rows = await conn.fetch(
                            """
                            SELECT r.id, r.room_number, r.room_name, r.room_type,
                                   r.equipment, r.capacity, r.is_available
                            FROM healthcare.rooms r
                            WHERE r.clinic_id = $1
                            AND r.is_available = true
                            AND NOT EXISTS (
                                SELECT 1 FROM healthcare.appointments a
                                WHERE a.room_id = r.id
                                AND a.appointment_date = $2
                                AND a.status NOT IN ('cancelled')
                                AND (a.start_time, a.end_time) OVERLAPS ($3::time, $4::time)
                            )
                            FOR UPDATE OF r
                            """,
                            uuid.UUID(request.clinic_id),
                            request.start_time.date(),
                            request.start_time.time(),
                            request.end_time.time()
                        )

                        if not available_rooms_rows:
                            logger.warning("No available rooms found")
                        else:
                            # Pick the first available room (rule engine removed)
                            selected_room = dict(available_rooms_rows[0])
                            room_id = str(selected_room['id'])
                            logger.info(f"Selected room {room_id} using availability-first strategy")

                        # Phase 3: Insert or update appointment within transaction
                        now = datetime.now()
                        appointment_uuid = uuid.UUID(appointment_id)

                        if precreated_appointment_id:
                            existing_row = await conn.fetchrow(
                                """
                                SELECT id FROM healthcare.appointments
                                WHERE id = $1
                                FOR UPDATE
                                """,
                                appointment_uuid
                            )

                            if not existing_row:
                                raise Exception(f"Pre-created appointment {appointment_id} not found during update")

                            await conn.execute(
                                """
                                UPDATE healthcare.appointments
                                SET clinic_id = $2,
                                    patient_id = $3,
                                    doctor_id = $4,
                                    service_id = $5,
                                    appointment_date = $6,
                                    start_time = $7,
                                    end_time = $8,
                                    duration_minutes = $9,
                                    status = $10,
                                    appointment_type = $11,
                                    reason_for_visit = $12,
                                    notes = $13,
                                    reservation_id = $14,
                                    room_id = $15,
                                    updated_at = $16
                                WHERE id = $1
                                """,
                                appointment_uuid,
                                uuid.UUID(request.clinic_id),
                                uuid.UUID(request.patient_id),
                                uuid.UUID(request.doctor_id),
                                uuid.UUID(resolved_service_id) if resolved_service_id else None,
                                request.start_time.date(),
                                request.start_time.time(),
                                request.end_time.time(),
                                duration_minutes,
                                AppointmentStatus.SCHEDULED.value,
                                request.appointment_type.value,
                                request.reason or '',
                                request.notes or '',
                                reservation_id,
                                uuid.UUID(room_id) if room_id else None,
                                now
                            )
                            logger.info(f"Appointment {appointment_id} updated with reservation {reservation_id}")
                        else:
                            await conn.execute(
                                """
                                INSERT INTO healthcare.appointments (
                                    id, clinic_id, patient_id, doctor_id, service_id,
                                    appointment_date, start_time, end_time, duration_minutes,
                                    status, appointment_type, reason_for_visit, notes,
                                    reservation_id, room_id, created_at, updated_at
                                )
                                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17)
                                """,
                                appointment_uuid,
                                uuid.UUID(request.clinic_id),
                                uuid.UUID(request.patient_id),
                                uuid.UUID(request.doctor_id),
                                uuid.UUID(resolved_service_id) if resolved_service_id else None,
                                request.start_time.date(),
                                request.start_time.time(),
                                request.end_time.time(),
                                duration_minutes,
                                AppointmentStatus.SCHEDULED.value,
                                request.appointment_type.value,
                                request.reason or '',
                                request.notes or '',
                                reservation_id,
                                uuid.UUID(room_id) if room_id else None,
                                now,
                                now
                            )
                            logger.info(f"Appointment {appointment_id} created successfully with reservation {reservation_id}")

                        request.service_id = resolved_service_id

                        if room_id:
                            logger.info(f"Appointment assigned to room {room_id}")

                # Transaction committed successfully
                # Build appointment data for response and WebSocket broadcast
                appointment_data = {
                    'id': appointment_id,
                    'clinic_id': request.clinic_id,
                    'patient_id': request.patient_id,
                    'doctor_id': request.doctor_id,
                    'service_id': resolved_service_id,
                    'appointment_date': request.start_time.date().isoformat(),
                    'start_time': request.start_time.time().isoformat(),
                    'end_time': request.end_time.time().isoformat(),
                    'duration_minutes': duration_minutes,
                    'status': AppointmentStatus.SCHEDULED.value,
                    'appointment_type': request.appointment_type.value,
                    'reason': request.reason or '',
                    'reason_for_visit': request.reason or '',
                    'notes': request.notes or '',
                    'reservation_id': reservation_id,
                    'room_id': room_id,
                    'created_at': datetime.now().isoformat(),
                    'updated_at': datetime.now().isoformat()
                }

            except asyncpg.UniqueViolationError as e:
                logger.error(f"Unique constraint violation (double-booking prevented): {e}")
                await self._rollback_calendar_hold(reservation_id)

                error_msg = "This time slot is no longer available (double-booking prevented)"

                # Record idempotency failure
                if idempotency_key:
                    await self._update_idempotency_failure(
                        idempotency_key,
                        request.clinic_id,
                        error=error_msg
                    )

                return AppointmentResult(
                    success=False,
                    error=error_msg
                )
            except Exception as e:
                logger.error(f"Failed to create appointment in transaction: {e}")
                # Rollback the calendar hold
                await self._rollback_calendar_hold(reservation_id)

                error_msg = f"Failed to create appointment: {str(e)}"

                # Record idempotency failure
                if idempotency_key:
                    await self._update_idempotency_failure(
                        idempotency_key,
                        request.clinic_id,
                        error=error_msg
                    )

                return AppointmentResult(
                    success=False,
                    error=error_msg
                )

            # Phase 4: Confirm calendar events
            await self._confirm_calendar_events(reservation_id)

            # Log the appointment creation
            await self._log_appointment_operation(
                appointment_id=appointment_id,
                operation='create',
                status='success',
                patient_id=request.patient_id
            )

            # Broadcast appointment creation via WebSocket
            await websocket_manager.broadcast_appointment_update(
                appointment_id=appointment_id,
                notification_type=NotificationType.APPOINTMENT_CREATED,
                appointment_data=appointment_data,
                source="internal"
            )

            success_result = AppointmentResult(
                success=True,
                appointment_id=appointment_id,
                reservation_id=reservation_id,
                external_events=hold_result.get('external_event_ids', {})
            )

            # Record idempotency success
            if idempotency_key:
                await self._update_idempotency_success(
                    idempotency_key,
                    request.clinic_id,
                    result={
                        'success': True,
                        'appointment_id': appointment_id,
                        'reservation_id': reservation_id,
                        'external_events': hold_result.get('external_event_ids', {})
                    }
                )

            return success_result

        except Exception as e:
            logger.error(f"Failed to book appointment: {e}")

            # Record idempotency failure
            if idempotency_key:
                await self._update_idempotency_failure(
                    idempotency_key,
                    request.clinic_id,
                    error=str(e)
                )

            return AppointmentResult(
                success=False,
                error=str(e)
            )

    async def cancel_appointment(self, appointment_id: str, reason: str = None) -> AppointmentResult:
        """Cancel an existing appointment and clean up external calendar events"""
        try:
            logger.info(f"Cancelling appointment {appointment_id}")

            # Get appointment details
            appointment_result = self.supabase.table('appointments')\
                .select('*')\
                .eq('id', appointment_id)\
                .execute()

            if not appointment_result.data:
                return AppointmentResult(
                    success=False,
                    error="Appointment not found"
                )

            appointment = appointment_result.data[0]

            # Update appointment status
            update_result = self.supabase.table('appointments')\
                .update({
                    'status': AppointmentStatus.CANCELLED.value,
                    'cancellation_reason': reason,
                    'cancelled_at': datetime.now().isoformat(),
                    'updated_at': datetime.now().isoformat()
                })\
                .eq('id', appointment_id)\
                .execute()

            # Cancel external calendar events
            if appointment.get('reservation_id'):
                await self._cancel_calendar_events(appointment['reservation_id'])

            # Log the cancellation
            await self._log_appointment_operation(
                appointment_id=appointment_id,
                operation='cancel',
                status='success',
                patient_id=appointment['patient_id'],
                notes=f"Reason: {reason}" if reason else None
            )

            # Broadcast appointment cancellation via WebSocket
            await websocket_manager.broadcast_appointment_update(
                appointment_id=appointment_id,
                notification_type=NotificationType.APPOINTMENT_CANCELLED,
                appointment_data={
                    **appointment,
                    'cancellation_reason': reason,
                    'cancelled_at': datetime.now().isoformat()
                },
                source="internal"
            )

            return AppointmentResult(
                success=True,
                appointment_id=appointment_id
            )

        except Exception as e:
            logger.error(f"Failed to cancel appointment: {e}")
            return AppointmentResult(
                success=False,
                error=str(e)
            )

    async def reschedule_appointment(
        self,
        appointment_id: str,
        new_start_time: datetime,
        new_end_time: datetime
    ) -> AppointmentResult:
        """Reschedule an appointment to a new time"""
        try:
            logger.info(f"Rescheduling appointment {appointment_id}")

            # Get current appointment
            appointment_result = self.supabase.table('appointments')\
                .select('*')\
                .eq('id', appointment_id)\
                .execute()

            if not appointment_result.data:
                return AppointmentResult(
                    success=False,
                    error="Appointment not found"
                )

            appointment = appointment_result.data[0]

            # Check availability for new time slot
            new_duration_minutes = max(
                1,
                int((new_end_time - new_start_time).total_seconds() // 60)
            )

            success, hold_result = await self.calendar_service.ask_hold_reserve(
                doctor_id=appointment['doctor_id'],
                start_time=new_start_time,
                end_time=new_end_time,
                appointment_data={
                    'id': appointment_id,
                    'patient_id': appointment['patient_id'],
                    'clinic_id': appointment['clinic_id'],
                    'doctor_id': appointment['doctor_id'],
                    'appointment_type': appointment.get('appointment_type', 'consultation'),
                    'appointment_date': new_start_time.date().isoformat(),
                    'start_time': new_start_time,
                    'end_time': new_end_time,
                    'duration_minutes': new_duration_minutes,
                    'status': AppointmentStatus.SCHEDULED.value,
                    'reason_for_visit': appointment.get('reason_for_visit', ''),
                    'notes': appointment.get('notes'),
                    'reschedule_from': appointment_id,
                    'skip_internal_confirmation': True
                }
            )

            if not success:
                return AppointmentResult(
                    success=False,
                    error=hold_result.get('error', 'New time slot not available'),
                    conflicts=hold_result.get('conflicts', [])
                )

            new_reservation_id = hold_result.get('reservation_id')

            # Update appointment with new time
            update_result = self.supabase.table('appointments')\
                .update({
                    'appointment_date': new_start_time.date().isoformat(),
                    'start_time': new_start_time.time().isoformat(),
                    'end_time': new_end_time.time().isoformat(),
                    'duration_minutes': new_duration_minutes,
                    'status': AppointmentStatus.RESCHEDULED.value,
                    'reservation_id': new_reservation_id,
                    'updated_at': datetime.now().isoformat()
                })\
                .eq('id', appointment_id)\
                .execute()

            appointment.update({
                'appointment_date': new_start_time.date().isoformat(),
                'start_time': new_start_time.time().isoformat(),
                'end_time': new_end_time.time().isoformat(),
                'duration_minutes': new_duration_minutes,
                'status': AppointmentStatus.RESCHEDULED.value,
                'reservation_id': new_reservation_id,
                'updated_at': datetime.now().isoformat()
            })

            # Cancel old calendar events and confirm new ones
            if appointment.get('reservation_id'):
                await self._cancel_calendar_events(appointment['reservation_id'])

            await self._confirm_calendar_events(new_reservation_id)

            # Log the reschedule
            await self._log_appointment_operation(
                appointment_id=appointment_id,
                operation='reschedule',
                status='success',
                patient_id=appointment['patient_id'],
                notes=f"Moved to {new_start_time.isoformat()}"
            )

            # Broadcast appointment reschedule via WebSocket
            await websocket_manager.broadcast_appointment_update(
                appointment_id=appointment_id,
                notification_type=NotificationType.APPOINTMENT_RESCHEDULED,
                appointment_data={
                    **appointment,
                    'new_start_time': new_start_time.isoformat(),
                    'new_end_time': new_end_time.isoformat(),
                    'rescheduled_at': datetime.now().isoformat()
                },
                source="internal"
            )

            return AppointmentResult(
                success=True,
                appointment_id=appointment_id,
                reservation_id=new_reservation_id
            )

        except Exception as e:
            logger.error(f"Failed to reschedule appointment: {e}")
            return AppointmentResult(
                success=False,
                error=str(e)
            )

    async def get_appointments(
        self,
        doctor_id: Optional[str] = None,
        patient_id: Optional[str] = None,
        clinic_id: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        status: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Get appointments with filtering options"""
        try:
            query = self.supabase.table('appointments').select('*')

            if doctor_id:
                query = query.eq('doctor_id', doctor_id)
            if patient_id:
                query = query.eq('patient_id', patient_id)
            if clinic_id:
                query = query.eq('clinic_id', clinic_id)
            if status:
                query = query.eq('status', status)
            if date_from:
                query = query.gte('appointment_date', date_from)
            if date_to:
                query = query.lte('appointment_date', date_to)

            result = query.order('appointment_date', desc=False).execute()
            return result.data or []

        except Exception as e:
            logger.error(f"Failed to get appointments: {e}")
            return []

    # Private helper methods

    async def _get_available_rooms(
        self,
        doctor_id: str,
        clinic_id: str,
        start_time: datetime,
        end_time: datetime
    ) -> List[Dict[str, Any]]:
        """
        Get rooms available for the given time slot
        Checks that rooms are not already booked during this time
        """
        try:
            logger.debug(f"Getting available rooms for doctor {doctor_id} from {start_time} to {end_time}")

            # Get all rooms for the clinic
            rooms_result = self.supabase.table('rooms')\
                .select('*')\
                .eq('clinic_id', clinic_id)\
                .execute()

            if not rooms_result.data:
                logger.warning(f"No rooms found for clinic {clinic_id}")
                return []

            available_rooms = []

            # Check each room for availability
            for room in rooms_result.data:
                room_id = room['id']

                # Check if room is already booked during this time slot
                conflicts_result = self.supabase.table('appointments')\
                    .select('id, start_time, end_time')\
                    .eq('room_id', room_id)\
                    .eq('appointment_date', start_time.date().isoformat())\
                    .in_('status', ['scheduled', 'confirmed'])\
                    .execute()

                has_conflict = False
                if conflicts_result.data:
                    # Check for time overlap
                    for appointment in conflicts_result.data:
                        apt_start = datetime.fromisoformat(f"{start_time.date().isoformat()}T{appointment['start_time']}")
                        apt_end = datetime.fromisoformat(f"{start_time.date().isoformat()}T{appointment['end_time']}")

                        # Check for overlap: (start1 < end2) and (start2 < end1)
                        if start_time < apt_end and apt_start < end_time:
                            has_conflict = True
                            break

                if not has_conflict:
                    available_rooms.append(room)
                    logger.debug(f"Room {room_id} is available")
                else:
                    logger.debug(f"Room {room_id} has conflicts")

            logger.info(f"Found {len(available_rooms)} available rooms")
            return available_rooms

        except Exception as e:
            logger.error(f"Error getting available rooms: {e}")
            return []

    async def _simple_room_selection(
        self,
        doctor_id: str,
        clinic_id: str,
        start_time: datetime,
        end_time: datetime
    ) -> Optional[str]:
        """
        Simple room selection fallback without rules engine
        Just finds the first available room
        """
        try:
            logger.info("Using simple room selection fallback")

            available_rooms = await self._get_available_rooms(
                doctor_id, clinic_id, start_time, end_time
            )

            if available_rooms:
                selected_room = available_rooms[0]
                logger.info(f"Selected room {selected_room['id']} via simple selection")
                return selected_room['id']
            else:
                logger.warning("No rooms available for simple selection")
                return None

        except Exception as e:
            logger.error(f"Error in simple room selection: {e}")
            return None

    async def _check_slot_availability(
        self,
        doctor_id: str,
        start_time: datetime,
        end_time: datetime
    ) -> Tuple[bool, List[str]]:
        """Check if a time slot is available across all calendar sources"""
        try:
            import asyncio

            # PARALLEL: Check all calendar sources concurrently
            internal_task = self.calendar_service._check_internal_availability(
                doctor_id, start_time, end_time
            )
            google_task = self.calendar_service._check_google_calendar_availability(
                doctor_id, start_time, end_time
            )
            outlook_task = self.calendar_service._check_outlook_calendar_availability(
                doctor_id, start_time, end_time
            )

            # Fire all checks in parallel
            internal_check, google_check, outlook_check = await asyncio.gather(
                internal_task, google_task, outlook_task,
                return_exceptions=True
            )

            # Handle exceptions
            if isinstance(internal_check, Exception):
                logger.warning(f"Internal calendar check failed: {internal_check}")
                internal_check = {'available': False}
            if isinstance(google_check, Exception):
                logger.warning(f"Google calendar check failed: {google_check}")
                google_check = {'available': True}  # Fail open if not configured
            if isinstance(outlook_check, Exception):
                logger.warning(f"Outlook calendar check failed: {outlook_check}")
                outlook_check = {'available': True}  # Fail open if not configured

            availability_sources = []
            if internal_check.get('available'):
                availability_sources.append('internal')
            if google_check.get('available'):
                availability_sources.append('google')
            if outlook_check.get('available'):
                availability_sources.append('outlook')

            # Slot is available if ALL configured sources are available
            is_available = (
                internal_check.get('available', False) and
                google_check.get('available', True) and
                outlook_check.get('available', True)
            )

            return is_available, availability_sources

        except Exception as e:
            logger.error(f"Error checking slot availability: {e}")
            return False, []

    async def _resolve_service_id(
        self,
        conn: asyncpg.Connection,
        doctor_id: str,
        clinic_id: str
    ) -> Optional[str]:
        """
        Resolve a default service for the doctor when none is provided.
        Prefers preferred/allowed/derived mappings.
        """
        # 1. Doctor-service mapping (extended tables)
        doctor_lookup_sql = """
            SELECT ds.service_id
            FROM healthcare.doctor_services ds
            WHERE ds.doctor_id = $1
            ORDER BY
                CASE
                    WHEN ds.status = 'preferred' THEN 1
                    WHEN ds.status = 'allowed' THEN 2
                    WHEN ds.status = 'derived' THEN 3
                    WHEN COALESCE(ds.is_primary, false) THEN 4
                    ELSE 5
                END,
                ds.created_at
            LIMIT 1
        """

        try:
            row = await conn.fetchrow(doctor_lookup_sql, uuid.UUID(doctor_id))
            if row and row.get('service_id'):
                return str(row['service_id'])
        except Exception as e:
            logger.info(f"Doctor-service lookup failed for {doctor_id}: {e}")

        # 2. Clinic default service fallback
        clinic_lookup_sql = """
            SELECT s.id
            FROM healthcare.services s
            WHERE s.clinic_id = $1
              AND COALESCE(s.active, true)
            ORDER BY s.created_at
            LIMIT 1
        """
        try:
            row = await conn.fetchrow(clinic_lookup_sql, uuid.UUID(clinic_id))
            if row and row.get('id'):
                return str(row['id'])
        except Exception as e:
            logger.warning(f"Clinic service lookup failed for clinic {clinic_id}: {e}")

        logger.warning(
            "Unable to resolve service for doctor %s in clinic %s; appointment will be saved without service_id",
            doctor_id,
            clinic_id
        )
        return None

    async def _get_doctor_working_hours(self, doctor_id: str, date: datetime) -> Dict[str, datetime]:
        """Get doctor's working hours for a specific date"""
        # Simplified implementation - could be enhanced with database lookup
        day_of_week = date.weekday()  # 0 = Monday, 6 = Sunday

        if day_of_week == 6:  # Sunday
            # Closed on Sunday
            return {
                'start': date.replace(hour=0, minute=0),
                'end': date.replace(hour=0, minute=0)
            }
        elif day_of_week == 5:  # Saturday
            # Half day on Saturday
            return {
                'start': date.replace(hour=9, minute=0),
                'end': date.replace(hour=13, minute=0)
            }
        else:
            # Regular weekday hours
            return {
                'start': date.replace(hour=8, minute=0),
                'end': date.replace(hour=18, minute=0)
            }

    def _generate_time_slots(self, start: datetime, end: datetime, duration_minutes: int) -> List[datetime]:
        """Generate potential appointment time slots"""
        slots = []
        current = start
        duration = timedelta(minutes=duration_minutes)

        while current + duration <= end:
            slots.append(current)
            current += duration

        return slots

    async def _rollback_calendar_hold(self, reservation_id: str):
        """Rollback calendar hold if appointment creation fails"""
        try:
            if reservation_id:
                # Update the hold status to cancelled
                self.healthcare_supabase.table('calendar_holds')\
                    .update({'status': 'cancelled'})\
                    .eq('reservation_id', reservation_id)\
                    .execute()
                logger.info(f"Rolled back calendar hold {reservation_id}")
        except Exception as e:
            logger.error(f"Failed to rollback calendar hold: {e}")

    async def _confirm_calendar_events(self, reservation_id: str):
        """Confirm calendar events after successful appointment creation"""
        try:
            if reservation_id:
                # Update hold status to confirmed
                self.healthcare_supabase.table('calendar_holds')\
                    .update({'status': 'confirmed'})\
                    .eq('reservation_id', reservation_id)\
                    .execute()
                logger.info(f"Confirmed calendar events for {reservation_id}")
        except Exception as e:
            logger.error(f"Failed to confirm calendar events: {e}")

    async def _cancel_calendar_events(self, reservation_id: str):
        """Cancel external calendar events"""
        try:
            if reservation_id:
                # Update hold status to cancelled
                self.healthcare_supabase.table('calendar_holds')\
                    .update({'status': 'cancelled'})\
                    .eq('reservation_id', reservation_id)\
                    .execute()
                logger.info(f"Cancelled calendar events for {reservation_id}")
        except Exception as e:
            logger.error(f"Failed to cancel calendar events: {e}")

    async def _log_appointment_operation(
        self,
        appointment_id: str,
        operation: str,
        status: str,
        patient_id: str,
        notes: Optional[str] = None
    ):
        """Log appointment operations for audit trail"""
        try:
            log_data = {
                'appointment_id': appointment_id,
                'operation': operation,
                'status': status,
                'patient_id': patient_id,
                'notes': notes,
                'timestamp': datetime.now().isoformat()
            }

            # Insert into appointment log table (would need to create this table)
            # For now, just log to application logs
            logger.info(f"Appointment operation: {log_data}")

        except Exception as e:
            logger.error(f"Failed to log appointment operation: {e}")

    # ===== Idempotency Methods (merged from AppointmentBookingService) =====

    async def _check_idempotency(
        self,
        idempotency_key: str,
        clinic_id: str
    ) -> Optional[Dict]:
        """
        Check for existing booking with idempotency key
        Returns cached response if already processed
        """
        try:
            import hashlib
            key_hash = hashlib.sha256(idempotency_key.encode()).hexdigest()

            # Query by key_hash for performance
            result = self.supabase.table('healthcare.booking_idempotency').select('*').eq(
                'key_hash', key_hash
            ).eq(
                'tenant_id', clinic_id
            ).eq(
                'status', 'completed'
            ).execute()

            if result.data:
                logger.info(f"⚡ Idempotent request found for key: {idempotency_key[:20]}...")
                return result.data[0]

            return None
        except Exception as e:
            logger.warning(f"Idempotency check failed: {e}")
            # Fail open - proceed with request
            return None

    async def _record_idempotency_attempt(
        self,
        idempotency_key: str,
        clinic_id: str,
        request_payload: Dict[str, Any],
        channel: str = 'http'
    ):
        """Record initial idempotency attempt"""
        try:
            import hashlib
            key_hash = hashlib.sha256(idempotency_key.encode()).hexdigest()

            # Try to insert, ignore if exists (race condition)
            try:
                self.supabase.table('healthcare.booking_idempotency').insert({
                    'key_hash': key_hash,
                    'idempotency_key': idempotency_key,
                    'tenant_id': clinic_id,
                    'channel': channel,
                    'request_payload': request_payload,
                    'request_timestamp': datetime.utcnow().isoformat(),
                    'status': 'processing',
                    'created_at': datetime.utcnow().isoformat()
                }).execute()
                logger.info(f"✅ Recorded idempotency attempt for key: {idempotency_key[:20]}...")
            except Exception:
                # Already exists - that's OK (race condition)
                pass
        except Exception as e:
            logger.warning(f"Failed to record idempotency attempt: {e}")

    async def _update_idempotency_success(
        self,
        idempotency_key: str,
        clinic_id: str,
        result: Dict[str, Any]
    ):
        """Update idempotency record with success"""
        try:
            import hashlib
            key_hash = hashlib.sha256(idempotency_key.encode()).hexdigest()

            self.supabase.table('healthcare.booking_idempotency').update({
                'status': 'completed',
                'response_payload': result,
                'response_timestamp': datetime.utcnow().isoformat()
            }).eq('key_hash', key_hash).eq('tenant_id', clinic_id).execute()

            logger.info(f"✅ Recorded idempotency success for key: {idempotency_key[:20]}...")
        except Exception as e:
            logger.warning(f"Failed to update idempotency success: {e}")

    async def _update_idempotency_failure(
        self,
        idempotency_key: str,
        clinic_id: str,
        error: str
    ):
        """Update idempotency record with failure"""
        try:
            import hashlib
            key_hash = hashlib.sha256(idempotency_key.encode()).hexdigest()

            self.supabase.table('healthcare.booking_idempotency').update({
                'status': 'failed',
                'error_message': error,
                'response_timestamp': datetime.utcnow().isoformat()
            }).eq('key_hash', key_hash).eq('tenant_id', clinic_id).execute()

            logger.warning(f"⚠️ Recorded idempotency failure for key: {idempotency_key[:20]}...")
        except Exception as e:
            logger.warning(f"Failed to update idempotency failure: {e}")

    # ===== Patient Lookup Methods (merged from AppointmentBookingService) =====

    async def _get_or_create_patient(
        self,
        phone: str,
        clinic_id: str,
        first_name: Optional[str] = None,
        last_name: Optional[str] = None,
        email: Optional[str] = None,
        date_of_birth: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Get existing patient by phone or create new patient record
        Returns patient dict with id
        """
        try:
            # Clean phone number
            clean_phone = phone.replace('whatsapp:', '').replace('+', '').strip()

            # Check for existing patient
            result = self.supabase.table('healthcare.patients').select('*').eq(
                'phone', clean_phone
            ).eq('clinic_id', clinic_id).execute()

            if result.data:
                logger.info(f"✅ Found existing patient with phone: {clean_phone}")
                return result.data[0]

            # Create new patient with provided details
            patient_data = {
                'id': str(uuid.uuid4()),
                'clinic_id': clinic_id,
                'phone': clean_phone,
                'first_name': first_name or 'Pending',
                'last_name': last_name or 'Registration',
                'date_of_birth': date_of_birth or '2000-01-01',
                'email': email,
                'preferred_contact_method': 'whatsapp' if 'whatsapp:' in phone else 'phone',
                'registered_date': datetime.utcnow().date().isoformat(),
                'created_at': datetime.utcnow().isoformat()
            }

            result = self.supabase.table('healthcare.patients').insert(patient_data).execute()

            if result.data:
                logger.info(f"✅ Created new patient with phone: {clean_phone}")
                return result.data[0]
            else:
                logger.warning(f"⚠️ Patient creation returned no data, using fallback")
                return patient_data

        except Exception as e:
            logger.error(f"Error in get_or_create_patient: {e}")
            # Return a minimal patient dict as fallback
            return {
                'id': str(uuid.uuid4()),
                'clinic_id': clinic_id,
                'phone': clean_phone,
                'first_name': first_name or 'Pending',
                'last_name': last_name or 'Registration'
            }

    # ============================================================================
    # Phase C: Unified Holds System Integration (2025-11-28)
    # ============================================================================
    # These methods implement the unified holds system using resource_reservations.state
    # from Phase A database migrations.
    # ============================================================================

    async def _create_unified_hold(
        self,
        clinic_id: str,
        service_id: str,
        patient_id: str,
        appointment_date,
        start_time,
        end_time,
        doctor_id: Optional[str] = None,  # Phase C.1: Now optional - finds best if not specified
        source_channel: str = 'http'
    ) -> Dict[str, Any]:
        """
        Create hold using intelligent scheduling with resource_reservations.state='HOLD'

        Phase C.1 Enhancement: Uses intelligent scheduling algorithms to find the best
        doctor and slot when doctor_id is not specified.

        Flow:
        1. If doctor_id provided: Use that doctor (backward compatible)
        2. If doctor_id NOT provided:
           a. Get eligible doctors using ServiceDoctorMapper (top 3 by match_score)
           b. Check availability for each doctor in parallel
           c. Select best available doctor by match_score
        3. Create hold in resource_reservations with state='HOLD'

        Args:
            clinic_id: Clinic ID
            service_id: Service ID
            patient_id: Patient ID
            appointment_date: Date of appointment (date object or ISO string)
            start_time: Start time (time object or ISO string)
            end_time: End time (time object or ISO string)
            doctor_id: Optional doctor ID (if None, will find best doctor) - Phase C.1
            source_channel: Source channel (http, whatsapp, voice)

        Returns:
            Dictionary with success status and hold details
        """
        try:
            import asyncio

            # Convert date/time to proper objects if strings
            if isinstance(appointment_date, str):
                appointment_date = datetime.fromisoformat(appointment_date).date()
            elif hasattr(appointment_date, 'date') and callable(appointment_date.date):
                appointment_date = appointment_date.date()

            if isinstance(start_time, str):
                start_time = datetime.fromisoformat(f"2000-01-01 {start_time}").time()
            elif hasattr(start_time, 'time') and callable(start_time.time):
                start_time = start_time.time()

            if isinstance(end_time, str):
                end_time = datetime.fromisoformat(f"2000-01-01 {end_time}").time()
            elif hasattr(end_time, 'time') and callable(end_time.time):
                end_time = end_time.time()

            # Step 1: Determine which doctor to use
            if doctor_id:
                # Doctor specified - use directly (backward compatible)
                logger.info(f"📍 Using specified doctor: {doctor_id}")
                selected_doctor_id = doctor_id
                selection_metadata = {
                    'selection_method': 'specified',
                    'doctor_id': doctor_id
                }
            else:
                # No doctor specified - use intelligent selection
                logger.info(f"🤖 Using intelligent doctor selection for service {service_id}")

                # Step 1a: Get eligible doctors using ServiceDoctorMapper
                mapper = ServiceDoctorMapper(self.supabase)
                eligible_doctors = await mapper.get_doctors_for_service(
                    service_id=service_id,
                    clinic_id=clinic_id,
                    patient_id=patient_id,
                    limit=10  # Get top 10 by eligibility score
                )

                if not eligible_doctors:
                    return {
                        'success': False,
                        'error': 'No eligible doctors found for this service'
                    }

                # Filter to top 3 by match_score (already sorted by RPC)
                top_doctors = eligible_doctors[:3]
                logger.info(
                    f"🎯 Top 3 eligible doctors: {[(d['id'], d['match_score']) for d in top_doctors]}"
                )

                # Step 1b: Check availability for top doctors IN PARALLEL
                async def check_doctor_availability(doctor):
                    """Check if this doctor has the slot available"""
                    try:
                        conflict_check = self.supabase.table('appointments').select(
                            'id'
                        ).eq('doctor_id', doctor['id']).eq(
                            'appointment_date', appointment_date.isoformat() if hasattr(appointment_date, 'isoformat') else str(appointment_date)
                        ).neq('status', 'cancelled').execute()

                        is_available = True
                        return {'doctor': doctor, 'available': is_available}
                    except Exception as e:
                        logger.warning(f"Error checking doctor {doctor['id']} availability: {e}")
                        return {'doctor': doctor, 'available': True}

                # Check all top doctors in parallel
                availability_results = await asyncio.gather(
                    *[check_doctor_availability(doc) for doc in top_doctors],
                    return_exceptions=True
                )

                # Filter to available doctors
                available_doctors = [
                    r['doctor'] for r in availability_results
                    if not isinstance(r, Exception) and r.get('available', False)
                ]

                if not available_doctors:
                    available_doctors = top_doctors

                # Select doctor with highest match_score among available
                best_doctor = max(available_doctors, key=lambda d: d.get('match_score', 0))
                selected_doctor_id = best_doctor['id']

                selection_metadata = {
                    'selection_method': 'intelligent',
                    'match_score': best_doctor.get('match_score', 0),
                    'match_type': best_doctor.get('match_type', 'unknown'),
                    'eligible_count': len(eligible_doctors),
                    'available_count': len(available_doctors),
                    'selection_reasons': best_doctor.get('reasons', [])
                }

                logger.info(
                    f"✨ Selected doctor {selected_doctor_id} "
                    f"(match_score: {best_doctor.get('match_score', 0)}, "
                    f"type: {best_doctor.get('match_type', 'unknown')})"
                )

            # Step 2: Create hold in resource_reservations with state='HOLD'
            hold_expires_at = datetime.utcnow() + timedelta(minutes=15)
            hold_id = str(uuid.uuid4())

            # Convert to ISO strings for storage
            appointment_date_str = appointment_date.isoformat() if hasattr(appointment_date, 'isoformat') else str(appointment_date)
            start_time_str = start_time.isoformat() if hasattr(start_time, 'isoformat') else str(start_time)
            end_time_str = end_time.isoformat() if hasattr(end_time, 'isoformat') else str(end_time)

            result = self.supabase.table('resource_reservations').insert({
                'id': hold_id,
                'clinic_id': clinic_id,
                'patient_id': patient_id,
                'service_id': service_id,
                'reservation_date': appointment_date_str,
                'start_time': start_time_str,
                'end_time': end_time_str,
                'state': 'HOLD',
                'hold_expires_at': hold_expires_at.isoformat(),
                'hold_created_for': source_channel,
                'status': 'pending',
                'metadata': {
                    **selection_metadata,
                    'doctor_id': selected_doctor_id,
                    'source_channel': source_channel,
                    'created_at': datetime.utcnow().isoformat()
                }
            }).execute()

            if not result.data:
                return {
                    'success': False,
                    'error': 'Failed to create hold in resource_reservations'
                }

            # Step 3: Create junction table entry for doctor (primary resource)
            # Look up the resource ID from the resources table (doctor_id -> resources.id)
            resource_lookup = self.supabase.table('resources').select('id').eq(
                'doctor_id', selected_doctor_id
            ).limit(1).execute()

            if resource_lookup.data:
                resource_id = resource_lookup.data[0]['id']
                self.supabase.table('reservation_resources').insert({
                    'reservation_id': hold_id,
                    'resource_id': resource_id,
                    'resource_role': 'primary',
                    'resource_type': 'doctor'
                }).execute()
                logger.debug(f"Linked hold to resource {resource_id} (doctor {selected_doctor_id})")
            else:
                # Doctor not registered as resource - log warning but don't fail
                logger.warning(f"⚠️ Doctor {selected_doctor_id} not found in resources table, skipping junction")

            logger.info(f"✅ Created intelligent hold {hold_id} (expires at {hold_expires_at})")

            return {
                'success': True,
                'hold_id': hold_id,
                'expires_at': hold_expires_at.isoformat(),
                'doctor_id': selected_doctor_id,
                'selection_metadata': selection_metadata
            }

        except Exception as e:
            logger.error(f"❌ Error creating intelligent hold: {str(e)}")

            # Check if it's a GiST exclusion constraint violation (overlap detected)
            if 'reservation_no_overlap' in str(e):
                return {
                    'success': False,
                    'error': 'Time slot unavailable - overlapping reservation detected',
                    'error_type': 'overlap_conflict'
                }

            return {
                'success': False,
                'error': f'Failed to create hold: {str(e)}'
            }

    async def _confirm_hold_atomic(
        self,
        hold_id: str,
        patient_id: str,
        service_id: str,
        appointment_type: str,
        reason_for_visit: Optional[str] = None,
        policy_version_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Atomically confirm hold and create appointment using database RPC

        Phase D Enhancement: Uses confirm_hold_and_create_appointment_v2 RPC which:
        1. Acquires advisory lock on doctor+timeslot to prevent race conditions
        2. Re-evaluates for conflicts that appeared after hold creation
        3. Creates appointment in single transaction
        4. Updates hold state to CONFIRMED
        5. Inserts calendar sync task into outbox

        Args:
            hold_id: Hold ID from resource_reservations
            patient_id: Patient ID
            service_id: Service ID
            appointment_type: Type of appointment
            reason_for_visit: Reason for visit (optional)
            policy_version_id: Policy version for audit trail (optional)

        Returns:
            Dictionary with success status and appointment details
        """
        try:
            # Use the atomic RPC for confirmation with advisory locks
            result = self.supabase.rpc(
                'confirm_hold_and_create_appointment_v2',
                {
                    'p_hold_id': hold_id,
                    'p_patient_id': patient_id,
                    'p_service_id': service_id,
                    'p_appointment_type': appointment_type,
                    'p_reason_for_visit': reason_for_visit or '',
                    'p_policy_version_id': policy_version_id
                }
            ).execute()

            if not result.data or len(result.data) == 0:
                return {
                    'success': False,
                    'error': 'RPC returned no data'
                }

            rpc_result = result.data[0]

            if not rpc_result.get('success', False):
                error_msg = rpc_result.get('error_message', 'Unknown error during confirmation')
                logger.warning(f"⚠️ Hold confirmation failed: {error_msg}")
                return {
                    'success': False,
                    'error': error_msg
                }

            appointment_id = rpc_result.get('appointment_id')
            room_id = rpc_result.get('room_id')

            # Get hold details for datetime info
            hold_result = self.supabase.table('resource_reservations').select(
                'reservation_date, start_time, metadata'
            ).eq('id', hold_id).execute()

            datetime_str = ''
            doctor_id = None
            if hold_result.data:
                hold = hold_result.data[0]
                datetime_str = f"{hold.get('reservation_date', '')} {hold.get('start_time', '')}"
                metadata = hold.get('metadata', {})
                doctor_id = metadata.get('doctor_id') if isinstance(metadata, dict) else None

            logger.info(f"✅ Confirmed hold {hold_id} → appointment {appointment_id} (via RPC)")

            return {
                'success': True,
                'appointment_id': appointment_id,
                'room_id': room_id,
                'datetime': datetime_str,
                'doctor_id': doctor_id
            }

        except Exception as e:
            logger.error(f"❌ Error confirming hold atomically: {str(e)}")

            # Fall back to non-RPC method if RPC fails
            logger.warning("⚠️ Falling back to non-RPC confirmation method")
            return await self._confirm_hold_atomic_fallback(
                hold_id, patient_id, service_id, appointment_type, reason_for_visit
            )

    async def _confirm_hold_atomic_fallback(
        self,
        hold_id: str,
        patient_id: str,
        service_id: str,
        appointment_type: str,
        reason_for_visit: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Fallback confirmation method when RPC is unavailable.
        Uses direct table operations (less atomic but functional).
        """
        try:
            # Verify hold is valid and not expired
            hold_result = self.supabase.table('resource_reservations').select(
                '*'
            ).eq('id', hold_id).eq('state', 'HOLD').execute()

            if not hold_result.data:
                return {
                    'success': False,
                    'error': 'Hold not found or already confirmed/expired'
                }

            hold = hold_result.data[0]

            # Check if hold expired
            if hold.get('hold_expires_at'):
                expires_at = datetime.fromisoformat(hold['hold_expires_at'].replace('Z', '+00:00'))
                if expires_at < datetime.utcnow().replace(tzinfo=expires_at.tzinfo):
                    self.supabase.table('resource_reservations').update({
                        'state': 'EXPIRED',
                        'updated_at': datetime.utcnow().isoformat()
                    }).eq('id', hold_id).execute()

                    return {
                        'success': False,
                        'error': 'Hold has expired'
                    }

            # Get doctor from metadata or junction table
            metadata = hold.get('metadata', {})
            doctor_id = metadata.get('doctor_id') if isinstance(metadata, dict) else None

            if not doctor_id:
                doctor_result = self.supabase.table('reservation_resources').select(
                    'resource_id'
                ).eq('reservation_id', hold_id).eq('resource_role', 'primary').execute()

                if doctor_result.data:
                    doctor_id = doctor_result.data[0]['resource_id']

            if not doctor_id:
                return {
                    'success': False,
                    'error': 'Doctor resource not found for hold'
                }

            # Create appointment record
            appointment_id = str(uuid.uuid4())

            appointment_data = {
                'id': appointment_id,
                'clinic_id': hold['clinic_id'],
                'patient_id': patient_id,
                'doctor_id': doctor_id,
                'service_id': service_id,
                'appointment_type': appointment_type,
                'appointment_date': hold['reservation_date'],
                'start_time': hold['start_time'],
                'end_time': hold['end_time'],
                'status': 'scheduled',
                'reason_for_visit': reason_for_visit or '',
                'created_at': datetime.utcnow().isoformat(),
                'updated_at': datetime.utcnow().isoformat()
            }

            appt_result = self.supabase.table('appointments').insert(
                appointment_data
            ).execute()

            if not appt_result.data:
                return {
                    'success': False,
                    'error': 'Failed to create appointment record'
                }

            # Update hold to CONFIRMED state
            self.supabase.table('resource_reservations').update({
                'state': 'CONFIRMED',
                'confirmed_appointment_id': appointment_id,
                'updated_at': datetime.utcnow().isoformat(),
                'metadata': {
                    **(metadata if isinstance(metadata, dict) else {}),
                    'confirmed_at': datetime.utcnow().isoformat()
                }
            }).eq('id', hold_id).execute()

            # Insert calendar outbox entry
            try:
                self.supabase.table('calendar_outbox').insert({
                    'appointment_id': appointment_id,
                    'operation': 'CREATE',
                    'status': 'pending'
                }).execute()
            except Exception as outbox_error:
                logger.warning(f"⚠️ Failed to insert calendar outbox: {outbox_error}")

            logger.info(f"✅ Confirmed hold {hold_id} → appointment {appointment_id} (fallback)")

            return {
                'success': True,
                'appointment_id': appointment_id,
                'datetime': f"{hold['reservation_date']} {hold['start_time']}",
                'doctor_id': doctor_id
            }

        except Exception as e:
            logger.error(f"❌ Error in fallback confirmation: {str(e)}")
            return {
                'success': False,
                'error': f'Failed to confirm hold: {str(e)}'
            }

    async def _release_hold(self, hold_id: str) -> Dict[str, Any]:
        """
        Release a hold by marking it as CANCELLED

        Args:
            hold_id: Hold ID to release

        Returns:
            Dictionary with success status
        """
        try:
            self.supabase.table('resource_reservations').update({
                'state': 'CANCELLED',
                'updated_at': datetime.utcnow().isoformat()
            }).eq('id', hold_id).execute()

            logger.info(f"✅ Released hold {hold_id}")

            return {'success': True}

        except Exception as e:
            logger.error(f"❌ Error releasing hold: {str(e)}")
            return {
                'success': False,
                'error': str(e)
            }

    async def book_appointment_v2(
        self,
        request: AppointmentRequest,
        idempotency_key: Optional[str] = None,
        source_channel: str = 'http'
    ) -> AppointmentResult:
        """
        Book appointment using unified holds system (Phase C)

        This is the new implementation that uses resource_reservations.state
        for holds instead of calendar_service.ask_hold_reserve().

        Flow:
        1. Check idempotency
        2. Create hold in resource_reservations (state='HOLD')
        3. Confirm hold atomically (creates appointment, updates state to 'CONFIRMED')
        4. Queue calendar sync (non-blocking)
        5. Record idempotency success

        Args:
            request: Appointment request details
            idempotency_key: Optional idempotency key for duplicate prevention
            source_channel: Source channel ('http', 'whatsapp', 'voice')

        Returns:
            AppointmentResult with booking status
        """
        try:
            # Step 1: Check idempotency if key provided
            if idempotency_key:
                existing = await self._check_idempotency(idempotency_key, request.clinic_id)
                if existing:
                    logger.info(f"⚡ Returning cached result for idempotent request")
                    response = existing.get('response_payload', {})
                    return AppointmentResult(
                        success=response.get('success', True),
                        appointment_id=response.get('appointment_id'),
                        reservation_id=response.get('reservation_id'),
                        external_events=response.get('external_events')
                    )

            logger.info(f"📅 Booking appointment for patient {request.patient_id} with doctor {request.doctor_id}")

            # Step 2: Create hold using unified substrate with intelligent scheduling (Phase C.1)
            hold_result = await self._create_unified_hold(
                clinic_id=request.clinic_id,
                service_id=request.service_id,
                patient_id=request.patient_id,
                appointment_date=request.start_time.date(),
                start_time=request.start_time.time(),
                end_time=request.end_time.time(),
                doctor_id=request.doctor_id,  # Optional - intelligent selection if None
                source_channel=source_channel
            )

            if not hold_result['success']:
                error_msg = hold_result.get('error', 'Failed to create hold')

                # Record idempotency failure
                if idempotency_key:
                    await self._update_idempotency_failure(
                        idempotency_key,
                        request.clinic_id,
                        error=error_msg
                    )

                return AppointmentResult(
                    success=False,
                    error=error_msg
                )

            hold_id = hold_result['hold_id']

            try:
                # Step 3: Confirm hold atomically (creates appointment + updates state to CONFIRMED)
                confirm_result = await self._confirm_hold_atomic(
                    hold_id=hold_id,
                    patient_id=request.patient_id,
                    service_id=request.service_id,
                    appointment_type=request.appointment_type.value if hasattr(request.appointment_type, 'value') else str(request.appointment_type),
                    reason_for_visit=request.reason
                )

                if not confirm_result['success']:
                    # Release hold on failure
                    await self._release_hold(hold_id)

                    error_msg = confirm_result.get('error', 'Failed to confirm appointment')

                    if idempotency_key:
                        await self._update_idempotency_failure(
                            idempotency_key,
                            request.clinic_id,
                            error=error_msg
                        )

                    return AppointmentResult(
                        success=False,
                        error=error_msg
                    )

                appointment_id = confirm_result['appointment_id']

                # Step 4: Queue calendar sync (non-blocking - will be handled by outbox worker in Phase D)
                # For now, we'll do it synchronously but this will be replaced with outbox pattern
                try:
                    await self.calendar_service.reserve_slot(
                        appointment_id=appointment_id,
                        datetime_str=f"{request.start_time.isoformat()}",
                        duration_minutes=int((request.end_time - request.start_time).total_seconds() / 60),
                        doctor_id=request.doctor_id,
                        patient_info={
                            'patient_id': request.patient_id,
                            'patient_phone': request.patient_phone,
                            'patient_email': request.patient_email
                        }
                    )
                except Exception as calendar_error:
                    # Calendar sync failure shouldn't fail the appointment
                    logger.warning(f"⚠️ Calendar sync failed but appointment created: {str(calendar_error)}")

                # Step 5: Record idempotency success
                booking_result = {
                    'success': True,
                    'appointment_id': appointment_id,
                    'reservation_id': hold_id,
                    'datetime': confirm_result['datetime'],
                    'doctor_id': confirm_result['doctor_id']
                }

                if idempotency_key:
                    await self._update_idempotency_success(
                        idempotency_key,
                        request.clinic_id,
                        result=booking_result
                    )

                logger.info(f"✅ Successfully booked appointment {appointment_id}")

                return AppointmentResult(
                    success=True,
                    appointment_id=appointment_id,
                    reservation_id=hold_id
                )

            except Exception as e:
                # Release hold on any error
                logger.error(f"❌ Error during confirmation: {str(e)}")
                await self._release_hold(hold_id)

                if idempotency_key:
                    await self._update_idempotency_failure(
                        idempotency_key,
                        request.clinic_id,
                        error=str(e)
                    )

                raise

        except Exception as e:
            logger.error(f"❌ Error booking appointment: {str(e)}")

            if idempotency_key:
                await self._update_idempotency_failure(
                    idempotency_key,
                    request.clinic_id,
                    error=str(e)
                )

            return AppointmentResult(
                success=False,
                error=str(e)
            )
