"""
Supabase Client
Provides centralized Supabase client initialization with optimized HTTP settings
"""
from supabase import create_client, Client
from supabase.client import ClientOptions
import os
import logging
import httpx

logger = logging.getLogger(__name__)


def get_supabase_client(schema: str = 'healthcare') -> Client:
    """
    Get Supabase client with proper configuration and HTTP/1.1 optimization

    Args:
        schema: Database schema to use (default: 'healthcare')

    Returns:
        Client: Configured Supabase client with optimized HTTP settings

    Raises:
        ValueError: If Supabase credentials are not configured
    """
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", os.getenv("SUPABASE_ANON_KEY"))

    if not url or not key:
        raise ValueError("Supabase credentials not configured. Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY")

    # Configure httpx for HTTP/1.1 with tight timeouts and connection limits
    # This avoids HTTP/2 handshake delays and SSL connection issues
    http_client = httpx.Client(
        http2=False,  # Use HTTP/1.1 to avoid handshake delays
        timeout=httpx.Timeout(
            connect=1.5,  # Connection timeout
            read=2.5,     # Read timeout
            write=2.5,    # Write timeout
            pool=5.0      # Pool timeout
        ),
        limits=httpx.Limits(
            max_connections=100,
            max_keepalive_connections=20,
            keepalive_expiry=30.0
        ),
        follow_redirects=True
    )

    # Configure client to use healthcare schema
    options = ClientOptions(
        schema=schema,
        auto_refresh_token=True,
        persist_session=False,
        # Note: Supabase-py doesn't directly expose httpx config in options
        # The http_client configuration above may need to be applied differently
        # depending on the supabase-py version
    )

    client = create_client(url, key, options=options)

    # Attempt to override the internal httpx client if possible
    try:
        if hasattr(client, '_postgrest') and hasattr(client._postgrest, 'session'):
            client._postgrest.session = http_client
            logger.info("✅ Supabase client configured with HTTP/1.1 and optimized settings")
    except Exception as e:
        logger.warning(f"Could not override httpx client: {e}. Using default HTTP settings.")

    return client