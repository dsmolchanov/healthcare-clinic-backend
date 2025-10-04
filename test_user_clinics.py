#!/usr/bin/env python3
"""Test RPC functions for fetching clinics for a specific user"""

import asyncio
import os
from supabase import create_client, Client
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Initialize Supabase client
supabase: Client = create_client(
    os.getenv("SUPABASE_URL"),
    os.getenv("SUPABASE_SERVICE_KEY")  # Using service key for testing
)

USER_ID = "88beb3b9-89a0-4902-ae06-5dae65f7b447"

async def test_user_clinics():
    """Test fetching clinics for specific user"""

    print(f"\n{'='*60}")
    print(f"Testing clinic access for user: {USER_ID}")
    print(f"{'='*60}\n")

    # 1. First check user's profile and organization
    print("1. Checking user profile...")
    user_result = supabase.table("profiles").select("*").eq("user_id", USER_ID).execute()
    if user_result.data:
        profile = user_result.data[0]
        print(f"   User found: {profile.get('name', 'No name')}")
        print(f"   Organization ID: {profile.get('organization_id', 'None')}")
        print(f"   Full profile: {profile}")
        org_id = profile.get('organization_id')
    else:
        print("   ❌ User profile not found!")

        # Check auth.users table
        print("\n2. Checking auth.users table...")
        auth_result = supabase.table("auth.users").select("*").eq("id", USER_ID).execute()
        if auth_result.data:
            print(f"   User found in auth.users: {auth_result.data[0]}")
        else:
            print("   ❌ User not found in auth.users either!")
        return

    # 2. Check if organization exists
    if org_id:
        print(f"\n2. Checking organization: {org_id}")
        org_result = supabase.table("organizations").select("*").eq("id", org_id).execute()
        if org_result.data:
            org = org_result.data[0]
            print(f"   Organization found: {org.get('name', 'No name')}")
            print(f"   Organization data: {org}")
        else:
            print(f"   ❌ Organization {org_id} not found!")
    else:
        print("\n2. User has no organization assigned")

        # Try to find organization by user email or other means
        print("\n   Searching for organizations...")

        # Check if user is in user_organizations table
        user_org_result = supabase.table("user_organizations").select("*").eq("user_id", USER_ID).execute()
        if user_org_result.data:
            print(f"   Found in user_organizations: {user_org_result.data}")
            if user_org_result.data:
                org_id = user_org_result.data[0].get('organization_id')
                print(f"   Using organization_id: {org_id}")
        else:
            print("   Not found in user_organizations table")

            # List all organizations to help debug
            all_orgs = supabase.table("organizations").select("*").execute()
            if all_orgs.data:
                print(f"\n   All organizations in system:")
                for org in all_orgs.data:
                    print(f"     - {org.get('id')}: {org.get('name')}")

                # Use first organization for testing
                if all_orgs.data:
                    org_id = all_orgs.data[0].get('id')
                    print(f"\n   Using first organization for testing: {org_id}")

    if not org_id:
        print("\n❌ No organization found, cannot fetch clinics")
        return

    # 3. Test RPC function: list_organization_clinics
    print(f"\n3. Testing RPC: list_organization_clinics")
    print(f"   Parameters: org_id={org_id}")
    try:
        result = supabase.rpc("list_organization_clinics", {"org_id": org_id}).execute()
        if result.data:
            print(f"   ✅ Found {len(result.data)} clinics:")
            for clinic in result.data:
                print(f"      - {clinic.get('id')}: {clinic.get('name')}")
        else:
            print(f"   ⚠️ No clinics returned")
            print(f"   Response: {result}")
    except Exception as e:
        print(f"   ❌ Error: {e}")

    # 4. Test RPC function: get_organization_clinics_simple
    print(f"\n4. Testing RPC: get_organization_clinics_simple")
    print(f"   Parameters: org_id={org_id}")
    try:
        result = supabase.rpc("get_organization_clinics_simple", {"org_id": org_id}).execute()
        if result.data:
            print(f"   ✅ Found {len(result.data)} clinics:")
            for clinic in result.data:
                print(f"      - {clinic.get('id')}: {clinic.get('name')}")
        else:
            print(f"   ⚠️ No clinics returned")
            print(f"   Response: {result}")
    except Exception as e:
        print(f"   ❌ Error: {e}")

    # 5. Test RPC function: get_organization_clinics_enhanced
    print(f"\n5. Testing RPC: get_organization_clinics_enhanced")
    print(f"   Parameters: org_id={org_id}")
    try:
        result = supabase.rpc("get_organization_clinics_enhanced", {"org_id": org_id}).execute()
        if result.data:
            print(f"   ✅ Found {len(result.data)} clinics:")
            for clinic in result.data:
                print(f"      - {clinic.get('id')}: {clinic.get('name')}")
                print(f"        Settings: {clinic.get('settings', {})}")
        else:
            print(f"   ⚠️ No clinics returned")
            print(f"   Response: {result}")
    except Exception as e:
        print(f"   ❌ Error: {e}")

    # 6. Check clinics table directly
    print(f"\n6. Checking clinics table directly")
    print(f"   Filtering by organization_id={org_id}")
    try:
        clinics_result = supabase.table("clinics").select("*").eq("organization_id", org_id).execute()
        if clinics_result.data:
            print(f"   ✅ Found {len(clinics_result.data)} clinics in table:")
            for clinic in clinics_result.data:
                print(f"      - {clinic.get('id')}: {clinic.get('name')}")
                print(f"        Org ID: {clinic.get('organization_id')}")
                print(f"        Active: {clinic.get('is_active', False)}")
        else:
            print(f"   ⚠️ No clinics found in table for this organization")

            # Check if there are any clinics at all
            all_clinics = supabase.table("clinics").select("*").limit(5).execute()
            if all_clinics.data:
                print(f"\n   Sample clinics in system:")
                for clinic in all_clinics.data:
                    print(f"      - {clinic.get('id')}: {clinic.get('name')} (org: {clinic.get('organization_id')})")
    except Exception as e:
        print(f"   ❌ Error: {e}")

    # 7. Check healthcare.clinics schema if it exists
    print(f"\n7. Checking healthcare.clinics table (if exists)")
    try:
        healthcare_result = supabase.table("clinics").select("*").execute()
        if healthcare_result.data:
            print(f"   Found {len(healthcare_result.data)} total clinics")
            # Try to find clinics for this organization
            org_clinics = [c for c in healthcare_result.data if c.get('organization_id') == org_id]
            if org_clinics:
                print(f"   ✅ Found {len(org_clinics)} clinics for organization:")
                for clinic in org_clinics:
                    print(f"      - {clinic.get('id')}: {clinic.get('name')}")
            else:
                print(f"   ⚠️ No clinics found for organization {org_id}")
                print(f"   All organization IDs in clinics table:")
                org_ids = set(c.get('organization_id') for c in healthcare_result.data if c.get('organization_id'))
                for oid in org_ids:
                    print(f"      - {oid}")
    except Exception as e:
        print(f"   ❌ Error accessing healthcare.clinics: {e}")

    print(f"\n{'='*60}")
    print("Testing complete!")
    print(f"{'='*60}\n")

if __name__ == "__main__":
    asyncio.run(test_user_clinics())