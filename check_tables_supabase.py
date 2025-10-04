import asyncio
from supabase import create_client, Client
from dotenv import load_dotenv
import os

load_dotenv()

def check_tables():
    # Get Supabase credentials
    supabase_url = os.getenv('SUPABASE_URL')
    supabase_key = os.getenv('SUPABASE_SERVICE_KEY') or os.getenv('SUPABASE_ANON_KEY')

    if not supabase_url or not supabase_key:
        print("❌ Missing SUPABASE_URL or SUPABASE_KEY")
        return

    # Create Supabase client
    supabase: Client = create_client(supabase_url, supabase_key)

    # Query for tables - we'll check what tables exist by trying to query them
    print("🔍 Checking for existing tables by attempting queries...")

    tables_to_check = [
        'doctors',
        'services',
        'appointments',
        'clinics',
        'doctor_specialty_assignments',
        'doctor_service_assignments',
        'specialties',
        'users',
        'organizations'
    ]

    existing_tables = []

    for table in tables_to_check:
        try:
            # Try to select with limit 1 to test if table exists
            response = supabase.table(table).select("*").limit(1).execute()
            print(f"  ✅ {table} - exists ({len(response.data)} sample record)")
            existing_tables.append(table)

            # For existing tables, show the first record structure
            if response.data:
                print(f"     Sample columns: {', '.join(response.data[0].keys())}")
        except Exception as e:
            if "relation" in str(e) and "does not exist" in str(e):
                print(f"  ❌ {table} - does not exist")
            else:
                print(f"  ⚠️  {table} - error: {str(e)[:100]}")

    print(f"\n📊 Found {len(existing_tables)} tables: {', '.join(existing_tables)}")

    # Check for any assignment/specialty related data
    if 'doctors' in existing_tables:
        print("\n🔍 Checking doctors table structure...")
        try:
            doctors = supabase.table('doctors').select("*").limit(1).execute()
            if doctors.data:
                print("Doctor columns:", list(doctors.data[0].keys()))
        except Exception as e:
            print(f"Error checking doctors: {e}")

    if 'services' in existing_tables:
        print("\n🔍 Checking services table structure...")
        try:
            services = supabase.table('services').select("*").limit(1).execute()
            if services.data:
                print("Service columns:", list(services.data[0].keys()))
        except Exception as e:
            print(f"Error checking services: {e}")

if __name__ == "__main__":
    check_tables()