"""
Booking module for appointment management.
"""

from typing import Optional, Dict, Any, List
from datetime import datetime, timedelta
from app.appointments import AppointmentBooking

class BookingManager(AppointmentBooking):
    """Extended booking manager with additional features"""

    def __init__(self, clinic_id: str):
        super().__init__(clinic_id)

    async def create_booking(
        self,
        patient_id: str,
        service: str,
        date: str,
        time: str,
        notes: Optional[str] = None
    ) -> Dict[str, Any]:
        """Create a new booking"""
        # Validate appointment
        is_valid, message = await self.validate_appointment(date, time)

        if not is_valid:
            return {
                'success': False,
                'error': message
            }

        # Create booking
        booking = {
            'id': f"APT-{datetime.now().strftime('%Y%m%d%H%M%S')}",
            'patient_id': patient_id,
            'clinic_id': self.clinic_id,
            'service': service,
            'date': date,
            'time': time,
            'notes': notes,
            'status': 'confirmed',
            'created_at': datetime.now().isoformat()
        }

        # In production, save to database
        return {
            'success': True,
            'booking': booking
        }

    async def get_bookings(
        self,
        patient_id: Optional[str] = None,
        date: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Get bookings with optional filters"""
        # In production, fetch from database
        return []

    async def cancel_booking(
        self,
        booking_id: str,
        reason: Optional[str] = None
    ) -> Dict[str, Any]:
        """Cancel a booking"""
        # In production, update database
        return {
            'success': True,
            'booking_id': booking_id,
            'status': 'cancelled',
            'reason': reason
        }

    async def reschedule_booking(
        self,
        booking_id: str,
        new_date: str,
        new_time: str
    ) -> Dict[str, Any]:
        """Reschedule an existing booking"""
        # Validate new appointment
        is_valid, message = await self.validate_appointment(new_date, new_time)

        if not is_valid:
            return {
                'success': False,
                'error': message
            }

        # In production, update database
        return {
            'success': True,
            'booking_id': booking_id,
            'new_date': new_date,
            'new_time': new_time,
            'status': 'rescheduled'
        }
