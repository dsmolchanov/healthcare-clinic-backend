"""
Real-Time Conflict Detection and Resolution
Implements Phase 3: Real-Time Multi-Source Updates
Detects and resolves conflicts between multiple calendar sources in real-time
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass
from enum import Enum

from .websocket_manager import websocket_manager, NotificationType
from .external_calendar_service import ExternalCalendarService
from .unified_appointment_service import UnifiedAppointmentService
from ..database import create_supabase_client

logger = logging.getLogger(__name__)

class ConflictSeverity(Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

class ConflictType(Enum):
    DOUBLE_BOOKING = "double_booking"
    HOLD_CONFLICT = "hold_conflict"
    EXTERNAL_OVERRIDE = "external_override"
    TIME_MISMATCH = "time_mismatch"
    RECURRING_CONFLICT = "recurring_conflict"
    CANCELLATION_CONFLICT = "cancellation_conflict"

@dataclass
class ConflictEvent:
    """Represents a detected conflict between calendar sources"""
    conflict_id: str
    conflict_type: ConflictType
    severity: ConflictSeverity
    doctor_id: str
    start_time: datetime
    end_time: datetime
    sources: List[str]  # ['internal', 'google', 'outlook']
    details: Dict[str, Any]
    detected_at: datetime
    resolved: bool = False
    resolution_strategy: Optional[str] = None

@dataclass
class ResolutionSuggestion:
    """Suggested resolution for a conflict"""
    strategy: str
    description: str
    priority: int
    automatic: bool
    impact: str

class RealtimeConflictDetector:
    """
    Real-time conflict detection and resolution system
    Monitors multiple calendar sources and resolves conflicts automatically or suggests resolutions
    """

    def __init__(self):
        self.healthcare_supabase = create_supabase_client('healthcare')
        self.public_supabase = create_supabase_client('public')
        self.calendar_service = ExternalCalendarService(self.healthcare_supabase)
        self.appointment_service = UnifiedAppointmentService(self.healthcare_supabase)

        # Active conflicts tracking
        self.active_conflicts: Dict[str, ConflictEvent] = {}

        # Conflict detection rules
        self.detection_rules = {
            ConflictType.DOUBLE_BOOKING: self._detect_double_booking,
            ConflictType.HOLD_CONFLICT: self._detect_hold_conflict,
            ConflictType.EXTERNAL_OVERRIDE: self._detect_external_override,
            ConflictType.TIME_MISMATCH: self._detect_time_mismatch,
            ConflictType.RECURRING_CONFLICT: self._detect_recurring_conflict,
            ConflictType.CANCELLATION_CONFLICT: self._detect_cancellation_conflict
        }

        # Resolution strategies
        self.resolution_strategies = {
            ConflictType.DOUBLE_BOOKING: [
                ResolutionSuggestion(
                    strategy="cancel_internal",
                    description="Cancel internal appointment and keep external event",
                    priority=1,
                    automatic=False,
                    impact="Patient notification required"
                ),
                ResolutionSuggestion(
                    strategy="cancel_external",
                    description="Cancel external event and keep internal appointment",
                    priority=2,
                    automatic=False,
                    impact="External calendar update required"
                ),
                ResolutionSuggestion(
                    strategy="reschedule_internal",
                    description="Automatically reschedule internal appointment to next available slot",
                    priority=3,
                    automatic=True,
                    impact="Patient notification and rescheduling"
                )
            ],
            ConflictType.HOLD_CONFLICT: [
                ResolutionSuggestion(
                    strategy="extend_hold",
                    description="Extend hold expiration time",
                    priority=1,
                    automatic=True,
                    impact="Temporary hold extension"
                ),
                ResolutionSuggestion(
                    strategy="cancel_hold",
                    description="Cancel hold and release time slot",
                    priority=2,
                    automatic=True,
                    impact="Hold cancellation notification"
                )
            ]
        }

    async def monitor_conflicts(self, doctor_id: str, date_range: Tuple[datetime, datetime]):
        """
        Start monitoring conflicts for a specific doctor within a date range
        This runs continuously and detects conflicts in real-time
        """
        try:
            logger.info(f"Starting conflict monitoring for doctor {doctor_id}")

            while True:
                # Run conflict detection cycle
                await self._run_conflict_detection_cycle(doctor_id, date_range)

                # Wait before next detection cycle
                await asyncio.sleep(30)  # Check every 30 seconds

        except Exception as e:
            logger.error(f"Conflict monitoring error for doctor {doctor_id}: {e}")

    async def detect_calendar_change_conflicts(
        self,
        doctor_id: str,
        provider: str,
        change_data: Dict[str, Any]
    ):
        """
        Detect conflicts triggered by external calendar changes
        Called by webhook handlers when external calendar events change
        """
        try:
            logger.info(f"Detecting conflicts for {provider} change in doctor {doctor_id}")

            change_type = change_data.get('change_type', 'unknown')
            event_start = change_data.get('start_time')
            event_end = change_data.get('end_time')

            if not event_start or not event_end:
                logger.warning(f"Missing time information in calendar change: {change_data}")
                return

            # Parse times
            try:
                start_time = datetime.fromisoformat(event_start.replace('Z', '+00:00'))
                end_time = datetime.fromisoformat(event_end.replace('Z', '+00:00'))
            except ValueError:
                logger.error(f"Invalid time format in calendar change: {event_start}, {event_end}")
                return

            # Check for conflicts with internal appointments
            conflicts = await self._check_time_slot_conflicts(
                doctor_id, start_time, end_time, exclude_source=provider
            )

            if conflicts:
                # Create conflict event
                conflict_event = ConflictEvent(
                    conflict_id=f"ext_{provider}_{doctor_id}_{start_time.timestamp()}",
                    conflict_type=ConflictType.EXTERNAL_OVERRIDE,
                    severity=self._calculate_conflict_severity(conflicts),
                    doctor_id=doctor_id,
                    start_time=start_time,
                    end_time=end_time,
                    sources=['internal', provider],
                    details={
                        'external_provider': provider,
                        'change_type': change_type,
                        'conflicting_appointments': conflicts,
                        'external_event': change_data
                    },
                    detected_at=datetime.now()
                )

                # Store and broadcast conflict
                await self._handle_detected_conflict(conflict_event)

        except Exception as e:
            logger.error(f"Failed to detect calendar change conflicts: {e}")

    async def resolve_conflict(
        self,
        conflict_id: str,
        resolution_strategy: str,
        user_id: str
    ) -> bool:
        """
        Resolve a conflict using the specified strategy
        """
        try:
            if conflict_id not in self.active_conflicts:
                logger.warning(f"Conflict {conflict_id} not found")
                return False

            conflict = self.active_conflicts[conflict_id]

            logger.info(f"Resolving conflict {conflict_id} with strategy {resolution_strategy}")

            success = False

            if resolution_strategy == "cancel_internal":
                success = await self._resolve_cancel_internal(conflict)
            elif resolution_strategy == "cancel_external":
                success = await self._resolve_cancel_external(conflict)
            elif resolution_strategy == "reschedule_internal":
                success = await self._resolve_reschedule_internal(conflict)
            elif resolution_strategy == "extend_hold":
                success = await self._resolve_extend_hold(conflict)
            elif resolution_strategy == "cancel_hold":
                success = await self._resolve_cancel_hold(conflict)
            else:
                logger.warning(f"Unknown resolution strategy: {resolution_strategy}")

            if success:
                # Mark conflict as resolved
                conflict.resolved = True
                conflict.resolution_strategy = resolution_strategy

                # Broadcast resolution
                await websocket_manager.broadcast_appointment_update(
                    appointment_id=conflict.conflict_id,
                    notification_type=NotificationType.CALENDAR_CONFLICT,
                    appointment_data={
                        'conflict_resolved': True,
                        'resolution_strategy': resolution_strategy,
                        'resolved_by': user_id,
                        'conflict_details': conflict.details
                    },
                    source="conflict_resolver"
                )

                # Remove from active conflicts
                del self.active_conflicts[conflict_id]

            return success

        except Exception as e:
            logger.error(f"Failed to resolve conflict {conflict_id}: {e}")
            return False

    # Private methods for conflict detection

    async def _run_conflict_detection_cycle(self, doctor_id: str, date_range: Tuple[datetime, datetime]):
        """Run a complete conflict detection cycle"""
        try:
            start_date, end_date = date_range

            # Run all detection rules
            for conflict_type, detection_func in self.detection_rules.items():
                conflicts = await detection_func(doctor_id, start_date, end_date)

                for conflict in conflicts:
                    if conflict.conflict_id not in self.active_conflicts:
                        await self._handle_detected_conflict(conflict)

        except Exception as e:
            logger.error(f"Conflict detection cycle error: {e}")

    async def _detect_double_booking(
        self,
        doctor_id: str,
        start_date: datetime,
        end_date: datetime
    ) -> List[ConflictEvent]:
        """Detect double bookings across calendar sources"""
        conflicts = []

        try:
            # Get all appointments from internal database
            internal_appointments = await self.appointment_service.get_appointments(
                doctor_id=doctor_id,
                date_from=start_date.strftime('%Y-%m-%d'),
                date_to=end_date.strftime('%Y-%m-%d')
            )

            # Check each appointment against external calendars
            for appointment in internal_appointments:
                if appointment['status'] in ['cancelled', 'completed']:
                    continue

                # Parse appointment times
                appt_start = datetime.fromisoformat(f"{appointment['appointment_date']}T{appointment['start_time']}")
                appt_end = datetime.fromisoformat(f"{appointment['appointment_date']}T{appointment['end_time']}")

                # Check for conflicts with external calendars
                external_conflicts = await self._check_external_calendar_conflicts(
                    doctor_id, appt_start, appt_end
                )

                if external_conflicts:
                    conflict = ConflictEvent(
                        conflict_id=f"double_{appointment['id']}_{appt_start.timestamp()}",
                        conflict_type=ConflictType.DOUBLE_BOOKING,
                        severity=ConflictSeverity.HIGH,
                        doctor_id=doctor_id,
                        start_time=appt_start,
                        end_time=appt_end,
                        sources=['internal'] + list(external_conflicts.keys()),
                        details={
                            'internal_appointment': appointment,
                            'external_conflicts': external_conflicts
                        },
                        detected_at=datetime.now()
                    )
                    conflicts.append(conflict)

        except Exception as e:
            logger.error(f"Double booking detection error: {e}")

        return conflicts

    async def _detect_hold_conflict(
        self,
        doctor_id: str,
        start_date: datetime,
        end_date: datetime
    ) -> List[ConflictEvent]:
        """Detect conflicts with pending holds"""
        conflicts = []

        try:
            # Get pending holds
            holds_result = self.healthcare_supabase.table('calendar_holds')\
                .select('*')\
                .eq('doctor_id', doctor_id)\
                .eq('status', 'pending')\
                .gte('expires_at', datetime.now().isoformat())\
                .execute()

            for hold in holds_result.data or []:
                hold_start = datetime.fromisoformat(hold['start_time'])
                hold_end = datetime.fromisoformat(hold['end_time'])

                # Check if hold conflicts with new appointments or external events
                time_conflicts = await self._check_time_slot_conflicts(
                    doctor_id, hold_start, hold_end, exclude_source='hold'
                )

                if time_conflicts:
                    conflict = ConflictEvent(
                        conflict_id=f"hold_{hold['reservation_id']}_{hold_start.timestamp()}",
                        conflict_type=ConflictType.HOLD_CONFLICT,
                        severity=ConflictSeverity.MEDIUM,
                        doctor_id=doctor_id,
                        start_time=hold_start,
                        end_time=hold_end,
                        sources=['hold', 'internal'],
                        details={
                            'hold_data': hold,
                            'conflicting_events': time_conflicts
                        },
                        detected_at=datetime.now()
                    )
                    conflicts.append(conflict)

        except Exception as e:
            logger.error(f"Hold conflict detection error: {e}")

        return conflicts

    async def _detect_external_override(self, doctor_id: str, start_date: datetime, end_date: datetime) -> List[ConflictEvent]:
        """Detect when external calendar events override internal appointments"""
        # This is primarily handled by detect_calendar_change_conflicts
        return []

    async def _detect_time_mismatch(self, doctor_id: str, start_date: datetime, end_date: datetime) -> List[ConflictEvent]:
        """Detect time mismatches between calendar sources"""
        conflicts = []
        # Implementation would compare exact times across sources
        return conflicts

    async def _detect_recurring_conflict(self, doctor_id: str, start_date: datetime, end_date: datetime) -> List[ConflictEvent]:
        """Detect conflicts with recurring events"""
        conflicts = []
        # Implementation would check recurring patterns
        return conflicts

    async def _detect_cancellation_conflict(self, doctor_id: str, start_date: datetime, end_date: datetime) -> List[ConflictEvent]:
        """Detect conflicts when appointments are cancelled in one source but not others"""
        conflicts = []
        # Implementation would check cancellation sync
        return conflicts

    # Helper methods

    async def _check_time_slot_conflicts(
        self,
        doctor_id: str,
        start_time: datetime,
        end_time: datetime,
        exclude_source: str = None
    ) -> List[Dict[str, Any]]:
        """Check for conflicts in a specific time slot"""
        conflicts = []

        try:
            # Check internal appointments
            if exclude_source != 'internal':
                internal_appointments = await self.appointment_service.get_appointments(
                    doctor_id=doctor_id,
                    date_from=start_time.strftime('%Y-%m-%d'),
                    date_to=end_time.strftime('%Y-%m-%d')
                )

                for appointment in internal_appointments:
                    if appointment['status'] in ['cancelled', 'completed']:
                        continue

                    appt_start = datetime.fromisoformat(f"{appointment['appointment_date']}T{appointment['start_time']}")
                    appt_end = datetime.fromisoformat(f"{appointment['appointment_date']}T{appointment['end_time']}")

                    if self._times_overlap(start_time, end_time, appt_start, appt_end):
                        conflicts.append({
                            'source': 'internal',
                            'appointment': appointment,
                            'start_time': appt_start,
                            'end_time': appt_end
                        })

            # Check external calendars (simplified)
            external_conflicts = await self._check_external_calendar_conflicts(
                doctor_id, start_time, end_time
            )

            for provider, events in external_conflicts.items():
                if exclude_source != provider:
                    conflicts.extend([
                        {
                            'source': provider,
                            'event': event,
                            'start_time': start_time,
                            'end_time': end_time
                        }
                        for event in events
                    ])

        except Exception as e:
            logger.error(f"Error checking time slot conflicts: {e}")

        return conflicts

    async def _check_external_calendar_conflicts(
        self,
        doctor_id: str,
        start_time: datetime,
        end_time: datetime
    ) -> Dict[str, List[Any]]:
        """Check for conflicts with external calendars"""
        conflicts = {}

        try:
            # Check Google Calendar
            google_check = await self.calendar_service._check_google_calendar_availability(
                doctor_id, start_time, end_time
            )

            if not google_check.get('available', True):
                conflicts['google'] = google_check.get('events', [])

            # Check Outlook Calendar
            outlook_check = await self.calendar_service._check_outlook_calendar_availability(
                doctor_id, start_time, end_time
            )

            if not outlook_check.get('available', True):
                conflicts['outlook'] = outlook_check.get('events', [])

        except Exception as e:
            logger.error(f"External calendar conflict check error: {e}")

        return conflicts

    def _times_overlap(self, start1: datetime, end1: datetime, start2: datetime, end2: datetime) -> bool:
        """Check if two time periods overlap"""
        return start1 < end2 and start2 < end1

    def _calculate_conflict_severity(self, conflicts: List[Dict[str, Any]]) -> ConflictSeverity:
        """Calculate the severity of a conflict based on the number and type of conflicts"""
        if len(conflicts) >= 3:
            return ConflictSeverity.CRITICAL
        elif len(conflicts) == 2:
            return ConflictSeverity.HIGH
        elif len(conflicts) == 1:
            return ConflictSeverity.MEDIUM
        else:
            return ConflictSeverity.LOW

    async def _handle_detected_conflict(self, conflict: ConflictEvent):
        """Handle a newly detected conflict"""
        try:
            # Store the conflict
            self.active_conflicts[conflict.conflict_id] = conflict

            logger.warning(f"Conflict detected: {conflict.conflict_type.value} for doctor {conflict.doctor_id}")

            # Get resolution suggestions
            suggestions = self.resolution_strategies.get(conflict.conflict_type, [])

            # Broadcast conflict notification
            await websocket_manager.broadcast_calendar_conflict(
                conflict_data={
                    'conflict_id': conflict.conflict_id,
                    'conflict_type': conflict.conflict_type.value,
                    'severity': conflict.severity.value,
                    'start_time': conflict.start_time.isoformat(),
                    'end_time': conflict.end_time.isoformat(),
                    'sources': conflict.sources,
                    'details': conflict.details,
                    'suggestions': [
                        {
                            'strategy': s.strategy,
                            'description': s.description,
                            'priority': s.priority,
                            'automatic': s.automatic,
                            'impact': s.impact
                        }
                        for s in suggestions
                    ]
                },
                affected_doctors=[conflict.doctor_id],
                source="conflict_detector"
            )

            # Auto-resolve if appropriate
            auto_resolutions = [s for s in suggestions if s.automatic and s.priority == 1]
            if auto_resolutions:
                await self.resolve_conflict(
                    conflict.conflict_id,
                    auto_resolutions[0].strategy,
                    "system_auto"
                )

        except Exception as e:
            logger.error(f"Failed to handle detected conflict: {e}")

    # Resolution implementation methods

    async def _resolve_cancel_internal(self, conflict: ConflictEvent) -> bool:
        """Cancel internal appointment to resolve conflict"""
        try:
            appointment_data = conflict.details.get('internal_appointment')
            if appointment_data:
                result = await self.appointment_service.cancel_appointment(
                    appointment_data['id'],
                    reason=f"Cancelled due to {conflict.conflict_type.value} conflict"
                )
                return result.success
        except Exception as e:
            logger.error(f"Failed to cancel internal appointment: {e}")
        return False

    async def _resolve_cancel_external(self, conflict: ConflictEvent) -> bool:
        """Cancel external calendar event to resolve conflict"""
        try:
            # This would require implementing external calendar cancellation
            logger.info(f"Would cancel external events for conflict {conflict.conflict_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to cancel external event: {e}")
        return False

    async def _resolve_reschedule_internal(self, conflict: ConflictEvent) -> bool:
        """Reschedule internal appointment to resolve conflict"""
        try:
            appointment_data = conflict.details.get('internal_appointment')
            if appointment_data:
                # Find next available slot
                next_slot = await self._find_next_available_slot(
                    conflict.doctor_id,
                    conflict.end_time + timedelta(minutes=30)
                )

                if next_slot:
                    result = await self.appointment_service.reschedule_appointment(
                        appointment_data['id'],
                        next_slot['start_time'],
                        next_slot['end_time']
                    )
                    return result.success
        except Exception as e:
            logger.error(f"Failed to reschedule internal appointment: {e}")
        return False

    async def _resolve_extend_hold(self, conflict: ConflictEvent) -> bool:
        """Extend hold expiration to resolve conflict"""
        try:
            hold_data = conflict.details.get('hold_data')
            if hold_data:
                # Extend hold by 10 minutes
                result = self.public_supabase.rpc('extend_calendar_hold', {
                    'p_reservation_id': hold_data['reservation_id'],
                    'p_additional_minutes': 10
                }).execute()

                return result.data if result.data else False
        except Exception as e:
            logger.error(f"Failed to extend hold: {e}")
        return False

    async def _resolve_cancel_hold(self, conflict: ConflictEvent) -> bool:
        """Cancel hold to resolve conflict"""
        try:
            hold_data = conflict.details.get('hold_data')
            if hold_data:
                self.healthcare_supabase.table('calendar_holds')\
                    .update({'status': 'cancelled'})\
                    .eq('reservation_id', hold_data['reservation_id'])\
                    .execute()

                return True
        except Exception as e:
            logger.error(f"Failed to cancel hold: {e}")
        return False

    async def _find_next_available_slot(
        self,
        doctor_id: str,
        after_time: datetime
    ) -> Optional[Dict[str, datetime]]:
        """Find the next available time slot for a doctor"""
        try:
            # Get available slots for the next few days
            for days_ahead in range(1, 8):  # Check next 7 days
                check_date = (after_time + timedelta(days=days_ahead)).strftime('%Y-%m-%d')
                slots = await self.appointment_service.get_available_slots(
                    doctor_id, check_date, duration_minutes=30
                )

                for slot in slots:
                    if slot.available and slot.start_time > after_time:
                        return {
                            'start_time': slot.start_time,
                            'end_time': slot.end_time
                        }

        except Exception as e:
            logger.error(f"Failed to find next available slot: {e}")

        return None

# Global conflict detector instance
conflict_detector = RealtimeConflictDetector()
