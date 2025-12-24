"""
Appointment Tools for LangGraph Orchestrators
Provides calendar appointment reservation capabilities for healthcare and general use cases
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Tuple
from enum import Enum
import asyncio
import json

logger = logging.getLogger(__name__)


class AppointmentType(Enum):
    """Types of appointments"""
    CONSULTATION = "consultation"
    CHECKUP = "checkup"
    DENTAL_CLEANING = "dental_cleaning"
    EMERGENCY = "emergency"
    FOLLOWUP = "followup"
    PROCEDURE = "procedure"
    GENERAL = "general"


class TimeSlot:
    """Represents an available time slot"""
    def __init__(self, start: datetime, end: datetime, available: bool = True):
        self.start = start
        self.end = end
        self.available = available
        self.duration = end - start

    def to_dict(self):
        return {
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "available": self.available,
            "duration_minutes": int(self.duration.total_seconds() / 60)
        }


class AppointmentTools:
    """
    Calendar appointment tools for orchestrators
    Integrates with external calendar services and database
    """

    def __init__(
        self,
        supabase_client: Optional[Any] = None,
        calendar_service: Optional[Any] = None,
        clinic_id: Optional[str] = None
    ):
        """
        Initialize appointment tools

        Args:
            supabase_client: Supabase client for database operations
            calendar_service: External calendar service (Google, Outlook, etc.)
            clinic_id: Optional clinic identifier for multi-tenant scenarios
        """
        self.supabase = supabase_client
        self.calendar_service = calendar_service
        self.clinic_id = clinic_id

        # Initialize with mock service if no real service provided
        if not self.calendar_service:
            self.calendar_service = MockCalendarService()

    async def check_availability(
        self,
        doctor_id: Optional[str] = None,
        date: Optional[str] = None,
        appointment_type: str = "general",
        duration_minutes: int = 30
    ) -> Dict[str, Any]:
        """
        Check availability for appointments

        Args:
            doctor_id: Optional doctor/provider ID
            date: Date to check (ISO format or natural language)
            appointment_type: Type of appointment
            duration_minutes: Required duration

        Returns:
            Dictionary with available slots
        """
        try:
            # Parse date if provided
            target_date = self._parse_date(date) if date else datetime.now().date()

            # Get business hours
            business_hours = await self._get_business_hours(target_date)

            # Get existing appointments
            existing = await self._get_existing_appointments(doctor_id, target_date)

            # Calculate available slots
            slots = self._calculate_available_slots(
                business_hours,
                existing,
                duration_minutes
            )

            return {
                "success": True,
                "date": target_date.isoformat(),
                "doctor_id": doctor_id,
                "available_slots": [slot.to_dict() for slot in slots],
                "total_slots": len(slots),
                "next_available": slots[0].to_dict() if slots else None
            }

        except Exception as e:
            logger.error(f"Error checking availability: {e}")
            return {
                "success": False,
                "error": str(e)
            }

    async def book_appointment(
        self,
        patient_id: str,
        doctor_id: Optional[str] = None,
        datetime_str: str = None,
        appointment_type: str = "general",
        duration_minutes: int = 30,
        notes: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Book an appointment

        Args:
            patient_id: Patient identifier
            doctor_id: Optional doctor/provider ID
            datetime_str: Appointment datetime (ISO format or natural)
            appointment_type: Type of appointment
            duration_minutes: Appointment duration
            notes: Optional appointment notes

        Returns:
            Booking confirmation or error
        """
        try:
            # Parse datetime
            start_time = self._parse_datetime(datetime_str) if datetime_str else None
            if not start_time:
                return {
                    "success": False,
                    "error": "Invalid or missing appointment time"
                }

            end_time = start_time + timedelta(minutes=duration_minutes)

            # Check if slot is available
            is_available = await self._verify_slot_available(
                doctor_id,
                start_time,
                end_time
            )

            if not is_available:
                return {
                    "success": False,
                    "error": "Requested time slot is not available"
                }

            # Create appointment (with hold pattern if using external calendar)
            appointment_id = await self._create_appointment(
                patient_id=patient_id,
                doctor_id=doctor_id,
                start_time=start_time,
                end_time=end_time,
                appointment_type=appointment_type,
                notes=notes
            )

            # Sync with external calendar if available
            if self.calendar_service:
                external_id = await self.calendar_service.create_event(
                    title=f"Appointment: {appointment_type}",
                    start_time=start_time,
                    end_time=end_time,
                    description=notes
                )

            return {
                "success": True,
                "appointment_id": appointment_id,
                "patient_id": patient_id,
                "doctor_id": doctor_id,
                "datetime": start_time.isoformat(),
                "duration_minutes": duration_minutes,
                "type": appointment_type,
                "confirmation_message": f"Your appointment has been booked for {start_time.strftime('%B %d at %I:%M %p')}"
            }

        except Exception as e:
            logger.error(f"Error booking appointment: {e}")
            return {
                "success": False,
                "error": str(e)
            }

    async def cancel_appointment(
        self,
        appointment_id: str,
        reason: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Cancel an existing appointment

        Args:
            appointment_id: Appointment identifier
            reason: Optional cancellation reason

        Returns:
            Cancellation confirmation
        """
        try:
            # Get appointment details
            appointment = await self._get_appointment(appointment_id)
            if not appointment:
                return {
                    "success": False,
                    "error": "Appointment not found"
                }

            # Cancel in database
            await self._cancel_appointment_db(appointment_id, reason)

            # Cancel in external calendar
            if self.calendar_service and appointment.get('external_event_id'):
                await self.calendar_service.cancel_event(
                    appointment['external_event_id']
                )

            return {
                "success": True,
                "appointment_id": appointment_id,
                "cancelled_at": datetime.now().isoformat(),
                "reason": reason,
                "message": "Appointment has been successfully cancelled"
            }

        except Exception as e:
            logger.error(f"Error cancelling appointment: {e}")
            return {
                "success": False,
                "error": str(e)
            }

    async def reschedule_appointment(
        self,
        appointment_id: str,
        new_datetime: str,
        reason: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Reschedule an existing appointment

        Args:
            appointment_id: Appointment identifier
            new_datetime: New appointment datetime
            reason: Optional reason for rescheduling

        Returns:
            Rescheduling confirmation
        """
        try:
            # Get existing appointment
            appointment = await self._get_appointment(appointment_id)
            if not appointment:
                return {
                    "success": False,
                    "error": "Appointment not found"
                }

            # Parse new datetime
            new_start = self._parse_datetime(new_datetime)
            duration = appointment.get('duration_minutes', 30)
            new_end = new_start + timedelta(minutes=duration)

            # Check new slot availability
            is_available = await self._verify_slot_available(
                appointment.get('doctor_id'),
                new_start,
                new_end
            )

            if not is_available:
                return {
                    "success": False,
                    "error": "New time slot is not available"
                }

            # Update appointment
            await self._update_appointment(
                appointment_id,
                new_start,
                new_end,
                reason
            )

            # Update external calendar
            if self.calendar_service and appointment.get('external_event_id'):
                await self.calendar_service.update_event(
                    appointment['external_event_id'],
                    start_time=new_start,
                    end_time=new_end
                )

            return {
                "success": True,
                "appointment_id": appointment_id,
                "old_datetime": appointment['start_time'],
                "new_datetime": new_start.isoformat(),
                "reason": reason,
                "message": f"Appointment rescheduled to {new_start.strftime('%B %d at %I:%M %p')}"
            }

        except Exception as e:
            logger.error(f"Error rescheduling appointment: {e}")
            return {
                "success": False,
                "error": str(e)
            }

    async def get_upcoming_appointments(
        self,
        patient_id: Optional[str] = None,
        doctor_id: Optional[str] = None,
        days_ahead: int = 7
    ) -> Dict[str, Any]:
        """
        Get upcoming appointments

        Args:
            patient_id: Optional patient filter
            doctor_id: Optional doctor filter
            days_ahead: Number of days to look ahead

        Returns:
            List of upcoming appointments
        """
        try:
            start_date = datetime.now()
            end_date = start_date + timedelta(days=days_ahead)

            appointments = await self._query_appointments(
                patient_id=patient_id,
                doctor_id=doctor_id,
                start_date=start_date,
                end_date=end_date
            )

            return {
                "success": True,
                "appointments": appointments,
                "count": len(appointments),
                "period": {
                    "start": start_date.isoformat(),
                    "end": end_date.isoformat()
                }
            }

        except Exception as e:
            logger.error(f"Error getting upcoming appointments: {e}")
            return {
                "success": False,
                "error": str(e)
            }

    # Private helper methods

    def _parse_date(self, date_str: str) -> datetime:
        """Parse date string to datetime object"""
        # Handle various date formats
        try:
            # Try ISO format first
            return datetime.fromisoformat(date_str).date()
        except:
            # Try common formats
            formats = [
                "%Y-%m-%d",
                "%m/%d/%Y",
                "%d/%m/%Y",
                "%B %d, %Y",
                "%b %d, %Y"
            ]
            for fmt in formats:
                try:
                    return datetime.strptime(date_str, fmt).date()
                except:
                    continue

            # Handle natural language (simplified)
            if "tomorrow" in date_str.lower():
                return (datetime.now() + timedelta(days=1)).date()
            elif "today" in date_str.lower():
                return datetime.now().date()

            raise ValueError(f"Could not parse date: {date_str}")

    def _parse_datetime(self, datetime_str: str) -> datetime:
        """Parse datetime string to datetime object"""
        try:
            return datetime.fromisoformat(datetime_str)
        except:
            # Try other formats
            formats = [
                "%Y-%m-%d %H:%M",
                "%m/%d/%Y %I:%M %p",
                "%d/%m/%Y %H:%M"
            ]
            for fmt in formats:
                try:
                    return datetime.strptime(datetime_str, fmt)
                except:
                    continue
            raise ValueError(f"Could not parse datetime: {datetime_str}")

    async def _get_business_hours(self, date: datetime) -> Tuple[datetime, datetime]:
        """Get business hours for a given date"""
        # Default business hours (can be customized per clinic)
        day_of_week = date.weekday()

        # Skip weekends by default
        if day_of_week >= 5:  # Saturday or Sunday
            return None, None

        # Default hours: 9 AM to 5 PM
        start_hour = 9
        end_hour = 17

        # Query database for custom hours if available
        if self.supabase and self.clinic_id:
            try:
                result = self.supabase.table("business_hours").select("*").eq(
                    "clinic_id", self.clinic_id
                ).eq(
                    "day_of_week", day_of_week
                ).single().execute()

                if result.data:
                    start_hour = result.data.get('start_hour', start_hour)
                    end_hour = result.data.get('end_hour', end_hour)
            except:
                pass

        start = datetime.combine(date, datetime.min.time().replace(hour=start_hour))
        end = datetime.combine(date, datetime.min.time().replace(hour=end_hour))

        return start, end

    async def _get_existing_appointments(
        self,
        doctor_id: Optional[str],
        date: datetime
    ) -> List[Dict]:
        """Get existing appointments for a date"""
        if not self.supabase:
            return []

        try:
            query = self.supabase.table("appointments").select("*")

            if doctor_id:
                query = query.eq("doctor_id", doctor_id)

            start = datetime.combine(date, datetime.min.time())
            end = start + timedelta(days=1)

            result = query.gte("start_time", start.isoformat()).lt(
                "start_time", end.isoformat()
            ).execute()

            return result.data if result.data else []
        except:
            return []

    def _calculate_available_slots(
        self,
        business_hours: Tuple[datetime, datetime],
        existing_appointments: List[Dict],
        duration_minutes: int
    ) -> List[TimeSlot]:
        """Calculate available time slots, excluding past times"""
        if not business_hours[0] or not business_hours[1]:
            return []

        slots = []
        slot_duration = timedelta(minutes=duration_minutes)

        # Get current time with 30-minute buffer for realistic booking
        now = datetime.now()
        min_booking_time = now + timedelta(minutes=30)

        # Start from business hours or min_booking_time, whichever is later
        current_time = max(business_hours[0], min_booking_time)

        # Create sorted list of existing appointment times
        blocked_times = []
        for appt in existing_appointments:
            start = datetime.fromisoformat(appt['start_time'])
            end = datetime.fromisoformat(appt['end_time'])
            blocked_times.append((start, end))

        blocked_times.sort(key=lambda x: x[0])

        # Find available slots
        while current_time + slot_duration <= business_hours[1]:
            slot_end = current_time + slot_duration

            # Check if slot conflicts with existing appointments
            is_available = True
            for blocked_start, blocked_end in blocked_times:
                if not (slot_end <= blocked_start or current_time >= blocked_end):
                    is_available = False
                    # Move to end of blocking appointment
                    current_time = blocked_end
                    break

            if is_available:
                slots.append(TimeSlot(current_time, slot_end, True))
                current_time += timedelta(minutes=15)  # 15-minute increments
            else:
                continue

        return slots

    async def _verify_slot_available(
        self,
        doctor_id: Optional[str],
        start_time: datetime,
        end_time: datetime
    ) -> bool:
        """Verify if a time slot is available"""
        # Check against existing appointments
        existing = await self._get_existing_appointments(
            doctor_id,
            start_time.date()
        )

        for appt in existing:
            appt_start = datetime.fromisoformat(appt['start_time'])
            appt_end = datetime.fromisoformat(appt['end_time'])

            # Check for overlap
            if not (end_time <= appt_start or start_time >= appt_end):
                return False

        return True

    async def _create_appointment(self, **kwargs) -> str:
        """Create appointment in database"""
        if not self.supabase:
            # Return mock ID for testing
            return f"appt_{datetime.now().timestamp()}"

        try:
            result = self.supabase.table("appointments").insert({
                "patient_id": kwargs.get('patient_id'),
                "doctor_id": kwargs.get('doctor_id'),
                "start_time": kwargs.get('start_time').isoformat(),
                "end_time": kwargs.get('end_time').isoformat(),
                "appointment_type": kwargs.get('appointment_type'),
                "notes": kwargs.get('notes'),
                "status": "confirmed",
                "clinic_id": self.clinic_id
            }).execute()

            return result.data[0]['id'] if result.data else None
        except Exception as e:
            logger.error(f"Error creating appointment: {e}")
            raise

    async def _get_appointment(self, appointment_id: str) -> Optional[Dict]:
        """Get appointment details"""
        if not self.supabase:
            return None

        try:
            result = self.supabase.table("appointments").select("*").eq(
                "id", appointment_id
            ).single().execute()
            return result.data
        except:
            return None

    async def _cancel_appointment_db(self, appointment_id: str, reason: str):
        """Cancel appointment in database"""
        if not self.supabase:
            return

        try:
            self.supabase.table("appointments").update({
                "status": "cancelled",
                "cancellation_reason": reason,
                "cancelled_at": datetime.now().isoformat()
            }).eq("id", appointment_id).execute()
        except Exception as e:
            logger.error(f"Error cancelling appointment: {e}")
            raise

    async def _update_appointment(
        self,
        appointment_id: str,
        new_start: datetime,
        new_end: datetime,
        reason: str
    ):
        """Update appointment in database"""
        if not self.supabase:
            return

        try:
            self.supabase.table("appointments").update({
                "start_time": new_start.isoformat(),
                "end_time": new_end.isoformat(),
                "rescheduled_at": datetime.now().isoformat(),
                "reschedule_reason": reason
            }).eq("id", appointment_id).execute()
        except Exception as e:
            logger.error(f"Error updating appointment: {e}")
            raise

    async def _query_appointments(self, **filters) -> List[Dict]:
        """Query appointments with filters"""
        if not self.supabase:
            return []

        try:
            query = self.supabase.table("appointments").select("*")

            if filters.get('patient_id'):
                query = query.eq("patient_id", filters['patient_id'])
            if filters.get('doctor_id'):
                query = query.eq("doctor_id", filters['doctor_id'])
            if filters.get('start_date'):
                query = query.gte("start_time", filters['start_date'].isoformat())
            if filters.get('end_date'):
                query = query.lt("start_time", filters['end_date'].isoformat())

            result = query.order("start_time").execute()
            return result.data if result.data else []
        except:
            return []


class MockCalendarService:
    """Mock calendar service for testing"""

    async def create_event(self, **kwargs):
        return f"mock_event_{datetime.now().timestamp()}"

    async def cancel_event(self, event_id):
        return True

    async def update_event(self, event_id, **kwargs):
        return True


# Export main tools class
__all__ = ['AppointmentTools', 'AppointmentType', 'TimeSlot']