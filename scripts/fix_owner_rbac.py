#!/usr/bin/env python3
"""Fix RBAC for user 88beb3b9-89a0-4902-ae06-5dae65f7b447 - add as owner."""

import os
import sys
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

supabase_url = os.getenv('SUPABASE_URL')
supabase_key = os.getenv('SUPABASE_SERVICE_KEY') or os.getenv('SUPABASE_ANON_KEY')

if not supabase_url or not supabase_key:
    print("❌ Missing SUPABASE_URL or SUPABASE_SERVICE_KEY")
    sys.exit(1)

supabase = create_client(supabase_url, supabase_key)

USER_ID = '88beb3b9-89a0-4902-ae06-5dae65f7b447'
ORG_ID = '4e8ddba1-ad52-4613-9a03-ec64636b3f6c'  # Shtern Dental Clinic's org

print("=" * 70)
print("🔧 Fixing RBAC for Owner")
print("=" * 70)
print(f"User ID: {USER_ID}")
print(f"Org ID:  {ORG_ID}")
print("-" * 70)

# 1. Check if user_organizations record exists
print("\n1️⃣ Checking existing record...")
try:
    result = supabase.schema('core').table('user_organizations')\
        .select('*')\
        .eq('user_id', USER_ID)\
        .eq('organization_id', ORG_ID)\
        .execute()

    if result.data:
        print(f"   Found existing record: {result.data[0]}")
        existing = result.data[0]
        if existing.get('unified_role') != 'owner':
            print(f"   ⚠️ unified_role is '{existing.get('unified_role')}', updating to 'owner'...")
            # Update the unified_role
            update_result = supabase.schema('core').table('user_organizations')\
                .update({'unified_role': 'owner', 'is_active': True})\
                .eq('user_id', USER_ID)\
                .eq('organization_id', ORG_ID)\
                .execute()
            print(f"   ✅ Updated unified_role to 'owner'")
        else:
            print(f"   ✅ Already has unified_role='owner'")
    else:
        print("   No existing record, creating new one...")
        insert_result = supabase.schema('core').table('user_organizations')\
            .insert({
                'user_id': USER_ID,
                'organization_id': ORG_ID,
                'role': 'owner',  # Old column for compatibility
                'unified_role': 'owner',  # New column used by permission service
                'is_active': True,
                'permissions': {'all': True}
            })\
            .execute()
        print(f"   ✅ Created user_organizations record")
        print(f"   Result: {insert_result.data}")

except Exception as e:
    print(f"   ❌ Error: {e}")
    # Try with upsert
    print("   Trying upsert...")
    try:
        upsert_result = supabase.schema('core').table('user_organizations')\
            .upsert({
                'user_id': USER_ID,
                'organization_id': ORG_ID,
                'role': 'owner',
                'unified_role': 'owner',
                'is_active': True,
                'permissions': {'all': True}
            })\
            .execute()
        print(f"   ✅ Upserted record: {upsert_result.data}")
    except Exception as e2:
        print(f"   ❌ Upsert also failed: {e2}")

# 2. Verify permissions are now accessible
print("\n2️⃣ Verifying permissions setup...")
try:
    result = supabase.schema('public').table('role_permissions')\
        .select('*, permissions(action)')\
        .eq('role', 'owner')\
        .eq('granted', True)\
        .execute()

    if result.data:
        print(f"   ✅ Owner has {len(result.data)} permissions")
        # Check for appointments:view
        has_calendar = any(
            row.get('permissions', {}).get('action') == 'appointments:view'
            for row in result.data
        )
        if has_calendar:
            print(f"   ✅ appointments:view is granted - calendar should now show!")
        else:
            print(f"   ❌ appointments:view not found!")
except Exception as e:
    print(f"   ❌ Error: {e}")

# 3. Final verification
print("\n3️⃣ Final verification of user_organizations...")
try:
    result = supabase.schema('core').table('user_organizations')\
        .select('*')\
        .eq('user_id', USER_ID)\
        .execute()

    if result.data:
        for row in result.data:
            print(f"   ✅ Record found:")
            print(f"      organization_id: {row.get('organization_id')}")
            print(f"      unified_role:    {row.get('unified_role')}")
            print(f"      is_active:       {row.get('is_active')}")
    else:
        print("   ❌ No record found!")
except Exception as e:
    print(f"   ❌ Error: {e}")

print("\n" + "=" * 70)
print("📋 Next Steps")
print("=" * 70)
print("""
1. User should LOG OUT and LOG BACK IN
   (This refreshes their JWT token with updated organization_id)

2. Clear browser cache / use incognito if still not working

3. If using Redis cache, it will auto-clear in 5 minutes
   Or manually clear via API: POST /api/permissions/cache/clear
""")
