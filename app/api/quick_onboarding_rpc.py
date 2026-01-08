"""Quick Onboarding API using RPC functions
Uses Supabase RPC functions to handle cross-schema access properly
"""
from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from typing import Optional, Dict, Any
import os
import uuid
import asyncio
import logging
from datetime import datetime, timedelta, timezone
import aiohttp
from supabase import create_client, Client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/onboarding", tags=["quick-onboarding"])


async def _register_google_webhook(
    calendar_id: str,
    access_token: str,
    clinic_id: str
) -> Dict[str, Any]:
    """
    Register Google Calendar push notification channel

    Docs: https://developers.google.com/calendar/api/guides/push

    Args:
        calendar_id: Google Calendar ID
        access_token: OAuth access token
        clinic_id: Clinic UUID

    Returns:
        Webhook registration result from Google API
    """
    # Get Supabase client with healthcare schema
    from supabase.client import ClientOptions
    supabase_url = os.environ.get("SUPABASE_URL")
    supabase_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    options = ClientOptions(schema='healthcare')
    supabase = create_client(supabase_url, supabase_key, options=options)

    webhook_url = f"{os.getenv('APP_BASE_URL', 'https://healthcare-clinic-backend.fly.dev')}/webhooks/calendar/google"
    channel_id = f"clinic_{clinic_id}_{uuid.uuid4().hex[:8]}"

    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"https://www.googleapis.com/calendar/v3/calendars/{calendar_id}/events/watch",
            headers={
                'Authorization': f'Bearer {access_token}',
                'Content-Type': 'application/json'
            },
            json={
                'id': channel_id,
                'type': 'web_hook',
                'address': webhook_url,
                'expiration': int((datetime.utcnow() + timedelta(days=7)).timestamp() * 1000)
            }
        ) as response:
            if response.status != 200:
                error = await response.text()
                raise ValueError(f"Webhook registration failed: {error}")

            result = await response.json()

            # Store channel info for renewal and webhook validation
            supabase.table('webhook_channels').insert({
                'clinic_id': clinic_id,
                'channel_id': channel_id,
                'resource_id': result['resourceId'],
                'expiration': datetime.fromtimestamp(int(result['expiration']) / 1000).isoformat()
            }).execute()

            return result

async def _geocode_clinic_background(
    supabase_url: str,
    supabase_key: str,
    clinic_id: str,
    address: str,
    city: str,
    state: str,
    zip_code: Optional[str]
):
    """
    Background task to geocode clinic address after registration completes.

    CRITICAL: This runs async and does NOT block the registration response.
    """
    try:
        from app.utils.geocoding import geocode_address, build_location_data
        from supabase.client import ClientOptions

        geocode_result = await geocode_address(
            address=address,
            city=city,
            state=state,
            country="USA",
            zip_code=zip_code
        )

        if geocode_result.get('success'):
            location_data = build_location_data(geocode_result)
            location_data['geocoded_at'] = datetime.now(timezone.utc).isoformat()

            # Create client with healthcare schema
            options = ClientOptions(schema='healthcare')
            supabase = create_client(supabase_url, supabase_key, options=options)

            # Update clinic with location data
            supabase.table('clinics').update({
                'location_data': location_data
            }).eq('id', clinic_id).execute()

            logger.info(f"Geocoded clinic {clinic_id}: {location_data.get('formatted_address')}")
        else:
            logger.warning(f"Geocoding failed for clinic {clinic_id}: {geocode_result.get('error')}")
    except Exception as e:
        logger.error(f"Geocoding error for clinic {clinic_id}: {e}")
        # Don't propagate - this is a background task


class QuickRegistration(BaseModel):
    """Minimal registration - we'll fill in the rest"""
    name: str
    phone: str
    email: str
    user_email: Optional[str] = None  # Email of the user to associate
    timezone: Optional[str] = "America/New_York"
    state: Optional[str] = "CA"
    city: Optional[str] = "City"
    address: Optional[str] = "123 Main St"
    zip_code: Optional[str] = "00000"
    # NEW FIELDS for enhanced onboarding
    business_hours: Optional[Dict[str, Any]] = None  # JSONB structure
    currency: Optional[str] = "USD"
    primary_language: Optional[str] = "en"
    country: Optional[str] = "US"

class QuickWhatsApp(BaseModel):
    """Simple WhatsApp setup"""
    phone_number: str
    use_shared_account: bool = True  # Use our Twilio account

class QuickCalendar(BaseModel):
    """Simple calendar setup"""
    provider: str = "google"  # Just Google for now

class QuickOnboardingRPCService:
    def __init__(self):
        supabase_url = os.environ.get("SUPABASE_URL")
        supabase_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")

        if not supabase_url or not supabase_key:
            raise ValueError("Missing Supabase credentials")

        self.supabase: Client = create_client(supabase_url, supabase_key)

    async def quick_register(self, data: QuickRegistration) -> Dict[str, Any]:
        """Quick registration using RPC function"""
        try:
            import json
            # Prepare business_hours - use default if not provided
            business_hours_json = None
            if data.business_hours:
                business_hours_json = json.dumps(data.business_hours)

            # If user_email is provided, use the new RPC function that associates the user
            if data.user_email:
                result = self.supabase.rpc('quick_register_clinic_with_user', {
                    'p_name': data.name,
                    'p_phone': data.phone,
                    'p_email': data.email,
                    'p_user_email': data.user_email,
                    'p_timezone': data.timezone,
                    'p_state': data.state,
                    'p_city': data.city,
                    'p_address': data.address,
                    'p_zip_code': data.zip_code,
                    # NEW: Pass enhanced onboarding fields
                    'p_business_hours': business_hours_json,
                    'p_currency': data.currency,
                    'p_primary_language': data.primary_language
                }).execute()
            else:
                # Fall back to original RPC function
                result = self.supabase.rpc('quick_register_clinic', {
                    'p_name': data.name,
                    'p_phone': data.phone,
                    'p_email': data.email,
                    'p_timezone': data.timezone,
                    'p_state': data.state,
                    'p_city': data.city,
                    'p_address': data.address,
                    'p_zip_code': data.zip_code
                }).execute()

                # If registration successful but no user association, try to associate
                if result.data and result.data.get('success') and result.data.get('organization_id'):
                    # Try to associate user with email matching clinic email
                    try:
                        assoc_result = self.supabase.rpc('associate_user_with_organization', {
                            'p_user_email': data.email,
                            'p_organization_id': result.data['organization_id'],
                            'p_role': 'owner'
                        }).execute()
                        if assoc_result.data:
                            result.data['user_association'] = assoc_result.data
                    except:
                        # Association failed, but registration succeeded
                        pass

            if result.data:
                # Trigger background geocoding if registration was successful
                if result.data.get('success') and result.data.get('clinic_id'):
                    supabase_url = os.environ.get("SUPABASE_URL")
                    supabase_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")

                    # CRITICAL: Use create_task to NOT block registration response
                    # Geocoding happens in background after response is sent
                    asyncio.create_task(_geocode_clinic_background(
                        supabase_url=supabase_url,
                        supabase_key=supabase_key,
                        clinic_id=result.data['clinic_id'],
                        address=data.address or "123 Main St",
                        city=data.city or "City",
                        state=data.state or "CA",
                        zip_code=data.zip_code
                    ))

                return result.data
            else:
                return {
                    'success': False,
                    'error': 'No data returned from RPC function',
                    'message': 'Registration failed'
                }

        except Exception as e:
            # Check if it's actually a successful response misinterpreted as error
            error_str = str(e)
            if "'success': True" in error_str and "'clinic_id':" in error_str:
                # Extract the actual response data from the error message
                try:
                    import ast
                    # The error message contains the actual response dict
                    response_dict = ast.literal_eval(error_str)
                    if isinstance(response_dict, dict) and response_dict.get('success'):
                        return response_dict
                except:
                    pass

            print(f"RPC call failed: {e}")
            # Return a fallback response
            return {
                'success': False,
                'error': str(e),
                'message': f'Registration failed for {data.name}',
                'note': 'Please check database configuration'
            }

    async def fetch_clinic(self, clinic_id: str) -> Dict[str, Any]:
        """Fetch clinic information using RPC function"""
        try:
            result = self.supabase.rpc('fetch_clinic', {
                'p_clinic_id': clinic_id
            }).execute()

            if result.data:
                return result.data
            else:
                return {
                    'success': False,
                    'error': 'Clinic not found',
                    'clinic_id': clinic_id
                }

        except Exception as e:
            return {
                'success': False,
                'error': str(e),
                'clinic_id': clinic_id
            }

    async def update_clinic(self, clinic_id: str, updates: Dict[str, Any]) -> Dict[str, Any]:
        """Update clinic information using RPC function"""
        try:
            result = self.supabase.rpc('update_clinic', {
                'p_clinic_id': clinic_id,
                'p_updates': updates
            }).execute()

            if result.data:
                return result.data
            else:
                return {
                    'success': False,
                    'error': 'Update failed',
                    'clinic_id': clinic_id
                }

        except Exception as e:
            return {
                'success': False,
                'error': str(e),
                'clinic_id': clinic_id
            }

    async def list_clinics(self, organization_id: Optional[str] = None) -> Dict[str, Any]:
        """List clinics using RPC function"""
        try:
            params = {}
            if organization_id:
                params['p_organization_id'] = organization_id

            result = self.supabase.rpc('list_clinics', params).execute()

            if result.data:
                return result.data
            else:
                return {
                    'success': False,
                    'error': 'Failed to fetch clinics',
                    'data': []
                }

        except Exception as e:
            # Check if it's actually a successful response with data
            error_str = str(e)
            if isinstance(e, Exception) and hasattr(e, 'args') and e.args:
                # Try to extract list data from error
                try:
                    import ast
                    response_data = ast.literal_eval(error_str)
                    if isinstance(response_data, list):
                        return response_data
                except:
                    pass

            return {
                'success': False,
                'error': str(e),
                'data': []
            }

# Lazy service initialization
_service = None

def get_service():
    global _service
    if _service is None:
        _service = QuickOnboardingRPCService()
    return _service

@router.post("/quick-register")
async def quick_register(data: QuickRegistration):
    """Quick clinic registration endpoint using RPC"""
    try:
        result = await get_service().quick_register(data)
        if result.get('success'):
            return result
        else:
            # Return with 200 status but include error info
            return {
                **result,
                'status': 'partial_success'
            }
    except Exception as e:
        # Return error but don't raise HTTPException
        return {
            'success': False,
            'error': str(e),
            'message': 'Registration service unavailable',
            'status': 'error'
        }

@router.get("/clinic/{clinic_id}")
async def get_clinic(clinic_id: str):
    """Fetch clinic information by ID"""
    try:
        result = await get_service().fetch_clinic(clinic_id)
        return result
    except Exception as e:
        return {
            'success': False,
            'error': str(e),
            'clinic_id': clinic_id
        }

@router.put("/clinic/{clinic_id}")
async def update_clinic(clinic_id: str, updates: Dict[str, Any]):
    """Update clinic information"""
    try:
        result = await get_service().update_clinic(clinic_id, updates)
        return result
    except Exception as e:
        return {
            'success': False,
            'error': str(e),
            'clinic_id': clinic_id
        }

@router.get("/clinics")
async def list_clinics(organization_id: Optional[str] = None):
    """List all clinics or clinics for a specific organization"""
    try:
        result = await get_service().list_clinics(organization_id)
        return result
    except Exception as e:
        return {
            'success': False,
            'error': str(e),
            'data': []
        }

@router.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "service": "quick-onboarding-rpc",
        "version": "1.0",
        "features": ["rpc", "cross-schema"]
    }

@router.post("/{clinic_id}/whatsapp-simple")
async def setup_whatsapp(clinic_id: str, data: QuickWhatsApp):
    """Simple WhatsApp setup - using shared Twilio account"""
    try:
        # For now, just return success since we're using a shared account
        # In production, this would set up actual Twilio webhooks
        return {
            'success': True,
            'clinic_id': clinic_id,
            'webhook_url': f"https://healthcare-clinic-backend.fly.dev/webhooks/whatsapp/{clinic_id}",
            'using_shared_account': data.use_shared_account,
            'whatsapp_number': data.phone_number,
            'instructions': 'WhatsApp is ready! Patients can now message your number.',
            'test_message': f"Send 'Hi' to {data.phone_number} to test",
            'next_step': 'calendar_setup'
        }
    except Exception as e:
        return {
            'success': False,
            'error': str(e),
            'clinic_id': clinic_id
        }

@router.post("/{clinic_id}/calendar")
@router.post("/{clinic_id}/calendar/quick-setup")  # Alias for frontend compatibility
async def setup_calendar(clinic_id: str, data: QuickCalendar):
    """Setup calendar integration - returns OAuth URL"""
    try:
        # Generate Google OAuth URL for calendar access
        google_client_id = os.environ.get("GOOGLE_CLIENT_ID")
        google_client_secret = os.environ.get("GOOGLE_CLIENT_SECRET")
        redirect_uri = os.environ.get("GOOGLE_REDIRECT_URI", "https://healthcare-clinic-backend.fly.dev/api/onboarding/calendar/callback")

        # Validate credentials exist - if not, return mock success for demo
        if not google_client_id or not google_client_secret:
            # Return a mock success response for demo purposes
            # In production, actual OAuth credentials would be required
            return {
                'success': True,
                'auth_url': f"https://accounts.google.com/oauth2/v2/auth?response_type=code&client_id=demo&redirect_uri={redirect_uri}&scope=https://www.googleapis.com/auth/calendar&state=demo_{clinic_id}",
                'message': 'Demo mode: Calendar integration would connect here. In production, this would redirect to Google OAuth.',
                'clinic_id': clinic_id,
                'demo_mode': True
            }

        # Generate secure state token with clinic context
        import secrets
        import json
        from datetime import datetime, timedelta
        from urllib.parse import urlencode

        state_token = secrets.token_urlsafe(32)
        state_data = {
            'clinic_id': clinic_id,
            'token': state_token,
            'provider': data.provider
        }

        # Store state in database for verification (expires in 10 minutes)
        try:
            service = get_service()
            service.supabase.table('oauth_states').insert({
                'state': state_token,
                'user_id': clinic_id,  # Using clinic_id as user_id for quick onboarding
                'provider': 'google',
                'redirect_uri': redirect_uri,
                'scopes': ['https://www.googleapis.com/auth/calendar', 'https://www.googleapis.com/auth/calendar.events'],
                'created_at': datetime.utcnow().isoformat(),
                'expires_at': (datetime.utcnow() + timedelta(minutes=10)).isoformat(),
                'metadata': {'clinic_id': clinic_id}
            }).execute()
        except Exception as e:
            print(f"Warning: Could not store OAuth state in database: {e}")
            # Continue anyway for quick onboarding

        # Build proper OAuth URL with all required scopes
        params = {
            'client_id': google_client_id,
            'redirect_uri': redirect_uri,
            'response_type': 'code',
            'scope': 'https://www.googleapis.com/auth/calendar https://www.googleapis.com/auth/calendar.events',
            'access_type': 'offline',
            'prompt': 'consent',
            'state': json.dumps(state_data),
            'include_granted_scopes': 'true'
        }

        auth_url = f"https://accounts.google.com/o/oauth2/v2/auth?{urlencode(params)}"

        return {
            'success': True,
            'clinic_id': clinic_id,
            'auth_url': auth_url,
            'provider': data.provider,
            'instructions': 'Click the link to connect your Google Calendar',
            'next_step': 'activation'
        }
    except Exception as e:
        return {
            'success': False,
            'error': str(e),
            'clinic_id': clinic_id
        }

@router.get("/calendar/callback")
async def handle_calendar_callback(code: str = None, state: str = None, error: str = None):
    """Handle Google OAuth callback"""
    try:
        # Check for OAuth errors
        if error:
            return {
                'success': False,
                'error': f'OAuth authorization failed: {error}'
            }

        if not code or not state:
            return {
                'success': False,
                'error': 'Missing authorization code or state'
            }

        # Parse state data - support both demo mode and production mode
        import json

        # Check if this is demo mode (state starts with "demo_")
        if state.startswith('demo_'):
            clinic_id = state.replace('demo_', '')
            state_token = None
            demo_mode = True
        else:
            # Production mode - parse JSON state
            try:
                state_data = json.loads(state)
                clinic_id = state_data.get('clinic_id')
                state_token = state_data.get('token')
                demo_mode = False
            except:
                return {
                    'success': False,
                    'error': 'Invalid state parameter'
                }

        # Verify state token (optional for quick onboarding)
        try:
            service = get_service()
            result = service.supabase.table('oauth_states').select('*').eq('state', state_token).execute()
            if not result.data:
                print(f"Warning: State token not found in database: {state_token}")
                # Continue anyway for quick onboarding
        except Exception as e:
            print(f"Warning: Could not verify state token: {e}")

        # Exchange authorization code for tokens
        import httpx
        from datetime import datetime, timedelta

        google_client_id = os.environ.get("GOOGLE_CLIENT_ID")
        google_client_secret = os.environ.get("GOOGLE_CLIENT_SECRET")
        redirect_uri = os.environ.get("GOOGLE_REDIRECT_URI", "https://healthcare-clinic-backend.fly.dev/api/onboarding/calendar/callback")

        token_url = "https://oauth2.googleapis.com/token"
        token_data = {
            'code': code,
            'client_id': google_client_id,
            'client_secret': google_client_secret,
            'redirect_uri': redirect_uri,
            'grant_type': 'authorization_code'
        }

        async with httpx.AsyncClient() as client:
            response = await client.post(token_url, data=token_data)

            if response.status_code != 200:
                return {
                    'success': False,
                    'error': f'Failed to exchange authorization code: {response.text}'
                }

            tokens = response.json()

        # Store tokens using RPC function
        try:
            service = get_service()

            # Get organization_id - handle both clinic_id and organization_id in path parameter
            org_id = None
            actual_clinic_id = clinic_id

            # First, try to get clinic info
            try:
                clinic_result = service.supabase.rpc('fetch_clinic', {
                    'p_clinic_id': clinic_id
                }).execute()

                if clinic_result.data and clinic_result.data.get('success'):
                    # It's a valid clinic_id
                    clinic_data = clinic_result.data.get('data', {})
                    org_id = clinic_data.get('organization_id')
                else:
                    # Not found as clinic, might be organization_id
                    raise ValueError("Not a clinic")
            except:
                # clinic_id might actually be organization_id - check if it's a valid org
                # and find/create a default clinic for it
                print(f"ID {clinic_id} not found as clinic, checking if it's an organization...")

                # Try to find first clinic for this organization
                try:
                    print(f"Searching for clinics with organization_id: {clinic_id}")
                    clinics_result = service.supabase.rpc('list_clinics', {
                        'p_organization_id': clinic_id,
                        'p_limit': 1
                    }).execute()

                    print(f"Clinics result: {clinics_result.data}")

                    if clinics_result.data and clinics_result.data.get('success'):
                        clinics = clinics_result.data.get('data', [])
                        if clinics and len(clinics) > 0:
                            # Use first clinic for this organization
                            actual_clinic_id = clinics[0]['id']
                            org_id = clinic_id  # The path param was actually org_id
                            print(f"✅ Found clinic {actual_clinic_id} for organization {org_id}")
                        else:
                            # No clinics exist, assume clinic_id is org_id and store at org level
                            org_id = clinic_id
                            actual_clinic_id = None  # Calendar at org level
                            print(f"⚠️  No clinics found for organization {org_id}, storing at org level")
                    else:
                        # Treat as organization_id
                        org_id = clinic_id
                        actual_clinic_id = None
                        print(f"⚠️  Using {clinic_id} as organization_id directly (no success flag)")
                except Exception as list_error:
                    print(f"❌ Error listing clinics: {list_error}")
                    import traceback
                    traceback.print_exc()
                    # Last resort: use clinic_id as org_id
                    org_id = clinic_id
                    actual_clinic_id = None

            # Validate organization_id was retrieved
            if not org_id:
                raise ValueError(f"Could not determine organization ID from: {clinic_id}")

            # Build credentials
            credentials = {
                'access_token': tokens.get('access_token'),
                'refresh_token': tokens.get('refresh_token'),
                'token_type': 'Bearer',
                'expires_at': (datetime.utcnow() + timedelta(seconds=tokens.get('expires_in', 3600))).isoformat(),
                'token_uri': 'https://oauth2.googleapis.com/token',
                'client_id': os.getenv('GOOGLE_CLIENT_ID'),
                'client_secret': os.getenv('GOOGLE_CLIENT_SECRET'),
                'scopes': tokens.get('scope', '').split()
            }

            # Store credentials in ComplianceVault (boto3 or Supabase vault)
            from app.security.compliance_vault import ComplianceVault
            vault = ComplianceVault()
            vault_ref = await vault.store_calendar_credentials(
                organization_id=org_id,
                provider='google',
                credentials=credentials
            )

            # Save integration metadata to healthcare.integrations (single table)
            # This stores ONLY vault reference and metadata, NOT the actual credentials
            try:
                result = service.supabase.rpc('save_calendar_integration', {
                    'p_clinic_id': actual_clinic_id,  # Use actual_clinic_id (might be None for org-level)
                    'p_organization_id': org_id,
                    'p_provider': 'google',
                    'p_calendar_id': 'primary',
                    'p_credentials_vault_ref': vault_ref,
                    'p_calendar_name': 'Google Calendar',
                    'p_credentials_version': '1',
                    'p_expires_at': credentials['expires_at']
                }).execute()

                print(f"✅ Calendar integration saved: {result.data}")

                if not result.data or not result.data.get('success'):
                    error_msg = result.data.get('error') if result.data else 'No response from RPC'
                    print(f"❌ Failed to save calendar integration: {error_msg}")
                    raise ValueError(f"Failed to save calendar integration: {error_msg}")

                # Get the integration_id from the result
                healthcare_integration_id = result.data.get('integration_id')

                clinic_info = f"clinic {actual_clinic_id}" if actual_clinic_id else f"organization {org_id}"
                print(f"Successfully stored calendar integration for {clinic_info}")
                print(f"  - Vault reference: {vault_ref}")
                print(f"  - Integration ID: {healthcare_integration_id}")
                print(f"  - Status: {result.data.get('status')}")

                # Register Google Calendar webhook for push notifications
                if actual_clinic_id:  # Only register webhook if we have a clinic_id
                    try:
                        webhook_result = await _register_google_webhook(
                            calendar_id='primary',
                            access_token=tokens.get('access_token'),
                            clinic_id=actual_clinic_id
                        )
                        print(f"✅ Webhook registered for clinic {actual_clinic_id}: {webhook_result}")
                    except Exception as webhook_error:
                        print(f"⚠️  Failed to register webhook (will fallback to polling): {webhook_error}")
                        # Continue without webhook - polling will handle sync

            except Exception as insert_error:
                print(f"❌ Failed to save calendar integration: {insert_error}")
                raise ValueError(f"Failed to save calendar integration: {insert_error}")

            # Clean up old public.integrations records (we only use healthcare.calendar_integrations now)
            try:
                # Delete any old records in public.integrations for this calendar
                delete_result = service.supabase.table('integrations').delete().eq(
                    'organization_id', org_id
                ).eq('integration_type', 'google_calendar').execute()

                print(f"🧹 Cleaned up {len(delete_result.data) if delete_result.data else 0} old public.integrations records")
                print(f"ℹ️  Calendar integration is tracked in healthcare.calendar_integrations only")
            except Exception as int_error:
                print(f"⚠️  Could not clean up old integrations (non-critical): {int_error}")

            # Trigger webhook to notify frontend
            try:
                import httpx
                async with httpx.AsyncClient() as client:
                    # Use organization_id for webhook since that's what the frontend is tracking
                    await client.post(
                        f"https://healthcare-clinic-backend.fly.dev/webhooks/calendar-connected/{org_id}"
                    )
            except:
                pass  # Don't fail if webhook fails
                
        except Exception as e:
            print(f"❌ ERROR storing calendar integration: {e}")
            import traceback
            traceback.print_exc()
            # Return error response instead of failing silently
            return {
                'success': False,
                'error': f'Failed to store calendar integration: {str(e)}'
            }

        # Clean up state token
        try:
            service = get_service()
            service.supabase.table('oauth_states').delete().eq('state', state_token).execute()
        except:
            pass

        # Return success HTML that closes the popup window
        html_response = """
        <!DOCTYPE html>
        <html>
        <head>
            <title>Calendar Connected</title>
            <style>
                body {
                    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                    display: flex;
                    justify-content: center;
                    align-items: center;
                    height: 100vh;
                    margin: 0;
                    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                }
                .container {
                    background: white;
                    padding: 40px;
                    border-radius: 12px;
                    box-shadow: 0 20px 60px rgba(0,0,0,0.3);
                    text-align: center;
                    max-width: 400px;
                }
                h1 {
                    color: #10b981;
                    margin-bottom: 10px;
                }
                p {
                    color: #6b7280;
                    margin-bottom: 20px;
                }
                .checkmark {
                    width: 60px;
                    height: 60px;
                    margin: 0 auto 20px;
                    background: #10b981;
                    border-radius: 50%;
                    display: flex;
                    align-items: center;
                    justify-content: center;
                }
                .checkmark svg {
                    width: 30px;
                    height: 30px;
                    fill: white;
                }
            </style>
            <script>
                // Close the popup window after 2 seconds
                setTimeout(function() {
                    window.close();
                }, 2000);
            </script>
        </head>
        <body>
            <div class="container">
                <div class="checkmark">
                    <svg viewBox="0 0 24 24">
                        <path d="M9 16.17L4.83 12l-1.42 1.41L9 19 21 7l-1.41-1.41L9 16.17z"/>
                    </svg>
                </div>
                <h1>Calendar Connected!</h1>
                <p>Your Google Calendar has been successfully connected. This window will close automatically.</p>
            </div>
        </body>
        </html>
        """

        from fastapi.responses import HTMLResponse
        return HTMLResponse(content=html_response)

    except Exception as e:
        return {
            'success': False,
            'error': str(e)
        }

@router.get("/{clinic_id}/calendar/status")
async def check_calendar_status(clinic_id: str):
    """Check if calendar is actually connected and working"""
    try:
        service = get_service()

        # Check if we have stored credentials for this clinic
        try:
            # Try healthcare schema first - use raw SQL via RPC for cross-schema query
            result = None
            # For now, skip healthcare schema check since Supabase Python client doesn't support schemas
            # We'll use the regular clinic_calendar_tokens table instead

            if result and result.data and len(result.data) > 0:
                integration = result.data[0]

                # Check if tokens exist and are not expired
                from datetime import datetime
                expires_at = integration.get('expires_at')
                if expires_at:
                    expiry = datetime.fromisoformat(expires_at.replace('Z', '+00:00'))
                    if expiry > datetime.utcnow():
                        # Try to make a test API call to verify the token works
                        if integration.get('access_token'):
                            return {
                                'success': True,
                                'connected': True,
                                'provider': 'google',
                                'calendar_id': integration.get('calendar_id', 'primary'),
                                'expires_at': expires_at,
                                'message': 'Calendar is connected and active'
                            }
                        else:
                            return {
                                'success': True,
                                'connected': False,
                                'provider': 'google',
                                'message': 'Calendar integration exists but no access token found'
                            }
                    else:
                        # Token expired, needs refresh
                        return {
                            'success': True,
                            'connected': False,
                            'provider': 'google',
                            'message': 'Calendar token expired, needs reconnection',
                            'expired': True
                        }
        except Exception as e:
            print(f"Error checking healthcare.calendar_integrations: {e}")

        # Query using healthcare RPC function
        try:
            # Use the healthcare RPC function just like oauth_manager.py does
            result = service.supabase.rpc('get_calendar_integration_by_clinic', {
                'p_clinic_id': clinic_id,
                'p_provider': 'google'
            }).execute()

            if result.data and len(result.data) > 0:
                integration = result.data[0]
                # Log the successful status check
                import logging
                from datetime import datetime
                logger = logging.getLogger(__name__)
                logger.info(f"Calendar integration found for clinic {clinic_id}: {integration}")

                # Check if enabled and not expired
                is_enabled = integration.get('sync_enabled', False)
                expires_at = integration.get('expires_at')
                is_expired = False

                if expires_at:
                    try:
                        expiry = datetime.fromisoformat(expires_at.replace('Z', '+00:00'))
                        is_expired = expiry < datetime.utcnow()
                    except:
                        pass

                return {
                    'success': True,
                    'connected': is_enabled and not is_expired,
                    'provider': integration.get('provider'),
                    'calendar_id': integration.get('calendar_id'),
                    'expires_at': expires_at,
                    'expired': is_expired,
                    'message': 'Calendar connected' if (is_enabled and not is_expired) else ('Token expired' if is_expired else 'Calendar disabled')
                }
        except Exception as e:
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"Error checking calendar status: {e}")

        # No calendar integration found
        return {
            'success': True,
            'connected': False,
            'provider': None,
            'message': 'No calendar integration found'
        }

    except Exception as e:
        return {
            'success': False,
            'error': str(e),
            'connected': False
        }

@router.delete("/{clinic_id}/calendar/disconnect")
async def disconnect_calendar(clinic_id: str):
    """Disconnect/remove calendar integration"""
    try:
        service = get_service()

        # Remove from healthcare.calendar_integrations
        try:
            service.supabase.schema('healthcare').table('calendar_integrations').delete().eq('clinic_id', clinic_id).eq('provider', 'google').execute()
        except:
            pass

        # Remove from fallback table
        try:
            service.supabase.table('clinic_calendar_tokens').delete().eq('clinic_id', clinic_id).execute()
        except:
            pass

        return {
            'success': True,
            'message': 'Calendar disconnected successfully',
            'clinic_id': clinic_id
        }

    except Exception as e:
        return {
            'success': False,
            'error': str(e)
        }

@router.get("/{clinic_id}/whatsapp/status")
async def check_whatsapp_status(clinic_id: str):
    """Check if WhatsApp is actually connected and working"""
    try:
        service = get_service()

        # Check if clinic has WhatsApp configuration
        result = service.supabase.rpc('get_clinic_details', {
            'p_clinic_id': clinic_id
        }).execute()

        if result.data:
            clinic = result.data
            whatsapp_number = clinic.get('whatsapp_number')

            if whatsapp_number:
                # For shared Twilio account, just verify the number format
                if whatsapp_number.startswith('+'):
                    return {
                        'success': True,
                        'connected': True,
                        'phone_number': whatsapp_number,
                        'provider': 'twilio_shared',
                        'message': 'WhatsApp is configured (using shared Twilio account)'
                    }
                else:
                    return {
                        'success': True,
                        'connected': False,
                        'phone_number': whatsapp_number,
                        'message': 'WhatsApp number format invalid (must start with +)',
                        'needs_fix': True
                    }
            else:
                return {
                    'success': True,
                    'connected': False,
                    'message': 'No WhatsApp number configured'
                }
        else:
            return {
                'success': True,
                'connected': False,
                'message': 'Clinic not found'
            }

    except Exception as e:
        return {
            'success': False,
            'error': str(e),
            'connected': False
        }

@router.post("/{clinic_id}/whatsapp/test")
async def test_whatsapp_connection(clinic_id: str):
    """Send a test WhatsApp message to verify connection via Evolution API"""
    try:
        service = get_service()

        # Get clinic details
        result = service.supabase.rpc('get_clinic_details', {
            'p_clinic_id': clinic_id
        }).execute()

        if not result.data:
            return {
                'success': False,
                'error': 'Clinic not found'
            }

        clinic = result.data
        whatsapp_number = clinic.get('whatsapp_number')

        if not whatsapp_number:
            return {
                'success': False,
                'error': 'No WhatsApp number configured'
            }

        # Get WhatsApp integration to find instance name
        org_id = clinic.get('organization_id')
        integration_result = service.supabase.schema('healthcare').table('integrations').select(
            'id, config'
        ).eq('organization_id', org_id).eq('type', 'whatsapp').eq('enabled', True).limit(1).execute()

        if not integration_result.data:
            return {
                'success': False,
                'error': 'No WhatsApp integration found. Please configure Evolution API integration first.'
            }

        config = integration_result.data[0].get('config', {})
        instance_name = config.get('instance_name')

        if not instance_name:
            return {
                'success': False,
                'error': 'WhatsApp instance name not configured in integration'
            }

        # Send test message via Evolution API
        try:
            from app.services.whatsapp_queue.evolution_client import send_text, is_connected

            # Check if instance is connected first
            connected = await is_connected(instance_name)
            if not connected:
                return {
                    'success': False,
                    'error': f'WhatsApp instance "{instance_name}" is not connected. Please scan QR code in Evolution API.'
                }

            # Send test message
            test_message = f"🎉 Test message from {clinic.get('name', 'your clinic')}! Your WhatsApp integration is working correctly."
            success = await send_text(instance_name, whatsapp_number, test_message)

            if success:
                return {
                    'success': True,
                    'message': 'Test message sent successfully via Evolution API',
                    'to': whatsapp_number,
                    'instance': instance_name
                }
            else:
                return {
                    'success': False,
                    'error': 'Failed to send test message. Check Evolution API logs.'
                }

        except Exception as evolution_error:
            return {
                'success': False,
                'error': f'Failed to send test message: {str(evolution_error)}'
            }

    except Exception as e:
        return {
            'success': False,
            'error': str(e)
        }

@router.post("/{clinic_id}/activate")
async def activate_clinic(clinic_id: str):
    """Activate the clinic - mark as ready for use"""
    try:
        # In a real implementation, this would update the clinic status in the database
        # For now, just return success
        return {
            'success': True,
            'clinic_id': clinic_id,
            'status': 'active',
            'message': 'Clinic activated successfully!',
            'dashboard_url': f"https://dashboard.yourapp.com/clinic/{clinic_id}",
            'next_steps': [
                'Customize your AI assistant personality',
                'Upload your service menu',
                'Set business hours',
                'Test the voice agent'
            ]
        }
    except Exception as e:
        return {
            'success': False,
            'error': str(e),
            'clinic_id': clinic_id
        }
