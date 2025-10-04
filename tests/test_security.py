"""
Security tests for dental clinic system
Tests webhook verification, rate limiting, session management
"""

import hashlib
import hmac
import base64
import time
from unittest.mock import patch, MagicMock, AsyncMock
import redis
from twilio.request_validator import RequestValidator

from .test_base import AsyncTestCase, MockTwilioClient


class TestWebhookSecurity(AsyncTestCase):
    """Test webhook signature verification"""

    def test_twilio_signature_verification_enabled(self):
        """Test that webhook signature verification is properly enabled"""
        from clinics.backend.app.security import verify_twilio_signature

        # Create test webhook data
        url = 'https://example.com/webhooks/twilio/whatsapp/test-clinic'
        params = {
            'From': 'whatsapp:+1234567890',
            'Body': 'Test message',
            'MessageSid': 'SMtest123'
        }

        # Generate valid signature
        auth_token = self.test_env['TWILIO_AUTH_TOKEN']
        validator = RequestValidator(auth_token)

        # Test with valid signature
        valid_signature = self._generate_twilio_signature(url, params, auth_token)
        self.assertTrue(
            verify_twilio_signature(url, params, valid_signature, auth_token),
            "Valid signature should be accepted"
        )

        # Test with invalid signature
        invalid_signature = 'invalid_signature_xyz'
        self.assertFalse(
            verify_twilio_signature(url, params, invalid_signature, auth_token),
            "Invalid signature should be rejected"
        )

    def test_webhook_rejects_unsigned_requests(self):
        """Test that webhooks reject requests without valid signatures"""
        with patch('clinics.backend.app.main.verify_twilio_signature') as mock_verify:
            mock_verify.return_value = False

            # Simulate webhook request without valid signature
            response = self._make_webhook_request(
                '/webhooks/twilio/whatsapp/test-clinic',
                signature='invalid'
            )

            self.assertEqual(response.status_code, 403)
            self.assertIn('Invalid signature', response.json().get('detail', ''))

    def _generate_twilio_signature(self, url: str, params: dict, auth_token: str) -> str:
        """Generate a valid Twilio signature for testing"""
        data = url
        sorted_params = sorted(params.items())
        for key, value in sorted_params:
            data += key + str(value)

        signature = base64.b64encode(
            hmac.new(
                auth_token.encode('utf-8'),
                data.encode('utf-8'),
                hashlib.sha1
            ).digest()
        ).decode('utf-8')

        return signature

    def _make_webhook_request(self, path: str, signature: str = None, **params):
        """Helper to make webhook requests in tests"""
        from fastapi.testclient import TestClient
        from clinics.backend.app.main import app

        client = TestClient(app)
        headers = {}
        if signature:
            headers['X-Twilio-Signature'] = signature

        return client.post(path, data=params, headers=headers)


class TestRateLimiting(AsyncTestCase):
    """Test rate limiting implementation"""

    @patch('slowapi.Limiter')
    def test_rate_limiter_configured(self, mock_limiter_class):
        """Test that rate limiter is properly configured"""
        from clinics.backend.app.middleware import configure_rate_limiting

        mock_app = MagicMock()
        configure_rate_limiting(mock_app)

        # Verify limiter was created
        mock_limiter_class.assert_called_once()

        # Verify limiter was attached to app
        self.assertIsNotNone(mock_app.state.limiter)

    def test_rate_limit_enforced_on_webhooks(self):
        """Test that rate limits are enforced on webhook endpoints"""
        from fastapi.testclient import TestClient
        from clinics.backend.app.main import app

        client = TestClient(app)

        # Make requests up to the limit (30/minute)
        responses = []
        for i in range(35):
            response = client.post(
                '/webhooks/twilio/whatsapp/test-clinic',
                data=self.create_whatsapp_webhook_payload()
            )
            responses.append(response)

        # First 30 should succeed (or return 403 for signature)
        # Request 31+ should return 429 (rate limited)
        rate_limited = [r for r in responses[30:] if r.status_code == 429]

        self.assertGreater(
            len(rate_limited), 0,
            "Rate limiting should kick in after 30 requests"
        )

    @patch('redis.Redis')
    def test_distributed_rate_limiting(self, mock_redis):
        """Test rate limiting works across multiple instances"""
        from clinics.backend.app.middleware import DistributedRateLimiter

        limiter = DistributedRateLimiter(redis_client=mock_redis)

        # Simulate requests from same IP
        ip_address = '192.168.1.100'

        # Should allow first 30 requests
        for i in range(30):
            allowed = self.run_async(limiter.check_rate_limit(ip_address))
            self.assertTrue(allowed, f"Request {i+1} should be allowed")

        # Should block request 31
        allowed = self.run_async(limiter.check_rate_limit(ip_address))
        self.assertFalse(allowed, "Request 31 should be blocked")


class TestSessionManagement(AsyncTestCase):
    """Test Redis session management"""

    @patch('redis.Redis')
    def test_redis_session_creation(self, mock_redis):
        """Test that sessions are created in Redis, not memory"""
        from clinics.backend.app.session_manager import RedisSessionManager

        mock_redis_instance = MagicMock()
        mock_redis.return_value = mock_redis_instance
        mock_redis_instance.get.return_value = None  # No existing session

        manager = RedisSessionManager()
        session = self.run_async(
            manager.get_or_create_session(self.test_phone, self.test_clinic_id)
        )

        # Verify session was created
        self.assertIsNotNone(session['id'])
        self.assertEqual(session['clinic_id'], self.test_clinic_id)
        self.assertEqual(session['phone'], self.test_phone)

        # Verify Redis was called
        mock_redis_instance.setex.assert_called_once()
        call_args = mock_redis_instance.setex.call_args

        # Check TTL is set (24 hours)
        self.assertEqual(call_args[0][1], 86400)

    @patch('redis.Redis')
    def test_session_retrieval(self, mock_redis):
        """Test retrieving existing sessions from Redis"""
        from clinics.backend.app.session_manager import RedisSessionManager
        import json

        # Mock existing session
        existing_session = {
            'id': 'existing-session-id',
            'clinic_id': self.test_clinic_id,
            'phone': self.test_phone,
            'messages': ['Previous message']
        }

        mock_redis_instance = MagicMock()
        mock_redis.return_value = mock_redis_instance
        mock_redis_instance.get.return_value = json.dumps(existing_session)

        manager = RedisSessionManager()
        session = self.run_async(
            manager.get_or_create_session(self.test_phone, self.test_clinic_id)
        )

        # Should return existing session
        self.assertEqual(session['id'], 'existing-session-id')
        self.assertEqual(session['messages'], ['Previous message'])

        # Should not create new session
        mock_redis_instance.setex.assert_not_called()

    @patch('redis.Redis')
    def test_session_expiry(self, mock_redis):
        """Test that sessions expire after 24 hours"""
        from clinics.backend.app.session_manager import RedisSessionManager

        mock_redis_instance = MagicMock()
        mock_redis.return_value = mock_redis_instance

        manager = RedisSessionManager()

        # Create session
        session = self.run_async(
            manager.get_or_create_session(self.test_phone, self.test_clinic_id)
        )

        # Verify TTL was set correctly
        call_args = mock_redis_instance.setex.call_args
        ttl = call_args[0][1]

        self.assertEqual(ttl, 86400, "Session TTL should be 24 hours (86400 seconds)")

    def test_no_memory_sessions(self):
        """Ensure no in-memory session storage is used"""
        # This test verifies that the old in-memory pattern is not present
        from clinics.backend.app import session_manager

        # Should not have any global dictionaries for sessions
        module_attrs = dir(session_manager)

        memory_patterns = ['sessions', 'cache', 'memory', 'dict']
        for pattern in memory_patterns:
            for attr in module_attrs:
                if pattern in attr.lower() and isinstance(getattr(session_manager, attr), dict):
                    self.fail(f"Found in-memory storage: {attr}")


class TestAuditLogging(AsyncTestCase):
    """Test audit logging for compliance"""

    @patch('clinics.backend.app.database.supabase')
    async def test_conversation_audit_logging(self, mock_supabase):
        """Test that all conversations are logged for compliance"""
        from clinics.backend.app.audit import log_conversation

        mock_supabase.table.return_value.insert.return_value.execute = AsyncMock()

        # Log a conversation
        await log_conversation(
            clinic_id=self.test_clinic_id,
            patient_phone=self.test_phone,
            message_type='incoming',
            content='Quiero agendar una cita'
        )

        # Verify audit log was created
        mock_supabase.table.assert_called_with('core.audit_logs')
        insert_call = mock_supabase.table.return_value.insert.call_args[0][0]

        self.assertEqual(insert_call['organization_id'], self.test_clinic_id)
        self.assertEqual(insert_call['event_type'], 'whatsapp_message')
        self.assertEqual(insert_call['event_category'], 'incoming')

        # Verify phone is hashed for privacy
        self.assertNotIn(self.test_phone, str(insert_call))
        self.assertIn('phone', insert_call['event_data'])

    def test_phone_hashing(self):
        """Test that phone numbers are properly hashed in logs"""
        from clinics.backend.app.audit import hash_phone

        # Hash should be consistent
        hash1 = hash_phone(self.test_phone)
        hash2 = hash_phone(self.test_phone)
        self.assertEqual(hash1, hash2)

        # Hash should not reveal original phone
        self.assertNotEqual(hash1, self.test_phone)
        self.assertNotIn(self.test_phone, hash1)

        # Different phones should have different hashes
        other_phone = '+529876543210'
        hash3 = hash_phone(other_phone)
        self.assertNotEqual(hash1, hash3)

    @patch('clinics.backend.app.database.supabase')
    async def test_audit_log_retention(self, mock_supabase):
        """Test audit logs are retained according to policy"""
        from clinics.backend.app.audit import get_audit_retention_policy

        policy = await get_audit_retention_policy('mexico')

        # Mexican law requires 5 years retention
        self.assertEqual(policy['retention_years'], 5)
        self.assertEqual(policy['deletion_allowed'], True)
        self.assertEqual(policy['encryption_required'], False)


class TestEncryption(AsyncTestCase):
    """Test data encryption based on market"""

    def test_mexico_encryption_level(self):
        """Test that Mexico uses appropriate encryption"""
        from clinics.backend.app.security import get_encryption_config

        config = get_encryption_config(market='mexico')

        self.assertEqual(config['algorithm'], 'AES')
        self.assertEqual(config['key_size'], 128)
        self.assertFalse(config['phi_protection'])

    def test_future_us_encryption_ready(self):
        """Test that US encryption can be enabled"""
        from clinics.backend.app.security import get_encryption_config

        config = get_encryption_config(market='us')

        self.assertEqual(config['algorithm'], 'AES')
        self.assertEqual(config['key_size'], 256)
        self.assertTrue(config['phi_protection'])
        self.assertTrue(config['hipaa_compliant'])

    def test_sensitive_data_encrypted(self):
        """Test that sensitive data is encrypted in storage"""
        from clinics.backend.app.security import encrypt_sensitive_data

        sensitive_data = {
            'patient_name': 'Juan Pérez',
            'phone': self.test_phone,
            'medical_notes': 'Patient has diabetes'
        }

        encrypted = encrypt_sensitive_data(sensitive_data, market='mexico')

        # Data should be encrypted
        self.assertNotEqual(encrypted['patient_name'], sensitive_data['patient_name'])
        self.assertNotEqual(encrypted['phone'], sensitive_data['phone'])

        # Should be reversible
        from clinics.backend.app.security import decrypt_sensitive_data
        decrypted = decrypt_sensitive_data(encrypted, market='mexico')

        self.assertEqual(decrypted['patient_name'], sensitive_data['patient_name'])
        self.assertEqual(decrypted['phone'], sensitive_data['phone'])
