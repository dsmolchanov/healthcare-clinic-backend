"""
Clinic Onboarding API Endpoints
Handles clinic registration, configuration, and activation
"""

from fastapi import APIRouter, HTTPException, Depends, Request, Response
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, Field, validator
from typing import Dict, List, Optional, Any
from datetime import datetime, timedelta
import uuid
import secrets
import logging
import os
from supabase import create_client, Client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/onboarding", tags=["onboarding"])
security = HTTPBearer()


# Pydantic models for request/response
class ClinicRegistration(BaseModel):
    """Clinic registration request model"""
    name: str = Field(..., min_length=2, max_length=100)
    email: str = Field(..., pattern=r'^[\w\.-]+@[\w\.-]+\.\w+$')
    phone: str = Field(..., min_length=10)
    address: str
    city: str
    state: str = Field(..., min_length=2, max_length=2)
    zip_code: str = Field(..., pattern=r'^\d{5}(-\d{4})?$')
    timezone: str = Field(default="America/New_York")
    specialties: List[str] = Field(default_factory=list)
    business_hours: Dict[str, str] = Field(default_factory=dict)
    admin_first_name: str
    admin_last_name: str
    admin_email: str
    accept_terms: bool = Field(..., description="Must accept terms and conditions")
    accept_baa: bool = Field(..., description="Must accept Business Associate Agreement")

    @validator('accept_terms', 'accept_baa')
    def validate_agreements(cls, v):
        if not v:
            raise ValueError('Must accept all agreements')
        return v


class WhatsAppConfiguration(BaseModel):
    """WhatsApp configuration model"""
    provider: str = Field(default="twilio", pattern=r'^(twilio|meta|dialog360)$')
    phone_number: str
    twilio_account_sid: Optional[str] = None
    twilio_auth_token: Optional[str] = None
    meta_app_id: Optional[str] = None
    meta_app_secret: Optional[str] = None
    meta_access_token: Optional[str] = None


class CalendarIntegrationRequest(BaseModel):
    """Calendar integration request model"""
    doctor_id: str
    provider: str = Field(..., pattern=r'^(google|outlook|apple)$')


class DoctorProfile(BaseModel):
    """Doctor profile model"""
    first_name: str
    last_name: str
    specialization: str
    license_number: str
    email: str
    phone: Optional[str]
    available_days: List[str] = Field(default_factory=lambda: ["monday", "tuesday", "wednesday", "thursday", "friday"])
    working_hours: Dict[str, str] = Field(default_factory=dict)


class ServiceDefinition(BaseModel):
    """Service definition model"""
    name: str
    category: str
    duration_minutes: int = Field(default=30, ge=15, le=240)
    base_price: Optional[float] = None
    description: Optional[str] = None
    requires_referral: bool = False


class OnboardingService:
    """Service layer for onboarding operations"""

    def __init__(self):
        self.supabase: Client = create_client(
            os.environ.get("SUPABASE_URL"),
            os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
        )
        # These would be initialized with actual implementations
        self.vault = None  # ComplianceVault()
        self.compliance = None  # ComplianceManager()
        self.oauth_manager = None  # CalendarOAuthManager()
        self.booking_service = None  # AppointmentBookingService()

    async def register_clinic(self, registration: ClinicRegistration) -> Dict[str, Any]:
        """Register a new clinic"""

        try:
            # 1. Create organization
            org_data = {
                'id': str(uuid.uuid4()),
                'name': registration.name,
                'slug': registration.name.lower().replace(' ', '-'),
                'industry': 'healthcare',
                'subscription_tier': 'starter',
                'settings': {
                    'timezone': registration.timezone,
                    'baa_signed': registration.accept_baa,
                    'terms_accepted': registration.accept_terms,
                    'onboarding_completed': False
                },
                'billing_email': registration.email,
                'is_active': True,
                'created_at': datetime.utcnow().isoformat()
            }

            org_result = self.supabase.schema('core').table('organizations').insert(org_data).execute()

            if not org_result.data:
                raise ValueError("Failed to create organization")

            organization = org_result.data[0]

            # 2. Create clinic record
            clinic_data = {
                'id': str(uuid.uuid4()),
                'organization_id': organization['id'],
                'name': registration.name,
                'address': registration.address,
                'city': registration.city,
                'state': registration.state,
                'zip_code': registration.zip_code,
                'phone': registration.phone,
                'email': registration.email,
                'timezone': registration.timezone,
                'business_hours': registration.business_hours,
                'specialties': registration.specialties,
                'hipaa_compliant': True,
                'is_active': True,
                'created_at': datetime.utcnow().isoformat()
            }

            clinic_result = self.supabase.schema('healthcare').table('clinics').insert(clinic_data).execute()

            if not clinic_result.data:
                raise ValueError("Failed to create clinic")

            clinic = clinic_result.data[0]

            # 3. Create AI agent configuration
            agent_data = {
                'id': str(uuid.uuid4()),
                'organization_id': organization['id'],
                'name': f"{registration.name} AI Assistant",
                'description': f"AI assistant for {registration.name}",
                'type': 'assistant',
                'configuration': {
                    'greeting': f"Hello! I'm the AI assistant for {registration.name}. How can I help you today?",
                    'capabilities': ['appointment_booking', 'clinic_info', 'treatment_questions']
                },
                'voice_config': {
                    'provider': 'elevenlabs',
                    'voice_id': 'julia',
                    'language': 'en-US'
                },
                'is_active': True,
                'created_at': datetime.utcnow().isoformat()
            }

            agent_result = self.supabase.schema('core').table('agents').insert(agent_data).execute()

            # 4. Create admin user (simplified - in production, handle auth properly)
            # This would normally integrate with your auth system

            # 5. Record consent for compliance
            consent_data = {
                'id': str(uuid.uuid4()),
                'organization_id': organization['id'],
                'user_identifier': registration.admin_email,
                'channel_type': 'web',
                'consent_type': 'data_processing',
                'consent_method': 'opt_in_form',
                'consent_given': True,
                'policy_version': '1.0',
                'created_at': datetime.utcnow().isoformat()
            }

            self.supabase.schema('core').table('consent_records').insert(consent_data).execute()

            # 6. Initialize onboarding status
            onboarding_data = {
                'id': str(uuid.uuid4()),
                'clinic_id': clinic['id'],
                'registration_completed': True,
                'whatsapp_configured': False,
                'calendar_integrated': False,
                'ai_customized': False,
                'testing_completed': False,
                'onboarding_metadata': {
                    'started_at': datetime.utcnow().isoformat(),
                    'current_step': 'whatsapp_setup'
                }
            }

            self.supabase.schema('healthcare').table('clinic_onboarding').insert(onboarding_data).execute()

            # 7. Audit log
            await self.compliance.soc2_audit_trail(
                operation='clinic_registered',
                details={
                    'clinic_id': clinic['id'],
                    'organization_id': organization['id'],
                    'clinic_name': registration.name
                },
                organization_id=organization['id']
            )

            return {
                'success': True,
                'organization_id': organization['id'],
                'clinic_id': clinic['id'],
                'agent_id': agent_result.data[0]['id'] if agent_result.data else None,
                'next_step': 'whatsapp_configuration',
                'message': 'Clinic successfully registered!'
            }

        except Exception as e:
            logger.error(f"Clinic registration failed: {str(e)}")
            raise HTTPException(status_code=400, detail=str(e))

    async def configure_whatsapp(
        self,
        clinic_id: str,
        config: WhatsAppConfiguration
    ) -> Dict[str, Any]:
        """Configure WhatsApp integration"""

        try:
            # Get clinic and organization
            clinic_result = self.supabase.schema('healthcare').table('clinics').select('*').eq(
                'id', clinic_id
            ).single().execute()

            if not clinic_result.data:
                raise ValueError("Clinic not found")

            clinic = clinic_result.data

            # Store credentials securely
            credentials = {}

            if config.provider == 'twilio':
                credentials = {
                    'account_sid': config.twilio_account_sid,
                    'auth_token': config.twilio_auth_token,
                    'phone_number': config.phone_number
                }
            elif config.provider == 'meta':
                credentials = {
                    'app_id': config.meta_app_id,
                    'app_secret': config.meta_app_secret,
                    'access_token': config.meta_access_token,
                    'phone_number': config.phone_number
                }

            # Store in vault
            vault_ref = await self.vault.store_calendar_credentials(
                organization_id=clinic['organization_id'],
                provider=f'whatsapp_{config.provider}',
                credentials=credentials
            )

            # Create WhatsApp configuration
            webhook_token = secrets.token_urlsafe(32)

            whatsapp_config = {
                'id': str(uuid.uuid4()),
                'organization_id': clinic['organization_id'],
                'business_name': clinic['name'],
                'whatsapp_phone_number': config.phone_number,
                'webhook_url': f"https://healthcare-clinic-backend.fly.dev/webhooks/whatsapp/{clinic_id}",
                'webhook_verify_token': webhook_token,
                'connection_status': 'pending',
                'is_active': True,
                'created_at': datetime.utcnow().isoformat()
            }

            result = self.supabase.schema('core').table('whatsapp_business_configs').insert(
                whatsapp_config
            ).execute()

            # Update onboarding status
            self.supabase.schema('healthcare').table('clinic_onboarding').update({
                'whatsapp_configured': True,
                'onboarding_metadata': {
                    'current_step': 'calendar_integration'
                }
            }).eq('clinic_id', clinic_id).execute()

            return {
                'success': True,
                'webhook_url': whatsapp_config['webhook_url'],
                'webhook_verify_token': webhook_token,
                'next_step': 'calendar_integration',
                'message': 'WhatsApp successfully configured!'
            }

        except Exception as e:
            logger.error(f"WhatsApp configuration failed: {str(e)}")
            raise HTTPException(status_code=400, detail=str(e))

    async def initiate_calendar_oauth(
        self,
        clinic_id: str,
        request: CalendarIntegrationRequest
    ) -> Dict[str, Any]:
        """Initiate calendar OAuth flow"""

        try:
            if request.provider == 'google':
                auth_url = await self.oauth_manager.initiate_google_oauth(
                    clinic_id,
                    request.doctor_id
                )
            elif request.provider == 'outlook':
                auth_url = await self.oauth_manager.initiate_outlook_oauth(
                    clinic_id,
                    request.doctor_id
                )
            else:
                raise ValueError(f"Unsupported provider: {request.provider}")

            return {
                'success': True,
                'auth_url': auth_url,
                'provider': request.provider,
                'message': f'Please authorize access to your {request.provider.title()} calendar'
            }

        except Exception as e:
            logger.error(f"Calendar OAuth initiation failed: {str(e)}")
            raise HTTPException(status_code=400, detail=str(e))

    async def add_doctor(
        self,
        clinic_id: str,
        doctor: DoctorProfile
    ) -> Dict[str, Any]:
        """Add doctor to clinic"""

        try:
            doctor_data = {
                'id': str(uuid.uuid4()),
                'clinic_id': clinic_id,
                'first_name': doctor.first_name,
                'last_name': doctor.last_name,
                'specialization': doctor.specialization,
                'license_number': doctor.license_number,
                'email': doctor.email,
                'phone': doctor.phone,
                'available_days': doctor.available_days,
                'working_hours': doctor.working_hours,
                'active': True,
                'accepting_new_patients': True,
                'created_at': datetime.utcnow().isoformat()
            }

            result = self.supabase.schema('healthcare').table('doctors').insert(doctor_data).execute()

            if result.data:
                return {
                    'success': True,
                    'doctor_id': result.data[0]['id'],
                    'message': f'Doctor {doctor.first_name} {doctor.last_name} added successfully'
                }
            else:
                raise ValueError("Failed to add doctor")

        except Exception as e:
            logger.error(f"Failed to add doctor: {str(e)}")
            raise HTTPException(status_code=400, detail=str(e))

    async def add_service(
        self,
        clinic_id: str,
        service: ServiceDefinition
    ) -> Dict[str, Any]:
        """Add service to clinic"""

        try:
            service_data = {
                'id': str(uuid.uuid4()),
                'clinic_id': clinic_id,
                'name': service.name,
                'category': service.category,
                'duration_minutes': service.duration_minutes,
                'base_price': service.base_price,
                'description': service.description,
                'requires_referral': service.requires_referral,
                'is_active': True,
                'created_at': datetime.utcnow().isoformat()
            }

            result = self.supabase.schema('healthcare').table('services').insert(service_data).execute()

            if result.data:
                return {
                    'success': True,
                    'service_id': result.data[0]['id'],
                    'message': f'Service {service.name} added successfully'
                }
            else:
                raise ValueError("Failed to add service")

        except Exception as e:
            logger.error(f"Failed to add service: {str(e)}")
            raise HTTPException(status_code=400, detail=str(e))

    async def complete_onboarding(self, clinic_id: str) -> Dict[str, Any]:
        """Complete clinic onboarding and activate"""

        try:
            # Run compliance checks
            clinic_result = self.supabase.schema('healthcare').table('clinics').select(
                'organization_id'
            ).eq('id', clinic_id).single().execute()

            if not clinic_result.data:
                raise ValueError("Clinic not found")

            org_id = clinic_result.data['organization_id']

            # Validate HIPAA compliance
            hipaa_check = await self.compliance.validate_hipaa_requirements(org_id)

            if not all(hipaa_check.values()):
                failed_checks = [k for k, v in hipaa_check.items() if not v]
                raise ValueError(f"HIPAA compliance checks failed: {', '.join(failed_checks)}")

            # Generate compliance report
            report = await self.compliance.generate_compliance_report(
                org_id,
                [ComplianceStandard.HIPAA, ComplianceStandard.SOC2, ComplianceStandard.GDPR]
            )

            # Update onboarding status
            self.supabase.schema('healthcare').table('clinic_onboarding').update({
                'testing_completed': True,
                'live_date': datetime.utcnow().isoformat(),
                'onboarding_metadata': {
                    'completed_at': datetime.utcnow().isoformat(),
                    'compliance_score': report['compliance_score']
                }
            }).eq('clinic_id', clinic_id).execute()

            # Update organization settings
            self.supabase.schema('core').table('organizations').update({
                'settings': {
                    'onboarding_completed': True,
                    'compliance_verified': True,
                    'go_live_date': datetime.utcnow().isoformat()
                }
            }).eq('id', org_id).execute()

            return {
                'success': True,
                'compliance_score': report['compliance_score'],
                'status': 'active',
                'message': 'Clinic onboarding completed successfully! You are now live.'
            }

        except Exception as e:
            logger.error(f"Failed to complete onboarding: {str(e)}")
            raise HTTPException(status_code=400, detail=str(e))


# Initialize service
onboarding_service = OnboardingService()


# API Endpoints
@router.post("/register")
async def register_clinic(registration: ClinicRegistration):
    """Register a new clinic"""
    return await onboarding_service.register_clinic(registration)


@router.post("/{clinic_id}/whatsapp")
async def configure_whatsapp(clinic_id: str, config: WhatsAppConfiguration):
    """Configure WhatsApp for clinic"""
    return await onboarding_service.configure_whatsapp(clinic_id, config)


@router.post("/{clinic_id}/calendar/oauth")
async def initiate_calendar_oauth(clinic_id: str, request: CalendarIntegrationRequest):
    """Initiate calendar OAuth flow"""
    return await onboarding_service.initiate_calendar_oauth(clinic_id, request)


@router.get("/{clinic_id}/calendar/callback")
async def handle_calendar_callback(
    clinic_id: str,
    code: str,
    state: str,
    provider: str
):
    """Handle OAuth callback from calendar provider"""
    try:
        if provider == 'google':
            result = await onboarding_service.oauth_manager.handle_google_callback(code, state)
        elif provider == 'outlook':
            result = await onboarding_service.oauth_manager.handle_outlook_callback(code, state)
        else:
            raise ValueError(f"Unknown provider: {provider}")

        return result
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{clinic_id}/doctors")
async def add_doctor(clinic_id: str, doctor: DoctorProfile):
    """Add doctor to clinic"""
    return await onboarding_service.add_doctor(clinic_id, doctor)


@router.post("/{clinic_id}/services")
async def add_service(clinic_id: str, service: ServiceDefinition):
    """Add service to clinic"""
    return await onboarding_service.add_service(clinic_id, service)


@router.post("/{clinic_id}/complete")
async def complete_onboarding(clinic_id: str):
    """Complete onboarding and activate clinic"""
    return await onboarding_service.complete_onboarding(clinic_id)


@router.get("/{clinic_id}/status")
async def get_onboarding_status(clinic_id: str):
    """Get current onboarding status"""
    try:
        result = onboarding_service.supabase.schema('healthcare').table('clinic_onboarding').select('*').eq(
            'clinic_id', clinic_id
        ).single().execute()

        if result.data:
            return {
                'success': True,
                'status': result.data,
                'progress': {
                    'registration': result.data.get('registration_completed', False),
                    'whatsapp': result.data.get('whatsapp_configured', False),
                    'calendar': result.data.get('calendar_integrated', False),
                    'customization': result.data.get('ai_customized', False),
                    'testing': result.data.get('testing_completed', False)
                }
            }
        else:
            raise ValueError("Onboarding status not found")

    except Exception as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/{clinic_id}/test-booking")
async def test_appointment_booking(clinic_id: str):
    """Test appointment booking flow"""
    try:
        # Create test appointment
        test_details = {
            'doctor_id': 'test-doctor-id',
            'service_id': 'test-service-id',
            'date': (datetime.utcnow() + timedelta(days=7)).date().isoformat(),
            'time': '10:00:00',
            'duration_minutes': 30,
            'type': 'test',
            'reason': 'Testing appointment booking'
        }

        result = await onboarding_service.booking_service.book_appointment(
            patient_phone='test-phone',
            clinic_id=clinic_id,
            appointment_details=test_details,
            idempotency_key=f"test-{clinic_id}-{datetime.utcnow().isoformat()}"
        )

        return result

    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
