#!/usr/bin/env python3
"""
Direct association script using user_id
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
    print("❌ Missing Supabase service role configuration")
    print("Using anon key as fallback...")
    SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_ANON_KEY")
    if not SUPABASE_SERVICE_ROLE_KEY:
        print("Error: No Supabase keys found")
        exit(1)

# Create Supabase client with service role for direct database access
supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)

def create_association():
    """Directly create user-organization association"""
    try:
        print(f"🔗 Creating association...")
        print(f"   User ID: {USER_ID}")
        print(f"   Organization ID: {SHTERN_ORG_ID}")
        print(f"   Clinic: Shtern Dental Clinic")
        print()

        # First check if association already exists
        existing = supabase.table('user_organizations').select('*').eq(
            'user_id', USER_ID
        ).eq('organization_id', SHTERN_ORG_ID).execute()

        if existing.data and len(existing.data) > 0:
            print("✅ Association already exists!")
            print(f"   Role: {existing.data[0].get('role', 'member')}")
            print(f"   Active: {existing.data[0].get('is_active', False)}")

            # Update to ensure it's active and owner role
            update_result = supabase.table('user_organizations').update({
                'role': 'owner',
                'is_active': True
            }).eq('user_id', USER_ID).eq('organization_id', SHTERN_ORG_ID).execute()

            if update_result.data:
                print("✅ Updated to owner role and active status")
        else:
            # Create new association
            result = supabase.table('user_organizations').insert({
                'user_id': USER_ID,
                'organization_id': SHTERN_ORG_ID,
                'role': 'owner',
                'permissions': {'all': True},
                'is_active': True
            }).execute()

            if result.data:
                print("✅ Successfully created association!")
                print(f"   Association ID: {result.data[0].get('id')}")
            else:
                print("❌ Failed to create association")
                return

        # Update user metadata
        print("\n📝 Updating user metadata...")
        # Note: This requires service role key
        try:
            # Update raw_user_meta_data to include organization_id
            update_meta = supabase.auth.admin.update_user_by_id(
                USER_ID,
                {
                    'user_metadata': {
                        'organization_id': SHTERN_ORG_ID
                    }
                }
            )
            print("✅ User metadata updated with organization_id")
        except Exception as e:
            print(f"⚠️  Could not update user metadata (this may require admin access): {e}")
            print("   The association was created, but you may need to log out and back in")

        print("\n🎉 SUCCESS!")
        print("\n📍 Your clinic is now accessible at:")
        print(f"   Organization Dashboard: https://plaintalk.vercel.app/organization/dashboard")
        print(f"   Organization Settings: https://plaintalk.vercel.app/organization/settings")
        print(f"   Clinic Dashboard: https://plaintalk.vercel.app/clinic-dashboard/{SHTERN_CLINIC_ID}")
        print("\n⚠️  IMPORTANT: Log out and log back in to see your clinic!")

    except Exception as e:
        print(f"❌ Error: {e}")
        print("\nTrying alternative approach...")

        # Try using RPC function with email lookup
        try:
            # Get user email first
            user_result = supabase.table('auth.users').select('email').eq('id', USER_ID).execute()
            if user_result.data:
                email = user_result.data[0]['email']
                print(f"Found email: {email}")

                # Use RPC function
                rpc_result = supabase.rpc('associate_user_with_organization', {
                    'p_user_email': email,
                    'p_organization_id': SHTERN_ORG_ID,
                    'p_role': 'owner'
                }).execute()

                if rpc_result.data:
                    print("✅ Successfully associated using RPC function!")
                    print("\n🎉 SUCCESS!")
                    print("\n⚠️  IMPORTANT: Log out and log back in to see your clinic!")
        except Exception as e2:
            print(f"Alternative approach also failed: {e2}")

if __name__ == "__main__":
    print("🦷 Direct User Association for Shtern Dental Clinic")
    print("=" * 60)
    create_association()
