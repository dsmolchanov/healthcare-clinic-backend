#!/usr/bin/env python3
"""Check full user metadata including clinic_id."""

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

USER_ID = '88beb3b9-89a0-4902-ae06-5dae65f7b447'

async def check():
    conn = await asyncpg.connect(DATABASE_URL, statement_cache_size=0)

    try:
        print("=" * 70)
        print("🔍 Checking Full User Metadata for Dani Castro")
        print("=" * 70)

        # Get user metadata
        row = await conn.fetchrow("""
            SELECT email, raw_user_meta_data
            FROM auth.users
            WHERE id = $1::uuid
        """, USER_ID)

        if row:
            meta = row['raw_user_meta_data']
            if isinstance(meta, str):
                meta = json.loads(meta)

            print(f"\nEmail: {row['email']}")
            print(f"\nFull metadata:")
            print(json.dumps(meta, indent=2))

            print("\n" + "-" * 70)
            print("Key fields for RequireOrganization check:")
            print(f"  organization_id: {meta.get('organization_id', 'MISSING!')}")
            print(f"  clinic_id:       {meta.get('clinic_id', 'MISSING!')}")

            if not meta.get('organization_id'):
                print("\n  ❌ organization_id is MISSING - user will redirect to /onboarding")
            if not meta.get('clinic_id'):
                print("\n  ❌ clinic_id is MISSING - user will redirect to /onboarding")

            if meta.get('organization_id') and meta.get('clinic_id'):
                print("\n  ✅ Both required fields are present!")

        # Also check what clinic this user should have
        print("\n" + "-" * 70)
        print("Checking clinics for user's organization...")
        clinics = await conn.fetch("""
            SELECT c.id, c.name
            FROM healthcare.clinics c
            WHERE c.organization_id = '4e8ddba1-ad52-4613-9a03-ec64636b3f6c'::uuid
        """)

        if clinics:
            print(f"Found {len(clinics)} clinic(s):")
            for c in clinics:
                print(f"  - {c['name']} (id: {c['id']})")
                # Use the first clinic
                if not meta.get('clinic_id'):
                    print(f"\n    👆 Use this clinic_id to fix the metadata!")
        else:
            print("  No clinics found for this organization!")

    finally:
        await conn.close()

asyncio.run(check())
