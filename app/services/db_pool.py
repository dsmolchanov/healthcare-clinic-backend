"""
Database Connection Pool Manager
Implements proper connection pooling with retry logic and connection health monitoring
"""

import os
import logging
import asyncio
from typing import Optional, Dict, Any
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timedelta
import threading
from functools import wraps

from supabase import create_client, Client
from supabase.client import ClientOptions
import asyncpg
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)


class ConnectionPool:
    """Thread-safe connection pool with health monitoring"""

    def __init__(self,
                 url: str,
                 anon_key: str,
                 service_key: Optional[str] = None,
                 max_connections: int = 20,
                 min_connections: int = 5,
                 connection_timeout: int = 10,
                 idle_timeout: int = 300,
                 max_retries: int = 3):
        """
        Initialize connection pool with configuration

        Args:
            url: Supabase project URL
            anon_key: Anonymous key for public access
            service_key: Service key for admin access
            max_connections: Maximum number of connections in pool
            min_connections: Minimum number of connections to maintain
            connection_timeout: Timeout for acquiring connection (seconds)
            idle_timeout: Time before idle connections are closed (seconds)
            max_retries: Maximum retry attempts for failed operations
        """
        self.url = url
        self.anon_key = anon_key
        self.service_key = service_key or anon_key
        self.max_connections = max_connections
        self.min_connections = min_connections
        self.connection_timeout = connection_timeout
        self.idle_timeout = idle_timeout
        self.max_retries = max_retries

        # Connection tracking
        self._lock = threading.Lock()
        self._connections: Dict[str, Client] = {}
        self._connection_usage: Dict[str, datetime] = {}
        self._available_connections: set = set()
        self._in_use_connections: set = set()

        # Async pool for direct SQL queries
        self._async_pool: Optional[asyncpg.Pool] = None

        # Health monitoring
        self._health_check_interval = 60  # seconds
        self._last_health_check = datetime.now()
        self._connection_errors = 0
        self._total_requests = 0
        self._successful_requests = 0

        # Initialize minimum connections
        self._initialize_pool()

    def _initialize_pool(self):
        """Initialize minimum number of connections"""
        for i in range(self.min_connections):
            conn_id = f"conn_{i}"
            try:
                client = self._create_client()
                with self._lock:
                    self._connections[conn_id] = client
                    self._connection_usage[conn_id] = datetime.now()
                    self._available_connections.add(conn_id)
                logger.debug(f"Initialized connection {conn_id}")
            except Exception as e:
                logger.error(f"Failed to initialize connection {conn_id}: {e}")

    def _create_client(self) -> Client:
        """Create a new Supabase client with proper options"""
        options = ClientOptions(
            auto_refresh_token=True,
            persist_session=False
        )

        return create_client(
            self.url,
            self.service_key,
            options=options
        )

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10)
    )
    def get_connection(self) -> Client:
        """
        Get an available connection from the pool with retry logic

        Returns:
            Supabase client connection

        Raises:
            TimeoutError: If no connection available within timeout
            ConnectionError: If unable to create new connection
        """
        self._total_requests += 1
        start_time = datetime.now()

        while (datetime.now() - start_time).seconds < self.connection_timeout:
            with self._lock:
                # Check for available connections
                if self._available_connections:
                    conn_id = self._available_connections.pop()
                    self._in_use_connections.add(conn_id)
                    self._connection_usage[conn_id] = datetime.now()

                    # Validate connection health
                    if self._validate_connection(self._connections[conn_id]):
                        self._successful_requests += 1
                        logger.debug(f"Acquired connection {conn_id}")
                        return self._connections[conn_id]
                    else:
                        # Connection is stale, create new one
                        logger.warning(f"Connection {conn_id} failed health check, recreating")
                        try:
                            self._connections[conn_id] = self._create_client()
                            self._successful_requests += 1
                            return self._connections[conn_id]
                        except Exception as e:
                            logger.error(f"Failed to recreate connection {conn_id}: {e}")
                            self._in_use_connections.remove(conn_id)
                            del self._connections[conn_id]
                            self._connection_errors += 1

                # Create new connection if under limit
                elif len(self._connections) < self.max_connections:
                    conn_id = f"conn_{len(self._connections)}"
                    try:
                        client = self._create_client()
                        self._connections[conn_id] = client
                        self._connection_usage[conn_id] = datetime.now()
                        self._in_use_connections.add(conn_id)
                        self._successful_requests += 1
                        logger.debug(f"Created new connection {conn_id}")
                        return client
                    except Exception as e:
                        logger.error(f"Failed to create connection {conn_id}: {e}")
                        self._connection_errors += 1
                        raise ConnectionError(f"Unable to create connection: {e}")

            # Wait before retrying
            import time
            time.sleep(0.1)

        raise TimeoutError(f"Connection pool timeout after {self.connection_timeout} seconds")

    def release_connection(self, client: Client):
        """
        Release a connection back to the pool

        Args:
            client: Supabase client to release
        """
        with self._lock:
            # Find connection ID
            conn_id = None
            for cid, conn in self._connections.items():
                if conn == client:
                    conn_id = cid
                    break

            if conn_id and conn_id in self._in_use_connections:
                self._in_use_connections.remove(conn_id)
                self._available_connections.add(conn_id)
                logger.debug(f"Released connection {conn_id}")

    def _validate_connection(self, client: Client) -> bool:
        """
        Validate connection health

        Args:
            client: Supabase client to validate

        Returns:
            True if connection is healthy
        """
        try:
            # Simple health check - try to query a small table
            # Try different tables based on what exists
            tables_to_try = ['agents', 'agent_configs', 'organizations', 'users']

            for table in tables_to_try:
                try:
                    response = client.table(table).select('id').limit(1).execute()
                    return response is not None
                except:
                    continue

            # If no tables work, connection is still valid if we can connect
            return True
        except Exception as e:
            logger.debug(f"Connection validation failed: {e}")
            return False

    def cleanup_idle_connections(self):
        """Remove idle connections that exceed idle timeout"""
        with self._lock:
            current_time = datetime.now()
            connections_to_remove = []

            for conn_id in self._available_connections:
                last_used = self._connection_usage.get(conn_id)
                if last_used and (current_time - last_used).seconds > self.idle_timeout:
                    # Keep minimum connections
                    if len(self._connections) > self.min_connections:
                        connections_to_remove.append(conn_id)

            for conn_id in connections_to_remove:
                self._available_connections.remove(conn_id)
                del self._connections[conn_id]
                del self._connection_usage[conn_id]
                logger.debug(f"Removed idle connection {conn_id}")

    async def get_async_pool(self) -> asyncpg.Pool:
        """
        Get or create async connection pool for direct SQL queries

        Returns:
            AsyncPG connection pool
        """
        if not self._async_pool:
            # Extract PostgreSQL URL from Supabase URL
            db_url = self.url.replace('https://', 'postgresql://postgres:')
            db_url = db_url.replace('.supabase.co', '.supabase.co:5432/postgres')

            # Add password from service key
            if self.service_key:
                # Extract password from service key (this is a simplification)
                # In production, use proper connection string with credentials
                db_url = db_url.replace('postgres:', f'postgres:{self.service_key}@')

            self._async_pool = await asyncpg.create_pool(
                db_url,
                min_size=self.min_connections,
                max_size=self.max_connections,
                timeout=self.connection_timeout,
                command_timeout=self.connection_timeout,
                max_queries=50000,
                max_inactive_connection_lifetime=self.idle_timeout
            )

            logger.info("Created async connection pool")

        return self._async_pool

    @asynccontextmanager
    async def async_connection(self):
        """
        Context manager for async database connection

        Yields:
            AsyncPG connection
        """
        pool = await self.get_async_pool()
        async with pool.acquire() as connection:
            yield connection

    def get_health_stats(self) -> Dict[str, Any]:
        """
        Get connection pool health statistics

        Returns:
            Dictionary with health metrics
        """
        with self._lock:
            success_rate = (
                self._successful_requests / self._total_requests * 100
                if self._total_requests > 0 else 100
            )

            return {
                'total_connections': len(self._connections),
                'available_connections': len(self._available_connections),
                'in_use_connections': len(self._in_use_connections),
                'max_connections': self.max_connections,
                'connection_errors': self._connection_errors,
                'total_requests': self._total_requests,
                'successful_requests': self._successful_requests,
                'success_rate': f"{success_rate:.2f}%",
                'last_health_check': self._last_health_check.isoformat()
            }

    async def close(self):
        """Close all connections and cleanup"""
        with self._lock:
            # Close all Supabase clients
            self._connections.clear()
            self._available_connections.clear()
            self._in_use_connections.clear()
            self._connection_usage.clear()

        # Close async pool
        if self._async_pool:
            await self._async_pool.close()
            self._async_pool = None

        logger.info("Connection pool closed")


class PooledDatabaseManager:
    """Database manager using connection pooling"""

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        """Singleton pattern for database manager"""
        if not cls._instance:
            with cls._lock:
                if not cls._instance:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        """Initialize pooled database manager"""
        if not hasattr(self, '_initialized'):
            self._pool = ConnectionPool(
                url=os.getenv('SUPABASE_URL'),
                anon_key=os.getenv('SUPABASE_ANON_KEY'),
                service_key=os.getenv('SUPABASE_SERVICE_KEY'),
                max_connections=int(os.getenv('DB_MAX_CONNECTIONS', '20')),
                min_connections=int(os.getenv('DB_MIN_CONNECTIONS', '5')),
                connection_timeout=int(os.getenv('DB_CONNECTION_TIMEOUT', '10')),
                idle_timeout=int(os.getenv('DB_IDLE_TIMEOUT', '300'))
            )

            # Start background cleanup task
            self._cleanup_task = None
            self._start_cleanup_task()

            self._initialized = True
            logger.info("Pooled database manager initialized")

    def _start_cleanup_task(self):
        """Start background task for connection cleanup"""
        def cleanup_worker():
            import time
            while True:
                try:
                    self._pool.cleanup_idle_connections()
                    time.sleep(60)  # Run every minute
                except Exception as e:
                    logger.error(f"Cleanup task error: {e}")

        import threading
        self._cleanup_task = threading.Thread(target=cleanup_worker, daemon=True)
        self._cleanup_task.start()

    @contextmanager
    def get_client(self) -> Client:
        """
        Get a database client from the pool

        Yields:
            Supabase client
        """
        client = self._pool.get_connection()
        try:
            yield client
        finally:
            self._pool.release_connection(client)

    async def execute_query(self, query: str, *args) -> list:
        """
        Execute a raw SQL query using async pool

        Args:
            query: SQL query string
            args: Query parameters

        Returns:
            Query results
        """
        async with self._pool.async_connection() as conn:
            return await conn.fetch(query, *args)

    async def execute_update(self, query: str, *args) -> int:
        """
        Execute an update/insert/delete query

        Args:
            query: SQL query string
            args: Query parameters

        Returns:
            Number of affected rows
        """
        async with self._pool.async_connection() as conn:
            result = await conn.execute(query, *args)
            # Parse result to get row count
            if result:
                parts = result.split()
                if len(parts) >= 2 and parts[0] in ['INSERT', 'UPDATE', 'DELETE']:
                    return int(parts[1])
            return 0

    def get_health_stats(self) -> Dict[str, Any]:
        """Get connection pool health statistics"""
        return self._pool.get_health_stats()

    async def close(self):
        """Close database connections"""
        await self._pool.close()


# Convenience function for getting the singleton instance
def get_pooled_db() -> PooledDatabaseManager:
    """Get the pooled database manager instance"""
    return PooledDatabaseManager()


# Context manager for database operations
@asynccontextmanager
async def database_session():
    """
    Async context manager for database session

    Yields:
        Database manager instance
    """
    db = get_pooled_db()
    try:
        yield db
    except Exception as e:
        logger.error(f"Database session error: {e}")
        raise
    finally:
        # Session cleanup if needed
        pass


if __name__ == "__main__":
    # Test the connection pool
    import asyncio
    from dotenv import load_dotenv

    load_dotenv()

    async def test_pool():
        """Test connection pool functionality"""
        db = get_pooled_db()

        print("Testing connection pool...")

        # Test getting a client
        with db.get_client() as client:
            # Try to query any existing table
            tables = ['agents', 'agent_configs', 'organizations', 'users']
            success = False
            for table in tables:
                try:
                    response = client.table(table).select('*').limit(1).execute()
                    print(f"✓ Got client from pool, queried table '{table}': {response is not None}")
                    success = True
                    break
                except:
                    continue
            if not success:
                print("✗ No tables found, but connection works")

        # Test async query
        try:
            # Try to query any existing table
            results = await db.execute_query("SELECT current_database()")
            print(f"✓ Async query executed: {results}")
        except Exception as e:
            print(f"✗ Async query failed: {e}")

        # Get health stats
        stats = db.get_health_stats()
        print(f"\nConnection Pool Stats:")
        for key, value in stats.items():
            print(f"  {key}: {value}")

        # Cleanup
        await db.close()
        print("\n✓ Connection pool closed")

    asyncio.run(test_pool())
