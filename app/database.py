"""
Canonical Supabase client module - SINGLE SOURCE OF TRUTH.

IMPORTANT: This is the ONLY module allowed to import create_client/create_async_client directly.
All other modules MUST use the helpers: get_healthcare_client(), get_main_client(), etc.

To enforce this, run: ruff check --select=TID251

NOTE: Prefer async versions (get_healthcare_client_async) for FastAPI endpoints.
Sync versions are maintained for backward compatibility during migration.
"""
import asyncio
import os
import logging
from typing import Dict, Optional
from contextlib import asynccontextmanager

import httpx
from supabase import create_client, Client
from supabase.client import ClientOptions

# Try to import async client (supabase >= 2.0)
try:
    from supabase import create_async_client, AsyncClient
    ASYNC_CLIENT_AVAILABLE = True
except ImportError:
    ASYNC_CLIENT_AVAILABLE = False
    AsyncClient = None  # type: ignore

try:
    import asyncpg
    ASYNCPG_AVAILABLE = True
except ImportError:
    ASYNCPG_AVAILABLE = False

logger = logging.getLogger(__name__)


# =============================================================================
# Schema constants
# =============================================================================

class Schema:
    """Database schema constants for explicit schema binding."""
    PUBLIC = 'public'
    HEALTHCARE = 'healthcare'
    CORE = 'core'


# =============================================================================
# Configuration
# =============================================================================

# Timeouts (configured once, used throughout)
DEFAULT_DB_TIMEOUT = 30.0  # seconds
DEFAULT_CONNECT_TIMEOUT = 10.0  # seconds


def _get_credentials() -> tuple:
    """Get Supabase credentials from environment."""
    supabase_url = os.getenv("SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_ANON_KEY")

    if not supabase_url or not supabase_key:
        raise ValueError("SUPABASE_URL and SUPABASE_ANON_KEY/SERVICE_ROLE_KEY must be set")

    return supabase_url, supabase_key


# =============================================================================
# Sync Client (for backward compatibility during migration)
# =============================================================================

# Cached sync clients per schema
_supabase_clients: Dict[str, Client] = {}


def _build_http_client() -> httpx.Client:
    """Build optimized sync HTTP client with HTTP/1.1 and tight timeouts."""
    return httpx.Client(
        http2=False,  # Use HTTP/1.1 to avoid handshake delays
        timeout=httpx.Timeout(
            connect=DEFAULT_CONNECT_TIMEOUT,
            read=DEFAULT_DB_TIMEOUT,
            write=DEFAULT_DB_TIMEOUT,
            pool=DEFAULT_DB_TIMEOUT
        ),
        limits=httpx.Limits(
            max_connections=100,
            max_keepalive_connections=20,
            keepalive_expiry=30.0
        ),
        follow_redirects=True
    )


def create_supabase_client(schema: str = Schema.HEALTHCARE) -> Client:
    """
    Create or get cached Supabase client for specified schema (SYNC version).

    DEPRECATED: Prefer get_healthcare_client_async() for FastAPI endpoints.
    This sync version blocks the event loop and should only be used in:
    - CLI scripts
    - Background workers
    - Migration during async transition

    Args:
        schema: Database schema to use ('healthcare', 'public', 'core')

    Returns:
        Configured sync Supabase client
    """
    global _supabase_clients

    if schema in _supabase_clients:
        return _supabase_clients[schema]

    supabase_url, supabase_key = _get_credentials()

    options = ClientOptions(
        schema=schema,
        auto_refresh_token=False,  # For server/service-role usage
        persist_session=False
    )

    client = create_client(supabase_url, supabase_key, options=options)

    # Apply HTTP/1.1 optimization
    try:
        http_client = _build_http_client()
        if hasattr(client, '_postgrest') and hasattr(client._postgrest, 'session'):
            client._postgrest.session = http_client
    except Exception as e:
        logger.warning(f"Could not apply HTTP optimization: {e}")

    _supabase_clients[schema] = client
    logger.info(f"Created sync Supabase client for schema: {schema}")

    return client


# Sync convenience helpers (for backward compatibility)
def get_healthcare_client() -> Client:
    """Get sync Supabase client bound to healthcare schema (PHI data).

    DEPRECATED: Prefer get_healthcare_client_async() for FastAPI endpoints.
    """
    return create_supabase_client(Schema.HEALTHCARE)


def get_main_client() -> Client:
    """Get sync Supabase client bound to public schema (non-PHI data).

    DEPRECATED: Prefer get_main_client_async() for FastAPI endpoints.
    """
    return create_supabase_client(Schema.PUBLIC)


def get_core_client() -> Client:
    """Get sync Supabase client bound to core schema (organizations, agents).

    DEPRECATED: Prefer get_core_client_async() for FastAPI endpoints.
    """
    return create_supabase_client(Schema.CORE)


# =============================================================================
# Async Client (PREFERRED for FastAPI)
# =============================================================================

# Async-safe singleton cache
_async_supabase_clients: Dict[str, "AsyncClient"] = {}
_async_client_lock = asyncio.Lock()


async def create_async_supabase_client(schema: str = Schema.HEALTHCARE) -> "AsyncClient":
    """
    Create or get cached async Supabase client for specified schema.

    This is the PREFERRED method for FastAPI endpoints - does not block event loop.

    Args:
        schema: Database schema to bind (healthcare, public, core)

    Returns:
        Cached or newly created async Supabase client
    """
    global _async_supabase_clients

    if not ASYNC_CLIENT_AVAILABLE:
        raise RuntimeError(
            "AsyncClient not available. Upgrade supabase package: pip install 'supabase>=2.0'"
        )

    async with _async_client_lock:
        if schema in _async_supabase_clients:
            return _async_supabase_clients[schema]

        supabase_url, supabase_key = _get_credentials()

        options = ClientOptions(
            schema=schema,
            auto_refresh_token=False,  # For server/service-role usage
            persist_session=False,
        )

        client = await create_async_client(supabase_url, supabase_key, options=options)

        _async_supabase_clients[schema] = client
        logger.info(f"Created async Supabase client for schema: {schema}")

        return client


# Async convenience helpers (PREFERRED)
async def get_healthcare_client_async() -> "AsyncClient":
    """Get async Supabase client for healthcare schema (PHI data).

    This is the PREFERRED method for FastAPI endpoints.
    """
    return await create_async_supabase_client(Schema.HEALTHCARE)


async def get_main_client_async() -> "AsyncClient":
    """Get async Supabase client for public schema (non-PHI data).

    This is the PREFERRED method for FastAPI endpoints.
    """
    return await create_async_supabase_client(Schema.PUBLIC)


async def get_core_client_async() -> "AsyncClient":
    """Get async Supabase client for core schema (organizations, agents).

    This is the PREFERRED method for FastAPI endpoints.
    """
    return await create_async_supabase_client(Schema.CORE)


# Legacy alias for get_supabase
async def get_supabase(schema: str = Schema.HEALTHCARE) -> Client:
    """
    Get Supabase client (legacy async wrapper around sync client).

    DEPRECATED: Use get_healthcare_client_async() instead.
    """
    return create_supabase_client(schema)


# =============================================================================
# Client Management
# =============================================================================

def get_client_stats() -> Dict:
    """Get statistics about active clients (for monitoring)."""
    return {
        "sync_clients": list(_supabase_clients.keys()),
        "async_clients": list(_async_supabase_clients.keys()),
        "total_sync": len(_supabase_clients),
        "total_async": len(_async_supabase_clients),
    }


async def close_all_clients() -> None:
    """Close all cached clients (for graceful shutdown)."""
    global _supabase_clients, _async_supabase_clients

    # Close async clients
    async with _async_client_lock:
        for schema, client in _async_supabase_clients.items():
            try:
                if hasattr(client, 'aclose'):
                    await client.aclose()
            except Exception as e:
                logger.warning(f"Error closing async client for {schema}: {e}")
        _async_supabase_clients.clear()

    # Clear sync clients (they don't have explicit close)
    _supabase_clients.clear()

    logger.info("All Supabase clients closed")


# =============================================================================
# Database Pool (asyncpg for direct SQL access)
# =============================================================================

_db_pool = None


async def init_db_pool():
    """Initialize database connection pool for direct SQL access."""
    global _db_pool

    if not ASYNCPG_AVAILABLE:
        logger.warning("asyncpg not available, direct SQL not supported")
        return None

    if _db_pool is not None:
        return _db_pool

    db_url = (
        os.getenv('SUPABASE_DB_URL') or
        os.getenv('DATABASE_URL') or
        os.getenv('DB_URL')
    )

    if not db_url:
        logger.warning("No database URL found, direct SQL not available")
        return None

    try:
        _db_pool = await asyncpg.create_pool(
            db_url,
            min_size=2,
            max_size=10,
            command_timeout=60,
            statement_cache_size=0  # Disable for pgbouncer compatibility
        )
        logger.info("Database connection pool initialized")
        return _db_pool
    except Exception as e:
        logger.error(f"Failed to initialize database pool: {e}")
        return None


async def close_db_pool():
    """Close database connection pool."""
    global _db_pool

    if _db_pool:
        await _db_pool.close()
        _db_pool = None
        logger.info("Database connection pool closed")


@asynccontextmanager
async def get_db_connection():
    """Get a database connection from the pool."""
    pool = await init_db_pool()

    if pool:
        async with pool.acquire() as connection:
            yield connection
    else:
        yield None


# =============================================================================
# Mock Database (for testing)
# =============================================================================

class MockDatabase:
    """Mock database for testing."""

    def __init__(self):
        self.data = {}

    def table(self, table_name: str):
        return MockTable(table_name, self.data)

    @asynccontextmanager
    async def transaction(self):
        yield self


class MockTable:
    """Mock table for testing."""

    def __init__(self, table_name: str, data_store: dict):
        self.table_name = table_name
        self.data_store = data_store
        if table_name not in self.data_store:
            self.data_store[table_name] = []
        self._filters = []
        self._updates = {}
        self._single = False
        self._limit = None
        self._columns = None
        self._operation = None
        self._insert_data = None

    def select(self, *columns):
        self._columns = columns
        self._operation = 'select'
        return self

    def insert(self, data: dict):
        self._operation = 'insert'
        self._insert_data = data
        return self

    def update(self, data: dict):
        self._operation = 'update'
        self._updates = data
        return self

    def delete(self):
        self._operation = 'delete'
        return self

    def eq(self, column: str, value):
        self._filters.append(('eq', column, value))
        return self

    def single(self):
        self._single = True
        return self

    def limit(self, n: int):
        self._limit = n
        return self

    async def execute(self):
        import uuid
        if self._operation == 'insert':
            if 'id' not in self._insert_data:
                self._insert_data['id'] = str(uuid.uuid4())
            self.data_store[self.table_name].append(self._insert_data)
            return MockResult(data=self._insert_data)
        elif self._operation == 'select':
            result = self.data_store.get(self.table_name, [])
            for filter_type, column, value in self._filters:
                if filter_type == 'eq':
                    result = [r for r in result if r.get(column) == value]
            if self._limit:
                result = result[:self._limit]
            if self._single:
                return MockResult(data=result[0] if result else {})
            return MockResult(data=result)
        return MockResult()


class MockResult:
    """Mock query result."""

    def __init__(self, data=None, count=None):
        self.data = data
        self.count = count


# Global mock database instance (for testing only)
db = MockDatabase()
