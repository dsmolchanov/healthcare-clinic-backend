#!/usr/bin/env python3
"""Diagnose RBAC permissions for a specific user."""

import os
import sys
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

supabase_url = os.getenv('SUPABASE_URL')
supabase_key = os.getenv('SUPABASE_SERVICE_KEY') or os.getenv('SUPABASE_ANON_KEY')

if not supabase_url or not supabase_key:
    print("❌ Missing SUPABASE_URL or SUPABASE_SERVICE_KEY/SUPABASE_ANON_KEY")
    sys.exit(1)

supabase = create_client(supabase_url, supabase_key)

USER_ID = '88beb3b9-89a0-4902-ae06-5dae65f7b447'

print("=" * 70)
print("🔍 RBAC Permission Diagnostics")
print("=" * 70)
print(f"User ID: {USER_ID}")
print("-" * 70)

# 1. Check user_organizations record
print("\n1️⃣ Checking core.user_organizations...")
try:
    result = supabase.schema('core').table('user_organizations')\
        .select('*')\
        .eq('user_id', USER_ID)\
        .execute()

    if result.data:
        for row in result.data:
            print(f"   ✅ Found record:")
            print(f"      organization_id: {row.get('organization_id')}")
            print(f"      role (old):      {row.get('role')}")
            print(f"      unified_role:    {row.get('unified_role')}")  # This is what permission_service uses!
            print(f"      is_active:       {row.get('is_active')}")
            print(f"      permissions:     {row.get('permissions')}")

            if not row.get('unified_role'):
                print(f"\n   ⚠️  WARNING: unified_role is NULL!")
                print(f"      The permission system uses unified_role, not role.")
                print(f"      This is likely the cause of the RBAC issue!")
    else:
        print("   ❌ No user_organizations record found!")
        print("      The user needs to be added to an organization.")
except Exception as e:
    print(f"   ❌ Error: {e}")

# 2. Check role_permissions for 'owner' role
print("\n2️⃣ Checking public.role_permissions for 'owner' role...")
try:
    result = supabase.schema('public').table('role_permissions')\
        .select('*, permissions(action)')\
        .eq('role', 'owner')\
        .eq('granted', True)\
        .execute()

    if result.data:
        print(f"   ✅ Owner has {len(result.data)} permissions granted:")
        for row in result.data[:10]:  # Show first 10
            action = row.get('permissions', {}).get('action', 'unknown')
            print(f"      - {action}")
        if len(result.data) > 10:
            print(f"      ... and {len(result.data) - 10} more")

        # Check specifically for appointments:view
        has_appointments = any(
            row.get('permissions', {}).get('action') == 'appointments:view'
            for row in result.data
        )
        if has_appointments:
            print(f"\n   ✅ appointments:view is granted to owner role")
        else:
            print(f"\n   ❌ appointments:view NOT found for owner role!")
    else:
        print("   ❌ No permissions found for 'owner' role!")
        print("      The 2026-01-13-seed-permissions.sql migration may not have run.")
except Exception as e:
    print(f"   ❌ Error: {e}")

# 3. Check permissions table exists and has data
print("\n3️⃣ Checking public.permissions table...")
try:
    result = supabase.schema('public').table('permissions')\
        .select('*')\
        .execute()

    if result.data:
        print(f"   ✅ {len(result.data)} permissions defined")
        appointments_view = [p for p in result.data if p.get('action') == 'appointments:view']
        if appointments_view:
            print(f"   ✅ appointments:view exists (id: {appointments_view[0].get('id')})")
        else:
            print(f"   ❌ appointments:view NOT defined in permissions table!")
    else:
        print("   ❌ No permissions defined!")
except Exception as e:
    print(f"   ❌ Error: {e}")

# 4. Check user's JWT metadata
print("\n4️⃣ Checking auth.users metadata...")
try:
    # Need to use RPC or different approach for auth.users
    print("   ℹ️  Cannot check auth.users directly from service role")
    print("      Check via Supabase dashboard or browser console")
except Exception as e:
    print(f"   ⚠️  {e}")

print("\n" + "=" * 70)
print("📋 Summary & Fix Commands")
print("=" * 70)
print("""
If unified_role is NULL, run this SQL to fix:

UPDATE core.user_organizations
SET unified_role = 'owner'
WHERE user_id = '88beb3b9-89a0-4902-ae06-5dae65f7b447';

Then clear the Redis permission cache (or wait 5 minutes).

If role_permissions is empty, run:
apps/healthcare-backend/apply_migration.py migrations/2026-01-13-seed-permissions.sql
""")
