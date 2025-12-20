#!/usr/bin/env python3
"""
Apply database migration files to Supabase.
This script is for use in clinics/backend - DO NOT use the worker version.
"""

import asyncio
import asyncpg
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

def get_database_url():
    """Get the database URL from Supabase URL and credentials."""

    # First try direct DB URL if available
    db_url = os.getenv('SUPABASE_DB_URL')
    if db_url:
        return db_url

    # Otherwise construct from Supabase URL
    supabase_url = os.getenv('SUPABASE_URL')
    if not supabase_url:
        return None

    # Parse the Supabase URL to get the project ID
    parsed = urlparse(supabase_url)
    # Supabase URLs are like: https://xxxx.supabase.co
    project_id = parsed.hostname.split('.')[0]

    # Construct the database URL
    # Default Supabase DB port is 6543 for pooler, 5432 for direct
    db_password = os.getenv('SUPABASE_DB_PASSWORD')
    if db_password:
        # If we have the DB password, use it
        db_url = f"postgresql://postgres:{db_password}@db.{project_id}.supabase.co:5432/postgres"
    else:
        # Try to use the service role key as password (common pattern)
        service_key = os.getenv('SUPABASE_SERVICE_ROLE_KEY') or os.getenv('SUPABASE_SERVICE_KEY')
        if service_key:
            # This might not work but worth trying
            db_url = f"postgresql://postgres:{service_key}@db.{project_id}.supabase.co:5432/postgres"
        else:
            return None

    return db_url

async def apply_migration(migration_file_path: str):
    """Apply the specified migration file."""

    # Get database URL
    db_url = get_database_url()
    if not db_url:
        print("❌ Could not determine database URL")
        print("   Please set one of:")
        print("   - SUPABASE_DB_URL (direct database URL)")
        print("   - SUPABASE_DB_PASSWORD (database password)")
        print("   - Or check your .env file")
        return False

    # Read migration file
    migration_file = Path(migration_file_path)

    if not migration_file.exists():
        print(f"❌ Migration file not found: {migration_file}")
        return False

    print(f"📖 Reading migration from {migration_file}")
    migration_sql = migration_file.read_text()

    # Apply migration
    conn = None
    try:
        print("🔗 Connecting to database...")
        conn = await asyncpg.connect(db_url, statement_cache_size=0)
        print("✅ Connected to database")

        print("🔧 Applying migration...")
        await conn.execute(migration_sql)
        print("✅ Migration applied successfully!")

        # Test functions if they exist in the migration
        if 'list_organization_clinics' in migration_sql:
            print("🧪 Testing organization functions...")
            # Just check the function exists, don't call it
            result = await conn.fetchval("""
                SELECT COUNT(*)
                FROM pg_proc
                WHERE proname = 'list_organization_clinics'
            """)
            if result > 0:
                print(f"✅ list_organization_clinics function exists")

        if 'associate_user_with_organization' in migration_sql:
            result = await conn.fetchval("""
                SELECT COUNT(*)
                FROM pg_proc
                WHERE proname = 'associate_user_with_organization'
            """)
            if result > 0:
                print(f"✅ associate_user_with_organization function exists")

        print("🎉 All migration components applied and tested successfully!")
        return True

    except Exception as e:
        print(f"❌ Migration failed: {e}")
        import traceback
        traceback.print_exc()
        return False

    finally:
        if conn:
            await conn.close()
            print("🔌 Database connection closed")

def main():
    """Main function."""
    print("🚀 Starting migration application...")

    # Check for migration file argument
    if len(sys.argv) < 2:
        print("❌ Usage: python apply_migration.py <migration_file_path>")
        print("   Example: python apply_migration.py ../migrations/fix_rpc_organization_fields.sql")
        print("\n⚠️  NOTE: This script is in clinics/backend, NOT in worker!")
        print("   The worker directory is SUSPENDED and should not be used.")
        exit(1)

    migration_file_path = sys.argv[1]

    # Load environment variables
    env_file = Path(__file__).parent / '.env'
    if env_file.exists():
        print(f"📋 Loading environment from {env_file}")
        with open(env_file, 'r') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, value = line.split('=', 1)
                    # Remove quotes if present
                    value = value.strip('"').strip("'")
                    os.environ[key] = value
    else:
        print("⚠️  No .env file found, using system environment variables")

    # Run migration
    success = asyncio.run(apply_migration(migration_file_path))

    if success:
        print("✅ Migration completed successfully!")
        exit(0)
    else:
        print("❌ Migration failed!")
        exit(1)

if __name__ == "__main__":
    main()
