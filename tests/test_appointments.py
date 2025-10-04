"""
Appointment booking and management tests
Tests scheduling, availability, confirmations, reminders
"""

from datetime import datetime, timedelta, time
from unittest.mock import patch, MagicMock, AsyncMock
from .test_base import AsyncTestCase, MockSupabaseClient


class TestAppointmentBooking(AsyncTestCase):
    """Test basic appointment booking functionality"""

    @patch('clinics.backend.app.database.supabase')
    async def test_successful_appointment_booking(self, mock_supabase):
        """Test successful appointment booking flow"""
        from clinics.backend.app.appointments import SimpleAppointmentBooking

        # Mock available slot
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute = AsyncMock(
            return_value={'data': [], 'count': 0}  # No existing appointments
        )

        # Mock clinic config
        clinic_config = {'max_appointments_per_slot': 2}
        mock_supabase.table.return_value.select.return_value.eq.return_value.single.return_value.execute = AsyncMock(
            return_value={'data': clinic_config}
        )

        # Mock appointment creation
        new_appointment = self.create_test_appointment()
        mock_supabase.table.return_value.insert.return_value.execute = AsyncMock(
            return_value={'data': new_appointment}
        )

        booking = SimpleAppointmentBooking()
        result = await booking.book_appointment(
            clinic_id=self.test_clinic_id,
            patient_phone=self.test_phone,
            requested_date='2024-12-20',
            requested_time='14:00'
        )

        self.assertTrue(result['success'])
        self.assertIn('appointment_id', result)
        self.assertIn('confirmada', result['message'])

    @patch('clinics.backend.app.database.supabase')
    async def test_appointment_slot_unavailable(self, mock_supabase):
        """Test handling of unavailable appointment slots"""
        from clinics.backend.app.appointments import SimpleAppointmentBooking

        # Mock slot at capacity
        existing_appointments = [
            self.create_test_appointment(),
            self.create_test_appointment()
        ]
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute = AsyncMock(
            return_value={'data': existing_appointments, 'count': 2}
        )

        # Mock clinic config
        clinic_config = {'max_appointments_per_slot': 2}
        mock_supabase.table.return_value.select.return_value.eq.return_value.single.return_value.execute = AsyncMock(
            return_value={'data': clinic_config}
        )

        booking = SimpleAppointmentBooking()
        result = await booking.book_appointment(
            clinic_id=self.test_clinic_id,
            patient_phone=self.test_phone,
            requested_date='2024-12-20',
            requested_time='14:00'
        )

        self.assertFalse(result['success'])
        self.assertIn('alternatives', result)
        self.assertIn('no disponible', result['message'])

    async def test_appointment_validation(self):
        """Test appointment request validation"""
        from clinics.backend.app.appointments import validate_appointment_request

        # Test valid request
        valid_request = {
            'date': '2024-12-20',
            'time': '14:00',
            'service': 'cleaning'
        }

        is_valid, errors = await validate_appointment_request(valid_request)
        self.assertTrue(is_valid)
        self.assertEqual(len(errors), 0)

        # Test invalid date format
        invalid_date = {
            'date': '20-12-2024',  # Wrong format
            'time': '14:00',
            'service': 'cleaning'
        }

        is_valid, errors = await validate_appointment_request(invalid_date)
        self.assertFalse(is_valid)
        self.assertIn('date', str(errors))

        # Test past date
        past_date = {
            'date': '2020-01-01',
            'time': '14:00',
            'service': 'cleaning'
        }

        is_valid, errors = await validate_appointment_request(past_date)
        self.assertFalse(is_valid)
        self.assertIn('past', str(errors).lower())


class TestAvailabilityChecking(AsyncTestCase):
    """Test appointment availability checking"""

    @patch('clinics.backend.app.database.supabase')
    async def test_business_hours_checking(self, mock_supabase):
        """Test that appointments respect business hours"""
        from clinics.backend.app.appointments import check_business_hours

        clinic = self.create_test_clinic()

        # Test during business hours (Monday 10:00)
        available = await check_business_hours(
            clinic,
            day='monday',
            time='10:00'
        )
        self.assertTrue(available)

        # Test outside business hours (Monday 20:00)
        available = await check_business_hours(
            clinic,
            day='monday',
            time='20:00'
        )
        self.assertFalse(available)

        # Test on closed day (Sunday)
        available = await check_business_hours(
            clinic,
            day='sunday',
            time='10:00'
        )
        self.assertFalse(available)

    @patch('clinics.backend.app.database.supabase')
    async def test_slot_capacity_checking(self, mock_supabase):
        """Test appointment slot capacity limits"""
        from clinics.backend.app.appointments import check_slot_availability

        # Test with available capacity
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute = AsyncMock(
            return_value={'data': [self.create_test_appointment()], 'count': 1}
        )

        available = await check_slot_availability(
            clinic_id=self.test_clinic_id,
            date='2024-12-20',
            time='14:00',
            max_capacity=2
        )
        self.assertTrue(available)

        # Test at full capacity
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute = AsyncMock(
            return_value={'data': [
                self.create_test_appointment(),
                self.create_test_appointment()
            ], 'count': 2}
        )

        available = await check_slot_availability(
            clinic_id=self.test_clinic_id,
            date='2024-12-20',
            time='14:00',
            max_capacity=2
        )
        self.assertFalse(available)

    @patch('clinics.backend.app.database.supabase')
    async def test_alternative_slots_suggestion(self, mock_supabase):
        """Test suggesting alternative appointment slots"""
        from clinics.backend.app.appointments import suggest_alternatives

        # Mock some available and unavailable slots
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute = AsyncMock(
            side_effect=[
                {'data': [], 'count': 0},  # 14:30 available
                {'data': [self.create_test_appointment()], 'count': 1},  # 15:00 has space
                {'data': [self.create_test_appointment(), self.create_test_appointment()], 'count': 2},  # 15:30 full
                {'data': [], 'count': 0},  # 16:00 available
            ]
        )

        alternatives = await suggest_alternatives(
            clinic_id=self.test_clinic_id,
            requested_date='2024-12-20',
            requested_time='14:00',
            num_alternatives=3
        )

        self.assertEqual(len(alternatives), 3)
        self.assertIn('14:30', [a['time'] for a in alternatives])
        self.assertIn('16:00', [a['time'] for a in alternatives])


class TestAppointmentConfirmations(AsyncTestCase):
    """Test appointment confirmation system"""

    @patch('clinics.backend.app.messaging.send_whatsapp_message')
    async def test_confirmation_message_sent(self, mock_send):
        """Test that confirmation messages are sent after booking"""
        from clinics.backend.app.appointments import send_confirmation

        mock_send.return_value = AsyncMock()

        appointment = self.create_test_appointment()
        await send_confirmation(self.test_phone, appointment)

        # Verify message was sent
        mock_send.assert_called_once()
        sent_message = mock_send.call_args[0][1]

        # Check confirmation content
        self.assertIn('confirmada', sent_message.lower())
        self.assertIn(appointment['appointment_date'], sent_message)
        self.assertIn(appointment['start_time'], sent_message)

    @patch('clinics.backend.app.messaging.send_whatsapp_message')
    async def test_confirmation_includes_clinic_info(self, mock_send):
        """Test that confirmations include clinic information"""
        from clinics.backend.app.appointments import send_detailed_confirmation

        mock_send.return_value = AsyncMock()

        appointment = self.create_test_appointment()
        clinic = self.create_test_clinic()

        await send_detailed_confirmation(
            phone=self.test_phone,
            appointment=appointment,
            clinic=clinic
        )

        sent_message = mock_send.call_args[0][1]

        # Should include clinic details
        self.assertIn(clinic['name'], sent_message)
        self.assertIn(clinic['website'], sent_message)

    @patch('clinics.backend.app.database.supabase')
    async def test_confirmation_status_updated(self, mock_supabase):
        """Test that appointment status is updated after confirmation"""
        from clinics.backend.app.appointments import confirm_appointment

        mock_supabase.table.return_value.update.return_value.eq.return_value.execute = AsyncMock()

        appointment_id = 'test-appointment-id'
        await confirm_appointment(appointment_id)

        # Verify status update
        mock_supabase.table.assert_called_with('healthcare.appointments')
        update_call = mock_supabase.table.return_value.update.call_args[0][0]

        self.assertEqual(update_call['status'], 'confirmed')
        self.assertIn('confirmed_at', update_call)


class TestAppointmentReminders(AsyncTestCase):
    """Test appointment reminder system"""

    @patch('clinics.backend.app.scheduler.schedule_task')
    async def test_reminder_scheduled_after_booking(self, mock_schedule):
        """Test that reminders are scheduled when appointments are booked"""
        from clinics.backend.app.appointments import schedule_reminder

        appointment = self.create_test_appointment()
        appointment['appointment_date'] = '2024-12-20'
        appointment['start_time'] = '14:00'

        await schedule_reminder(appointment)

        # Verify reminder was scheduled
        mock_schedule.assert_called()

        # Check reminder is scheduled for 24 hours before
        scheduled_time = mock_schedule.call_args[0][0]
        appointment_datetime = datetime.strptime(
            f"{appointment['appointment_date']} {appointment['start_time']}",
            '%Y-%m-%d %H:%M'
        )
        expected_reminder = appointment_datetime - timedelta(hours=24)

        self.assertEqual(scheduled_time.date(), expected_reminder.date())

    @patch('clinics.backend.app.messaging.send_whatsapp_message')
    async def test_reminder_message_content(self, mock_send):
        """Test reminder message content"""
        from clinics.backend.app.appointments import send_reminder

        mock_send.return_value = AsyncMock()

        appointment = self.create_test_appointment()
        await send_reminder(appointment)

        sent_message = mock_send.call_args[0][1]

        # Check reminder content
        self.assertIn('recordatorio', sent_message.lower())
        self.assertIn('mañana', sent_message.lower())
        self.assertIn(appointment['start_time'], sent_message)
        self.assertIn(appointment['service'], sent_message)

    @patch('clinics.backend.app.database.supabase')
    @patch('clinics.backend.app.messaging.send_whatsapp_message')
    async def test_multiple_reminders(self, mock_send, mock_supabase):
        """Test multiple reminder schedule (24h and 2h before)"""
        from clinics.backend.app.appointments import schedule_multiple_reminders

        appointment = self.create_test_appointment()

        reminders = await schedule_multiple_reminders(appointment)

        # Should schedule 2 reminders
        self.assertEqual(len(reminders), 2)

        # Check timing
        self.assertEqual(reminders[0]['hours_before'], 24)
        self.assertEqual(reminders[1]['hours_before'], 2)


class TestAppointmentCancellation(AsyncTestCase):
    """Test appointment cancellation functionality"""

    @patch('clinics.backend.app.database.supabase')
    async def test_appointment_cancellation(self, mock_supabase):
        """Test cancelling an appointment"""
        from clinics.backend.app.appointments import cancel_appointment

        mock_supabase.table.return_value.update.return_value.eq.return_value.execute = AsyncMock()

        result = await cancel_appointment(
            appointment_id='test-appointment-id',
            reason='Patient request'
        )

        self.assertTrue(result['success'])

        # Verify status update
        update_call = mock_supabase.table.return_value.update.call_args[0][0]
        self.assertEqual(update_call['status'], 'cancelled')
        self.assertEqual(update_call['cancellation_reason'], 'Patient request')

    @patch('clinics.backend.app.messaging.send_whatsapp_message')
    async def test_cancellation_confirmation_sent(self, mock_send):
        """Test that cancellation confirmations are sent"""
        from clinics.backend.app.appointments import send_cancellation_confirmation

        mock_send.return_value = AsyncMock()

        appointment = self.create_test_appointment()
        await send_cancellation_confirmation(
            phone=self.test_phone,
            appointment=appointment
        )

        sent_message = mock_send.call_args[0][1]

        self.assertIn('cancelada', sent_message.lower())
        self.assertIn(appointment['appointment_date'], sent_message)

    async def test_cancellation_time_limit(self):
        """Test cancellation time limit (e.g., 24 hours before)"""
        from clinics.backend.app.appointments import can_cancel_appointment

        # Appointment tomorrow - should not be cancellable
        tomorrow = datetime.now() + timedelta(days=1)
        appointment = self.create_test_appointment(
            appointment_date=tomorrow.strftime('%Y-%m-%d'),
            start_time='14:00'
        )

        can_cancel = await can_cancel_appointment(appointment)
        self.assertFalse(can_cancel['allowed'])
        self.assertIn('24 horas', can_cancel['reason'])

        # Appointment in 3 days - should be cancellable
        future = datetime.now() + timedelta(days=3)
        appointment = self.create_test_appointment(
            appointment_date=future.strftime('%Y-%m-%d'),
            start_time='14:00'
        )

        can_cancel = await can_cancel_appointment(appointment)
        self.assertTrue(can_cancel['allowed'])
