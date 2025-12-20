#!/usr/bin/env python3
"""
Query database and display results.
"""

import asyncio
import asyncpg
import os
import sys
from pathlib import Path
from urllib.parse import urlparse
import json

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
    db_password = os.getenv('SUPABASE_DB_PASSWORD')
    if db_password:
        db_url = f"postgresql://postgres:{db_password}@db.{project_id}.supabase.co:5432/postgres"
    else:
        # Try to use the service role key as password (common pattern)
        service_key = os.getenv('SUPABASE_SERVICE_ROLE_KEY') or os.getenv('SUPABASE_SERVICE_KEY')
        if service_key:
            db_url = f"postgresql://postgres:{service_key}@db.{project_id}.supabase.co:5432/postgres"
        else:
            return None

    return db_url

async def run_query(query: str):
    """Run a query and display results."""

    # Get database URL
    db_url = get_database_url()
    if not db_url:
        print("❌ Could not determine database URL")
        return False

    conn = None
    try:
        print("🔗 Connecting to database...")
        conn = await asyncpg.connect(db_url, statement_cache_size=0)
        print("✅ Connected to database")

        print(f"\n📊 Running query...")
        results = await conn.fetch(query)

        print(f"\n✅ Found {len(results)} rows\n")

        if results:
            # Print as formatted table
            for i, row in enumerate(results, 1):
                print(f"\n--- Row {i} ---")
                for key, value in row.items():
                    print(f"{key}: {value}")
        else:
            print("No results found")

        return True

    except Exception as e:
        print(f"❌ Query failed: {e}")
        import traceback
        traceback.print_exc()
        return False

    finally:
        if conn:
            await conn.close()
            print("\n🔌 Database connection closed")

def main():
    """Main function."""
    print("🚀 Starting database query...")

    # Load environment variables
    env_file = Path(__file__).parent / '.env'
    if env_file.exists():
        print(f"📋 Loading environment from {env_file}")
        with open(env_file, 'r') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, value = line.split('=', 1)
                    value = value.strip('"').strip("'")
                    os.environ[key] = value
    else:
        print("⚠️  No .env file found, using system environment variables")

    # Get query from command line or file
    if len(sys.argv) < 2:
        print("❌ Usage: python query_database.py <query_or_file>")
        print("   Example: python query_database.py \"SELECT * FROM auth.users LIMIT 5\"")
        print("   Example: python query_database.py query_file.sql")
        exit(1)

    query_input = sys.argv[1]

    # Check if it's a file
    query_file = Path(query_input)
    if query_file.exists():
        print(f"📖 Reading query from {query_file}")
        query = query_file.read_text()
    else:
        query = query_input

    # Run query
    success = asyncio.run(run_query(query))

    if success:
        print("\n✅ Query completed successfully!")
        exit(0)
    else:
        print("\n❌ Query failed!")
        exit(1)

if __name__ == "__main__":
    main()
