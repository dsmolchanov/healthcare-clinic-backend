#!/usr/bin/env python3
"""
Rebuild search vectors for services with Russian configuration.
Processes in batches to avoid stack depth issues.
"""

import asyncio
import asyncpg
import os
from dotenv import load_dotenv

# Load environment
load_dotenv()

async def rebuild_search_vectors():
    """Rebuild search vectors in batches"""

    # Connect to database (disable statement caching for pgbouncer compatibility)
    db_url = os.getenv('DATABASE_URL') or os.getenv('SUPABASE_DB_URL')
    if not db_url:
        raise ValueError("No DATABASE_URL or SUPABASE_DB_URL found in environment")
    conn = await asyncpg.connect(db_url, statement_cache_size=0)

    try:
        # Get total count
        total = await conn.fetchval(
            "SELECT COUNT(*) FROM healthcare.services"
        )
        print(f"📊 Found {total} services to update")

        # Trigger the trigger by updating a field (name = name)
        # This avoids the complex tsvector concatenation in SQL
        services = await conn.fetch(
            "SELECT id FROM healthcare.services ORDER BY id"
        )

        updated = 0
        for i, service in enumerate(services, 1):
            if i % 10 == 0:
                print(f"Progress: {i}/{total}")

            # Update name = name to trigger the search_vector update
            await conn.execute("""
                UPDATE healthcare.services
                SET name = name
                WHERE id = $1
            """, service['id'])
            updated += 1

        print(f"✅ Successfully updated {updated} services")

        # Test the search
        print("\n🔍 Testing search with 'пломба'...")
        results = await conn.fetch("""
            SELECT name, category, description
            FROM public.search_services(
                (SELECT id FROM healthcare.clinics LIMIT 1),
                'пломба',
                5
            )
        """)

        if results:
            print(f"✅ Found {len(results)} matching services:")
            for r in results:
                print(f"  - {r['name']} ({r['category']})")
        else:
            print("❌ No results found")

    finally:
        await conn.close()

if __name__ == "__main__":
    asyncio.run(rebuild_search_vectors())
