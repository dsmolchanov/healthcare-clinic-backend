"""
Quick and Simple Onboarding API
Minimal friction for clinic setup
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, Dict, Any
from datetime import datetime
import uuid
import os
import re
import json
import requests
from bs4 import BeautifulSoup
import openai
from supabase import create_client, Client

router = APIRouter(prefix="/api/onboarding", tags=["quick-onboarding"])


class QuickRegistration(BaseModel):
    """Minimal registration - we'll fill in the rest"""
    name: str
    phone: str
    email: str
    timezone: Optional[str] = "America/New_York"
    state: Optional[str] = "CA"
    city: Optional[str] = "City"
    address: Optional[str] = "123 Main St"
    zip_code: Optional[str] = "00000"


class QuickWhatsApp(BaseModel):
    """Simple WhatsApp setup"""
    phone_number: str
    use_shared_account: bool = True  # Use our Twilio account


class QuickCalendar(BaseModel):
    """Simple calendar setup"""
    provider: str = "google"  # Just Google for now

class WebsiteParseRequest(BaseModel):
    """Request to parse clinic information from website"""
    url: str

class QuickOnboardingService:
    def __init__(self):
        self.supabase: Client = create_client(
            os.environ.get("SUPABASE_URL"),
            os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
        )

        # Shared Twilio credentials for all clinics initially
        self.shared_twilio = {
            'account_sid': os.environ.get("SHARED_TWILIO_SID"),
            'auth_token': os.environ.get("SHARED_TWILIO_TOKEN"),
            'phone_number': os.environ.get("SHARED_WHATSAPP_NUMBER", "+14155238886")  # Twilio sandbox
        }

    async def quick_register(self, data: QuickRegistration) -> Dict[str, Any]:
        """Super quick registration with minimal fields"""

        # Generate IDs
        org_id = str(uuid.uuid4())
        clinic_id = str(uuid.uuid4())
        agent_id = str(uuid.uuid4())

        # 1. Create organization with defaults
        org_data = {
            'id': org_id,
            'name': data.name,
            'slug': data.name.lower().replace(' ', '-')[:50],
            'industry': 'healthcare',
            'subscription_tier': 'starter',
            'settings': {
                'timezone': data.timezone,
                'quick_setup': True,
                'onboarding_version': 'v2_simple'
            },
            'billing_email': data.email,
            'is_active': True
        }

        self.supabase.schema('core').table('organizations').insert(org_data).execute()

        # 2. Create clinic with smart defaults
        business_hours = {
            'monday': '9:00 AM - 6:00 PM',
            'tuesday': '9:00 AM - 6:00 PM',
            'wednesday': '9:00 AM - 6:00 PM',
            'thursday': '9:00 AM - 6:00 PM',
            'friday': '9:00 AM - 5:00 PM',
            'saturday': '9:00 AM - 2:00 PM',
            'sunday': 'Closed'
        }

        clinic_data = {
            'id': clinic_id,
            'organization_id': org_id,
            'name': data.name,
            'phone': data.phone,
            'email': data.email,
            'address': data.address,
            'city': data.city,
            'state': data.state,
            'zip_code': data.zip_code,
            'timezone': data.timezone,
            'business_hours': business_hours,
            'specialties': ['general_dentistry'],  # Default
            'services': ['checkup', 'cleaning', 'filling', 'crown', 'root_canal'],  # Common services
            'languages_supported': ['English'],
            'is_active': True
        }

        self.supabase.schema('healthcare').table('clinics').insert(clinic_data).execute()

        # 3. Create default AI agent (Julia)
        agent_data = {
            'id': agent_id,
            'organization_id': org_id,
            'name': 'Julia',
            'description': f'AI Assistant for {data.name}',
            'type': 'assistant',
            'configuration': {
                'model': 'gpt-4o-mini',
                'greeting': f"Hi! I'm Julia, the AI assistant for {data.name}. I can help you book appointments, answer questions about our services, and provide clinic information. How can I help you today?",
                'personality': 'friendly, professional, helpful',
                'capabilities': ['appointment_booking', 'service_info', 'clinic_hours', 'general_questions']
            },
            'is_active': True
        }

        self.supabase.schema('core').table('agents').insert(agent_data).execute()

        # 4. Create a default doctor profile
        doctor_data = {
            'id': str(uuid.uuid4()),
            'clinic_id': clinic_id,
            'first_name': 'Dr.',
            'last_name': data.name.split()[0] if data.name else 'Smith',
            'specialization': 'General Dentistry',
            'license_number': 'PENDING',
            'email': data.email,
            'phone': data.phone,
            'active': True,
            'accepting_new_patients': True
        }

        self.supabase.schema('healthcare').table('doctors').insert(doctor_data).execute()

        # 5. Auto-accept terms (they clicked agree on frontend)
        consent_data = {
            'id': str(uuid.uuid4()),
            'organization_id': org_id,
            'user_identifier': data.email,
            'channel_type': 'web',
            'consent_type': 'data_processing',
            'consent_method': 'opt_in_form',
            'consent_given': True,
            'policy_version': '1.0'
        }

        self.supabase.schema('core').table('consent_records').insert(consent_data).execute()

        return {
            'success': True,
            'clinic_id': clinic_id,
            'organization_id': org_id,
            'agent_id': agent_id,
            'doctor_id': doctor_data['id'],
            'message': f'Welcome {data.name}!'
        }

    async def setup_whatsapp_simple(
        self,
        clinic_id: str,
        data: QuickWhatsApp
    ) -> Dict[str, Any]:
        """Ultra-simple WhatsApp setup using shared account"""

        # Get clinic info
        clinic = self.supabase.schema('healthcare').table('clinics').select('*').eq(
            'id', clinic_id
        ).single().execute()

        if not clinic.data:
            raise ValueError("Clinic not found")

        # Create WhatsApp config
        config_data = {
            'id': str(uuid.uuid4()),
            'organization_id': clinic.data['organization_id'],
            'business_name': clinic.data['name'],
            'whatsapp_phone_number': data.phone_number,
            'webhook_url': f"https://healthcare-clinic-backend.fly.dev/webhooks/whatsapp/{clinic_id}",
            'webhook_verify_token': 'shared-token',  # Using shared
            'connection_status': 'active' if data.use_shared_account else 'pending',
            'is_active': True
        }

        # If using shared account, store reference
        if data.use_shared_account:
            # Store in organization_secrets that we're using shared account
            self.supabase.schema('core').table('organization_secrets').insert({
                'id': str(uuid.uuid4()),
                'organization_id': clinic.data['organization_id'],
                'secret_type': 'whatsapp_creds',
                'secret_name': 'Shared WhatsApp Account',
                'encrypted_value': 'SHARED_ACCOUNT',  # Special marker
                'encryption_key_id': 'shared',
                'created_by': clinic.data['organization_id']
            }).execute()

        self.supabase.schema('core').table('whatsapp_business_configs').insert(config_data).execute()

        return {
            'success': True,
            'webhook_url': config_data['webhook_url'],
            'using_shared_account': data.use_shared_account,
            'instructions': 'Your WhatsApp is ready! Patients can now message your number.',
            'test_message': f"Send 'Hi' to {data.phone_number} to test"
        }

    async def quick_calendar_setup(
        self,
        clinic_id: str,
        data: QuickCalendar
    ) -> Dict[str, Any]:
        """Generate OAuth URL for calendar - simplified"""

        # Get clinic and doctor info
        clinic = self.supabase.schema('healthcare').table('clinics').select('*').eq(
            'id', clinic_id
        ).single().execute()

        doctor = self.supabase.schema('healthcare').table('doctors').select('id').eq(
            'clinic_id', clinic_id
        ).limit(1).execute()

        if not clinic.data or not doctor.data:
            raise ValueError("Clinic or doctor not found")

        doctor_id = doctor.data[0]['id']

        # For Google Calendar
        if data.provider == 'google':
            # Simple OAuth URL without complex state management
            redirect_uri = os.environ.get('GOOGLE_REDIRECT_URI', 'http://localhost:3000/calendar/callback')

            oauth_url = (
                "https://accounts.google.com/o/oauth2/v2/auth?"
                f"client_id={os.environ.get('GOOGLE_CLIENT_ID')}&"
                f"redirect_uri={redirect_uri}&"
                f"response_type=code&"
                f"scope=https://www.googleapis.com/auth/calendar https://www.googleapis.com/auth/calendar.events&"
                f"access_type=offline&"
                f"prompt=consent&"
                f"state={clinic_id}:{doctor_id}"  # Simple state
            )

            return {
                'success': True,
                'auth_url': oauth_url,
                'provider': 'google'
            }

        return {'success': False, 'error': 'Provider not supported'}

    async def activate_clinic(self, clinic_id: str) -> Dict[str, Any]:
        """Activate clinic - skip complex compliance for MVP"""

        # Get clinic info
        clinic = self.supabase.table('healthcare.clinics').select('organization_id').eq(
            'id', clinic_id
        ).single().execute()

        if not clinic.data:
            raise ValueError("Clinic not found")

        # Mark as active and onboarded
        self.supabase.schema('core').table('organizations').update({
            'settings': {
                'onboarding_completed': True,
                'activated_at': datetime.utcnow().isoformat()
            }
        }).eq('id', clinic.data['organization_id']).execute()

        # Send welcome email (implement later)
        # await send_welcome_email(clinic_id)

        return {
            'success': True,
            'status': 'active',
            'message': 'Your clinic is now active!',
            'dashboard_url': '/dashboard'
        }

    async def parse_website(self, url: str) -> Dict[str, Any]:
        """Parse clinic information from website using AI"""
        try:
            # Fetch website content
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
            }
            response = requests.get(url, headers=headers, timeout=10)
            response.raise_for_status()

            # Parse HTML for better structure awareness
            soup = BeautifulSoup(response.text, 'html.parser')

            # Extract structured data with more context
            contact_info = []

            # Extract all text with structure preserved
            for element in soup.find_all(['p', 'div', 'span', 'a', 'address', 'footer', 'header', 'section']):
                text = element.get_text(strip=True)
                if text:
                    contact_info.append(text)

            # Look for specific metadata
            meta_description = soup.find('meta', {'name': 'description'})
            if meta_description:
                contact_info.append(meta_description.get('content', ''))

            # Look for phone links
            phone_links = soup.find_all('a', href=re.compile(r'^tel:'))
            for link in phone_links:
                phone_text = link.get_text(strip=True)
                phone_href = link.get('href', '').replace('tel:', '')
                contact_info.append(f"Phone link: {phone_href} (text: {phone_text})")

            # Look for WhatsApp links
            whatsapp_links = soup.find_all('a', href=re.compile(r'whatsapp|wa\.me'))
            for link in whatsapp_links:
                whatsapp_info = link.get('href', '')
                contact_info.append(f"WhatsApp: {whatsapp_info}")

            # Look for email links
            email_links = soup.find_all('a', href=re.compile(r'^mailto:'))
            for link in email_links:
                email_text = link.get_text(strip=True)
                email_href = link.get('href', '').replace('mailto:', '')
                contact_info.append(f"Email link: {email_href} (text: {email_text})")

            # Get full page text as fallback
            full_text = soup.get_text()
            lines = (line.strip() for line in full_text.splitlines())
            chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
            cleaned_text = ' '.join(chunk for chunk in chunks if chunk)

            # Combine structured and unstructured data
            structured_content = '\n'.join(contact_info[:100])  # Increased limit
            page_content = cleaned_text[:4000]  # Increased for better context

            # Get OpenAI client
            openai.api_key = os.environ.get("OPENAI_API_KEY")

            # Enhanced prompt with URL directly passed to AI
            prompt = f"""
            You are analyzing a medical/dental clinic website to extract ALL contact information.
            URL: {url}

            STRUCTURED CONTENT EXTRACTED:
            {structured_content}

            FULL PAGE CONTENT:
            {page_content}

            IMPORTANT: Extract ALL available information, including:
            1. Clinic/Business name (from logo, header, title, footer)
            2. ALL phone numbers found (main, WhatsApp, mobile, fax, emergency)
            3. ALL email addresses (patient, info, support, appointments)
            4. Complete physical address (street, suite, building, floor)
            5. City
            6. State/Province (2-letter code for US/Canada/Mexico)
            7. ZIP/Postal code
            8. Country
            9. WhatsApp numbers (look for WhatsApp mentions, wa.me links)
            10. Business hours if available
            11. Timezone (infer from location)

            Search thoroughly in:
            - Contact sections and pages
            - Footer information
            - Header details
            - About sections
            - Social media links
            - Phone/email links (href="tel:" and href="mailto:")
            - WhatsApp links and mentions
            - Address tags
            - Structured data/JSON-LD

            Return a JSON object with these keys:
            {{
                "name": "clinic/business name",
                "phone": "primary phone with country code",
                "email": "primary email address",
                "address": "complete street address",
                "city": "city name",
                "state": "XX (2-letter code)",
                "zip_code": "postal/zip code",
                "timezone": "America/XXX format",
                "additional_phones": ["all other phone numbers found"],
                "additional_emails": ["all other email addresses found"],
                "whatsapp": "whatsapp number if found",
                "country": "country name",
                "hours": "business hours if found",
                "website": "{url}"
            }}

            Be THOROUGH. Extract EVERYTHING available. Use null only if truly not found.
            Return ONLY the JSON object, no other text.
            """

            # Call OpenAI
            client = openai.OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

            # Try to use new responses.create() API if available
            try:
                # Use new responses.create() API with gpt-5-mini
                response = client.responses.create(
                    model="gpt-5-mini",
                    input=prompt,
                    instructions="You are an expert at extracting ALL business contact information from websites. Be extremely thorough and extract every piece of contact information available. Look for patterns, links, and structured data.",
                    reasoning={"effort": "high"},
                    text={"verbosity": "medium"}
                )
                # Parse the response
                content = response.output_text.strip()
            except (AttributeError, Exception) as e:
                # Fallback to chat.completions API
                response = client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[
                        {"role": "system", "content": "You are an expert at extracting ALL business contact information from websites. Be extremely thorough - extract every phone number, email, and address detail you can find."},
                        {"role": "user", "content": prompt}
                    ],
                    temperature=0.1,
                    max_tokens=1000
                )
                content = response.choices[0].message.content.strip()
            # Remove markdown code blocks if present
            content = re.sub(r'^```json\s*|\s*```$', '', content, flags=re.MULTILINE)

            result = json.loads(content)

            # Clean up phone numbers
            if result.get('phone'):
                phone = re.sub(r'[^\d+]', '', result['phone'])
                if len(phone) == 10:  # US number without country code
                    phone = f"+1{phone}"
                elif len(phone) == 11 and phone[0] == '1':  # US number with 1
                    phone = f"+{phone}"
                elif not phone.startswith('+'):
                    phone = f"+{phone}"
                result['phone'] = phone

            # Clean up WhatsApp number
            if result.get('whatsapp'):
                whatsapp = re.sub(r'[^\d+]', '', result['whatsapp'])
                if len(whatsapp) == 10:
                    whatsapp = f"+1{whatsapp}"
                elif len(whatsapp) == 11 and whatsapp[0] == '1':
                    whatsapp = f"+{whatsapp}"
                elif not whatsapp.startswith('+'):
                    whatsapp = f"+{whatsapp}"
                result['whatsapp'] = whatsapp

            # If AI didn't find contact info, try regex patterns as last resort
            if not result.get('phone') and not result.get('email'):
                # Enhanced phone patterns
                phone_patterns = [
                    r'[\+]?[(]?[0-9]{1,3}[)]?[-\s\.]?[(]?[0-9]{1,4}[)]?[-\s\.]?[0-9]{1,4}[-\s\.]?[0-9]{1,9}',
                    r'\b\d{3}[-.]?\d{3}[-.]?\d{4}\b',  # US format
                    r'\+\d{1,3}\s?\d{1,14}',  # International format
                ]

                email_pattern = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'

                all_phones = []
                for pattern in phone_patterns:
                    phones = re.findall(pattern, cleaned_text)
                    all_phones.extend(phones)

                emails = re.findall(email_pattern, cleaned_text)

                if all_phones and not result.get('phone'):
                    result['phone'] = all_phones[0]
                if emails and not result.get('email'):
                    result['email'] = emails[0]
                if len(all_phones) > 1:
                    result['additional_phones'] = list(set(all_phones[1:6]))  # Unique, limit to 5
                if len(emails) > 1:
                    result['additional_emails'] = list(set(emails[1:6]))  # Unique, limit to 5

            # Ensure website URL is included
            if not result.get('website'):
                result['website'] = url

            return result

        except requests.RequestException as e:
            raise ValueError(f"Failed to fetch website: {str(e)}")
        except json.JSONDecodeError as e:
            raise ValueError(f"Failed to parse AI response: {str(e)}")
        except Exception as e:
            raise ValueError(f"Failed to parse website: {str(e)}")


# Initialize service
service = QuickOnboardingService()


# Routes
@router.post("/parse-website")
async def parse_website(data: WebsiteParseRequest):
    """Parse clinic information from website using AI"""
    try:
        return await service.parse_website(data.url)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/quick-register")
async def quick_register(data: QuickRegistration):
    """Quick registration with minimal fields"""
    try:
        return await service.quick_register(data)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{clinic_id}/whatsapp-simple")
async def setup_whatsapp(clinic_id: str, data: QuickWhatsApp):
    """Simple WhatsApp setup"""
    try:
        return await service.setup_whatsapp_simple(clinic_id, data)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{clinic_id}/calendar/quick-setup")
async def quick_calendar(clinic_id: str, data: QuickCalendar):
    """Quick calendar setup"""
    try:
        return await service.quick_calendar_setup(clinic_id, data)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{clinic_id}/activate")
async def activate_clinic(clinic_id: str):
    """Activate clinic"""
    try:
        return await service.activate_clinic(clinic_id)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/test-flow")
async def test_onboarding_flow():
    """Test the entire flow with dummy data"""

    # 1. Register
    test_clinic = QuickRegistration(
        name="Test Dental Clinic",
        phone="(555) 123-4567",
        email="test@dental.com",
        timezone="America/Los_Angeles"
    )

    reg_result = await service.quick_register(test_clinic)
    clinic_id = reg_result['clinic_id']

    # 2. WhatsApp
    whatsapp = QuickWhatsApp(
        phone_number="(555) 123-4567",
        use_shared_account=True
    )

    wa_result = await service.setup_whatsapp_simple(clinic_id, whatsapp)

    # 3. Calendar (just return URL, don't actually OAuth)
    cal_result = await service.quick_calendar_setup(
        clinic_id,
        QuickCalendar(provider="google")
    )

    # 4. Activate
    activate_result = await service.activate_clinic(clinic_id)

    return {
        'test_complete': True,
        'clinic_id': clinic_id,
        'steps': {
            'registration': reg_result,
            'whatsapp': wa_result,
            'calendar_url': cal_result.get('auth_url', ''),
            'activation': activate_result
        }
    }
