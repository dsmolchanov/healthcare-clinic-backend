"""
Availability Service

P2: FSM Integration - Availability & Hold System
Queries doctor schedules and existing holds/appointments to find available slots

Implements:
- P0 Fix #4: Timezone handling with ZoneInfo
- P0 Fix #5: Supabase schema naming
- Smart availability checking
- Alternative slot suggestion
"""

import logging
from datetime import datetime, date, time, timedelta, timezone
from typing import Dict, List, Optional, Tuple
from supabase import Client
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)


class AvailabilityService:
    """
    Finds available appointment slots based on:
    - Doctor work schedules
    - Existing appointments
    - Active holds
    - Clinic hours
    - Timezone-aware datetime handling
    """

    def __init__(self, supabase_client: Client):
        self.supabase = supabase_client

    async def get_available_slots(
        self,
        clinic_id: str,
        doctor_id: str,
        preferred_date: date,
        clinic_timezone: str = "UTC",
        limit: int = 5
    ) -> List[Dict[str, str]]:
        """
        Get next available slots for doctor on or after preferred_date

        Args:
            clinic_id: Clinic UUID
            doctor_id: Doctor UUID
            preferred_date: Start date for availability search
            clinic_timezone: Clinic timezone string (e.g., 'America/Los_Angeles')
            limit: Maximum number of slots to return

        Returns:
            List of available slots:
            [
              {
                "date": "2025-01-17",
                "time": "14:00",
                "display": "Пятница, 17 января в 14:00",
                "start_datetime_utc": "2025-01-17T22:00:00+00:00"
              },
              ...
            ]
        """
        available_slots = []
        check_date = preferred_date
        max_days_ahead = 14  # Search up to 2 weeks ahead

        # Get clinic timezone
        clinic_tz = ZoneInfo(clinic_timezone)

        for days_offset in range(max_days_ahead):
            check_date = preferred_date + timedelta(days=days_offset)
            day_of_week = check_date.weekday()  # 0=Monday, 6=Sunday

            # Get doctor's schedule for this day
            schedule = await self._get_doctor_schedule(doctor_id, day_of_week)

            if not schedule:
                # Doctor doesn't work this day
                continue

            # Get all slots for this day
            slots = self._generate_time_slots(
                schedule['start_time'],
                schedule['end_time'],
                schedule['slot_duration_minutes']
            )

            # Check availability for each slot
            for slot_time in slots:
                # Create timezone-aware datetime in clinic timezone
                naive_dt = datetime.combine(check_date, slot_time)
                local_dt = naive_dt.replace(tzinfo=clinic_tz)

                # Convert to UTC for comparison and storage
                start_datetime_utc = local_dt.astimezone(timezone.utc)

                # Skip past slots (compare in UTC)
                if start_datetime_utc < datetime.now(timezone.utc):
                    continue

                # Check if slot is available
                is_available = await self._check_slot_available(
                    clinic_id,
                    doctor_id,
                    start_datetime_utc,
                    schedule['slot_duration_minutes']
                )

                if is_available:
                    available_slots.append({
                        'date': check_date.isoformat(),
                        'time': slot_time.isoformat(),
                        'display': self._format_slot_display(check_date, slot_time),
                        'start_datetime_utc': start_datetime_utc.isoformat()
                    })

                    if len(available_slots) >= limit:
                        return available_slots

        return available_slots

    async def _get_doctor_schedule(
        self,
        doctor_id: str,
        day_of_week: int
    ) -> Optional[Dict]:
        """
        Get doctor's schedule for specific day of week

        Args:
            doctor_id: Doctor UUID
            day_of_week: 0=Monday, 1=Tuesday, ..., 6=Sunday

        Returns:
            Schedule dict with start_time, end_time, slot_duration_minutes
            or None if no schedule for this day
        """
        try:
            result = self.supabase.schema('healthcare').table('doctor_schedules').select('*').eq(
                'doctor_id', doctor_id
            ).eq(
                'weekday', day_of_week  # Uses 'weekday' column name from existing schema
            ).eq(
                'is_active', True
            ).limit(1).execute()

            if result.data and len(result.data) > 0:
                schedule = result.data[0]
                # Parse time strings if needed
                if isinstance(schedule['start_time'], str):
                    schedule['start_time'] = time.fromisoformat(schedule['start_time'])
                if isinstance(schedule['end_time'], str):
                    schedule['end_time'] = time.fromisoformat(schedule['end_time'])

                return schedule
            else:
                return None

        except Exception as e:
            logger.error(f"Error fetching doctor schedule: {e}", exc_info=True)
            return None

    def _generate_time_slots(
        self,
        start_time: time,
        end_time: time,
        slot_duration_minutes: int
    ) -> List[time]:
        """
        Generate list of time slots between start and end

        Args:
            start_time: Schedule start time
            end_time: Schedule end time
            slot_duration_minutes: Duration of each slot

        Returns:
            List of time objects representing slot start times
        """
        slots = []
        current = datetime.combine(date.today(), start_time)
        end_dt = datetime.combine(date.today(), end_time)

        while current + timedelta(minutes=slot_duration_minutes) <= end_dt:
            slots.append(current.time())
            current += timedelta(minutes=slot_duration_minutes)

        return slots

    async def _check_slot_available(
        self,
        clinic_id: str,
        doctor_id: str,
        start_datetime: datetime,
        duration_minutes: int
    ) -> bool:
        """
        Check if slot is available (no holds or appointments)

        Args:
            clinic_id: Clinic UUID
            doctor_id: Doctor UUID
            start_datetime: Slot start time (UTC)
            duration_minutes: Slot duration

        Returns:
            True if slot is available, False otherwise
        """
        end_datetime = start_datetime + timedelta(minutes=duration_minutes)

        try:
            # Check holds (active holds block availability)
            holds = self.supabase.schema('healthcare').table('appointment_holds').select('id').eq(
                'clinic_id', clinic_id
            ).eq(
                'doctor_id', doctor_id
            ).in_(
                'status', ['held', 'reserved']
            ).gte(
                'start_time', start_datetime.isoformat()
            ).lt(
                'end_time', end_datetime.isoformat()
            ).limit(1).execute()

            if holds.data and len(holds.data) > 0:
                return False

            # Check appointments (scheduled appointments block availability)
            appointments = self.supabase.schema('healthcare').table('appointments').select('id').eq(
                'doctor_id', doctor_id
            ).eq(
                'appointment_date', start_datetime.date().isoformat()
            ).gte(
                'start_time', start_datetime.time().isoformat()
            ).lt(
                'end_time', end_datetime.time().isoformat()
            ).eq(
                'status', 'scheduled'
            ).limit(1).execute()

            if appointments.data and len(appointments.data) > 0:
                return False

            return True

        except Exception as e:
            logger.error(f"Error checking slot availability: {e}", exc_info=True)
            # Conservative: assume slot is not available on error
            return False

    def _format_slot_display(self, slot_date: date, slot_time: time) -> str:
        """
        Format slot for user-friendly display in Russian

        Args:
            slot_date: Slot date
            slot_time: Slot time

        Returns:
            Formatted string like "Пятница, 17 января в 14:00"
        """
        # Get day name in Russian
        day_names = [
            "Понедельник", "Вторник", "Среда", "Четверг",
            "Пятница", "Суббота", "Воскресенье"
        ]
        day_name = day_names[slot_date.weekday()]

        # Format date as "17 января"
        month_names = [
            "января", "февраля", "марта", "апреля", "мая", "июня",
            "июля", "августа", "сентября", "октября", "ноября", "декабря"
        ]
        date_str = f"{slot_date.day} {month_names[slot_date.month - 1]}"

        # Format time as "14:00"
        time_str = slot_time.strftime("%H:%M")

        return f"{day_name}, {date_str} в {time_str}"

    async def get_clinic_timezone(self, clinic_id: str) -> str:
        """
        Get clinic timezone string (e.g., 'America/Los_Angeles')

        Args:
            clinic_id: Clinic UUID

        Returns:
            Timezone string or 'UTC' if not found
        """
        try:
            result = self.supabase.schema('healthcare').table('clinics').select('timezone').eq(
                'id', clinic_id
            ).single().execute()

            return result.data.get('timezone', 'UTC') if result.data else 'UTC'

        except Exception as e:
            logger.error(f"Error fetching clinic timezone: {e}", exc_info=True)
            return 'UTC'

    async def suggest_alternatives(
        self,
        clinic_id: str,
        doctor_id: str,
        preferred_date: date,
        clinic_timezone: str = "UTC",
        count: int = 3
    ) -> List[Dict[str, str]]:
        """
        Suggest alternative slots when preferred slot is unavailable

        Args:
            clinic_id: Clinic UUID
            doctor_id: Doctor UUID
            preferred_date: Originally requested date
            clinic_timezone: Clinic timezone string
            count: Number of alternatives to suggest

        Returns:
            List of alternative slots (same format as get_available_slots)
        """
        # Get next available slots starting from preferred_date
        return await self.get_available_slots(
            clinic_id=clinic_id,
            doctor_id=doctor_id,
            preferred_date=preferred_date,
            clinic_timezone=clinic_timezone,
            limit=count
        )
