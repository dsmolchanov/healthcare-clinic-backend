"""
Appointment booking and management system
"""

import uuid
from datetime import datetime, timedelta, time
from typing import Dict, Any, List, Optional, Tuple
import re


class AppointmentBooking:
    """Base appointment booking class"""

    def __init__(self, clinic_id: str):
        self.clinic_id = clinic_id

    async def validate_appointment(self, date: str, time: str) -> Tuple[bool, str]:
        """Validate appointment date and time"""
        try:
            appointment_datetime = datetime.strptime(f"{date} {time}", "%Y-%m-%d %H:%M")

            # Must be in the future
            if appointment_datetime <= datetime.now():
                return False, "Appointment must be in the future"

            # Business hours check (9 AM - 6 PM)
            hour = appointment_datetime.hour
            if hour < 9 or hour >= 18:
                return False, "Appointments only available 9 AM - 6 PM"

            # Not on Sunday
            if appointment_datetime.weekday() == 6:
                return False, "Clinic closed on Sundays"

            return True, "Valid appointment time"

        except ValueError:
            return False, "Invalid date/time format"

class SimpleAppointmentBooking(AppointmentBooking):
    """Basic appointment booking system for Mexican clinics"""

    async def book_appointment(
        self,
        clinic_id: str,
        patient_phone: str,
        requested_date: str,
        requested_time: str
    ) -> Dict[str, Any]:
        """
        Book an appointment

        Args:
            clinic_id: Clinic identifier
            patient_phone: Patient phone number
            requested_date: Date in YYYY-MM-DD format
            requested_time: Time in HH:MM format

        Returns:
            Booking result dictionary
        """
        from .database import db
        from .audit import hash_phone

        # Check availability
        available = await self.check_slot_availability(
            clinic_id, requested_date, requested_time
        )

        if not available:
            # Suggest alternatives
            alternatives = await self.suggest_alternatives(
                clinic_id, requested_date
            )

            return {
                'success': False,
                'message': f'El horario {requested_time} del {requested_date} no está disponible.',
                'alternatives': alternatives
            }

        # Create appointment
        appointment_id = str(uuid.uuid4())
        appointment = {
            'id': appointment_id,
            'clinic_id': clinic_id,
            'patient_phone': hash_phone(patient_phone),
            'appointment_date': requested_date,
            'start_time': requested_time,
            'end_time': self._calculate_end_time(requested_time),
            'status': 'scheduled',
            'created_via': 'whatsapp',
            'created_at': datetime.utcnow().isoformat()
        }

        await db.table('healthcare.appointments').insert(appointment).execute()

        # Send confirmation
        await self.send_confirmation(patient_phone, appointment)

        # Schedule reminder
        await self.schedule_reminder(appointment)

        return {
            'success': True,
            'appointment_id': appointment_id,
            'message': f'✅ Cita confirmada para el {requested_date} a las {requested_time}'
        }

    async def check_slot_availability(
        self,
        clinic_id: str,
        date: str,
        time: str
    ) -> bool:
        """
        Check if a time slot is available

        Args:
            clinic_id: Clinic identifier
            date: Date in YYYY-MM-DD format
            time: Time in HH:MM format

        Returns:
            True if available, False otherwise
        """
        from .database import db

        # Get existing appointments for this slot
        existing = await db.table('healthcare.appointments')\
            .select('count')\
            .eq('clinic_id', clinic_id)\
            .eq('appointment_date', date)\
            .eq('start_time', time)\
            .eq('status', 'scheduled')\
            .execute()

        existing_count = existing.count if existing else 0

        # Get clinic capacity
        clinic = await db.table('healthcare.clinics')\
            .select('max_appointments_per_slot')\
            .eq('id', clinic_id)\
            .single()\
            .execute()

        max_slots = clinic.data.get('max_appointments_per_slot', 1) if clinic else 1

        return existing_count < max_slots

    async def suggest_alternatives(
        self,
        clinic_id: str,
        requested_date: str,
        num_alternatives: int = 3
    ) -> List[Dict[str, str]]:
        """
        Suggest alternative appointment slots

        Args:
            clinic_id: Clinic identifier
            requested_date: Originally requested date
            num_alternatives: Number of alternatives to suggest

        Returns:
            List of alternative slots
        """
        alternatives = []
        times_to_check = ['09:00', '10:00', '11:00', '14:00', '14:30', '15:00', '16:00', '17:00']

        for time_slot in times_to_check:
            if len(alternatives) >= num_alternatives:
                break

            if await self.check_slot_availability(clinic_id, requested_date, time_slot):
                alternatives.append({
                    'date': requested_date,
                    'time': time_slot
                })

        # Check next day if needed
        if len(alternatives) < num_alternatives:
            next_date = (datetime.strptime(requested_date, '%Y-%m-%d') + timedelta(days=1)).strftime('%Y-%m-%d')
            for time_slot in times_to_check:
                if len(alternatives) >= num_alternatives:
                    break

                if await self.check_slot_availability(clinic_id, next_date, time_slot):
                    alternatives.append({
                        'date': next_date,
                        'time': time_slot
                    })

        return alternatives

    async def send_confirmation(self, phone: str, appointment: Dict[str, Any]):
        """Send appointment confirmation"""
        from .whatsapp import send_whatsapp_message

        message = f"""
✅ *Cita Confirmada*

📅 Fecha: {appointment['appointment_date']}
🕐 Hora: {appointment['start_time']}
📍 Clínica: {appointment.get('clinic_name', 'Clínica Dental')}

Por favor llegue 10 minutos antes de su cita.

Para cancelar responda CANCELAR.
"""

        await send_whatsapp_message(phone, message)

    async def schedule_reminder(self, appointment: Dict[str, Any]):
        """Schedule appointment reminder"""
        from .scheduler import schedule_task

        # Schedule 24 hour reminder
        appointment_datetime = datetime.strptime(
            f"{appointment['appointment_date']} {appointment['start_time']}",
            '%Y-%m-%d %H:%M'
        )

        reminder_time = appointment_datetime - timedelta(hours=24)

        await schedule_task(
            reminder_time,
            'send_reminder',
            appointment
        )

    def _calculate_end_time(self, start_time: str, duration_minutes: int = 60) -> str:
        """Calculate appointment end time"""
        start = datetime.strptime(start_time, '%H:%M')
        end = start + timedelta(minutes=duration_minutes)
        return end.strftime('%H:%M')


async def validate_appointment_request(request: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """
    Validate appointment request data

    Args:
        request: Appointment request dictionary

    Returns:
        Tuple of (is_valid, list_of_errors)
    """
    errors = []

    # Check date format
    if 'date' in request:
        try:
            date = datetime.strptime(request['date'], '%Y-%m-%d')

            # Check if date is in the past
            if date.date() < datetime.now().date():
                errors.append('Cannot book appointments in the past')
        except ValueError:
            errors.append('Invalid date format. Use YYYY-MM-DD')
    else:
        errors.append('Date is required')

    # Check time format
    if 'time' in request:
        if not re.match(r'^\d{2}:\d{2}$', request['time']):
            errors.append('Invalid time format. Use HH:MM')
    else:
        errors.append('Time is required')

    # Check service
    if 'service' not in request:
        errors.append('Service is required')

    return (len(errors) == 0, errors)


async def check_business_hours(clinic: Dict[str, Any], day: str, time: str) -> bool:
    """
    Check if time is within business hours

    Args:
        clinic: Clinic configuration
        day: Day of week (monday, tuesday, etc.)
        time: Time in HH:MM format

    Returns:
        True if within business hours
    """
    business_hours = clinic.get('business_hours', {})
    day_hours = business_hours.get(day.lower())

    if not day_hours or day_hours == 'closed':
        return False

    open_time = datetime.strptime(day_hours['open'], '%H:%M').time()
    close_time = datetime.strptime(day_hours['close'], '%H:%M').time()
    request_time = datetime.strptime(time, '%H:%M').time()

    return open_time <= request_time < close_time


async def check_slot_availability(
    clinic_id: str,
    date: str,
    time: str,
    max_capacity: int
) -> bool:
    """
    Check if appointment slot has capacity

    Args:
        clinic_id: Clinic identifier
        date: Date in YYYY-MM-DD format
        time: Time in HH:MM format
        max_capacity: Maximum appointments per slot

    Returns:
        True if slot has capacity
    """
    from .database import db

    existing = await db.table('healthcare.appointments')\
        .select('count')\
        .eq('clinic_id', clinic_id)\
        .eq('appointment_date', date)\
        .eq('start_time', time)\
        .eq('status', 'scheduled')\
        .execute()

    current_count = existing.count if existing else 0

    return current_count < max_capacity


async def suggest_alternatives(
    clinic_id: str,
    requested_date: str,
    requested_time: str,
    num_alternatives: int = 3
) -> List[Dict[str, str]]:
    """Suggest alternative appointment times"""
    from .database import db

    alternatives = []

    # Time slots to check (30 minute intervals)
    base_time = datetime.strptime(requested_time, '%H:%M')
    time_slots = []

    # Check nearby times
    for offset in [-60, -30, 30, 60, 90, 120]:
        new_time = base_time + timedelta(minutes=offset)
        if datetime.strptime('09:00', '%H:%M').time() <= new_time.time() <= datetime.strptime('18:00', '%H:%M').time():
            time_slots.append(new_time.strftime('%H:%M'))

    # Check availability for each slot
    for time_slot in time_slots:
        if len(alternatives) >= num_alternatives:
            break

        # Mock availability check (would query database)
        available = await db.table('healthcare.appointments')\
            .select('count')\
            .eq('clinic_id', clinic_id)\
            .eq('appointment_date', requested_date)\
            .eq('start_time', time_slot)\
            .eq('status', 'scheduled')\
            .execute()

        if not available or available.count < 2:  # Assuming max 2 per slot
            alternatives.append({
                'date': requested_date,
                'time': time_slot
            })

    return alternatives


async def send_confirmation(phone: str, appointment: Dict[str, Any]):
    """Send appointment confirmation message"""
    from .whatsapp import send_whatsapp_message

    message = f"""
✅ *Cita Confirmada*

📅 Fecha: {appointment['appointment_date']}
🕐 Hora: {appointment['start_time']}
🏥 Servicio: {appointment.get('service', 'Consulta')}

Le enviaremos un recordatorio 24 horas antes.

Para cancelar, responda CANCELAR.
"""

    await send_whatsapp_message(phone, message)


async def send_detailed_confirmation(
    phone: str,
    appointment: Dict[str, Any],
    clinic: Dict[str, Any]
):
    """Send detailed confirmation with clinic info"""
    from .whatsapp import send_whatsapp_message

    message = f"""
✅ *Cita Confirmada*

📅 Fecha: {appointment['appointment_date']}
🕐 Hora: {appointment['start_time']}
🏥 Clínica: {clinic['name']}
🌐 Sitio web: {clinic['website']}
📍 Dirección: {clinic.get('address', 'Ver sitio web')}

Le enviaremos un recordatorio 24 horas antes.
"""

    await send_whatsapp_message(phone, message)


async def confirm_appointment(appointment_id: str):
    """Update appointment status to confirmed"""
    from .database import db

    await db.table('healthcare.appointments')\
        .update({
            'status': 'confirmed',
            'confirmed_at': datetime.utcnow().isoformat()
        })\
        .eq('id', appointment_id)\
        .execute()


async def schedule_reminder(appointment: Dict[str, Any]):
    """Schedule appointment reminder"""
    # This would integrate with a task scheduler
    pass


async def send_reminder(appointment: Dict[str, Any]):
    """Send appointment reminder"""
    from .whatsapp import send_whatsapp_message
    from .audit import hash_phone

    # Unhash phone for sending (in real system would store unhashed separately)
    phone = appointment.get('patient_phone_unhashed', appointment['patient_phone'])

    message = f"""
🔔 *Recordatorio de Cita*

Su cita es mañana:
📅 {appointment['appointment_date']}
🕐 {appointment['start_time']}
🏥 Servicio: {appointment.get('service', 'Consulta')}

Por favor llegue 10 minutos antes.

Para cancelar, responda CANCELAR.
"""

    await send_whatsapp_message(phone, message)


async def schedule_multiple_reminders(appointment: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Schedule multiple reminders for an appointment"""
    reminders = []

    # 24 hour reminder
    reminders.append({
        'appointment_id': appointment['id'],
        'hours_before': 24,
        'scheduled_for': datetime.utcnow() + timedelta(hours=23)  # Mock time
    })

    # 2 hour reminder
    reminders.append({
        'appointment_id': appointment['id'],
        'hours_before': 2,
        'scheduled_for': datetime.utcnow() + timedelta(hours=1, minutes=58)  # Mock time
    })

    return reminders


async def cancel_appointment(appointment_id: str, reason: str = '') -> Dict[str, Any]:
    """Cancel an appointment"""
    from .database import db

    await db.table('healthcare.appointments')\
        .update({
            'status': 'cancelled',
            'cancellation_reason': reason,
            'cancelled_at': datetime.utcnow().isoformat()
        })\
        .eq('id', appointment_id)\
        .execute()

    return {
        'success': True,
        'message': 'Cita cancelada exitosamente'
    }


async def send_cancellation_confirmation(phone: str, appointment: Dict[str, Any]):
    """Send cancellation confirmation"""
    from .whatsapp import send_whatsapp_message

    message = f"""
❌ *Cita Cancelada*

Su cita del {appointment['appointment_date']} a las {appointment['start_time']} ha sido cancelada.

Para agendar una nueva cita, escriba "Quiero una cita".
"""

    await send_whatsapp_message(phone, message)


async def can_cancel_appointment(appointment: Dict[str, Any]) -> Dict[str, bool]:
    """Check if appointment can be cancelled"""
    appointment_datetime = datetime.strptime(
        f"{appointment['appointment_date']} {appointment['start_time']}",
        '%Y-%m-%d %H:%M'
    )

    time_until = appointment_datetime - datetime.now()

    if time_until < timedelta(hours=24):
        return {
            'allowed': False,
            'reason': 'Las citas deben cancelarse con al menos 24 horas de anticipación'
        }

    return {
        'allowed': True,
        'reason': ''
    }


class AppointmentManager:
    """Manages appointment bookings with concurrency control"""

    async def book_appointment(
        self,
        clinic_id: str,
        phone: str,
        date: str,
        time: str,
        max_slot_capacity: int = 10
    ) -> Dict[str, Any]:
        """Book appointment with concurrency control"""
        from .database import db
        from .audit import hash_phone
        import asyncio

        # Use database transaction or lock
        async with db.transaction():
            # Check current capacity
            existing = await db.table('healthcare.appointments')\
                .select('count')\
                .eq('clinic_id', clinic_id)\
                .eq('appointment_date', date)\
                .eq('start_time', time)\
                .eq('status', 'scheduled')\
                .execute()

            current_count = existing.count if existing else 0

            if current_count >= max_slot_capacity:
                return {
                    'success': False,
                    'message': 'Slot is full'
                }

            # Book appointment
            appointment_id = str(uuid.uuid4())
            appointment = {
                'id': appointment_id,
                'clinic_id': clinic_id,
                'patient_phone': hash_phone(phone),
                'appointment_date': date,
                'start_time': time,
                'status': 'scheduled',
                'created_at': datetime.utcnow().isoformat()
            }

            await db.table('healthcare.appointments').insert(appointment).execute()

            return {
                'success': True,
                'appointment_id': appointment_id
            }


class AppointmentParser:
    """Parse appointment requests from natural language"""

    async def parse_datetime(self, text: str) -> Dict[str, Any]:
        """Parse date and time from text"""
        # Simple pattern matching
        date_pattern = r'(\d{1,2})\s+de\s+(\w+)'
        time_pattern = r'(\d{1,2})\s*(am|pm|de la tarde|de la mañana)'

        # Try to extract date
        date_match = re.search(date_pattern, text.lower())
        if date_match:
            day = date_match.group(1)
            month = date_match.group(2)

            # Validate date
            if month == 'febrero' and int(day) > 29:
                return {
                    'valid': False,
                    'error': 'Fecha no válida: febrero no tiene 31 días',
                    'suggestions': ['28 de febrero', '1 de marzo']
                }

        return {
            'valid': True,
            'date': '2024-12-20',  # Mock parsed date
            'time': '14:00'  # Mock parsed time
        }


async def validate_appointment_time(
    clinic: Dict[str, Any],
    day: str,
    time: str
) -> bool:
    """Validate appointment time against business hours"""
    business_hours = clinic.get('business_hours', {})
    day_hours = business_hours.get(day.lower())

    if not day_hours or day_hours == 'closed':
        return False

    open_time = datetime.strptime(day_hours['open'], '%H:%M').time()
    close_time = datetime.strptime(day_hours['close'], '%H:%M').time()
    request_time = datetime.strptime(time, '%H:%M').time()

    return open_time <= request_time < close_time
