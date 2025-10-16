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
from .rule_evaluator import RuleEvaluator, EvaluationContext, TimeSlot as RuleTimeSlot
from .policy_cache import PolicyCache
from ..database import get_db_connection
import asyncpg

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
        self.calendar_service = ExternalCalendarService(self.supabase)
        self.default_appointment_duration = timedelta(minutes=30)

        # Initialize room assignment components
        self.policy_cache = PolicyCache(self.supabase)
        self.rule_evaluator = RuleEvaluator(self.supabase, self.policy_cache)

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

            # Check availability for each slot across all calendar sources
            available_slots = []
            for slot_start in potential_slots:
                slot_end = slot_start + timedelta(minutes=duration_minutes)

                # Use ask-hold-reserve to check true availability
                is_available, sources = await self._check_slot_availability(
                    doctor_id, slot_start, slot_end
                )

                available_slots.append(TimeSlot(
                    start_time=slot_start,
                    end_time=slot_end,
                    doctor_id=doctor_id,
                    available=is_available,
                    source=','.join(sources) if sources else 'unknown'
                ))

            # Filter to only available slots
            return [slot for slot in available_slots if slot.available]

        except Exception as e:
            logger.error(f"Failed to get available slots: {e}")
            return []

    async def book_appointment(self, request: AppointmentRequest) -> AppointmentResult:
        """
        Book appointment using ask-hold-reserve pattern
        This is the core replacement for the existing booking endpoint
        """
        try:
            logger.info(f"Booking appointment for patient {request.patient_id} with doctor {request.doctor_id}")

            # Phase 1: Use calendar service to check and hold
            success, hold_result = await self.calendar_service.ask_hold_reserve(
                doctor_id=request.doctor_id,
                start_time=request.start_time,
                end_time=request.end_time,
                appointment_data={
                    'patient_id': request.patient_id,
                    'clinic_id': request.clinic_id,
                    'type': request.appointment_type.value,
                    'reason': request.reason,
                    'notes': request.notes
                }
            )

            if not success:
                return AppointmentResult(
                    success=False,
                    error=hold_result.get('error', 'Slot not available'),
                    conflicts=hold_result.get('conflicts', [])
                )

            # Phase 2 & 3: Room assignment + Create appointment (wrapped in transaction)
            appointment_id = str(uuid.uuid4())
            room_id = None

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

                        if conflict_check:
                            raise Exception(f"Doctor is already booked during this time slot")

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
                            # Convert rows to dict for compatibility with scoring logic
                            available_rooms = [dict(row) for row in available_rooms_rows]

                            # Create evaluation context
                            context = EvaluationContext(
                                clinic_id=request.clinic_id,
                                patient_id=request.patient_id,
                                requested_service=request.appointment_type.value
                            )

                            # Score each available room
                            scored_slots = []
                            for room in available_rooms:
                                # Create a TimeSlot for rules evaluation
                                slot = RuleTimeSlot(
                                    id=str(uuid.uuid4()),
                                    doctor_id=request.doctor_id,
                                    room_id=str(room['id']),
                                    service_id=request.appointment_type.value,
                                    start_time=request.start_time,
                                    end_time=request.end_time
                                )

                                # Evaluate the slot
                                result = await self.rule_evaluator.evaluate_slot(context, slot)

                                if result.is_valid:
                                    scored_slots.append((room, result.score))
                                    logger.debug(f"Room {room['id']} scored {result.score}")
                                else:
                                    logger.debug(f"Room {room['id']} invalid: {result.explanations}")

                            # Select the room with the highest score
                            if scored_slots:
                                best_room, best_score = max(scored_slots, key=lambda x: x[1])
                                room_id = str(best_room['id'])
                                logger.info(f"Selected room {room_id} with score {best_score}")
                            else:
                                logger.warning("Rules engine found no valid rooms")

                        # Phase 3: Insert appointment within transaction
                        appointment_insert = await conn.execute(
                            """
                            INSERT INTO healthcare.appointments (
                                id, clinic_id, patient_id, doctor_id,
                                appointment_date, start_time, end_time,
                                status, appointment_type, reason_for_visit, notes,
                                reservation_id, room_id, created_at, updated_at
                            )
                            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15)
                            """,
                            uuid.UUID(appointment_id),
                            uuid.UUID(request.clinic_id),
                            uuid.UUID(request.patient_id),
                            uuid.UUID(request.doctor_id),
                            request.start_time.date(),
                            request.start_time.time(),
                            request.end_time.time(),
                            AppointmentStatus.SCHEDULED.value,
                            request.appointment_type.value,
                            request.reason or '',
                            request.notes or '',
                            hold_result.get('reservation_id'),
                            uuid.UUID(room_id) if room_id else None,
                            datetime.now(),
                            datetime.now()
                        )

                        logger.info(f"Appointment {appointment_id} created successfully in transaction")
                        if room_id:
                            logger.info(f"Appointment assigned to room {room_id}")

                # Transaction committed successfully
                # Build appointment data for response and WebSocket broadcast
                appointment_data = {
                    'id': appointment_id,
                    'clinic_id': request.clinic_id,
                    'patient_id': request.patient_id,
                    'doctor_id': request.doctor_id,
                    'appointment_date': request.start_time.date().isoformat(),
                    'start_time': request.start_time.time().isoformat(),
                    'end_time': request.end_time.time().isoformat(),
                    'status': AppointmentStatus.SCHEDULED.value,
                    'appointment_type': request.appointment_type.value,
                    'reason': request.reason or '',
                    'notes': request.notes or '',
                    'reservation_id': hold_result.get('reservation_id'),
                    'room_id': room_id,
                    'created_at': datetime.now().isoformat(),
                    'updated_at': datetime.now().isoformat()
                }

            except asyncpg.UniqueViolationError as e:
                logger.error(f"Unique constraint violation (double-booking prevented): {e}")
                await self._rollback_calendar_hold(hold_result.get('reservation_id'))
                return AppointmentResult(
                    success=False,
                    error="This time slot is no longer available (double-booking prevented)"
                )
            except Exception as e:
                logger.error(f"Failed to create appointment in transaction: {e}")
                # Rollback the calendar hold
                await self._rollback_calendar_hold(hold_result.get('reservation_id'))
                return AppointmentResult(
                    success=False,
                    error=f"Failed to create appointment: {str(e)}"
                )

            # Phase 4: Confirm calendar events
            await self._confirm_calendar_events(hold_result.get('reservation_id'))

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

            return AppointmentResult(
                success=True,
                appointment_id=appointment_id,
                reservation_id=hold_result.get('reservation_id'),
                external_events=hold_result.get('external_event_ids', {})
            )

        except Exception as e:
            logger.error(f"Failed to book appointment: {e}")
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
            success, hold_result = await self.calendar_service.ask_hold_reserve(
                doctor_id=appointment['doctor_id'],
                start_time=new_start_time,
                end_time=new_end_time,
                appointment_data={
                    'patient_id': appointment['patient_id'],
                    'clinic_id': appointment['clinic_id'],
                    'type': appointment.get('appointment_type', 'consultation'),
                    'reason': appointment.get('reason', ''),
                    'reschedule_from': appointment_id
                }
            )

            if not success:
                return AppointmentResult(
                    success=False,
                    error=hold_result.get('error', 'New time slot not available'),
                    conflicts=hold_result.get('conflicts', [])
                )

            # Update appointment with new time
            update_result = self.supabase.table('appointments')\
                .update({
                    'appointment_date': new_start_time.date().isoformat(),
                    'start_time': new_start_time.time().isoformat(),
                    'end_time': new_end_time.time().isoformat(),
                    'status': AppointmentStatus.RESCHEDULED.value,
                    'reservation_id': hold_result.get('reservation_id'),
                    'updated_at': datetime.now().isoformat()
                })\
                .eq('id', appointment_id)\
                .execute()

            # Cancel old calendar events and confirm new ones
            if appointment.get('reservation_id'):
                await self._cancel_calendar_events(appointment['reservation_id'])

            await self._confirm_calendar_events(hold_result.get('reservation_id'))

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
                reservation_id=hold_result.get('reservation_id')
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
            # Quick check using the calendar service availability logic
            # This reuses the ask-hold-reserve pattern's ASK phase
            availability_sources = []

            # Check internal availability
            internal_check = await self.calendar_service._check_internal_availability(
                doctor_id, start_time, end_time
            )
            if internal_check.get('available'):
                availability_sources.append('internal')

            # Check external calendars (if configured)
            google_check = await self.calendar_service._check_google_calendar_availability(
                doctor_id, start_time, end_time
            )
            if google_check.get('available'):
                availability_sources.append('google')

            outlook_check = await self.calendar_service._check_outlook_calendar_availability(
                doctor_id, start_time, end_time
            )
            if outlook_check.get('available'):
                availability_sources.append('outlook')

            # Slot is available if ALL configured sources are available
            is_available = (
                internal_check.get('available', False) and
                google_check.get('available', True) and  # True if not configured
                outlook_check.get('available', True)     # True if not configured
            )

            return is_available, availability_sources

        except Exception as e:
            logger.error(f"Error checking slot availability: {e}")
            return False, []

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
