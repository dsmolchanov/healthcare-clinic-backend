import asyncio
from supabase import create_client, Client
from dotenv import load_dotenv
import os
import json

load_dotenv()

def test_privilege_rpcs():
    # Get Supabase credentials
    supabase_url = os.getenv('SUPABASE_URL')
    supabase_key = os.getenv('SUPABASE_SERVICE_KEY') or os.getenv('SUPABASE_ANON_KEY')

    if not supabase_url or not supabase_key:
        print("❌ Missing SUPABASE_URL or SUPABASE_KEY")
        return

    # Create Supabase client
    supabase: Client = create_client(supabase_url, supabase_key)

    # Test clinic ID (Shtern Dental)
    clinic_id = "3e411ecb-3411-4add-91e2-8fa897310cb0"

    print("🔍 Testing Privilege RPC Functions...")
    print("=" * 50)

    # 1. Test get_matrix_slice
    print("\n1️⃣ Testing get_matrix_slice...")
    try:
        response = supabase.rpc('get_matrix_slice', {
            'p_clinic_id': clinic_id,
            'p_row_offset': 0,
            'p_row_limit': 5,
            'p_col_offset': 0,
            'p_col_limit': 5
        }).execute()

        if response.data:
            print(f"✅ Matrix slice returned {len(response.data)} cells")
            if response.data:
                # Show first cell as example
                first = response.data[0]
                print(f"   Sample cell: Doctor={first.get('doctor_name')}, Service={first.get('service_name')}, Level={first.get('privilege_level')}")
        else:
            print("⚠️  No matrix data returned")
    except Exception as e:
        print(f"❌ Error: {e}")

    # 2. Get first doctor from the clinic to test dossier
    print("\n2️⃣ Getting doctors from clinic...")
    try:
        doctors = supabase.table('doctors').select("*").eq('clinic_id', clinic_id).limit(3).execute()
        if doctors.data:
            print(f"✅ Found {len(doctors.data)} doctors")
            for doctor in doctors.data:
                print(f"   - {doctor['first_name']} {doctor['last_name']} (ID: {doctor['id']})")

            # Test get_doctor_dossier with first doctor
            first_doctor = doctors.data[0]
            doctor_id = first_doctor['id']

            print(f"\n3️⃣ Testing get_doctor_dossier for {first_doctor['first_name']} {first_doctor['last_name']}...")
            try:
                response = supabase.rpc('get_doctor_dossier', {
                    'p_clinic_id': clinic_id,
                    'p_doctor_id': doctor_id
                }).execute()

                if response.data:
                    dossier = response.data
                    if 'error' in dossier:
                        print(f"⚠️  {dossier['error']}")
                    else:
                        print(f"✅ Doctor dossier retrieved")
                        if 'doctor' in dossier:
                            print(f"   Name: {dossier['doctor'].get('name')}")
                            print(f"   Status: {dossier['doctor'].get('status')}")
                            print(f"   License: {dossier['doctor'].get('license_number')}")
                        if 'stats' in dossier:
                            print(f"   Active privileges: {dossier['stats'].get('active_privileges')}")
                            print(f"   Total cases YTD: {dossier['stats'].get('total_cases_ytd')}")
                else:
                    print("⚠️  No dossier data returned")
            except Exception as e:
                print(f"❌ Error: {e}")
        else:
            print("⚠️  No doctors found in clinic")
    except Exception as e:
        print(f"❌ Error getting doctors: {e}")

    # 3. Get services and test service roster
    print("\n4️⃣ Getting services from clinic...")
    try:
        services = supabase.table('services').select("*").eq('clinic_id', clinic_id).limit(3).execute()
        if services.data:
            print(f"✅ Found {len(services.data)} services")
            for service in services.data:
                print(f"   - {service['name']} (ID: {service['id']})")

            # Test get_service_roster with first service
            first_service = services.data[0]
            service_id = first_service['id']

            print(f"\n5️⃣ Testing get_service_roster for {first_service['name']}...")
            try:
                response = supabase.rpc('get_service_roster', {
                    'p_clinic_id': clinic_id,
                    'p_service_id': service_id
                }).execute()

                if response.data:
                    roster = response.data
                    if 'error' in roster:
                        print(f"⚠️  {roster['error']}")
                    else:
                        print(f"✅ Service roster retrieved")
                        if 'service' in roster:
                            print(f"   Service: {roster['service'].get('name')}")
                            print(f"   Category: {roster['service'].get('category')}")
                        if 'coverage' in roster:
                            coverage = roster['coverage']
                            print(f"   Coverage status: {coverage.get('coverage_status')}")
                            print(f"   Total providers: {coverage.get('total_count')}")
                            print(f"   Independent: {coverage.get('independent_count')}")
                else:
                    print("⚠️  No roster data returned")
            except Exception as e:
                print(f"❌ Error: {e}")
        else:
            print("⚠️  No services found in clinic")
    except Exception as e:
        print(f"❌ Error getting services: {e}")

    # 4. Check appointments to see what data we have
    print("\n6️⃣ Checking appointments data...")
    try:
        appointments = supabase.table('appointments').select("*").eq('clinic_id', clinic_id).limit(5).execute()
        if appointments.data:
            print(f"✅ Found {len(appointments.data)} appointments")
            for apt in appointments.data:
                print(f"   - Date: {apt.get('appointment_date')}, Doctor: {apt.get('doctor_id')}, Service: {apt.get('service_id')}")
        else:
            print("⚠️  No appointments found - privilege levels will be empty")
            print("   💡 Create appointments to populate privilege levels")
    except Exception as e:
        print(f"❌ Error checking appointments: {e}")

if __name__ == "__main__":
    test_privilege_rpcs()