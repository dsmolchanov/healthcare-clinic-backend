#!/usr/bin/env python3
"""
Script to associate a user with Shtern Dental Clinic
"""

import os
import sys
from dotenv import load_dotenv
from supabase import create_client, Client

# Load environment variables
load_dotenv('.env')

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY")

# Shtern Dental Clinic organization ID (from your database)
SHTERN_ORG_ID = "4e8ddba1-ad52-4613-9a03-ec64636b3f6c"
SHTERN_CLINIC_ID = "e0c84f56-235d-49f2-9a44-37c1be579afc"

if not SUPABASE_URL or not SUPABASE_ANON_KEY:
    print("❌ Missing Supabase configuration")
    print("Make sure you have SUPABASE_URL and SUPABASE_ANON_KEY in your .env file")
    sys.exit(1)

# Create Supabase client
supabase: Client = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)

def associate_user_with_shtern(user_email: str):
    """Associate a user with Shtern Dental Clinic organization"""
    try:
        print(f"🦷 Associating {user_email} with Shtern Dental Clinic")
        print(f"   Organization ID: {SHTERN_ORG_ID}")
        print(f"   Clinic ID: {SHTERN_CLINIC_ID}")
        print()

        # Call the associate RPC function
        result = supabase.rpc('associate_user_with_organization', {
            'p_user_email': user_email,
            'p_organization_id': SHTERN_ORG_ID,
            'p_role': 'owner'
        }).execute()

        if result.data:
            print(f"✅ Successfully associated user with Shtern Dental Clinic!")
            print(f"   Association details: {result.data}")

            # Test if we can now fetch the clinic
            print("\n🧪 Testing clinic access...")
            test_result = supabase.rpc('list_organization_clinics', {
                'org_id': SHTERN_ORG_ID
            }).execute()

            if test_result.data:
                print(f"✅ User can now access Shtern Dental Clinic!")
                clinics = test_result.data
                if clinics:
                    for clinic in clinics:
                        print(f"   - {clinic.get('name', 'Unknown')}")
            else:
                print("⚠️  Could not verify clinic access (but association was successful)")

            print("\n📝 Next steps:")
            print("1. Log out of the dashboard")
            print("2. Log back in")
            print("3. Go to https://plaintalk.vercel.app/organization/dashboard")
            print("4. You should see Shtern Dental Clinic!")

            print("\n🎯 Direct links:")
            print("   Dashboard: https://plaintalk.vercel.app/organization/dashboard")
            print("   Settings: https://plaintalk.vercel.app/organization/settings")
            print("   Clinic Dashboard: https://plaintalk.vercel.app/clinic-dashboard/" + SHTERN_CLINIC_ID)

        else:
            print(f"❌ Association failed - no data returned")

    except Exception as e:
        error_msg = str(e)

        # Check for specific error cases
        if "User with email" in error_msg and "not found" in error_msg:
            print(f"❌ No user found with email: {user_email}")
            print("\nPlease make sure:")
            print("1. You're using the exact email you use to log into the dashboard")
            print("2. The email is registered in Supabase Auth")
            print("3. Check for typos in the email address")
        elif "already a member" in error_msg:
            print(f"✅ User is already associated with Shtern Dental Clinic!")
            print("\nYou should already be able to see the clinic in your dashboard.")
            print("Try logging out and back in if you don't see it.")
        else:
            print(f"❌ Error: {e}")
            print("\nTroubleshooting:")
            print("1. Make sure the email matches your Supabase auth user email")
            print("2. Check your internet connection")
            print("3. Verify the RPC functions are properly created in the database")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("🦷 Shtern Dental Clinic Association Script")
        print("=" * 50)
        print("\nUsage: python3 associate_shtern_clinic.py <your-email@example.com>")
        print("\nThis will associate your user account with Shtern Dental Clinic.")
        print("\nExample:")
        print("  python3 associate_shtern_clinic.py john@example.com")
        sys.exit(1)

    user_email = sys.argv[1]
    print(f"🚀 Shtern Dental Clinic Association\n")
    print("=" * 50)
    associate_user_with_shtern(user_email)
