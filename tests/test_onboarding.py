"""
Comprehensive onboarding flow tests for dental clinic system
Tests both full HIPAA-compliant and quick Mexican market onboarding
"""

import asyncio
import uuid
import json
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock, AsyncMock
from typing import Dict, Any

from .test_base import AsyncTestCase, MockSupabaseClient


class TestQuickOnboarding(AsyncTestCase):
    """Test quick onboarding flow for Mexican market"""

    @patch('clinics.backend.app.api.quick_onboarding.create_client')
    async def test_quick_registration(self, mock_create_client):
        """Test minimal friction registration"""
        from clinics.backend.app.api.quick_onboarding import QuickOnboardingService, QuickRegistration

        # Setup mock Supabase
        mock_supabase = MockSupabaseClient()
        mock_create_client.return_value = mock_supabase

        service = QuickOnboardingService()

        # Test registration with minimal data
        registration = QuickRegistration(
            name="Clínica Dental Sonrisa",
            phone="+521234567890",
            email="contacto@sonrisa.mx"
        )

        result = await service.quick_register(registration)

        # Verify response
        self.assertTrue(result['success'])
        self.assertIn('clinic_id', result)
        self.assertIn('organization_id', result)
        self.assertIn('agent_id', result)
        self.assertEqual(result['next_step'], 'whatsapp_setup')

        # Verify organization was created
        orgs = mock_supabase.data.get('core.organizations', [])
        self.assertEqual(len(orgs), 1)
        org = orgs[0]
        self.assertEqual(org['name'], "Clínica Dental Sonrisa")
        self.assertEqual(org['subscription_tier'], 'starter')
        self.assertTrue(org['settings']['quick_setup'])

        # Verify clinic was created with defaults
        clinics = mock_supabase.data.get('healthcare.clinics', [])
        self.assertEqual(len(clinics), 1)
        clinic = clinics[0]
        self.assertEqual(clinic['name'], "Clínica Dental Sonrisa")
        self.assertEqual(clinic['phone'], "+521234567890")
        self.assertIn('business_hours', clinic)

        # Verify default business hours (Mexican standard)
        self.assertIn('monday', clinic['business_hours'])
        self.assertIn('saturday', clinic['business_hours'])

    @patch('clinics.backend.app.api.quick_onboarding.create_client')
    async def test_shared_whatsapp_setup(self, mock_create_client):
        """Test WhatsApp setup using shared Twilio account"""
        from clinics.backend.app.api.quick_onboarding import QuickOnboardingService, QuickWhatsApp

        mock_supabase = MockSupabaseClient()
        mock_create_client.return_value = mock_supabase

        service = QuickOnboardingService()
        clinic_id = str(uuid.uuid4())

        # Test with shared account (no Twilio credentials needed)
        whatsapp_config = QuickWhatsApp(
            phone_number="+521234567890",
            use_shared_account=True
        )

        result = await service.setup_whatsapp(clinic_id, whatsapp_config)

        self.assertTrue(result['success'])
        self.assertEqual(result['webhook_url'], f"/webhooks/whatsapp/{clinic_id}")
        self.assertEqual(result['next_step'], 'test_integration')

        # Should use shared Twilio credentials
        self.assertIn('using_shared_account', result)
        self.assertTrue(result['using_shared_account'])

    async def test_automatic_spanish_configuration(self):
        """Test that Spanish language is automatically configured for Mexican clinics"""
        from clinics.backend.app.api.quick_onboarding import QuickOnboardingService, QuickRegistration

        service = QuickOnboardingService()

        # Registration with Mexican phone number
        registration = QuickRegistration(
            name="Clínica Dental México",
            phone="+52555123456",  # Mexico City area code
            email="info@clinica.mx"
        )

        # Detect Mexican market from phone
        market = service._detect_market(registration.phone)
        self.assertEqual(market, 'mexico')

        # Get language configuration
        lang_config = service._get_language_config(market)
        self.assertEqual(lang_config['primary'], 'es')
        self.assertIn('en', lang_config['secondary'])

    async def test_mexican_timezone_detection(self):
        """Test automatic timezone detection for Mexican cities"""
        from clinics.backend.app.api.quick_onboarding import detect_timezone_from_phone

        test_cases = [
            ("+525551234567", "America/Mexico_City"),  # Mexico City
            ("+526641234567", "America/Tijuana"),       # Tijuana
            ("+523331234567", "America/Mexico_City"),   # Guadalajara
            ("+528181234567", "America/Monterrey"),     # Monterrey
        ]

        for phone, expected_tz in test_cases:
            detected_tz = detect_timezone_from_phone(phone)
            self.assertEqual(
                detected_tz, expected_tz,
                f"Failed to detect timezone for {phone}"
            )


class TestFullOnboarding(AsyncTestCase):
    """Test full HIPAA-compliant onboarding flow"""

    @patch('clinics.backend.app.api.onboarding.create_client')
    async def test_complete_registration(self, mock_create_client):
        """Test full registration with all required fields"""
        from clinics.backend.app.api.onboarding import OnboardingService, ClinicRegistration

        mock_supabase = MockSupabaseClient()
        mock_create_client.return_value = mock_supabase

        service = OnboardingService()

        # Full registration data
        registration = ClinicRegistration(
            name="Professional Dental Care",
            email="admin@dentalcare.com",
            phone="(555) 123-4567",
            address="123 Main Street",
            city="Los Angeles",
            state="CA",
            zip_code="90001",
            timezone="America/Los_Angeles",
            specialties=["general", "orthodontics", "pediatric"],
            business_hours={
                "monday": "9:00 AM - 6:00 PM",
                "tuesday": "9:00 AM - 6:00 PM",
                "wednesday": "9:00 AM - 6:00 PM",
                "thursday": "9:00 AM - 6:00 PM",
                "friday": "9:00 AM - 5:00 PM"
            },
            admin_first_name="John",
            admin_last_name="Smith",
            admin_email="john@dentalcare.com",
            accept_terms=True,
            accept_baa=True  # Business Associate Agreement for HIPAA
        )

        result = await service.register_clinic(registration)

        # Verify complete registration
        self.assertTrue(result['success'])
        self.assertIn('organization_id', result)
        self.assertIn('clinic_id', result)
        self.assertIn('agent_id', result)

        # Verify HIPAA compliance flags
        orgs = mock_supabase.data.get('core.organizations', [])
        org = orgs[0]
        self.assertTrue(org['settings']['baa_signed'])
        self.assertTrue(org['settings']['terms_accepted'])

        clinics = mock_supabase.data.get('healthcare.clinics', [])
        clinic = clinics[0]
        self.assertTrue(clinic['hipaa_compliant'])

    @patch('clinics.backend.app.api.onboarding.create_client')
    @patch('clinics.backend.app.security.compliance_vault.ComplianceVault')
    async def test_whatsapp_credential_encryption(self, mock_vault, mock_create_client):
        """Test that WhatsApp credentials are encrypted in vault"""
        from clinics.backend.app.api.onboarding import OnboardingService, WhatsAppConfiguration

        mock_supabase = MockSupabaseClient()
        mock_create_client.return_value = mock_supabase

        # Mock vault for secure storage
        mock_vault_instance = MagicMock()
        mock_vault.return_value = mock_vault_instance
        mock_vault_instance.store_calendar_credentials = AsyncMock(
            return_value="vault_ref_123"
        )

        service = OnboardingService()
        service.vault = mock_vault_instance

        clinic_id = str(uuid.uuid4())

        # Add clinic to mock database
        mock_supabase.data['healthcare.clinics'] = [{
            'id': clinic_id,
            'organization_id': 'test-org-id',
            'name': 'Test Clinic'
        }]

        # Configure WhatsApp with Twilio
        config = WhatsAppConfiguration(
            provider="twilio",
            phone_number="+14155551234",
            twilio_account_sid="AC123456789",
            twilio_auth_token="secret_auth_token"
        )

        result = await service.configure_whatsapp(clinic_id, config)

        # Verify credentials were encrypted
        mock_vault_instance.store_calendar_credentials.assert_called_once()
        call_args = mock_vault_instance.store_calendar_credentials.call_args

        # Check that sensitive data was passed to vault
        self.assertEqual(call_args[1]['provider'], 'whatsapp_twilio')
        self.assertIn('auth_token', call_args[1]['credentials'])

        # Verify webhook configuration
        self.assertTrue(result['success'])
        self.assertIn('webhook_url', result)
        self.assertIn('webhook_verify_token', result)

    @patch('clinics.backend.app.calendar.oauth_manager.CalendarOAuthManager')
    async def test_calendar_oauth_flow(self, mock_oauth_manager):
        """Test calendar OAuth integration flow"""
        from clinics.backend.app.api.onboarding import OnboardingService, CalendarIntegrationRequest

        mock_oauth = MagicMock()
        mock_oauth_manager.return_value = mock_oauth

        # Mock OAuth URL generation
        mock_oauth.initiate_google_oauth = AsyncMock(
            return_value="https://accounts.google.com/oauth/authorize?..."
        )

        service = OnboardingService()
        service.oauth_manager = mock_oauth

        clinic_id = str(uuid.uuid4())

        # Request Google Calendar integration
        request = CalendarIntegrationRequest(
            doctor_id="doctor-123",
            provider="google"
        )

        result = await service.initiate_calendar_oauth(clinic_id, request)

        self.assertTrue(result['success'])
        self.assertIn('auth_url', result)
        self.assertEqual(result['provider'], 'google')

        # Verify OAuth was initiated
        mock_oauth.initiate_google_oauth.assert_called_with(
            clinic_id,
            "doctor-123"
        )

    @patch('clinics.backend.app.api.onboarding.create_client')
    async def test_doctor_profile_creation(self, mock_create_client):
        """Test adding doctor profiles to clinic"""
        from clinics.backend.app.api.onboarding import OnboardingService, DoctorProfile

        mock_supabase = MockSupabaseClient()
        mock_create_client.return_value = mock_supabase

        service = OnboardingService()
        clinic_id = str(uuid.uuid4())

        # Create doctor profile
        doctor = DoctorProfile(
            first_name="María",
            last_name="González",
            specialization="Orthodontics",
            license_number="DDS123456",
            email="maria@clinic.mx",
            phone="+521234567890",
            available_days=["monday", "tuesday", "wednesday", "thursday", "friday"],
            working_hours={
                "start": "09:00",
                "end": "18:00",
                "lunch_start": "14:00",
                "lunch_end": "15:00"
            }
        )

        result = await service.add_doctor(clinic_id, doctor)

        self.assertTrue(result['success'])
        self.assertIn('doctor_id', result)

        # Verify doctor was added
        doctors = mock_supabase.data.get('healthcare.doctors', [])
        self.assertEqual(len(doctors), 1)
        doc = doctors[0]
        self.assertEqual(doc['first_name'], "María")
        self.assertEqual(doc['specialization'], "Orthodontics")

    @patch('clinics.backend.app.api.onboarding.create_client')
    async def test_service_catalog_setup(self, mock_create_client):
        """Test adding services to clinic catalog"""
        from clinics.backend.app.api.onboarding import OnboardingService, ServiceDefinition

        mock_supabase = MockSupabaseClient()
        mock_create_client.return_value = mock_supabase

        service = OnboardingService()
        clinic_id = str(uuid.uuid4())

        # Add dental services
        services_to_add = [
            ServiceDefinition(
                name="Limpieza Dental",
                category="preventive",
                duration_minutes=45,
                base_price=600.00,
                description="Limpieza dental profesional"
            ),
            ServiceDefinition(
                name="Extracción Simple",
                category="surgery",
                duration_minutes=30,
                base_price=800.00,
                description="Extracción de diente simple"
            ),
            ServiceDefinition(
                name="Corona",
                category="restorative",
                duration_minutes=60,
                base_price=5000.00,
                description="Corona dental de porcelana"
            )
        ]

        for svc in services_to_add:
            result = await service.add_service(clinic_id, svc)
            self.assertTrue(result['success'])

        # Verify services were added
        services = mock_supabase.data.get('healthcare.services', [])
        self.assertEqual(len(services), 3)

        # Check price range
        prices = [s['base_price'] for s in services]
        self.assertEqual(min(prices), 600.00)
        self.assertEqual(max(prices), 5000.00)


class TestOnboardingValidation(AsyncTestCase):
    """Test onboarding validation and compliance checks"""

    async def test_registration_validation(self):
        """Test registration field validation"""
        from clinics.backend.app.api.onboarding import ClinicRegistration
        from pydantic import ValidationError

        # Test invalid email
        with self.assertRaises(ValidationError) as context:
            ClinicRegistration(
                name="Test Clinic",
                email="invalid-email",  # Invalid format
                phone="1234567890",
                address="123 Main",
                city="City",
                state="CA",
                zip_code="12345",
                admin_first_name="John",
                admin_last_name="Doe",
                admin_email="john@test.com",
                accept_terms=True,
                accept_baa=True
            )

        # Test state code validation (must be 2 chars)
        with self.assertRaises(ValidationError) as context:
            ClinicRegistration(
                name="Test Clinic",
                email="test@clinic.com",
                phone="1234567890",
                address="123 Main",
                city="City",
                state="California",  # Should be "CA"
                zip_code="12345",
                admin_first_name="John",
                admin_last_name="Doe",
                admin_email="john@test.com",
                accept_terms=True,
                accept_baa=True
            )

        # Test terms acceptance requirement
        with self.assertRaises(ValidationError) as context:
            ClinicRegistration(
                name="Test Clinic",
                email="test@clinic.com",
                phone="1234567890",
                address="123 Main",
                city="City",
                state="CA",
                zip_code="12345",
                admin_first_name="John",
                admin_last_name="Doe",
                admin_email="john@test.com",
                accept_terms=False,  # Must accept
                accept_baa=False     # Must accept
            )

    @patch('clinics.backend.app.security.compliance_manager.ComplianceManager')
    async def test_hipaa_compliance_check(self, mock_compliance):
        """Test HIPAA compliance validation before go-live"""
        from clinics.backend.app.api.onboarding import OnboardingService

        mock_compliance_instance = MagicMock()
        mock_compliance.return_value = mock_compliance_instance

        # Mock compliance check results
        mock_compliance_instance.validate_hipaa_requirements = AsyncMock(
            return_value={
                'encryption_at_rest': True,
                'encryption_in_transit': True,
                'access_controls': True,
                'audit_logging': True,
                'baa_signed': True,
                'employee_training': False  # This fails
            }
        )

        service = OnboardingService()
        service.compliance = mock_compliance_instance

        clinic_id = str(uuid.uuid4())

        # Try to complete onboarding
        with self.assertRaises(ValueError) as context:
            await service.complete_onboarding(clinic_id)

        # Should fail due to missing employee training
        self.assertIn('employee_training', str(context.exception))

    async def test_whatsapp_number_validation(self):
        """Test WhatsApp phone number format validation"""
        from clinics.backend.app.api.onboarding import validate_whatsapp_number

        # Valid formats
        valid_numbers = [
            "+14155551234",      # US
            "+521234567890",     # Mexico
            "+5511999887766",    # Brazil
        ]

        for number in valid_numbers:
            self.assertTrue(
                validate_whatsapp_number(number),
                f"Failed to validate: {number}"
            )

        # Invalid formats
        invalid_numbers = [
            "4155551234",        # Missing +
            "+1415555123",       # Too short
            "(415) 555-1234",    # Formatted
            "whatsapp:+14155551234",  # With prefix
        ]

        for number in invalid_numbers:
            self.assertFalse(
                validate_whatsapp_number(number),
                f"Should not validate: {number}"
            )


class TestOnboardingFlow(AsyncTestCase):
    """Test complete onboarding flow end-to-end"""

    @patch('clinics.backend.app.api.onboarding.create_client')
    @patch('clinics.backend.app.security.compliance_manager.ComplianceManager')
    async def test_complete_onboarding_journey(self, mock_compliance, mock_create_client):
        """Test complete onboarding from registration to go-live"""
        from clinics.backend.app.api.onboarding import OnboardingService
        from clinics.backend.app.api.onboarding import (
            ClinicRegistration,
            WhatsAppConfiguration,
            DoctorProfile,
            ServiceDefinition
        )

        # Setup mocks
        mock_supabase = MockSupabaseClient()
        mock_create_client.return_value = mock_supabase

        mock_compliance_instance = MagicMock()
        mock_compliance.return_value = mock_compliance_instance
        mock_compliance_instance.validate_hipaa_requirements = AsyncMock(
            return_value={k: True for k in [
                'encryption_at_rest',
                'encryption_in_transit',
                'access_controls',
                'audit_logging',
                'baa_signed',
                'employee_training'
            ]}
        )
        mock_compliance_instance.generate_compliance_report = AsyncMock(
            return_value={'compliance_score': 98.5}
        )

        service = OnboardingService()
        service.compliance = mock_compliance_instance

        # Step 1: Register clinic
        registration = ClinicRegistration(
            name="Complete Dental Care",
            email="admin@completecare.com",
            phone="(555) 987-6543",
            address="456 Oak Avenue",
            city="San Francisco",
            state="CA",
            zip_code="94102",
            admin_first_name="Jane",
            admin_last_name="Doe",
            admin_email="jane@completecare.com",
            accept_terms=True,
            accept_baa=True
        )

        reg_result = await service.register_clinic(registration)
        self.assertTrue(reg_result['success'])

        clinic_id = reg_result['clinic_id']
        org_id = reg_result['organization_id']

        # Step 2: Configure WhatsApp
        mock_supabase.data['healthcare.clinics'] = [{
            'id': clinic_id,
            'organization_id': org_id,
            'name': 'Complete Dental Care'
        }]

        whatsapp_config = WhatsAppConfiguration(
            provider="twilio",
            phone_number="+14155559999",
            twilio_account_sid="AC987654321",
            twilio_auth_token="auth_token_secret"
        )

        wa_result = await service.configure_whatsapp(clinic_id, whatsapp_config)
        self.assertTrue(wa_result['success'])

        # Step 3: Add doctors
        doctor = DoctorProfile(
            first_name="Robert",
            last_name="Johnson",
            specialization="General Dentistry",
            license_number="DDS789012",
            email="robert@completecare.com"
        )

        doc_result = await service.add_doctor(clinic_id, doctor)
        self.assertTrue(doc_result['success'])

        # Step 4: Add services
        service_def = ServiceDefinition(
            name="Comprehensive Exam",
            category="diagnostic",
            duration_minutes=60,
            base_price=150.00
        )

        svc_result = await service.add_service(clinic_id, service_def)
        self.assertTrue(svc_result['success'])

        # Step 5: Complete onboarding
        mock_supabase.data['healthcare.clinics'] = [{
            'id': clinic_id,
            'organization_id': org_id
        }]

        complete_result = await service.complete_onboarding(clinic_id)

        self.assertTrue(complete_result['success'])
        self.assertEqual(complete_result['status'], 'active')
        self.assertEqual(complete_result['compliance_score'], 98.5)

        # Verify onboarding status updated
        onboarding_records = mock_supabase.data.get('healthcare.clinic_onboarding', [])
        if onboarding_records:
            self.assertTrue(onboarding_records[0].get('testing_completed'))

    async def test_onboarding_progress_tracking(self):
        """Test tracking onboarding progress through steps"""
        from clinics.backend.app.api.onboarding import get_onboarding_progress

        # Mock onboarding status at different stages
        test_cases = [
            # (status_data, expected_percentage)
            (
                {
                    'registration_completed': True,
                    'whatsapp_configured': False,
                    'calendar_integrated': False,
                    'ai_customized': False,
                    'testing_completed': False
                },
                20  # 1 of 5 steps
            ),
            (
                {
                    'registration_completed': True,
                    'whatsapp_configured': True,
                    'calendar_integrated': True,
                    'ai_customized': False,
                    'testing_completed': False
                },
                60  # 3 of 5 steps
            ),
            (
                {
                    'registration_completed': True,
                    'whatsapp_configured': True,
                    'calendar_integrated': True,
                    'ai_customized': True,
                    'testing_completed': True
                },
                100  # All complete
            )
        ]

        for status_data, expected_pct in test_cases:
            progress = get_onboarding_progress(status_data)
            self.assertEqual(
                progress['percentage'], expected_pct,
                f"Wrong progress for {status_data}"
            )


# Helper functions for testing

def get_onboarding_progress(status: Dict[str, bool]) -> Dict[str, Any]:
    """Calculate onboarding progress percentage"""
    steps = [
        'registration_completed',
        'whatsapp_configured',
        'calendar_integrated',
        'ai_customized',
        'testing_completed'
    ]

    completed = sum(1 for step in steps if status.get(step, False))
    percentage = (completed / len(steps)) * 100

    return {
        'percentage': percentage,
        'completed_steps': completed,
        'total_steps': len(steps),
        'current_step': steps[completed] if completed < len(steps) else 'complete'
    }


def validate_whatsapp_number(number: str) -> bool:
    """Validate WhatsApp phone number format"""
    import re

    # Must start with + and have 10-15 digits
    pattern = r'^\+\d{10,15}$'
    return bool(re.match(pattern, number))


def detect_timezone_from_phone(phone: str) -> str:
    """Detect timezone from phone number"""
    # Mexican area codes to timezone mapping
    mx_area_codes = {
        '55': 'America/Mexico_City',     # Mexico City
        '33': 'America/Mexico_City',     # Guadalajara
        '81': 'America/Monterrey',       # Monterrey
        '664': 'America/Tijuana',        # Tijuana
        '656': 'America/Chihuahua',      # Juárez
    }

    # Extract area code from Mexican number
    if phone.startswith('+52'):
        area_code = phone[3:].lstrip('1')[:3]
        for code, tz in mx_area_codes.items():
            if area_code.startswith(code):
                return tz

    return 'America/Mexico_City'  # Default
