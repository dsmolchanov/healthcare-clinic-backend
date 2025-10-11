"""
Database management and client
"""

import os
from typing import Dict, Any, List, Optional
import asyncio
from contextlib import asynccontextmanager
import logging
from supabase import create_client, Client

try:
    import asyncpg
    ASYNCPG_AVAILABLE = True
except ImportError:
    ASYNCPG_AVAILABLE = False
    
logger = logging.getLogger(__name__)

# Database connection pool
_db_pool = None
_supabase_clients: Dict[str, Client] = {}


def create_supabase_client(schema: str = 'healthcare') -> Client:
    """Create a new Supabase client (sync version for compatibility)"""
    global _supabase_clients

    if schema in _supabase_clients:
        return _supabase_clients[schema]

    # Get credentials
    supabase_url = os.getenv("SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_ANON_KEY")

    if not supabase_url or not supabase_key:
        raise ValueError("SUPABASE_URL and SUPABASE_ANON_KEY/SERVICE_ROLE_KEY must be set")

    try:
        from supabase.client import ClientOptions

        # Configure client to use healthcare schema
        options = ClientOptions(
            schema=schema,
            auto_refresh_token=True,
            persist_session=False
        )

        client = create_client(supabase_url, supabase_key, options=options)
        _supabase_clients[schema] = client
        logger.info(f"Connected to Supabase: {supabase_url} (using {schema} schema)")
    except ImportError:
        # Fallback to regular client without options
        client = create_client(supabase_url, supabase_key)
        _supabase_clients[schema] = client
        logger.info(f"Connected to Supabase: {supabase_url} (using default schema)")

    return _supabase_clients[schema]


async def get_supabase(schema: str = 'healthcare') -> Client:
    """Get or create Supabase client"""
    global _supabase_clients

    if schema in _supabase_clients:
        return _supabase_clients[schema]
    
    # Get credentials
    supabase_url = os.getenv("SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_ANON_KEY")
    
    if not supabase_url or not supabase_key:
        raise ValueError("SUPABASE_URL and SUPABASE_ANON_KEY/SERVICE_ROLE_KEY must be set")
    
    try:
        from supabase.client import ClientOptions
        
        # Configure client to use healthcare schema
        options = ClientOptions(
            schema=schema,
            auto_refresh_token=True,
            persist_session=False
        )
        
        client = create_client(supabase_url, supabase_key, options=options)
        _supabase_clients[schema] = client
        logger.info(f"Connected to Supabase: {supabase_url} (using {schema} schema)")
        
    except Exception as e:
        logger.error(f"Failed to create Supabase client: {e}")
        raise
    
    return _supabase_clients[schema]

async def init_db_pool():
    """Initialize database connection pool"""
    global _db_pool
    
    if not ASYNCPG_AVAILABLE:
        logger.warning("asyncpg not available, using mock database")
        return None
        
    if _db_pool is not None:
        return _db_pool
    
    # Try multiple environment variable names
    db_url = (os.getenv('SUPABASE_DB_URL') or 
              os.getenv('DATABASE_URL') or
              os.getenv('DB_URL'))
    
    if not db_url:
        logger.warning("No database URL found in SUPABASE_DB_URL, DATABASE_URL, or DB_URL, using mock database")
        return None
    
    try:
        _db_pool = await asyncpg.create_pool(
            db_url,
            min_size=2,
            max_size=10,
            command_timeout=60,
            statement_cache_size=0  # Disable prepared statements for pgbouncer compatibility
        )
        logger.info("Database connection pool initialized")
        return _db_pool
    except Exception as e:
        logger.error(f"Failed to initialize database pool: {e}")
        return None

async def close_db_pool():
    """Close database connection pool"""
    global _db_pool
    
    if _db_pool:
        await _db_pool.close()
        _db_pool = None
        logger.info("Database connection pool closed")

@asynccontextmanager
async def get_db_connection():
    """Get a database connection from the pool"""
    pool = await init_db_pool()
    
    if pool:
        async with pool.acquire() as connection:
            yield connection
    else:
        # Return mock connection for testing
        yield None


class MockDatabase:
    """Mock database for testing"""

    def __init__(self):
        self.data = {}

    def table(self, table_name: str):
        """Get table reference"""
        return MockTable(table_name, self.data)

    @asynccontextmanager
    async def transaction(self):
        """Mock transaction context"""
        # In a real implementation, this would handle database transactions
        yield self


class MockTable:
    """Mock table for testing"""

    def __init__(self, table_name: str, data_store: dict):
        self.table_name = table_name
        self.data_store = data_store
        if table_name not in self.data_store:
            self.data_store[table_name] = []
        self._filters = []
        self._updates = {}
        self._single = False
        self._limit = None
        self._order = None

    def select(self, *columns):
        """Select columns"""
        self._columns = columns
        self._operation = 'select'
        return self

    def insert(self, data: Dict[str, Any]):
        """Insert data"""
        self._operation = 'insert'
        self._insert_data = data
        return self

    def update(self, data: Dict[str, Any]):
        """Update data"""
        self._operation = 'update'
        self._updates = data
        return self

    def delete(self):
        """Delete data"""
        self._operation = 'delete'
        return self

    def eq(self, column: str, value: Any):
        """Equal filter"""
        self._filters.append(('eq', column, value))
        return self

    def lt(self, column: str, value: Any):
        """Less than filter"""
        self._filters.append(('lt', column, value))
        return self

    def gte(self, column: str, value: Any):
        """Greater than or equal filter"""
        self._filters.append(('gte', column, value))
        return self

    def single(self):
        """Return single result"""
        self._single = True
        return self

    def limit(self, n: int):
        """Limit results"""
        self._limit = n
        return self

    def order(self, column: str, desc: bool = False):
        """Order results"""
        self._order = (column, desc)
        return self

    async def execute(self):
        """Execute the query"""
        if self._operation == 'insert':
            # Add to data store
            import uuid
            if 'id' not in self._insert_data:
                self._insert_data['id'] = str(uuid.uuid4())

            self.data_store[self.table_name].append(self._insert_data)
            return MockResult(data=self._insert_data)

        elif self._operation == 'select':
            # Filter data
            result = self.data_store.get(self.table_name, [])

            for filter_type, column, value in self._filters:
                if filter_type == 'eq':
                    result = [r for r in result if r.get(column) == value]
                elif filter_type == 'lt':
                    result = [r for r in result if r.get(column) < value]
                elif filter_type == 'gte':
                    result = [r for r in result if r.get(column) >= value]

            # Apply limit
            if self._limit:
                result = result[:self._limit]

            # Return count for count queries
            if self._columns and 'count' in self._columns:
                return MockResult(count=len(result))

            # Return single or multiple
            if self._single:
                return MockResult(data=result[0] if result else {})

            return MockResult(data=result)

        elif self._operation == 'update':
            # Update matching records
            updated = 0
            for record in self.data_store.get(self.table_name, []):
                matches = True
                for filter_type, column, value in self._filters:
                    if filter_type == 'eq' and record.get(column) != value:
                        matches = False
                        break

                if matches:
                    record.update(self._updates)
                    updated += 1

            return MockResult(data={'updated': updated})

        elif self._operation == 'delete':
            # Delete matching records
            original = self.data_store.get(self.table_name, [])
            remaining = []

            for record in original:
                matches = False
                for filter_type, column, value in self._filters:
                    if filter_type == 'eq' and record.get(column) == value:
                        matches = True
                        break

                if not matches:
                    remaining.append(record)

            self.data_store[self.table_name] = remaining
            deleted = len(original) - len(remaining)

            return MockResult(data={'deleted': deleted})

        return MockResult()


class MockResult:
    """Mock query result"""

    def __init__(self, data=None, count=None):
        self.data = data
        self.count = count


class DatabaseClient:
    """Database client for real operations"""

    def __init__(self):
        # In production, this would connect to Supabase
        pass

    async def execute_query(self, query_name: str, **params) -> Dict[str, Any]:
        """Execute a named query"""
        # Mock implementation
        return {'result': 'success'}


# Global database instance
db = MockDatabase()


# Scheduler functions (mock)
async def schedule_task(scheduled_time, task_type: str, data: Dict[str, Any]):
    """Schedule a task for later execution"""
    # In production, this would use a task queue like Celery or Redis
    pass
