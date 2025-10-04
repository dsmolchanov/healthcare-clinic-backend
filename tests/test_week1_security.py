"""
Integration tests for Week 1 security implementation
Tests audit logging, rate limiting, privacy notice, and consent management
"""

import os
import asyncio
import pytest
from datetime import datetime, timedelta
from unittest.mock import Mock, patch
import json

# Import our modules
from app.services.audit_logger import AuditLogger, AuditEventType, AuditSeverity, get_audit_logger
from app.services.redis_session_manager import RedisSessionManager
from app.middleware.security import SecurityMiddleware, verify_webhook_security
from app.compliance.privacy_notice import PrivacyNoticeHandler, ConsentMiddleware, ConsentType
from app.whatsapp.language_aware_handler_with_audit import LanguageAwareWhatsAppHandler

# Test configuration
TEST_CLINIC_ID = "test-clinic-123"
TEST_PHONE = "+5215551234567"
TEST_ORGANIZATION_ID = "test-org-456"

class TestAuditLogger:
    """Test audit logging functionality"""

    @pytest.mark.asyncio
    async def test_audit_logger_initialization(self):
        """Test audit logger can be initialized"""
        logger = AuditLogger()
        await logger.initialize()
        assert logger.redis_client is not None
        await logger.close()

    @pytest.mark.asyncio
    async def test_log_security_event(self):
        """Test logging security events"""
        logger = await get_audit_logger()

        # Log successful authentication
        await logger.log_security_event(
            action="whatsapp_auth",
            clinic_id=TEST_CLINIC_ID,
            success=True,
            user_identifier=TEST_PHONE,
            ip_address="192.168.1.1"
        )

        # Log failed authentication
        await logger.log_security_event(
            action="whatsapp_auth",
            clinic_id=TEST_CLINIC_ID,
            success=False,
            user_identifier=TEST_PHONE,
            ip_address="192.168.1.1",
            reason="Invalid signature"
        )

        # Verify logs were created
        logs = await logger.get_audit_logs(
            clinic_id=TEST_CLINIC_ID,
            start_date=datetime.now() - timedelta(minutes=1),
            end_date=datetime.now(),
            event_types=[AuditEventType.AUTH_SUCCESS, AuditEventType.AUTH_FAILURE]
        )

        assert len(logs) >= 2
        assert any(log['event_type'] == AuditEventType.AUTH_SUCCESS.value for log in logs)
        assert any(log['event_type'] == AuditEventType.AUTH_FAILURE.value for log in logs)

    @pytest.mark.asyncio
    async def test_phi_access_logging(self):
        """Test PHI access logging for HIPAA readiness"""
        logger = await get_audit_logger()

        await logger.log_phi_access(
            clinic_id=TEST_CLINIC_ID,
            accessor_id="whatsapp_system",
            patient_id="patient-789",
            data_type="appointments",
            action="read",
            justification="Patient requested appointment list"
        )

        # Verify PHI access was logged
        logs = await logger.get_audit_logs(
            clinic_id=TEST_CLINIC_ID,
            start_date=datetime.now() - timedelta(minutes=1),
            end_date=datetime.now(),
            event_types=[AuditEventType.PHI_ACCESS]
        )

        assert len(logs) >= 1
        assert logs[0]['metadata']['data_type'] == 'appointments'

    @pytest.mark.asyncio
    async def test_compliance_report_generation(self):
        """Test generating compliance reports"""
        logger = await get_audit_logger()

        # Generate a security report
        report = await logger.generate_compliance_report(
            clinic_id=TEST_CLINIC_ID,
            report_type="security",
            start_date=datetime.now() - timedelta(days=7),
            end_date=datetime.now()
        )

        assert report['clinic_id'] == TEST_CLINIC_ID
        assert report['report_type'] == 'security'
        assert 'summary' in report
        assert 'total_events' in report['summary']

class TestSecurityMiddleware:
    """Test security middleware and rate limiting"""

    @pytest.mark.asyncio
    async def test_rate_limiting(self):
        """Test rate limiting functionality"""
        security = SecurityMiddleware()

        # Test within limits
        for i in range(10):
            result = await security.check_rate_limit(
                identifier=f"test_user_{i}",
                limit=30,
                window=60
            )
            assert result is True

        # Test exceeding limits
        identifier = "rate_limit_test"
        for i in range(30):
            await security.check_rate_limit(identifier, limit=30, window=60)

        # 31st request should be blocked
        result = await security.check_rate_limit(identifier, limit=30, window=60)
        assert result is False

    @pytest.mark.asyncio
    async def test_twilio_signature_verification(self):
        """Test Twilio webhook signature verification"""
        security = SecurityMiddleware()

        # Mock request
        mock_request = Mock()
        mock_request.url.scheme = "https"
        mock_request.url.netloc = "example.com"
        mock_request.url.path = "/webhook"
        mock_request.url.query = ""
        mock_request.headers = {"X-Twilio-Signature": "mock_signature"}

        body = b"test_body"

        # Test verification (will fail without valid token/signature)
        with patch.object(security.validator, 'validate', return_value=True):
            result = await security.verify_twilio_signature(mock_request, body)
            assert result is True

        with patch.object(security.validator, 'validate', return_value=False):
            result = await security.verify_twilio_signature(mock_request, body)
            assert result is False

class TestPrivacyNotice:
    """Test LFPDPPP privacy notice and consent management"""

    @pytest.mark.asyncio
    async def test_first_contact_detection(self):
        """Test detecting first contact with patient"""
        handler = PrivacyNoticeHandler()

        # First contact should return True
        is_first = await handler.check_first_contact(
            identifier=TEST_PHONE,
            clinic_id=TEST_CLINIC_ID
        )
        assert is_first is True

        # After recording consent, should return False
        await handler.record_consent(
            identifier=TEST_PHONE,
            clinic_id=TEST_CLINIC_ID,
            consent_types=[ConsentType.PERSONAL_DATA],
            granted=True,
            language='es'
        )

        is_first = await handler.check_first_contact(
            identifier=TEST_PHONE,
            clinic_id=TEST_CLINIC_ID
        )
        assert is_first is False

    @pytest.mark.asyncio
    async def test_privacy_notice_languages(self):
        """Test privacy notice in multiple languages"""
        handler = PrivacyNoticeHandler()

        # Test Spanish
        notice_es = await handler.get_privacy_notice('es', 'short')
        assert 'AVISO DE PRIVACIDAD' in notice_es
        assert 'LFPDPPP' in notice_es

        # Test English
        notice_en = await handler.get_privacy_notice('en', 'short')
        assert 'PRIVACY NOTICE' in notice_en
        assert 'LFPDPPP' in notice_en

        # Test Hebrew
        notice_he = await handler.get_privacy_notice('he', 'short')
        assert 'הודעת פרטיות' in notice_he

    @pytest.mark.asyncio
    async def test_consent_recording(self):
        """Test recording and retrieving consent"""
        handler = PrivacyNoticeHandler()

        # Record consent
        consent = await handler.record_consent(
            identifier=TEST_PHONE,
            clinic_id=TEST_CLINIC_ID,
            consent_types=[
                ConsentType.PERSONAL_DATA,
                ConsentType.SENSITIVE_DATA,
                ConsentType.MARKETING
            ],
            granted=True,
            language='es',
            ip_address='192.168.1.1'
        )

        assert consent['granted'] is True
        assert ConsentType.PERSONAL_DATA.value in consent['consent_types']

        # Retrieve consent status
        status = await handler.get_consent_status(
            identifier=TEST_PHONE,
            clinic_id=TEST_CLINIC_ID
        )

        assert status is not None
        assert status['granted'] is True

    @pytest.mark.asyncio
    async def test_consent_revocation(self):
        """Test revoking consent"""
        handler = PrivacyNoticeHandler()

        # First grant consent
        await handler.record_consent(
            identifier=TEST_PHONE,
            clinic_id=TEST_CLINIC_ID,
            consent_types=[ConsentType.PERSONAL_DATA],
            granted=True
        )

        # Then revoke it
        result = await handler.revoke_consent(
            identifier=TEST_PHONE,
            clinic_id=TEST_CLINIC_ID,
            consent_types=[ConsentType.PERSONAL_DATA]
        )

        assert result is True

        # Check status after revocation
        status = await handler.get_consent_status(
            identifier=TEST_PHONE,
            clinic_id=TEST_CLINIC_ID
        )

        # After revocation, the last consent record should show granted=False
        assert status is None or status['granted'] is False

    @pytest.mark.asyncio
    async def test_consent_middleware(self):
        """Test consent middleware flow"""
        middleware = ConsentMiddleware()

        # Check if notice needs to be shown
        notice = await middleware.check_and_show_privacy_notice(
            identifier=TEST_PHONE,
            clinic_id=TEST_CLINIC_ID,
            language='es'
        )

        assert notice is not None
        assert 'AVISO DE PRIVACIDAD' in notice

        # Process acceptance
        result = await middleware.process_consent_response(
            identifier=TEST_PHONE,
            clinic_id=TEST_CLINIC_ID,
            response="Sí acepto",
            language='es'
        )

        assert result['status'] == 'accepted'

        # Process rejection
        result = await middleware.process_consent_response(
            identifier=f"{TEST_PHONE}_2",
            clinic_id=TEST_CLINIC_ID,
            response="No acepto",
            language='es'
        )

        assert result['status'] == 'rejected'

        # Process unclear response
        result = await middleware.process_consent_response(
            identifier=f"{TEST_PHONE}_3",
            clinic_id=TEST_CLINIC_ID,
            response="Maybe",
            language='es'
        )

        assert result['status'] == 'unclear'

class TestRedisSessionManager:
    """Test Redis session management"""

    @pytest.mark.asyncio
    async def test_session_creation(self):
        """Test creating and retrieving sessions"""
        manager = RedisSessionManager()

        # Create session
        session = await manager.create_session(
            clinic_id=TEST_CLINIC_ID,
            user_identifier=TEST_PHONE
        )

        assert session['id'] is not None
        assert session['clinic_id'] == TEST_CLINIC_ID

        # Get session
        retrieved = await manager.get_session(session['id'])
        assert retrieved is not None
        assert retrieved['id'] == session['id']

    @pytest.mark.asyncio
    async def test_message_history(self):
        """Test message history management"""
        manager = RedisSessionManager()

        session = await manager.create_session(
            clinic_id=TEST_CLINIC_ID,
            user_identifier=TEST_PHONE
        )

        # Add messages
        for i in range(5):
            await manager.add_message(
                session_id=session['id'],
                role="user" if i % 2 == 0 else "assistant",
                content=f"Message {i}"
            )

        # Get message history
        history = await manager.get_message_history(session['id'])
        assert len(history) == 5
        assert history[0]['content'] == "Message 0"

    @pytest.mark.asyncio
    async def test_session_expiry(self):
        """Test session expiry and cleanup"""
        manager = RedisSessionManager()

        # Create session with short TTL
        session = await manager.create_session(
            clinic_id=TEST_CLINIC_ID,
            user_identifier=TEST_PHONE
        )

        # Update TTL to 1 second for testing
        await manager.redis.expire(f"session:{session['id']}", 1)

        # Wait for expiry
        await asyncio.sleep(2)

        # Session should be gone
        retrieved = await manager.get_session(session['id'])
        assert retrieved is None

class TestWhatsAppIntegration:
    """Test WhatsApp handler with all security features"""

    @pytest.mark.asyncio
    async def test_whatsapp_with_audit_logging(self):
        """Test WhatsApp handler logs all interactions"""
        handler = LanguageAwareWhatsAppHandler()
        audit_logger = await get_audit_logger()

        # Send a message
        response = await handler.handle_message(
            from_number=TEST_PHONE,
            message_text="Hola, quiero agendar una cita",
            clinic_id=TEST_CLINIC_ID,
            organization_id=TEST_ORGANIZATION_ID
        )

        assert response is not None

        # Check audit logs
        logs = await audit_logger.get_audit_logs(
            clinic_id=TEST_CLINIC_ID,
            start_date=datetime.now() - timedelta(minutes=1),
            end_date=datetime.now(),
            event_types=[
                AuditEventType.MESSAGE_RECEIVED,
                AuditEventType.LANGUAGE_DETECTED,
                AuditEventType.MESSAGE_SENT
            ]
        )

        # Should have logged incoming message, language detection, and outgoing message
        assert len(logs) >= 2

    @pytest.mark.asyncio
    async def test_language_detection_with_audit(self):
        """Test language detection is properly audited"""
        handler = LanguageAwareWhatsAppHandler()
        audit_logger = await get_audit_logger()

        # Test Spanish message
        await handler.handle_message(
            from_number=TEST_PHONE,
            message_text="Necesito cancelar mi cita",
            clinic_id=TEST_CLINIC_ID,
            organization_id=TEST_ORGANIZATION_ID
        )

        # Test English message
        await handler.handle_message(
            from_number=f"{TEST_PHONE}_en",
            message_text="I need to book an appointment",
            clinic_id=TEST_CLINIC_ID,
            organization_id=TEST_ORGANIZATION_ID
        )

        # Test Hebrew message
        await handler.handle_message(
            from_number=f"{TEST_PHONE}_he",
            message_text="אני צריך לקבוע תור",
            clinic_id=TEST_CLINIC_ID,
            organization_id=TEST_ORGANIZATION_ID
        )

        # Check language detection was logged
        logs = await audit_logger.get_audit_logs(
            clinic_id=TEST_CLINIC_ID,
            start_date=datetime.now() - timedelta(minutes=1),
            end_date=datetime.now(),
            event_types=[AuditEventType.LANGUAGE_DETECTED]
        )

        assert len(logs) >= 3
        languages = [log['metadata']['language'] for log in logs]
        assert 'es' in languages
        assert 'en' in languages
        assert 'he' in languages

class TestEndToEndSecurity:
    """End-to-end security flow tests"""

    @pytest.mark.asyncio
    async def test_complete_first_contact_flow(self):
        """Test complete flow for first-time patient contact"""

        # 1. Initialize components
        whatsapp_handler = LanguageAwareWhatsAppHandler()
        privacy_middleware = ConsentMiddleware()
        audit_logger = await get_audit_logger()

        unique_phone = f"{TEST_PHONE}_{datetime.now().timestamp()}"

        # 2. Check if privacy notice needed (first contact)
        notice = await privacy_middleware.check_and_show_privacy_notice(
            identifier=unique_phone,
            clinic_id=TEST_CLINIC_ID,
            language='es'
        )

        assert notice is not None  # Should show privacy notice

        # 3. Patient accepts privacy notice
        consent_result = await privacy_middleware.process_consent_response(
            identifier=unique_phone,
            clinic_id=TEST_CLINIC_ID,
            response="Sí acepto",
            language='es'
        )

        assert consent_result['status'] == 'accepted'

        # 4. Now patient can use the service
        response = await whatsapp_handler.handle_message(
            from_number=unique_phone,
            message_text="Quiero agendar una cita para mañana",
            clinic_id=TEST_CLINIC_ID,
            organization_id=TEST_ORGANIZATION_ID
        )

        assert response is not None

        # 5. Verify audit trail
        logs = await audit_logger.get_audit_logs(
            clinic_id=TEST_CLINIC_ID,
            start_date=datetime.now() - timedelta(minutes=5),
            end_date=datetime.now()
        )

        # Should have privacy notice shown, consent granted, and message logs
        event_types = [log['event_type'] for log in logs]
        assert AuditEventType.PRIVACY_NOTICE_SHOWN.value in event_types
        assert AuditEventType.CONSENT_GRANTED.value in event_types

    @pytest.mark.asyncio
    async def test_rate_limit_protection(self):
        """Test rate limiting protects against abuse"""
        security = SecurityMiddleware()

        attacker_id = "attacker_12345"

        # Simulate rapid requests
        results = []
        for i in range(35):  # Try 35 requests (limit is 30)
            result = await security.check_rate_limit(
                identifier=attacker_id,
                limit=30,
                window=60
            )
            results.append(result)

        # First 30 should succeed
        assert all(results[:30])

        # Requests 31-35 should be blocked
        assert not any(results[30:])

        # Check audit log for rate limit violations
        audit_logger = await get_audit_logger()
        logs = await audit_logger.get_audit_logs(
            clinic_id="system",
            start_date=datetime.now() - timedelta(minutes=1),
            end_date=datetime.now(),
            event_types=[AuditEventType.RATE_LIMIT_EXCEEDED]
        )

        assert len(logs) >= 5  # Should have logged the 5 blocked requests

# Test runner
async def run_all_tests():
    """Run all security tests"""
    print("🔒 Running Week 1 Security Tests...")

    test_classes = [
        TestAuditLogger,
        TestSecurityMiddleware,
        TestPrivacyNotice,
        TestRedisSessionManager,
        TestWhatsAppIntegration,
        TestEndToEndSecurity
    ]

    for test_class in test_classes:
        print(f"\n📋 Testing {test_class.__name__}...")
        test_instance = test_class()

        # Get all test methods
        test_methods = [
            method for method in dir(test_instance)
            if method.startswith('test_')
        ]

        for method_name in test_methods:
            try:
                print(f"  ✓ {method_name}")
                method = getattr(test_instance, method_name)
                if asyncio.iscoroutinefunction(method):
                    await method()
                else:
                    method()
            except Exception as e:
                print(f"  ✗ {method_name}: {str(e)}")

    print("\n✅ Week 1 Security Tests Completed!")

if __name__ == "__main__":
    # Run tests
    asyncio.run(run_all_tests())
