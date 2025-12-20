#!/usr/bin/env python3
"""
Direct SQL association using Supabase
"""

import os
from dotenv import load_dotenv
from supabase import create_client, Client

# Load environment variables
load_dotenv('.env')

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

# Your specific IDs
USER_ID = "88beb3b9-89a0-4902-ae06-5dae65f7b447"
SHTERN_ORG_ID = "4e8ddba1-ad52-4613-9a03-ec64636b3f6c"
SHTERN_CLINIC_ID = "e0c84f56-235d-49f2-9a44-37c1be579afc"

if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
    print("❌ Missing Supabase configuration")
    exit(1)

# Create Supabase client
supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)

def associate_user():
    """Associate user with organization using RPC"""
    try:
        print("🦷 Associating User with Shtern Dental Clinic")
        print("=" * 60)
        print(f"User ID: {USER_ID}")
        print(f"Organization ID: {SHTERN_ORG_ID}")
        print()

        # First, try to get user email
        print("📧 Looking up user email...")
        # Use the RPC function that already exists

        # Since we can't directly query auth.users, let's try the association
        # with a known email or use the RPC function that handles this

        # Check if association already exists using RPC
        print("🔍 Checking existing associations...")
        check_result = supabase.rpc('get_all_user_organizations').execute()

        if check_result.data:
            existing = [a for a in check_result.data
                       if a.get('user_id') == USER_ID and
                          a.get('organization_id') == SHTERN_ORG_ID]
            if existing:
                print("✅ Association already exists!")
                print(f"   Role: {existing[0].get('role')}")
                print(f"   Active: {existing[0].get('is_active')}")
                print("\n⚠️  You should already be able to see your clinic.")
                print("   Try logging out and back in.")
                return

        # Try to create association manually
        print("🔗 Creating association...")

        # We need the user's email for the RPC function
        # Since we can't query auth.users directly, let's ask for it
        print("\n⚠️  We need your email to complete the association.")
        print("   Please run:")
        print(f"\n   python3 associate_shtern_clinic.py YOUR-EMAIL@example.com")
        print("\n   Replace YOUR-EMAIL with the email you use to log in.")

        # Alternative: Try direct insert if we have permission
        print("\n📝 Attempting direct association (may require admin access)...")

        # Use raw SQL through Supabase
        sql = """
        INSERT INTO core.user_organizations (
            user_id,
            organization_id,
            role,
            permissions,
            is_active,
            joined_at
        ) VALUES (
            %s, %s, 'owner', '{"all": true}'::jsonb, true, NOW()
        ) ON CONFLICT (user_id, organization_id)
        DO UPDATE SET
            role = 'owner',
            is_active = true;
        """

        # Note: Supabase Python client doesn't support raw SQL directly
        # We need to use RPC functions

        print("\n💡 Solution: Please run this command with your email:")
        print(f"\n   cd {os.getcwd()}")
        print(f"   python3 associate_shtern_clinic.py YOUR-EMAIL@example.com")
        print("\nThis will use the RPC function to properly associate your account.")

    except Exception as e:
        print(f"❌ Error: {e}")
        print("\n💡 To fix this, you need to run:")
        print(f"   python3 associate_shtern_clinic.py YOUR-EMAIL@example.com")

if __name__ == "__main__":
    associate_user()
