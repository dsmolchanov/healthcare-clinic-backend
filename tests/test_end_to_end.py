"""
End-to-end integration tests for dental clinic system
Tests complete user journeys from WhatsApp to appointment booking
"""

import asyncio
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock, AsyncMock, call
from .test_base import AsyncTestCase, MockSupabaseClient, MockTwilioClient


class TestCompleteBookingFlow(AsyncTestCase):
    """Test complete appointment booking flow from start to finish"""

    @patch('clinics.backend.app.database.supabase')
    @patch('twilio.rest.Client')
    async def test_new_patient_booking_journey(self, mock_twilio, mock_supabase):
        """Test complete journey for a new patient booking an appointment"""
        from clinics.backend.app.main import DentalClinicBot

        # Initialize mocks
        mock_db = MockSupabaseClient()
        mock_supabase.return_value = mock_db
        mock_twilio_client = MockTwilioClient()
        mock_twilio.return_value = mock_twilio_client

        # Create test clinic
        clinic = self.create_test_clinic()
        mock_db.data['healthcare.clinics'] = [clinic]

        bot = DentalClinicBot(clinic_id=self.test_clinic_id)

        # Step 1: First contact - should trigger privacy notice
        first_message = {
            'From': f'whatsapp:{self.test_phone}',
            'Body': 'Hola, necesito información'
        }

        response = await bot.handle_message(first_message)

        # Verify privacy notice sent
        self.assertEqual(len(mock_twilio_client.messages.sent_messages), 1)
        privacy_message = mock_twilio_client.messages.sent_messages[0]
        self.assertIn('Aviso de Privacidad', privacy_message['body'])

        # Step 2: Accept privacy notice
        consent_message = {
            'From': f'whatsapp:{self.test_phone}',
            'Body': 'ACEPTO'
        }

        response = await bot.handle_message(consent_message)

        # Verify consent recorded
        consent_records = mock_db.data.get('core.consent_records', [])
        self.assertEqual(len(consent_records), 1)
        self.assertTrue(consent_records[0]['consent_given'])

        # Step 3: Request appointment
        appointment_request = {
            'From': f'whatsapp:{self.test_phone}',
            'Body': 'Quiero agendar una cita para limpieza dental'
        }

        response = await bot.handle_message(appointment_request)

        # Should ask for preferred date/time
        self.assertIn('fecha', response['message'].lower())

        # Step 4: Provide date and time
        datetime_message = {
            'From': f'whatsapp:{self.test_phone}',
            'Body': 'El viernes a las 3 de la tarde'
        }

        response = await bot.handle_message(datetime_message)

        # Step 5: Confirm appointment
        appointments = mock_db.data.get('healthcare.appointments', [])
        self.assertEqual(len(appointments), 1)

        appointment = appointments[0]
        self.assertEqual(appointment['service'], 'cleaning')
        self.assertEqual(appointment['status'], 'scheduled')

        # Verify confirmation sent
        confirmation_messages = [m for m in mock_twilio_client.messages.sent_messages
                                if 'confirmada' in m['body'].lower()]
        self.assertGreater(len(confirmation_messages), 0)

        # Step 6: Verify reminder scheduled
        self.assertIn('reminder_scheduled', response)
        self.assertTrue(response['reminder_scheduled'])

    @patch('clinics.backend.app.database.supabase')
    @patch('twilio.rest.Client')
    async def test_returning_patient_flow(self, mock_twilio, mock_supabase):
        """Test flow for returning patient with existing consent"""
        from clinics.backend.app.main import DentalClinicBot

        # Setup mocks
        mock_db = MockSupabaseClient()
        mock_supabase.return_value = mock_db
        mock_twilio_client = MockTwilioClient()
        mock_twilio.return_value = mock_twilio_client

        # Pre-existing consent
        mock_db.data['core.consent_records'] = [{
            'user_identifier': self.test_phone,
            'organization_id': self.test_clinic_id,
            'consent_given': True,
            'timestamp': datetime.utcnow().isoformat()
        }]

        bot = DentalClinicBot(clinic_id=self.test_clinic_id)

        # Direct appointment request (no privacy notice needed)
        message = {
            'From': f'whatsapp:{self.test_phone}',
            'Body': 'Necesito cita para extracción de muela'
        }

        response = await bot.handle_message(message)

        # Should not send privacy notice
        privacy_messages = [m for m in mock_twilio_client.messages.sent_messages
                           if 'Aviso de Privacidad' in m.get('body', '')]
        self.assertEqual(len(privacy_messages), 0)

        # Should proceed directly to booking
        self.assertIn('fecha', response['message'].lower())


class TestMultiStepConversations(AsyncTestCase):
    """Test multi-step conversation flows"""

    @patch('redis.Redis')
    async def test_appointment_modification_flow(self, mock_redis):
        """Test modifying an existing appointment through conversation"""
        from clinics.backend.app.conversations import ConversationHandler

        mock_redis_instance = MagicMock()
        mock_redis.return_value = mock_redis_instance

        handler = ConversationHandler(redis_client=mock_redis_instance)

        # Existing appointment
        existing_appointment = self.create_test_appointment()

        # Step 1: Request modification
        response = await handler.process_message(
            phone=self.test_phone,
            message='Necesito cambiar mi cita',
            context={'appointments': [existing_appointment]}
        )

        self.assertEqual(response['intent'], 'appointment_modification')
        self.assertIn('cita actual', response['message'].lower())

        # Step 2: Specify new time
        response = await handler.process_message(
            phone=self.test_phone,
            message='Mejor el lunes a las 10 am',
            context={'modifying_appointment': existing_appointment['id']}
        )

        self.assertEqual(response['intent'], 'confirm_modification')
        self.assertIn('lunes', response['message'].lower())
        self.assertIn('10:00', response['message'])

        # Step 3: Confirm change
        response = await handler.process_message(
            phone=self.test_phone,
            message='Sí, confirmo',
            context={
                'modifying_appointment': existing_appointment['id'],
                'new_datetime': {'date': '2024-12-23', 'time': '10:00'}
            }
        )

        self.assertTrue(response['modification_complete'])
        self.assertIn('actualizada', response['message'].lower())

    async def test_service_inquiry_flow(self):
        """Test multi-step service inquiry conversation"""
        from clinics.backend.app.conversations import ServiceInquiryFlow

        flow = ServiceInquiryFlow()

        # Step 1: General inquiry
        response = await flow.handle_message(
            'Qué servicios ofrecen?',
            context={}
        )

        self.assertIn('servicios', response['message'].lower())
        self.assertIn('limpieza', response['message'].lower())
        self.assertIn('ortodoncia', response['message'].lower())

        # Step 2: Specific service question
        response = await flow.handle_message(
            'Cuánto cuesta una limpieza?',
            context={'previous_intent': 'service_list'}
        )

        self.assertIn('limpieza', response['message'].lower())
        self.assertIn('$', response['message'])

        # Step 3: Insurance question
        response = await flow.handle_message(
            'Aceptan seguro GNP?',
            context={'service': 'cleaning', 'price_inquired': True}
        )

        self.assertIn('GNP', response['message'])
        self.assertIn('aceptamos', response['message'].lower())


class TestErrorRecovery(AsyncTestCase):
    """Test error recovery and edge cases"""

    async def test_invalid_date_recovery(self):
        """Test recovery from invalid date input"""
        from clinics.backend.app.appointments import AppointmentParser

        parser = AppointmentParser()

        # Invalid date
        result = await parser.parse_datetime('31 de febrero a las 2pm')

        self.assertFalse(result['valid'])
        self.assertIn('fecha no válida', result['error'].lower())

        # Suggest correction
        self.assertIn('suggestions', result)
        self.assertGreater(len(result['suggestions']), 0)

    async def test_network_failure_recovery(self):
        """Test recovery from network failures"""
        from clinics.backend.app.resilience import with_retry

        call_count = 0

        @with_retry(max_attempts=3, delay=0.1)
        async def flaky_operation():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ConnectionError("Network error")
            return "Success"

        result = await flaky_operation()

        self.assertEqual(result, "Success")
        self.assertEqual(call_count, 3)

    @patch('clinics.backend.app.database.supabase')
    async def test_database_failure_handling(self, mock_supabase):
        """Test graceful handling of database failures"""
        from clinics.backend.app.appointments import book_appointment_with_fallback

        # Simulate database error
        mock_supabase.table.side_effect = Exception("Database connection failed")

        result = await book_appointment_with_fallback(
            clinic_id=self.test_clinic_id,
            phone=self.test_phone,
            date='2024-12-20',
            time='14:00'
        )

        self.assertFalse(result['success'])
        self.assertIn('sistema no disponible', result['message'].lower())
        self.assertIn('intente más tarde', result['message'].lower())


class TestClinicConfiguration(AsyncTestCase):
    """Test clinic-specific configuration and customization"""

    async def test_business_hours_enforcement(self):
        """Test that appointments respect clinic business hours"""
        from clinics.backend.app.appointments import validate_appointment_time

        clinic = self.create_test_clinic(
            business_hours={
                'monday': {'open': '09:00', 'close': '18:00'},
                'saturday': {'open': '09:00', 'close': '14:00'},
                'sunday': 'closed'
            }
        )

        # Valid time (Monday 10:00)
        is_valid = await validate_appointment_time(
            clinic, 'monday', '10:00'
        )
        self.assertTrue(is_valid)

        # After hours (Monday 19:00)
        is_valid = await validate_appointment_time(
            clinic, 'monday', '19:00'
        )
        self.assertFalse(is_valid)

        # Closed day (Sunday)
        is_valid = await validate_appointment_time(
            clinic, 'sunday', '10:00'
        )
        self.assertFalse(is_valid)

    async def test_service_availability(self):
        """Test clinic-specific service availability"""
        from clinics.backend.app.services import check_service_availability

        clinic = self.create_test_clinic(
            services=['cleaning', 'filling', 'extraction']
        )

        # Available service
        available = await check_service_availability(
            clinic, 'cleaning'
        )
        self.assertTrue(available)

        # Unavailable service
        available = await check_service_availability(
            clinic, 'orthodontics'
        )
        self.assertFalse(available)

    async def test_multi_language_support(self):
        """Test multi-language message handling"""
        from clinics.backend.app.i18n import get_message

        # Spanish (default)
        message = await get_message('appointment_confirmed', lang='es')
        self.assertIn('confirmada', message.lower())

        # English
        message = await get_message('appointment_confirmed', lang='en')
        self.assertIn('confirmed', message.lower())


class TestAuditAndCompliance(AsyncTestCase):
    """Test audit trail and compliance features"""

    @patch('clinics.backend.app.database.supabase')
    async def test_complete_audit_trail(self, mock_supabase):
        """Test that all actions are properly audited"""
        from clinics.backend.app.audit import AuditLogger

        mock_supabase.table.return_value.insert.return_value.execute = AsyncMock()

        logger = AuditLogger()

        # Track various events
        events = [
            ('consent_given', {'phone': self.test_phone, 'accepted': True}),
            ('appointment_booked', {'appointment_id': '123', 'service': 'cleaning'}),
            ('appointment_modified', {'appointment_id': '123', 'changes': {'time': '15:00'}}),
            ('appointment_cancelled', {'appointment_id': '123', 'reason': 'patient request'}),
            ('data_accessed', {'phone': self.test_phone, 'accessor': 'system'}),
        ]

        for event_type, event_data in events:
            await logger.log_event(
                clinic_id=self.test_clinic_id,
                event_type=event_type,
                event_data=event_data
            )

        # Verify all events were logged
        self.assertEqual(mock_supabase.table.call_count, len(events))

        # Verify sensitive data is hashed
        for call in mock_supabase.table.return_value.insert.call_args_list:
            insert_data = call[0][0]
            if 'phone' in str(insert_data):
                self.assertNotIn(self.test_phone, str(insert_data))

    @patch('clinics.backend.app.database.supabase')
    async def test_data_retention_compliance(self, mock_supabase):
        """Test data retention policy enforcement"""
        from clinics.backend.app.compliance import enforce_retention_policy

        # Create old and new records
        old_date = datetime.now() - timedelta(days=365 * 6)  # 6 years old
        new_date = datetime.now() - timedelta(days=365)  # 1 year old

        records = [
            {'id': '1', 'created_at': old_date.isoformat()},
            {'id': '2', 'created_at': new_date.isoformat()}
        ]

        mock_supabase.table.return_value.select.return_value.execute = AsyncMock(
            return_value={'data': records}
        )
        mock_supabase.table.return_value.delete.return_value.eq.return_value.execute = AsyncMock()

        # Enforce retention policy
        result = await enforce_retention_policy(
            market='mexico',
            retention_years=5
        )

        # Should delete old record
        self.assertEqual(result['deleted'], 1)
        self.assertEqual(result['retained'], 1)
