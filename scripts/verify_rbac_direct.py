#!/usr/bin/env python3
"""Verify RBAC setup using direct database connection (bypassing RLS)."""

import os
import sys
import asyncio
from dotenv import load_dotenv
import asyncpg

load_dotenv()

DATABASE_URL = os.getenv('SUPABASE_DB_URL') or os.getenv('DATABASE_URL')

if not DATABASE_URL:
    print("❌ Missing DATABASE_URL")
    sys.exit(1)

async def verify():
    print("=" * 70)
    print("🔍 Direct Database RBAC Verification (Bypasses RLS)")
    print("=" * 70)

    conn = await asyncpg.connect(DATABASE_URL)

    try:
        # 1. Check user_organizations
        print("\n1️⃣ Checking core.user_organizations...")
        rows = await conn.fetch("""
            SELECT * FROM core.user_organizations
            WHERE user_id = '88beb3b9-89a0-4902-ae06-5dae65f7b447'::uuid
        """)
        if rows:
            for row in rows:
                print(f"   ✅ Found record:")
                print(f"      organization_id: {row['organization_id']}")
                print(f"      role:            {row.get('role')}")
                print(f"      unified_role:    {row.get('unified_role')}")
                print(f"      is_active:       {row.get('is_active')}")
        else:
            print("   ❌ No record found!")

        # 2. Check permissions table
        print("\n2️⃣ Checking public.permissions table...")
        perms = await conn.fetch("SELECT * FROM public.permissions")
        print(f"   Found {len(perms)} permissions defined")
        if perms:
            for p in perms[:5]:
                print(f"      - {p['action']}")
            if len(perms) > 5:
                print(f"      ... and {len(perms) - 5} more")

        # 3. Check role_permissions for owner
        print("\n3️⃣ Checking public.role_permissions for 'owner'...")
        role_perms = await conn.fetch("""
            SELECT rp.*, p.action
            FROM public.role_permissions rp
            JOIN public.permissions p ON p.id = rp.permission_id
            WHERE rp.role = 'owner' AND rp.granted = true
        """)
        print(f"   Found {len(role_perms)} permissions for owner")
        if role_perms:
            has_calendar = any(r['action'] == 'appointments:view' for r in role_perms)
            print(f"   appointments:view granted: {has_calendar}")

        # 4. Check if permissions table has RLS
        print("\n4️⃣ Checking RLS policies on permissions...")
        policies = await conn.fetch("""
            SELECT tablename, policyname, cmd, qual
            FROM pg_policies
            WHERE schemaname IN ('public', 'core')
            AND tablename IN ('permissions', 'role_permissions', 'user_organizations')
        """)
        for p in policies:
            print(f"   {p['tablename']}: {p['policyname']} ({p['cmd']})")

    finally:
        await conn.close()

    print("\n" + "=" * 70)

asyncio.run(verify())
