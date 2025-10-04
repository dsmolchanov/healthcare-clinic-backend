"""
WhatsApp integration tests
Tests Twilio WhatsApp API, message handling, media processing
"""

import json
from datetime import datetime
from unittest.mock import patch, MagicMock, AsyncMock, Mock
from twilio.rest import Client
from .test_base import AsyncTestCase, MockTwilioClient


class TestWhatsAppWebhook(AsyncTestCase):
    """Test WhatsApp webhook handling"""

    @patch('clinics.backend.app.whatsapp.process_whatsapp_message')
    async def test_incoming_message_processing(self, mock_process):
        """Test processing incoming WhatsApp messages"""
        from clinics.backend.app.whatsapp import handle_whatsapp_webhook

        mock_process.return_value = AsyncMock()

        # Create webhook payload
        payload = self.create_whatsapp_webhook_payload(
            body='Hola, quiero agendar una cita para limpieza dental'
        )

        result = await handle_whatsapp_webhook(
            organization_id=self.test_clinic_id,
            payload=payload
        )

        # Verify message was processed
        mock_process.assert_called_once()
        call_args = mock_process.call_args[0]

        self.assertEqual(call_args[0], self.test_clinic_id)
        self.assertEqual(call_args[1], self.test_phone)
        self.assertIn('limpieza dental', call_args[2])

    @patch('clinics.backend.app.whatsapp.send_whatsapp_message')
    async def test_auto_response_sent(self, mock_send):
        """Test that auto-responses are sent for common queries"""
        from clinics.backend.app.whatsapp import handle_common_queries

        mock_send.return_value = AsyncMock()

        # Test hours query
        result = await handle_common_queries(
            phone=self.test_phone,
            message='¿Cuáles son sus horarios?',
            clinic_id=self.test_clinic_id
        )

        self.assertTrue(result['handled'])
        mock_send.assert_called()

        response = mock_send.call_args[0][1]
        self.assertIn('horarios', response.lower())
        self.assertIn('lunes', response.lower())

    async def test_message_language_detection(self):
        """Test language detection for incoming messages"""
        from clinics.backend.app.whatsapp import detect_language

        # Spanish message
        lang = await detect_language('Hola, necesito una cita')
        self.assertEqual(lang, 'es')

        # English message
        lang = await detect_language('Hello, I need an appointment')
        self.assertEqual(lang, 'en')

        # Default to Spanish for ambiguous
        lang = await detect_language('OK')
        self.assertEqual(lang, 'es')


class TestWhatsAppMessaging(AsyncTestCase):
    """Test sending WhatsApp messages via Twilio"""

    @patch('twilio.rest.Client')
    async def test_send_text_message(self, mock_twilio_client):
        """Test sending text messages via WhatsApp"""
        from clinics.backend.app.whatsapp import send_whatsapp_message

        mock_client = MockTwilioClient()
        mock_twilio_client.return_value = mock_client

        # Send message
        result = await send_whatsapp_message(
            to_phone=self.test_phone,
            message='Su cita está confirmada para mañana a las 14:00'
        )

        # Verify message was sent
        self.assertEqual(len(mock_client.messages.sent_messages), 1)
        sent = mock_client.messages.sent_messages[0]

        self.assertEqual(sent['to'], f'whatsapp:{self.test_phone}')
        self.assertIn('confirmada', sent['body'])

    @patch('twilio.rest.Client')
    async def test_send_template_message(self, mock_twilio_client):
        """Test sending template messages for appointments"""
        from clinics.backend.app.whatsapp import send_appointment_template

        mock_client = MockTwilioClient()
        mock_twilio_client.return_value = mock_client

        appointment = self.create_test_appointment()

        # Send template
        result = await send_appointment_template(
            to_phone=self.test_phone,
            appointment=appointment,
            template_type='confirmation'
        )

        # Verify template parameters
        sent = mock_client.messages.sent_messages[0]
        self.assertIn(appointment['appointment_date'], sent['body'])
        self.assertIn(appointment['start_time'], sent['body'])

    @patch('twilio.rest.Client')
    async def test_send_media_message(self, mock_twilio_client):
        """Test sending media messages (images, PDFs)"""
        from clinics.backend.app.whatsapp import send_whatsapp_media

        mock_client = MockTwilioClient()
        mock_twilio_client.return_value = mock_client

        # Send image
        result = await send_whatsapp_media(
            to_phone=self.test_phone,
            media_url='https://example.com/clinic-map.jpg',
            caption='Ubicación de la clínica'
        )

        sent = mock_client.messages.sent_messages[0]
        self.assertIn('media_url', sent)
        self.assertEqual(sent['body'], 'Ubicación de la clínica')

    @patch('twilio.rest.Client')
    async def test_message_status_callback(self, mock_twilio_client):
        """Test handling message status callbacks"""
        from clinics.backend.app.whatsapp import handle_status_callback

        # Status callback payload
        status_payload = {
            'MessageSid': 'SM123456',
            'MessageStatus': 'delivered',
            'To': f'whatsapp:{self.test_phone}'
        }

        result = await handle_status_callback(status_payload)

        self.assertEqual(result['status'], 'delivered')
        self.assertEqual(result['message_sid'], 'SM123456')


class TestWhatsAppSessionManagement(AsyncTestCase):
    """Test WhatsApp conversation session management"""

    @patch('redis.Redis')
    async def test_conversation_context_maintained(self, mock_redis):
        """Test that conversation context is maintained across messages"""
        from clinics.backend.app.whatsapp import WhatsAppSessionManager

        mock_redis_instance = MagicMock()
        mock_redis.return_value = mock_redis_instance

        manager = WhatsAppSessionManager(redis_client=mock_redis_instance)

        # First message
        session = await manager.get_or_create_session(
            phone=self.test_phone,
            clinic_id=self.test_clinic_id
        )

        await manager.add_message(
            session_id=session['id'],
            message='Quiero una cita',
            sender='user'
        )

        # Second message
        await manager.add_message(
            session_id=session['id'],
            message='Para limpieza dental',
            sender='user'
        )

        # Get context
        context = await manager.get_conversation_context(session['id'])

        self.assertEqual(len(context['messages']), 2)
        self.assertIn('limpieza dental', str(context['messages']))

    @patch('redis.Redis')
    async def test_session_timeout_handling(self, mock_redis):
        """Test handling of session timeouts (24 hour window)"""
        from clinics.backend.app.whatsapp import check_session_validity

        # Active session (recent activity)
        active_session = {
            'id': 'active-session',
            'last_activity': datetime.utcnow().isoformat()
        }

        is_valid = await check_session_validity(active_session)
        self.assertTrue(is_valid)

        # Expired session (25 hours old)
        from datetime import timedelta
        old_time = datetime.utcnow() - timedelta(hours=25)
        expired_session = {
            'id': 'expired-session',
            'last_activity': old_time.isoformat()
        }

        is_valid = await check_session_validity(expired_session)
        self.assertFalse(is_valid)


class TestWhatsAppIntentRecognition(AsyncTestCase):
    """Test intent recognition from WhatsApp messages"""

    async def test_appointment_intent_recognition(self):
        """Test recognizing appointment booking intent"""
        from clinics.backend.app.whatsapp import recognize_intent

        # Appointment requests
        intents = [
            ('Quiero agendar una cita', 'appointment_booking'),
            ('Necesito una consulta', 'appointment_booking'),
            ('¿Tienen disponibilidad para mañana?', 'appointment_availability'),
            ('Cancelar mi cita', 'appointment_cancellation'),
        ]

        for message, expected_intent in intents:
            intent = await recognize_intent(message)
            self.assertEqual(
                intent['type'], expected_intent,
                f"Failed to recognize intent for: {message}"
            )

    async def test_information_intent_recognition(self):
        """Test recognizing information request intents"""
        from clinics.backend.app.whatsapp import recognize_intent

        # Information requests
        intents = [
            ('¿Cuáles son sus horarios?', 'hours_inquiry'),
            ('¿Dónde están ubicados?', 'location_inquiry'),
            ('¿Cuánto cuesta una limpieza?', 'price_inquiry'),
            ('¿Aceptan mi seguro?', 'insurance_inquiry'),
        ]

        for message, expected_intent in intents:
            intent = await recognize_intent(message)
            self.assertEqual(intent['type'], expected_intent)

    async def test_extract_appointment_details(self):
        """Test extracting appointment details from messages"""
        from clinics.backend.app.whatsapp import extract_appointment_details

        message = 'Quiero una cita para limpieza dental el viernes a las 3 de la tarde'

        details = await extract_appointment_details(message)

        self.assertEqual(details['service'], 'limpieza dental')
        self.assertEqual(details['day'], 'viernes')
        self.assertEqual(details['time'], '15:00')


class TestWhatsAppErrorHandling(AsyncTestCase):
    """Test error handling in WhatsApp integration"""

    @patch('twilio.rest.Client')
    async def test_twilio_api_error_handling(self, mock_twilio_client):
        """Test handling Twilio API errors"""
        from clinics.backend.app.whatsapp import send_whatsapp_message
        from twilio.base.exceptions import TwilioRestException

        # Mock API error
        mock_client = Mock()
        mock_client.messages.create.side_effect = TwilioRestException(
            status=400,
            uri='/Messages',
            msg='Invalid phone number'
        )
        mock_twilio_client.return_value = mock_client

        # Should handle error gracefully
        result = await send_whatsapp_message(
            to_phone='invalid_number',
            message='Test'
        )

        self.assertFalse(result['success'])
        self.assertIn('error', result)
        self.assertIn('Invalid phone number', result['error'])

    @patch('clinics.backend.app.whatsapp.send_whatsapp_message')
    async def test_retry_on_rate_limit(self, mock_send):
        """Test retry logic when rate limited"""
        from clinics.backend.app.whatsapp import send_with_retry
        from twilio.base.exceptions import TwilioRestException

        # Mock rate limit error then success
        mock_send.side_effect = [
            TwilioRestException(status=429, uri='/', msg='Rate limit'),
            {'success': True, 'message_sid': 'SM123'}
        ]

        result = await send_with_retry(
            to_phone=self.test_phone,
            message='Test message'
        )

        # Should retry and succeed
        self.assertTrue(result['success'])
        self.assertEqual(mock_send.call_count, 2)

    async def test_fallback_response_on_error(self):
        """Test fallback responses when main processing fails"""
        from clinics.backend.app.whatsapp import get_fallback_response

        # Get fallback for different error types
        fallback = await get_fallback_response('processing_error')
        self.assertIn('momento', fallback.lower())
        self.assertIn('intente', fallback.lower())

        fallback = await get_fallback_response('unavailable')
        self.assertIn('disponible', fallback.lower())


class TestWhatsAppMediaHandling(AsyncTestCase):
    """Test handling media files in WhatsApp"""

    @patch('clinics.backend.app.storage.download_media')
    async def test_receive_image_message(self, mock_download):
        """Test receiving and processing image messages"""
        from clinics.backend.app.whatsapp import handle_media_message

        mock_download.return_value = b'image_data'

        # WhatsApp media webhook payload
        payload = self.create_whatsapp_webhook_payload(
            num_media='1'
        )
        payload['MediaUrl0'] = 'https://api.twilio.com/media/MM123'
        payload['MediaContentType0'] = 'image/jpeg'

        result = await handle_media_message(payload)

        self.assertTrue(result['processed'])
        self.assertEqual(result['media_type'], 'image/jpeg')
        mock_download.assert_called_with('https://api.twilio.com/media/MM123')

    @patch('clinics.backend.app.storage.process_document')
    async def test_receive_document_message(self, mock_process):
        """Test receiving and processing document messages (PDF)"""
        from clinics.backend.app.whatsapp import handle_media_message

        mock_process.return_value = {'extracted_text': 'Document content'}

        payload = self.create_whatsapp_webhook_payload(
            num_media='1'
        )
        payload['MediaUrl0'] = 'https://api.twilio.com/media/MM456'
        payload['MediaContentType0'] = 'application/pdf'

        result = await handle_media_message(payload)

        self.assertTrue(result['processed'])
        self.assertEqual(result['media_type'], 'application/pdf')
        mock_process.assert_called()

    async def test_media_size_validation(self):
        """Test validation of media file sizes"""
        from clinics.backend.app.whatsapp import validate_media_size

        # Valid size (under 16MB for WhatsApp)
        is_valid = await validate_media_size(size_bytes=15 * 1024 * 1024)
        self.assertTrue(is_valid)

        # Invalid size (over 16MB)
        is_valid = await validate_media_size(size_bytes=17 * 1024 * 1024)
        self.assertFalse(is_valid)
