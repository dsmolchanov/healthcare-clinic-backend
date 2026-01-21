#!/usr/bin/env python3
"""Final RBAC verification for user 88beb3b9-89a0-4902-ae06-5dae65f7b447."""

import os
import sys
import asyncio
from dotenv import load_dotenv
import asyncpg

load_dotenv()

DATABASE_URL = os.getenv('SUPABASE_DB_URL') or os.getenv('DATABASE_URL')

if not DATABASE_URL:
    print("❌ Missing SUPABASE_DB_URL")
    sys.exit(1)

USER_ID = '88beb3b9-89a0-4902-ae06-5dae65f7b447'
ORG_ID = '4e8ddba1-ad52-4613-9a03-ec64636b3f6c'

async def verify():
    print("=" * 70)
    print("🔍 Final RBAC Verification")
    print("=" * 70)
    print(f"User ID: {USER_ID}")
    print(f"Org ID:  {ORG_ID}")
    print("-" * 70)

    conn = await asyncpg.connect(DATABASE_URL, statement_cache_size=0)

    all_ok = True

    try:
        # 1. Check auth.users metadata
        print("\n1️⃣ User Metadata (auth.users)...")
        row = await conn.fetchrow("""
            SELECT raw_user_meta_data
            FROM auth.users
            WHERE id = $1::uuid
        """, USER_ID)
        if row:
            import json
            meta = row['raw_user_meta_data']
            if isinstance(meta, str):
                meta = json.loads(meta)
            org_id = meta.get('organization_id') if meta else None
            print(f"   organization_id: {org_id}")
            if org_id == ORG_ID:
                print(f"   ✅ Correct!")
            else:
                print(f"   ❌ Mismatch or missing!")
                all_ok = False
        else:
            print("   ❌ User not found!")
            all_ok = False

        # 2. Check user_organizations
        print("\n2️⃣ User-Org Association (core.user_organizations)...")
        row = await conn.fetchrow("""
            SELECT unified_role, is_active
            FROM core.user_organizations
            WHERE user_id = $1::uuid AND organization_id = $2::uuid
        """, USER_ID, ORG_ID)
        if row:
            print(f"   unified_role: {row['unified_role']}")
            print(f"   is_active:    {row['is_active']}")
            if row['unified_role'] == 'owner' and row['is_active']:
                print(f"   ✅ Correct!")
            else:
                print(f"   ❌ Wrong role or inactive!")
                all_ok = False
        else:
            print("   ❌ No association found!")
            all_ok = False

        # 3. Check role_permissions
        print("\n3️⃣ Role Permissions (public.role_permissions)...")
        row = await conn.fetchrow("""
            SELECT COUNT(*) as cnt
            FROM public.role_permissions rp
            JOIN public.permissions p ON p.id = rp.permission_id
            WHERE rp.role = 'owner' AND rp.granted = true
        """)
        perm_count = row['cnt'] if row else 0
        print(f"   Owner has {perm_count} permissions")

        # Check specific permission
        row = await conn.fetchrow("""
            SELECT 1
            FROM public.role_permissions rp
            JOIN public.permissions p ON p.id = rp.permission_id
            WHERE rp.role = 'owner' AND p.action = 'appointments:view' AND rp.granted = true
        """)
        if row:
            print(f"   ✅ appointments:view granted!")
        else:
            print(f"   ❌ appointments:view NOT granted!")
            all_ok = False

        # 4. Simulate what permission_service does
        print("\n4️⃣ Simulating Permission Service Query...")
        perms = await conn.fetch("""
            SELECT p.action
            FROM public.role_permissions rp
            JOIN public.permissions p ON p.id = rp.permission_id
            WHERE rp.role = (
                SELECT unified_role FROM core.user_organizations
                WHERE user_id = $1::uuid AND organization_id = $2::uuid AND is_active = true
            )
            AND rp.granted = true
        """, USER_ID, ORG_ID)
        print(f"   Would return {len(perms)} permissions:")
        for p in perms[:5]:
            print(f"      - {p['action']}")
        if len(perms) > 5:
            print(f"      ... and {len(perms) - 5} more")

        if any(p['action'] == 'appointments:view' for p in perms):
            print(f"\n   ✅ appointments:view WILL be returned!")
        else:
            print(f"\n   ❌ appointments:view WILL NOT be returned!")
            all_ok = False

    finally:
        await conn.close()

    print("\n" + "=" * 70)
    if all_ok:
        print("✅ ALL CHECKS PASSED!")
        print("\nThe user should now see the calendar topbar after:")
        print("1. Logging out")
        print("2. Logging back in (to refresh JWT token)")
        print("3. Waiting 5 minutes (or clearing Redis cache)")
    else:
        print("❌ SOME CHECKS FAILED - see above")
    print("=" * 70)

asyncio.run(verify())
