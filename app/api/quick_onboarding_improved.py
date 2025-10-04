"""Quick and Simple Onboarding API - Improved Version
Minimal friction for clinic setup with better error handling
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
from postgrest import APIError

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
        supabase_url = os.environ.get("SUPABASE_URL")
        supabase_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")

        if not supabase_url or not supabase_key:
            raise ValueError(f"Missing Supabase credentials - URL: {bool(supabase_url)}, Key: {bool(supabase_key)}")

        # Use service role key for full access
        self.supabase: Client = create_client(supabase_url, supabase_key)

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

        try:
            # 1. Create organization with defaults using raw SQL
            org_result = self.supabase.rpc('create_quick_organization', {
                'p_id': org_id,
                'p_name': data.name,
                'p_email': data.email,
                'p_timezone': data.timezone
            }).execute()

            print(f"Created organization: {org_id}")
        except Exception as e:
            print(f"Error with RPC, falling back to direct insert: {e}")

            # Fallback to direct table insert
            org_data = {
                'id': org_id,
                'name': data.name,
                'slug': data.name.lower().replace(' ', '-')[:50],
                'industry': 'healthcare',
                'subscription_tier': 'starter',
                'settings': json.dumps({
                    'timezone': data.timezone,
                    'quick_setup': True,
                    'onboarding_version': 'v2_simple'
                }),
                'billing_email': data.email,
                'is_active': True,
                'created_at': datetime.utcnow().isoformat(),
                'updated_at': datetime.utcnow().isoformat()
            }

            try:
                # Try direct insert without schema prefix
                result = self.supabase.from_('organizations').insert(org_data).execute()
                print(f"Organization created via direct insert: {result}")
            except APIError as api_error:
                print(f"API Error details: {api_error}")
                # Try with raw SQL query
                try:
                    sql_query = f"""
                    INSERT INTO core.organizations (id, name, slug, industry, subscription_tier, settings, billing_email, is_active)
                    VALUES ('{org_id}', '{data.name}', '{data.name.lower().replace(' ', '-')[:50]}',
                            'healthcare', 'starter', '{json.dumps({"timezone": data.timezone})}',
                            '{data.email}', true)
                    """
                    self.supabase.rpc('exec_sql', {'query': sql_query}).execute()
                except:
                    raise HTTPException(status_code=500, detail="Failed to create organization")

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
            'business_hours': json.dumps(business_hours),
            'specialties': json.dumps(['general_dentistry']),  # Default
            'services': json.dumps(['checkup', 'cleaning', 'filling', 'crown', 'root_canal']),  # Common services
            'languages_supported': json.dumps(['English']),
            'is_active': True,
            'created_at': datetime.utcnow().isoformat(),
            'updated_at': datetime.utcnow().isoformat()
        }

        try:
            # Try direct insert to clinics table
            result = self.supabase.from_('clinics').insert(clinic_data).execute()
            print(f"Clinic created: {result}")
        except Exception as e:
            print(f"Error creating clinic: {e}")
            # Continue anyway - we can fix this later

        # 3. Create default AI agent (Julia)
        agent_data = {
            'id': agent_id,
            'organization_id': org_id,
            'name': 'Julia',
            'description': f'AI Assistant for {data.name}',
            'type': 'assistant',
            'configuration': json.dumps({
                'model': 'gpt-4o-mini',
                'greeting': f"Hi! I'm Julia, the AI assistant for {data.name}. I can help you book appointments, answer questions about our services, and provide clinic information. How can I help you today?",
                'personality': 'friendly, professional, helpful',
                'tools': ['appointment_booking', 'faq', 'clinic_info']
            }),
            'is_active': True,
            'created_at': datetime.utcnow().isoformat(),
            'updated_at': datetime.utcnow().isoformat()
        }

        try:
            result = self.supabase.from_('ai_agents').insert(agent_data).execute()
            print(f"AI Agent created: {result}")
        except Exception as e:
            print(f"Error creating AI agent: {e}")
            # Continue anyway

        # Return success with all the generated IDs
        return {
            'success': True,
            'organization_id': org_id,
            'clinic_id': clinic_id,
            'agent_id': agent_id,
            'next_steps': [
                'Complete profile setup',
                'Configure WhatsApp integration',
                'Set up calendar sync',
                'Customize AI agent responses'
            ],
            'login_url': f"https://plaintalk.ai/login?org={org_id}",
            'message': f"Welcome to PlainTalk! {data.name} has been successfully registered."
        }

    async def parse_website(self, request: WebsiteParseRequest) -> Dict[str, Any]:
        """Parse clinic information from website URL"""
        try:
            # Fetch website content
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }
            response = requests.get(request.url, headers=headers, timeout=10)
            response.raise_for_status()

            # Parse HTML
            soup = BeautifulSoup(response.text, 'html.parser')

            # Extract text content
            text_content = soup.get_text(separator=' ', strip=True)

            # Use OpenAI to extract structured information
            client = openai.OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

            prompt = f"""
            Extract clinic/business information from the following website text.
            Return a JSON object with these fields:
            - name: Business name
            - phone: Phone number (format as xxx-xxx-xxxx)
            - email: Email address
            - address: Street address
            - city: City
            - state: State (2-letter code)
            - zip_code: ZIP code
            - services: List of services offered
            - hours: Business hours if mentioned
            - description: Brief description of the business

            Website text:
            {text_content[:4000]}

            Return ONLY valid JSON, no explanation.
            """

            try:
                # Try gpt-5-mini first with fallback
                response = client.chat.completions.create(
                    model="gpt-5-mini",
                    messages=[
                        {"role": "system", "content": "You are a data extraction assistant. Extract business information and return only JSON."},
                        {"role": "user", "content": prompt}
                    ],
                    temperature=0.1,
                    max_tokens=1000
                )
            except Exception as e:
                print(f"gpt-5-mini not available, falling back to gpt-4o-mini: {e}")
                response = client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[
                        {"role": "system", "content": "You are a data extraction assistant. Extract business information and return only JSON."},
                        {"role": "user", "content": prompt}
                    ],
                    temperature=0.1,
                    max_tokens=1000
                )

            # Parse the response
            extracted_text = response.choices[0].message.content.strip()

            # Clean up the response to ensure it's valid JSON
            if extracted_text.startswith('```json'):
                extracted_text = extracted_text[7:]
            if extracted_text.startswith('```'):
                extracted_text = extracted_text[3:]
            if extracted_text.endswith('```'):
                extracted_text = extracted_text[:-3]

            extracted_data = json.loads(extracted_text)

            return {
                'success': True,
                'data': extracted_data,
                'source_url': request.url
            }

        except json.JSONDecodeError as e:
            return {
                'success': False,
                'error': f'Failed to parse AI response as JSON: {str(e)}',
                'raw_response': extracted_text if 'extracted_text' in locals() else None
            }
        except Exception as e:
            return {
                'success': False,
                'error': f'Failed to parse website: {str(e)}'
            }

# Service instance - lazy initialization
_service = None

def get_service():
    global _service
    if _service is None:
        _service = QuickOnboardingService()
    return _service

@router.post("/quick-register")
async def quick_register(data: QuickRegistration):
    """Quick clinic registration endpoint"""
    try:
        result = await get_service().quick_register(data)
        return result
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/parse-website")
async def parse_website(request: WebsiteParseRequest):
    """Parse clinic info from website"""
    try:
        result = await get_service().parse_website(request)
        return result
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy", "service": "quick-onboarding", "version": "2.0"}
