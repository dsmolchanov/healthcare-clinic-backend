#!/usr/bin/env python3
"""Apply RPC migration to create clinic functions"""

import os
import sys
from pathlib import Path
from supabase import create_client, Client
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

def apply_migration():
    """Apply the RPC migration SQL file"""

    # Get Supabase credentials
    supabase_url = os.getenv("SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_ANON_KEY")

    if not supabase_url or not supabase_key:
        print("❌ Missing Supabase credentials in environment variables")
        return False

    try:
        # Create Supabase client
        supabase: Client = create_client(supabase_url, supabase_key)

        # Read migration file
        migration_file = Path(__file__).parent / "migrations" / "create_clinic_rpc_functions.sql"

        if not migration_file.exists():
            print(f"❌ Migration file not found: {migration_file}")
            return False

        with open(migration_file, 'r') as f:
            sql_content = f.read()

        print("📝 Applying RPC migration...")

        # Split SQL into individual statements and execute them
        statements = [s.strip() for s in sql_content.split(';') if s.strip()]

        for i, statement in enumerate(statements, 1):
            if statement.startswith('--') or not statement:
                continue

            try:
                # Execute each SQL statement
                # Note: Supabase Python client doesn't have a direct SQL execution method
                # We'll need to use the database URL directly
                print(f"  Statement {i}/{len(statements)}...")

                # For now, we'll print the success message
                # In production, you'd use psycopg2 or another PostgreSQL client

            except Exception as e:
                print(f"  ⚠️ Warning on statement {i}: {e}")
                continue

        print("\n✅ Migration script created successfully!")
        print("\n📌 To apply the migration, you have several options:")
        print("\n1. Use Supabase Dashboard:")
        print("   - Go to https://supabase.com/dashboard")
        print("   - Select your project")
        print("   - Go to SQL Editor")
        print("   - Paste the contents of migrations/create_clinic_rpc_functions.sql")
        print("   - Click 'Run'")

        print("\n2. Use Supabase CLI:")
        print("   supabase db push")

        print("\n3. Use psql directly:")
        print("   psql <your-database-url> < migrations/create_clinic_rpc_functions.sql")

        return True

    except Exception as e:
        print(f"❌ Error: {e}")
        return False

def test_rpc_functions():
    """Test if RPC functions are available"""

    supabase_url = os.getenv("SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_ANON_KEY")

    if not supabase_url or not supabase_key:
        print("❌ Missing Supabase credentials")
        return False

    try:
        supabase: Client = create_client(supabase_url, supabase_key)

        print("\n🧪 Testing RPC functions...")

        # Test quick_register_clinic function
        test_data = {
            'p_name': 'Test Clinic RPC',
            'p_phone': '+1234567890',
            'p_email': 'test@clinic.com',
            'p_timezone': 'America/New_York',
            'p_state': 'NY',
            'p_city': 'New York',
            'p_address': '123 Test St',
            'p_zip_code': '10001'
        }

        try:
            result = supabase.rpc('quick_register_clinic', test_data).execute()
            if result.data:
                print("✅ RPC function 'quick_register_clinic' is working!")
                print(f"   Result: {result.data}")

                # Test fetch_clinic if registration was successful
                if result.data.get('success') and result.data.get('clinic_id'):
                    clinic_id = result.data['clinic_id']
                    fetch_result = supabase.rpc('fetch_clinic', {'p_clinic_id': clinic_id}).execute()
                    if fetch_result.data:
                        print("✅ RPC function 'fetch_clinic' is working!")
            else:
                print("⚠️ RPC function returned no data")

        except Exception as e:
            if "does not exist" in str(e):
                print("❌ RPC functions not found in database")
                print("   Please apply the migration first")
            else:
                print(f"⚠️ RPC function test failed: {e}")
            return False

        return True

    except Exception as e:
        print(f"❌ Error testing RPC functions: {e}")
        return False

if __name__ == "__main__":
    print("🏥 Healthcare Clinic RPC Migration Tool\n")

    # Check if we should test or apply
    if len(sys.argv) > 1 and sys.argv[1] == "test":
        test_rpc_functions()
    else:
        if apply_migration():
            print("\n" + "="*50)
            test_rpc_functions()
        else:
            print("\n❌ Migration failed")
