#!/usr/bin/env python3
"""Find user by email to verify correct ID."""

import os
import sys
import asyncio
from dotenv import load_dotenv
import asyncpg
import json

load_dotenv()

DATABASE_URL = os.getenv('SUPABASE_DB_URL') or os.getenv('DATABASE_URL')

if not DATABASE_URL:
    print("❌ Missing SUPABASE_DB_URL")
    sys.exit(1)

async def find_user():
    conn = await asyncpg.connect(DATABASE_URL, statement_cache_size=0)

    try:
        print("=" * 70)
        print("🔍 Looking for user: trener2055@gmail.com (Dani Castro)")
        print("=" * 70)

        # Find user by email
        row = await conn.fetchrow("""
            SELECT id, email, raw_user_meta_data
            FROM auth.users
            WHERE email = 'trener2055@gmail.com'
        """)

        if row:
            meta = row['raw_user_meta_data']
            if isinstance(meta, str):
                meta = json.loads(meta)

            print(f"\n✅ Found user:")
            print(f"   ID:    {row['id']}")
            print(f"   Email: {row['email']}")
            print(f"   Name:  {meta.get('full_name') if meta else 'N/A'}")
            print(f"   Org:   {meta.get('organization_id') if meta else 'N/A'}")

            expected_id = '88beb3b9-89a0-4902-ae06-5dae65f7b447'
            if str(row['id']) == expected_id:
                print(f"\n   ✅ ID MATCHES the one we fixed!")
            else:
                print(f"\n   ❌ ID DOES NOT MATCH!")
                print(f"      Expected: {expected_id}")
                print(f"      Actual:   {row['id']}")
                print(f"\n   Need to apply fixes for the correct user ID!")
        else:
            print("\n❌ User not found with email: trener2055@gmail.com")

        # Also check what user has ID 88beb3b9-89a0-4902-ae06-5dae65f7b447
        print("\n" + "-" * 70)
        print("Checking who has ID 88beb3b9-89a0-4902-ae06-5dae65f7b447:")
        row2 = await conn.fetchrow("""
            SELECT id, email, raw_user_meta_data
            FROM auth.users
            WHERE id = '88beb3b9-89a0-4902-ae06-5dae65f7b447'::uuid
        """)

        if row2:
            meta2 = row2['raw_user_meta_data']
            if isinstance(meta2, str):
                meta2 = json.loads(meta2)
            print(f"   Email: {row2['email']}")
            print(f"   Name:  {meta2.get('full_name') if meta2 else 'N/A'}")
        else:
            print("   No user found with that ID")

    finally:
        await conn.close()

asyncio.run(find_user())
