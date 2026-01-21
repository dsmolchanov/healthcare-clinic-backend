"""
Sales OAuth Manager for Calendar Integrations

Handles Google Calendar OAuth flows for sales reps.
Mirrors the healthcare CalendarOAuthManager pattern but stores credentials
in the sales.calendar_integrations table.
"""

import os
import json
import secrets
import logging
import aiohttp
from typing import Dict, Optional, Any
from datetime import datetime, timedelta
from urllib.parse import urlencode

from supabase import create_client, Client
from supabase.client import ClientOptions

try:
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    GOOGLE_CALENDAR_AVAILABLE = True
except ImportError:
    Credentials = None
    build = None
    GOOGLE_CALENDAR_AVAILABLE = False

logger = logging.getLogger(__name__)


class SalesCalendarOAuthManager:
    """
    Manages OAuth flows for sales rep calendar integrations.
    """

    def __init__(self):
        # Initialize with sales schema for sales data
        sales_options = ClientOptions(schema='sales')
        self.supabase: Client = create_client(
            os.environ.get("SUPABASE_URL"),
            os.environ.get("SUPABASE_SERVICE_ROLE_KEY"),
            options=sales_options
        )

        # Initialize with public schema for oauth_states
        public_options = ClientOptions(schema='public')
        self.public_supabase: Client = create_client(
            os.environ.get("SUPABASE_URL"),
            os.environ.get("SUPABASE_SERVICE_ROLE_KEY"),
            options=public_options
        )

        # Google OAuth configuration
        self.google_config = {
            'client_id': os.getenv('GOOGLE_CLIENT_ID'),
            'client_secret': os.getenv('GOOGLE_CLIENT_SECRET'),
            'redirect_uri': os.getenv(
                'SALES_GOOGLE_REDIRECT_URI',
                os.getenv('GOOGLE_REDIRECT_URI', 'http://localhost:3000/calendar/callback')
            ),
            'auth_uri': 'https://accounts.google.com/o/oauth2/v2/auth',
            'token_uri': 'https://oauth2.googleapis.com/token',
            'scopes': [
                'https://www.googleapis.com/auth/calendar',
                'https://www.googleapis.com/auth/calendar.events'
            ]
        }

    async def initiate_google_oauth(
        self,
        rep_id: str,
        organization_id: str,
        user_id: Optional[str] = None
    ) -> str:
        """
        Initiate Google Calendar OAuth flow for a sales rep.

        Args:
            rep_id: Sales rep UUID
            organization_id: Organization UUID
            user_id: Optional auth.users UUID

        Returns:
            OAuth authorization URL
        """
        # Generate secure state token
        state = secrets.token_urlsafe(32)

        # Store state in database for verification (public schema)
        self.public_supabase.table('oauth_states').insert({
            'state': state,
            'user_id': rep_id,  # Using rep_id as user_id for consistency
            'rep_id': rep_id,
            'provider': 'google',
            'schema_type': 'sales',
            'redirect_uri': self.google_config['redirect_uri'],
            'scopes': self.google_config['scopes'],
            'created_at': datetime.utcnow().isoformat(),
            'expires_at': (datetime.utcnow() + timedelta(minutes=10)).isoformat()
        }).execute()

        # Store context in state JSON
        state_data = {
            'state': state,
            'rep_id': rep_id,
            'organization_id': organization_id,
            'user_id': user_id,
            'schema_type': 'sales'
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

        logger.info(f"Initiated Google OAuth for sales rep {rep_id}")

        return auth_url

    async def handle_google_callback(
        self,
        code: str,
        state: str
    ) -> Dict[str, Any]:
        """
        Handle Google OAuth callback for sales.

        Args:
            code: Authorization code from Google
            state: State parameter for verification

        Returns:
            Integration details
        """
        try:
            # Parse state data
            state_data = json.loads(state)

            # Verify this is a sales OAuth flow
            if state_data.get('schema_type') != 'sales':
                raise ValueError("Invalid OAuth flow type - expected sales")

            # Verify state token (public schema)
            result = self.public_supabase.table('oauth_states').select('*').eq(
                'state', state_data['state']
            ).eq('provider', 'google').eq('schema_type', 'sales').single().execute()

            if not result.data:
                raise ValueError("Invalid or expired state token")

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

            # Calculate token expiry
            expires_at = (
                datetime.utcnow() + timedelta(seconds=tokens.get('expires_in', 3600))
            ).isoformat()

            rep_id = state_data['rep_id']

            # Upsert calendar integration in sales schema
            integration_result = self.supabase.table('calendar_integrations').upsert({
                'rep_id': rep_id,
                'provider': 'google',
                'calendar_id': calendar_id,
                'access_token': tokens['access_token'],
                'refresh_token': tokens.get('refresh_token'),
                'token_expires_at': expires_at,
                'sync_enabled': True
            }, on_conflict='rep_id').execute()

            if not integration_result.data:
                raise ValueError("Failed to save calendar integration")

            integration_id = integration_result.data[0]['id']

            # Update rep with calendar_integration_id
            self.supabase.table('reps').update({
                'calendar_integration_id': integration_id
            }).eq('id', rep_id).execute()

            # Update notification preferences to enable google_calendar
            self.supabase.table('reps').update({
                'notification_preferences': {
                    'email': True,
                    'whatsapp': False,
                    'google_calendar': True
                }
            }).eq('id', rep_id).execute()

            # Clean up state token (public schema)
            self.public_supabase.table('oauth_states').delete().eq(
                'state', state_data['state']
            ).execute()

            logger.info(f"Successfully integrated Google Calendar for sales rep {rep_id}")

            return {
                'success': True,
                'integration_id': integration_id,
                'provider': 'google',
                'calendar_id': calendar_id,
                'rep_id': rep_id
            }

        except Exception as e:
            logger.error(f"Google OAuth callback failed: {str(e)}")
            raise

    async def _get_google_primary_calendar(self, access_token: str) -> str:
        """Get primary calendar ID from Google."""
        if not GOOGLE_CALENDAR_AVAILABLE or Credentials is None:
            return 'primary'

        try:
            credentials = Credentials(token=access_token)
            service = build('calendar', 'v3', credentials=credentials, cache_discovery=False)

            # Get calendar list
            calendar_list = service.calendarList().list().execute()

            # Find primary calendar
            for calendar in calendar_list.get('items', []):
                if calendar.get('primary'):
                    return calendar['id']

            # Default to 'primary'
            return 'primary'

        except Exception as e:
            logger.error(f"Failed to get Google calendar: {str(e)}")
            return 'primary'

    async def get_connection_status(self, rep_id: str) -> Dict[str, Any]:
        """
        Check calendar connection status for a sales rep.

        Args:
            rep_id: Sales rep UUID

        Returns:
            Connection status dict
        """
        try:
            result = self.supabase.table('calendar_integrations').select(
                'id, provider, calendar_id, token_expires_at, sync_enabled'
            ).eq('rep_id', rep_id).single().execute()

            if not result.data:
                return {
                    'connected': False,
                    'provider': None,
                    'message': 'No calendar connected'
                }

            integration = result.data
            expires_at = integration.get('token_expires_at')
            expired = False

            if expires_at:
                expires_dt = datetime.fromisoformat(expires_at.replace('Z', '+00:00'))
                expired = expires_dt < datetime.utcnow().replace(tzinfo=expires_dt.tzinfo)

            return {
                'connected': True,
                'provider': integration.get('provider'),
                'calendar_id': integration.get('calendar_id'),
                'sync_enabled': integration.get('sync_enabled'),
                'expired': expired,
                'message': 'Calendar connected' if not expired else 'Calendar connection expired'
            }

        except Exception as e:
            logger.error(f"Failed to get connection status: {e}")
            return {
                'connected': False,
                'provider': None,
                'message': f'Error checking status: {str(e)}'
            }

    async def disconnect_calendar(self, rep_id: str) -> Dict[str, Any]:
        """
        Disconnect calendar integration for a sales rep.

        Args:
            rep_id: Sales rep UUID

        Returns:
            Result dict
        """
        try:
            # Delete calendar integration
            self.supabase.table('calendar_integrations').delete().eq(
                'rep_id', rep_id
            ).execute()

            # Update rep to clear calendar_integration_id
            self.supabase.table('reps').update({
                'calendar_integration_id': None
            }).eq('id', rep_id).execute()

            # Update notification preferences to disable google_calendar
            rep_result = self.supabase.table('reps').select(
                'notification_preferences'
            ).eq('id', rep_id).single().execute()

            if rep_result.data:
                prefs = rep_result.data.get('notification_preferences', {})
                prefs['google_calendar'] = False
                self.supabase.table('reps').update({
                    'notification_preferences': prefs
                }).eq('id', rep_id).execute()

            logger.info(f"Disconnected calendar for sales rep {rep_id}")

            return {
                'success': True,
                'message': 'Calendar disconnected successfully'
            }

        except Exception as e:
            logger.error(f"Failed to disconnect calendar: {e}")
            return {
                'success': False,
                'message': f'Error disconnecting: {str(e)}'
            }

    async def refresh_google_token(self, rep_id: str) -> bool:
        """
        Refresh expired Google OAuth token for a sales rep.

        Args:
            rep_id: Sales rep UUID

        Returns:
            Success status
        """
        try:
            # Get current integration
            result = self.supabase.table('calendar_integrations').select(
                '*'
            ).eq('rep_id', rep_id).eq('provider', 'google').single().execute()

            if not result.data:
                raise ValueError(f"No Google integration found for rep: {rep_id}")

            integration = result.data
            refresh_token = integration.get('refresh_token')

            if not refresh_token:
                raise ValueError("No refresh token available")

            # Refresh token
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self.google_config['token_uri'],
                    data={
                        'refresh_token': refresh_token,
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
            expires_at = (
                datetime.utcnow() + timedelta(seconds=new_tokens.get('expires_in', 3600))
            ).isoformat()

            self.supabase.table('calendar_integrations').update({
                'access_token': new_tokens['access_token'],
                'token_expires_at': expires_at
            }).eq('rep_id', rep_id).execute()

            logger.info(f"Successfully refreshed Google token for rep {rep_id}")

            return True

        except Exception as e:
            logger.error(f"Failed to refresh Google token: {str(e)}")
            return False


# Singleton instance
_sales_oauth_manager: Optional[SalesCalendarOAuthManager] = None


def get_sales_calendar_oauth_manager() -> SalesCalendarOAuthManager:
    """Get or create global SalesCalendarOAuthManager."""
    global _sales_oauth_manager
    if _sales_oauth_manager is None:
        _sales_oauth_manager = SalesCalendarOAuthManager()
    return _sales_oauth_manager
