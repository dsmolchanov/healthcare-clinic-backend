#!/usr/bin/env python3
"""
Simple script to associate a user with a clinic's organization
Run this from the clinics/backend directory
"""

import os
import sys
from dotenv import load_dotenv
from supabase import create_client, Client

# Load environment variables from clinics/backend/.env
load_dotenv('.env')

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY")

if not SUPABASE_URL or not SUPABASE_ANON_KEY:
    print("❌ Missing Supabase configuration")
    print("Make sure you have SUPABASE_URL and SUPABASE_ANON_KEY in your .env file")
    sys.exit(1)

# Create Supabase client
supabase: Client = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)

def associate_user(user_email: str):
    """Associate a user with their clinic's organization"""
    try:
        print(f"🔍 Looking for clinics...")

        # Get all clinics
        clinics_result = supabase.table('clinics').select('*').execute()

        if not clinics_result.data:
            print("❌ No clinics found")
            return

        print(f"Found {len(clinics_result.data)} clinics:")
        for clinic in clinics_result.data:
            print(f"  - {clinic.get('name')}")
            print(f"    Organization ID: {clinic.get('organization_id')}")
            print(f"    Email: {clinic.get('email')}")

        # Use the first clinic (or you can modify to select specific one)
        clinic = clinics_result.data[0]
        org_id = clinic.get('organization_id')
        clinic_name = clinic.get('name')

        if not org_id:
            print("❌ Clinic has no organization_id")
            return

        print(f"\n🔗 Associating {user_email} with {clinic_name}")
        print(f"   Organization ID: {org_id}")

        # Call the associate RPC function
        result = supabase.rpc('associate_user_with_organization', {
            'p_user_email': user_email,
            'p_organization_id': org_id,
            'p_role': 'owner'
        }).execute()

        if result.data:
            print(f"✅ Successfully associated user with organization!")
            print(f"   Association details: {result.data}")

            # Now check if we can fetch clinics with the user's credentials
            print("\n🧪 Testing clinic access...")
            test_result = supabase.rpc('list_organization_clinics', {
                'org_id': org_id
            }).execute()

            if test_result.data:
                print(f"✅ User can now access clinics!")
                print(f"   Clinics accessible: {len(test_result.data)}")
            else:
                print("⚠️  Could not verify clinic access")

            print("\n📝 Next steps:")
            print("1. Log out of the dashboard")
            print("2. Log back in")
            print("3. Go to /organization/dashboard")
            print("4. Your clinic should now be visible!")
        else:
            print(f"❌ Association failed")

    except Exception as e:
        print(f"❌ Error: {e}")
        print("\nTroubleshooting:")
        print("1. Make sure the email matches your Supabase auth user email")
        print("2. Check that the clinic exists in the database")
        print("3. Verify the RPC functions are properly created")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 associate_user_with_clinic.py <your-email@example.com>")
        print("\nThis will associate your user account with the first clinic found.")
        print("\nExample:")
        print("  python3 associate_user_with_clinic.py john@example.com")
        sys.exit(1)

    user_email = sys.argv[1]
    print(f"🚀 Associating user {user_email} with clinic...\n")
    associate_user(user_email)
