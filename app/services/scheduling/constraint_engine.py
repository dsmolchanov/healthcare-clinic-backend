"""
Constraint Engine for Scheduling.

Checks hard constraints for appointment slot validity.
"""

import logging
from datetime import datetime, time
from typing import Optional, List, Dict, Any
from uuid import UUID

logger = logging.getLogger(__name__)


class ConstraintEngine:
    """
    Validates hard constraints for scheduling slots.

    Hard constraints are binary checks (pass/fail) that must be satisfied
    for a slot to be valid. This includes:
    - Doctor working hours
    - Doctor time-off
    - Room availability
    - Service eligibility
    """

    def __init__(self, db):
        """
        Initialize constraint engine.

        Args:
            db: Database client (Supabase or similar)
        """
        self.db = db
        self._doctor_schedules_cache = {}
        self._doctor_timeoff_cache = {}

    async def check_doctor_schedule(
        self,
        doctor_id: UUID,
        slot_time: datetime,
        duration_minutes: int = 30
    ) -> bool:
        """
        Verify doctor is working at this time.

        Args:
            doctor_id: Doctor UUID
            slot_time: Proposed slot start time
            duration_minutes: Appointment duration

        Returns:
            True if doctor is scheduled to work, False otherwise
        """
        try:
            # Get day of week (0=Monday, 6=Sunday)
            day_of_week = slot_time.weekday()
            day_name = [
                "monday", "tuesday", "wednesday", "thursday",
                "friday", "saturday", "sunday"
            ][day_of_week]

            # Query doctor_schedules table
            result = self.db.table("doctor_schedules")\
                .select("*")\
                .eq("doctor_id", str(doctor_id))\
                .eq("day_of_week", day_name)\
                .execute()

            if not result.data:
                logger.debug(f"No schedule found for doctor {doctor_id} on {day_name}")
                return False

            # Check if slot falls within any working period
            slot_start_time = slot_time.time()
            slot_end_time = (slot_time + timedelta(minutes=duration_minutes)).time()

            for schedule in result.data:
                # Parse schedule times
                work_start = datetime.strptime(schedule["start_time"], "%H:%M").time()
                work_end = datetime.strptime(schedule["end_time"], "%H:%M").time()

                # Check if slot fits within work hours
                if slot_start_time >= work_start and slot_end_time <= work_end:
                    return True

            logger.debug(
                f"Slot {slot_time} outside working hours for doctor {doctor_id}"
            )
            return False

        except Exception as e:
            logger.error(f"Error checking doctor schedule: {e}")
            return False

    async def check_doctor_time_off(
        self,
        doctor_id: UUID,
        slot_time: datetime
    ) -> bool:
        """
        Verify doctor is not on time-off.

        Args:
            doctor_id: Doctor UUID
            slot_time: Proposed slot start time

        Returns:
            True if doctor is available (not on time-off), False if on time-off
        """
        try:
            # Query doctor_time_off table for overlapping time-off
            result = self.db.table("doctor_time_off")\
                .select("*")\
                .eq("doctor_id", str(doctor_id))\
                .lte("start_date", slot_time.date().isoformat())\
                .gte("end_date", slot_time.date().isoformat())\
                .execute()

            if result.data:
                logger.debug(
                    f"Doctor {doctor_id} has time-off on {slot_time.date()}"
                )
                return False

            return True

        except Exception as e:
            logger.error(f"Error checking doctor time-off: {e}")
            # Fail-safe: assume available if check fails
            return True

    async def check_room_availability(
        self,
        room_id: UUID,
        start_time: datetime,
        end_time: datetime,
        exclude_appointment_id: Optional[UUID] = None
    ) -> bool:
        """
        Verify room is not booked.

        Args:
            room_id: Room UUID
            start_time: Proposed slot start
            end_time: Proposed slot end
            exclude_appointment_id: Optional appointment ID to exclude (for rescheduling)

        Returns:
            True if room is available, False if booked
        """
        try:
            # Query appointments table for overlapping bookings
            query = self.db.table("appointments")\
                .select("id")\
                .eq("room_id", str(room_id))\
                .neq("status", "cancelled")\
                .or_(
                    f"and(start_time.lt.{end_time.isoformat()},end_time.gt.{start_time.isoformat()})"
                )

            if exclude_appointment_id:
                query = query.neq("id", str(exclude_appointment_id))

            result = query.execute()

            if result.data:
                logger.debug(
                    f"Room {room_id} has conflicting appointment at {start_time}"
                )
                return False

            # Also check holds
            hold_result = self.db.table("appointment_holds")\
                .select("id")\
                .eq("room_id", str(room_id))\
                .gte("expires_at", datetime.utcnow().isoformat())\
                .or_(
                    f"and(start_time.lt.{end_time.isoformat()},end_time.gt.{start_time.isoformat()})"
                )\
                .execute()

            if hold_result.data:
                logger.debug(f"Room {room_id} has active hold at {start_time}")
                return False

            return True

        except Exception as e:
            logger.error(f"Error checking room availability: {e}")
            # Fail-safe: assume unavailable if check fails
            return False

    async def check_service_eligibility(
        self,
        doctor_id: UUID,
        service_id: UUID
    ) -> bool:
        """
        Verify doctor can perform this service.

        Args:
            doctor_id: Doctor UUID
            service_id: Service UUID

        Returns:
            True if doctor is qualified for this service, False otherwise
        """
        try:
            # Query doctor_services junction table
            result = self.db.table("doctor_services")\
                .select("*")\
                .eq("doctor_id", str(doctor_id))\
                .eq("service_id", str(service_id))\
                .execute()

            if not result.data:
                logger.debug(
                    f"Doctor {doctor_id} not eligible for service {service_id}"
                )
                return False

            return True

        except Exception as e:
            logger.error(f"Error checking service eligibility: {e}")
            # Fail-safe: assume not eligible if check fails
            return False

    async def check_all_constraints(
        self,
        doctor_id: UUID,
        room_id: UUID,
        service_id: UUID,
        start_time: datetime,
        end_time: datetime,
        exclude_appointment_id: Optional[UUID] = None
    ) -> Dict[str, bool]:
        """
        Check all constraints for a slot.

        Args:
            doctor_id: Doctor UUID
            room_id: Room UUID
            service_id: Service UUID
            start_time: Slot start time
            end_time: Slot end time
            exclude_appointment_id: Optional appointment to exclude

        Returns:
            Dict with constraint check results (all must be True for valid slot)
        """
        duration_minutes = int((end_time - start_time).total_seconds() / 60)

        checks = {
            "doctor_schedule": await self.check_doctor_schedule(
                doctor_id, start_time, duration_minutes
            ),
            "doctor_available": await self.check_doctor_time_off(
                doctor_id, start_time
            ),
            "room_available": await self.check_room_availability(
                room_id, start_time, end_time, exclude_appointment_id
            ),
            "service_eligible": await self.check_service_eligibility(
                doctor_id, service_id
            )
        }

        return checks

    def is_valid_slot(self, checks: Dict[str, bool]) -> bool:
        """
        Determine if all constraint checks passed.

        Args:
            checks: Result from check_all_constraints()

        Returns:
            True if all checks passed, False otherwise
        """
        return all(checks.values())


# Import timedelta at top
from datetime import timedelta
