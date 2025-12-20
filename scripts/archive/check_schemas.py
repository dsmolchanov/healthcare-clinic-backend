#!/usr/bin/env python3
"""
Check available schemas and tables in the database
"""

import os
from dotenv import load_dotenv
import sys

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Load environment variables
load_dotenv('../.env')

from supabase import create_client

def check_database_structure():
    """Check what schemas and tables exist"""

    print("🔍 Checking Database Structure\n")

    try:
        # Create Supabase client
        supabase = create_client(
            os.getenv('SUPABASE_URL'),
            os.getenv('SUPABASE_SERVICE_ROLE_KEY')
        )

        # Try different table references
        tables_to_check = [
            # Core schema tables
            ('organizations', 'core schema'),
            ('agents', 'core schema'),
            ('consent_records', 'core schema'),

            # Healthcare schema tables
            ('clinics', 'healthcare schema'),
            ('doctors', 'healthcare schema'),
            ('patients', 'healthcare schema'),
            ('appointments', 'healthcare schema'),
            ('services', 'healthcare schema'),

            # Check if calendar integration tables exist
            ('calendar_integrations', 'healthcare schema'),
            ('calendar_sync_log', 'healthcare schema'),
        ]

        print("Testing table access:\n")
        for table_name, description in tables_to_check:
            try:
                # Try simple table query
                result = supabase.table(table_name).select('*').limit(1).execute()
                print(f"✅ {table_name:25} - Found ({description})")
            except Exception as e:
                error_msg = str(e)
                if 'does not exist' in error_msg:
                    print(f"❌ {table_name:25} - Not found in public schema")
                else:
                    print(f"⚠️  {table_name:25} - Error: {error_msg[:50]}...")

        # Try to execute a raw SQL query to check schemas
        print("\n📊 Checking Available Schemas:\n")

        # Note: Supabase Python client doesn't directly support raw SQL
        # We'll need to use the REST API or check through migrations

        print("To verify schemas, please check:")
        print("1. Supabase Dashboard > SQL Editor")
        print("2. Run: SELECT schema_name FROM information_schema.schemata;")
        print("3. Run: SELECT table_schema, table_name FROM information_schema.tables WHERE table_schema IN ('core', 'healthcare');")

    except Exception as e:
        print(f"❌ Connection error: {e}")

if __name__ == "__main__":
    check_database_structure()
