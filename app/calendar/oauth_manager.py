"""
OAuth Manager for Calendar Integrations
Handles Google Calendar and Microsoft Outlook OAuth flows
"""

import os
import json
import secrets
import logging
import asyncio
from typing import Dict, Optional, Any
from datetime import datetime, timedelta
import uuid
from urllib.parse import urlencode, quote
import aiohttp
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from supabase import create_client, Client
from supabase.client import ClientOptions

from ..security.compliance_vault import ComplianceVault

logger = logging.getLogger(__name__)


class CalendarOAuthManager:
    """
    Manages OAuth flows for calendar providers
    """

    def __init__(self):
        # Initialize with healthcare schema
        options = ClientOptions(schema='healthcare')
        self.supabase: Client = create_client(
            os.environ.get("SUPABASE_URL"),
            os.environ.get("SUPABASE_SERVICE_ROLE_KEY"),
            options=options
        )

        self.vault = ComplianceVault()

        # OAuth configurations
        self.google_config = {
            'client_id': os.getenv('GOOGLE_CLIENT_ID'),
            'client_secret': os.getenv('GOOGLE_CLIENT_SECRET'),
            'redirect_uri': os.getenv('GOOGLE_REDIRECT_URI', 'http://localhost:3000/calendar/callback'),
            'auth_uri': 'https://accounts.google.com/o/oauth2/v2/auth',
            'token_uri': 'https://oauth2.googleapis.com/token',
            'scopes': [
                'https://www.googleapis.com/auth/calendar',
                'https://www.googleapis.com/auth/calendar.events'
            ]
        }

        self.outlook_config = {
            'client_id': os.environ.get('OUTLOOK_CLIENT_ID'),
            'client_secret': os.environ.get('OUTLOOK_CLIENT_SECRET'),
            'redirect_uri': os.environ.get('OUTLOOK_REDIRECT_URI', 'http://localhost:3000/calendar/callback'),
            'authority': 'https://login.microsoftonline.com/common',
            'auth_uri': 'https://login.microsoftonline.com/common/oauth2/v2.0/authorize',
            'token_uri': 'https://login.microsoftonline.com/common/oauth2/v2.0/token',
            'scopes': [
                'https://graph.microsoft.com/Calendars.ReadWrite',
                'https://graph.microsoft.com/User.Read',
                'offline_access'
            ]
        }

    async def initiate_google_oauth(
        self,
        clinic_id: str,
        doctor_id: str,
        user_id: Optional[str] = None
    ) -> str:
        """
        Initiate Google Calendar OAuth flow

        Returns:
            OAuth authorization URL
        """
        # Generate secure state token
        state = secrets.token_urlsafe(32)

        # Store state in database for verification
        self.supabase.table('oauth_states').insert({
            'state': state,
            'user_id': doctor_id,
            'provider': 'google',
            'redirect_uri': self.google_config['redirect_uri'],
            'scopes': self.google_config['scopes'],
            'created_at': datetime.utcnow().isoformat(),
            'expires_at': (datetime.utcnow() + timedelta(minutes=10)).isoformat()
        }).execute()

        # Store clinic context in state
        state_data = {
            'state': state,
            'clinic_id': clinic_id,
            'doctor_id': doctor_id,
            'user_id': user_id
        }

        # Build authorization URL
        params = {
            'client_id': self.google_config['client_id'],
            'redirect_uri': self.google_config['redirect_uri'],
            'response_type': 'code',
            'scope': ' '.join(self.google_config['scopes']),
            'state': json.dumps(state_data),
            'access_type': 'offline',
            'prompt': 'consent',
            'include_granted_scopes': 'true'
        }

        auth_url = f"{self.google_config['auth_uri']}?{urlencode(params)}"

        logger.info(f"Initiated Google OAuth for doctor {doctor_id} in clinic {clinic_id}")

        return auth_url

    async def initiate_outlook_oauth(
        self,
        clinic_id: str,
        doctor_id: str,
        user_id: Optional[str] = None
    ) -> str:
        """
        Initiate Microsoft Outlook OAuth flow

        Returns:
            OAuth authorization URL
        """
        # Generate secure state token
        state = secrets.token_urlsafe(32)

        # Store state in database
        self.supabase.table('oauth_states').insert({
            'state': state,
            'user_id': doctor_id,
            'provider': 'outlook',
            'redirect_uri': self.outlook_config['redirect_uri'],
            'scopes': self.outlook_config['scopes'],
            'created_at': datetime.utcnow().isoformat(),
            'expires_at': (datetime.utcnow() + timedelta(minutes=10)).isoformat()
        }).execute()

        # Store context in state
        state_data = {
            'state': state,
            'clinic_id': clinic_id,
            'doctor_id': doctor_id,
            'user_id': user_id
        }

        # Build authorization URL
        params = {
            'client_id': self.outlook_config['client_id'],
            'redirect_uri': self.outlook_config['redirect_uri'],
            'response_type': 'code',
            'scope': ' '.join(self.outlook_config['scopes']),
            'state': json.dumps(state_data),
            'response_mode': 'query',
            'prompt': 'consent'
        }

        auth_url = f"{self.outlook_config['auth_uri']}?{urlencode(params)}"

        logger.info(f"Initiated Outlook OAuth for doctor {doctor_id} in clinic {clinic_id}")

        return auth_url

    async def handle_google_callback(
        self,
        code: str,
        state: str
    ) -> Dict[str, Any]:
        """
        Handle Google OAuth callback

        Args:
            code: Authorization code from Google
            state: State parameter for verification

        Returns:
            Integration details
        """
        try:
            # Parse state data
            state_data = json.loads(state)

            # Verify state token
            result = self.supabase.table('oauth_states').select('*').eq(
                'state', state_data['state']
            ).eq('provider', 'google').single().execute()

            if not result.data:
                raise ValueError("Invalid or expired state token")

            oauth_state = result.data

            # Exchange code for tokens
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self.google_config['token_uri'],
                    data={
                        'code': code,
                        'client_id': self.google_config['client_id'],
                        'client_secret': self.google_config['client_secret'],
                        'redirect_uri': self.google_config['redirect_uri'],
                        'grant_type': 'authorization_code'
                    }
                ) as response:
                    if response.status != 200:
                        error_text = await response.text()
                        raise ValueError(f"Token exchange failed: {error_text}")

                    tokens = await response.json()

            # Get primary calendar ID
            calendar_id = await self._get_google_primary_calendar(tokens['access_token'])

            # Store credentials securely
            credentials_data = {
                'access_token': tokens['access_token'],
                'refresh_token': tokens.get('refresh_token'),
                'token_type': tokens.get('token_type', 'Bearer'),
                'expires_at': (
                    datetime.utcnow() + timedelta(seconds=tokens.get('expires_in', 3600))
                ).isoformat(),
                'scopes': self.google_config['scopes']
            }

            # Get organization_id for clinic
            clinic_result = self.supabase.table('healthcare.clinics').select('organization_id').eq(
                'id', state_data['clinic_id']
            ).execute()

            if not clinic_result.data:
                raise ValueError(f"Clinic not found: {state_data['clinic_id']}")

            org_id = clinic_result.data[0]['organization_id']

            # Store credentials in vault
            vault_ref = await self.vault.store_calendar_credentials(
                organization_id=org_id,
                provider='google',
                credentials=credentials_data,
                user_id=state_data.get('user_id')
            )

            # Save integration metadata to healthcare.integrations (single table)
            # Credentials are stored in vault, this only stores metadata
            result = self.supabase.rpc('save_calendar_integration', {
                'p_clinic_id': state_data['clinic_id'],
                'p_organization_id': org_id,
                'p_provider': 'google',
                'p_calendar_id': calendar_id,
                'p_credentials_vault_ref': vault_ref,
                'p_calendar_name': 'Google Calendar',
                'p_credentials_version': '1',
                'p_expires_at': credentials_data['expires_at']
            }).execute()

            if not result.data or not result.data.get('success'):
                raise ValueError(f"Failed to save calendar integration: {result.data.get('error')}")

            # Register Google Calendar webhook for push notifications
            try:
                webhook_result = await self._register_google_webhook(
                    calendar_id=calendar_id,
                    access_token=tokens['access_token'],
                    clinic_id=state_data['clinic_id']
                )
                logger.info(f"Webhook registered for clinic {state_data['clinic_id']}: {webhook_result}")
            except Exception as e:
                logger.warning(f"Failed to register webhook (will fallback to polling): {e}")
                # Continue without webhook - polling will handle sync

            # Clean up state token
            self.supabase.table('oauth_states').delete().eq(
                'state', state_data['state']
            ).execute()

            logger.info(f"Successfully integrated Google Calendar for doctor {state_data['doctor_id']}")

            # Trigger backfill sync in background
            asyncio.create_task(self._trigger_backfill_sync(state_data['clinic_id'], org_id))

            return {
                'success': True,
                'integration_id': result.data.get('integration_id'),
                'provider': 'google',
                'calendar_id': calendar_id
            }

        except Exception as e:
            logger.error(f"Google OAuth callback failed: {str(e)}")
            raise

    async def _trigger_backfill_sync(self, clinic_id: str, organization_id: str):
        """
        Trigger backfill of existing appointments after calendar OAuth success
        """
        try:
            logger.info(f"Triggering backfill sync for clinic {clinic_id}")

            # Import calendar sync API
            from app.api.calendar_sync import trigger_bulk_sync_background

            # Trigger background sync
            await trigger_bulk_sync_background(clinic_id, organization_id)

            logger.info(f"Backfill sync triggered successfully for clinic {clinic_id}")
        except Exception as e:
            logger.error(f"Failed to trigger backfill sync: {e}")
            # Don't fail OAuth flow if backfill fails

    async def _register_google_webhook(
        self,
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
                self.supabase.table('webhook_channels').insert({
                    'clinic_id': clinic_id,
                    'channel_id': channel_id,
                    'resource_id': result['resourceId'],
                    'expiration': datetime.fromtimestamp(int(result['expiration']) / 1000).isoformat()
                }).execute()

                return result

    async def handle_outlook_callback(
        self,
        code: str,
        state: str
    ) -> Dict[str, Any]:
        """
        Handle Microsoft Outlook OAuth callback

        Args:
            code: Authorization code from Microsoft
            state: State parameter for verification

        Returns:
            Integration details
        """
        try:
            # Parse state data
            state_data = json.loads(state)

            # Verify state token
            result = self.supabase.table('oauth_states').select('*').eq(
                'state', state_data['state']
            ).eq('provider', 'outlook').single().execute()

            if not result.data:
                raise ValueError("Invalid or expired state token")

            # Exchange code for tokens
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self.outlook_config['token_uri'],
                    data={
                        'code': code,
                        'client_id': self.outlook_config['client_id'],
                        'client_secret': self.outlook_config['client_secret'],
                        'redirect_uri': self.outlook_config['redirect_uri'],
                        'grant_type': 'authorization_code',
                        'scope': ' '.join(self.outlook_config['scopes'])
                    }
                ) as response:
                    if response.status != 200:
                        error_text = await response.text()
                        raise ValueError(f"Token exchange failed: {error_text}")

                    tokens = await response.json()

            # Get user's primary calendar
            calendar_id = await self._get_outlook_primary_calendar(tokens['access_token'])

            # Store credentials securely
            credentials_data = {
                'access_token': tokens['access_token'],
                'refresh_token': tokens.get('refresh_token'),
                'token_type': tokens.get('token_type', 'Bearer'),
                'expires_at': (
                    datetime.utcnow() + timedelta(seconds=tokens.get('expires_in', 3600))
                ).isoformat(),
                'scopes': self.outlook_config['scopes']
            }

            vault_ref = await self.vault.store_calendar_credentials(
                organization_id=state_data['clinic_id'],
                provider='outlook',
                credentials=credentials_data,
                user_id=state_data.get('user_id')
            )

            # Create calendar integration record
            integration_data = {
                'id': str(uuid.uuid4()),
                'doctor_id': state_data['doctor_id'],
                'clinic_id': state_data['clinic_id'],
                'provider': 'outlook',
                'calendar_id': calendar_id,
                'calendar_name': 'Primary Calendar',
                'credentials_vault_ref': vault_ref,
                'sync_enabled': True,
                'baa_signed': True,
                'consent_obtained': True,
                'created_at': datetime.utcnow().isoformat(),
                'expires_at': credentials_data['expires_at']
            }

            # Use RPC function to save calendar integration for Outlook
            result = self.supabase.rpc('save_calendar_integration', {
                'p_clinic_id': state_data['clinic_id'],
                'p_provider': 'outlook',
                'p_access_token': credentials_data['access_token'],
                'p_refresh_token': credentials_data.get('refresh_token'),
                'p_expires_at': credentials_data.get('expires_at'),
                'p_calendar_id': calendar_id,
                'p_calendar_name': 'Primary Calendar',
                'p_user_email': state_data.get('user_email'),
                'p_scope': ' '.join(credentials_data.get('scopes', []))
            }).execute()

            # Clean up state token
            self.supabase.table('oauth_states').delete().eq(
                'state', state_data['state']
            ).execute()

            logger.info(f"Successfully integrated Outlook Calendar for doctor {state_data['doctor_id']}")

            return {
                'success': True,
                'integration_id': integration_data['id'],
                'provider': 'outlook',
                'calendar_id': calendar_id
            }

        except Exception as e:
            logger.error(f"Outlook OAuth callback failed: {str(e)}")
            raise

    async def _get_google_primary_calendar(self, access_token: str) -> str:
        """Get primary calendar ID from Google"""
        try:
            credentials = Credentials(token=access_token)
            service = build('calendar', 'v3', credentials=credentials)

            # Get calendar list
            calendar_list = service.calendarList().list().execute()

            # Find primary calendar
            for calendar in calendar_list.get('items', []):
                if calendar.get('primary'):
                    return calendar['id']

            # Default to user's email
            return 'primary'

        except Exception as e:
            logger.error(f"Failed to get Google calendar: {str(e)}")
            return 'primary'

    async def _get_outlook_primary_calendar(self, access_token: str) -> str:
        """Get primary calendar ID from Outlook"""
        try:
            async with aiohttp.ClientSession() as session:
                headers = {
                    'Authorization': f'Bearer {access_token}',
                    'Content-Type': 'application/json'
                }

                # Get user's calendars
                async with session.get(
                    'https://graph.microsoft.com/v1.0/me/calendars',
                    headers=headers
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        calendars = data.get('value', [])

                        # Find default calendar
                        for calendar in calendars:
                            if calendar.get('isDefaultCalendar'):
                                return calendar['id']

                        # Return first calendar if no default
                        if calendars:
                            return calendars[0]['id']

            return 'primary'

        except Exception as e:
            logger.error(f"Failed to get Outlook calendar: {str(e)}")
            return 'primary'

    async def refresh_google_token(self, clinic_id: str, provider: str = 'google') -> bool:
        """
        Refresh expired Google OAuth token

        Args:
            clinic_id: Clinic ID
            provider: Calendar provider (default: google)

        Returns:
            Success status
        """
        try:
            # Get integration details using new RPC
            result = self.supabase.rpc('get_calendar_integration_by_clinic', {
                'p_clinic_id': clinic_id,
                'p_provider': provider
            }).execute()

            if not result.data or len(result.data) == 0:
                raise ValueError(f"Integration not found for clinic: {clinic_id}")

            integration = result.data[0]

            # Retrieve current credentials from vault
            credentials = await self.vault.retrieve_calendar_credentials(
                organization_id=integration['organization_id'],
                provider=provider
            )

            # Refresh token
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self.google_config['token_uri'],
                    data={
                        'refresh_token': credentials['refresh_token'],
                        'client_id': self.google_config['client_id'],
                        'client_secret': self.google_config['client_secret'],
                        'grant_type': 'refresh_token'
                    }
                ) as response:
                    if response.status != 200:
                        error_text = await response.text()
                        raise ValueError(f"Token refresh failed: {error_text}")

                    new_tokens = await response.json()

            # Update credentials
            credentials['access_token'] = new_tokens['access_token']
            credentials['expires_at'] = (
                datetime.utcnow() + timedelta(seconds=new_tokens.get('expires_in', 3600))
            ).isoformat()

            # Store updated credentials in vault
            new_vault_ref = await self.vault.rotate_credentials(
                organization_id=integration['organization_id'],
                provider=provider,
                new_credentials=credentials
            )

            # Update integration with new vault ref and expiry (rotates credentials)
            self.supabase.rpc('save_calendar_integration', {
                'p_clinic_id': clinic_id,
                'p_organization_id': integration['organization_id'],
                'p_provider': provider,
                'p_calendar_id': integration['calendar_id'],
                'p_credentials_vault_ref': new_vault_ref,
                'p_credentials_version': str(int(integration.get('credentials_version', '1')) + 1),
                'p_expires_at': credentials['expires_at']
            }).execute()

            logger.info(f"Successfully refreshed Google token for clinic {clinic_id}")

            return True

        except Exception as e:
            logger.error(f"Failed to refresh Google token: {str(e)}")
            return False

    async def refresh_outlook_token(self, clinic_id: str) -> bool:
        """
        Refresh expired Outlook OAuth token

        Args:
            clinic_id: Clinic ID

        Returns:
            Success status
        """
        try:
            # Get integration details using new RPC
            result = self.supabase.rpc('get_calendar_integration_by_clinic', {
                'p_clinic_id': clinic_id,
                'p_provider': 'outlook'
            }).execute()

            if not result.data or len(result.data) == 0:
                raise ValueError(f"Integration not found for clinic: {clinic_id}")

            integration = result.data[0]

            # Retrieve current credentials from vault
            credentials = await self.vault.retrieve_calendar_credentials(
                vault_ref=integration['credentials_vault_ref'],
                organization_id=integration['organization_id'],
                provider='outlook'
            )

            # Refresh token
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self.outlook_config['token_uri'],
                    data={
                        'refresh_token': credentials['refresh_token'],
                        'client_id': self.outlook_config['client_id'],
                        'client_secret': self.outlook_config['client_secret'],
                        'grant_type': 'refresh_token',
                        'scope': ' '.join(self.outlook_config['scopes'])
                    }
                ) as response:
                    if response.status != 200:
                        error_text = await response.text()
                        raise ValueError(f"Token refresh failed: {error_text}")

                    new_tokens = await response.json()

            # Update credentials
            credentials['access_token'] = new_tokens['access_token']
            if 'refresh_token' in new_tokens:
                credentials['refresh_token'] = new_tokens['refresh_token']
            credentials['expires_at'] = (
                datetime.utcnow() + timedelta(seconds=new_tokens.get('expires_in', 3600))
            ).isoformat()

            # Store updated credentials in vault
            new_vault_ref = await self.vault.rotate_credentials(
                organization_id=integration['organization_id'],
                provider='outlook',
                new_credentials=credentials
            )

            # Update integration with new vault ref and expiry (rotates credentials)
            self.supabase.rpc('save_calendar_integration', {
                'p_clinic_id': clinic_id,
                'p_organization_id': integration['organization_id'],
                'p_provider': 'outlook',
                'p_calendar_id': integration['calendar_id'],
                'p_credentials_vault_ref': new_vault_ref,
                'p_credentials_version': str(int(integration.get('credentials_version', '1')) + 1),
                'p_expires_at': credentials['expires_at']
            }).execute()

            logger.info(f"Successfully refreshed Outlook token for clinic {clinic_id}")

            return True

        except Exception as e:
            logger.error(f"Failed to refresh Outlook token: {str(e)}")
            return False
