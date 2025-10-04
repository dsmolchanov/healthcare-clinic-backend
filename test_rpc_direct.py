#!/usr/bin/env python3
"""
Test calling the RPC function directly via psycopg2
"""
import os
from dotenv import load_dotenv
import psycopg2

# Load environment variables
load_dotenv()

# Parse DATABASE_URL
database_url = os.getenv('DATABASE_URL')

print("🧪 Testing RPC function via direct SQL\n")

conn = psycopg2.connect(database_url)
cur = conn.cursor()

# Test if function exists
cur.execute("""
    SELECT EXISTS (
        SELECT 1 FROM pg_proc p
        JOIN pg_namespace n ON p.pronamespace = n.oid
        WHERE n.nspname = 'healthcare'
        AND p.proname = 'save_calendar_integration'
    );
""")

exists = cur.fetchone()[0]
print(f"Function exists in healthcare schema: {exists}")

if exists:
    print("\n✅ Function found! Testing call...\n")

    # Test calling it
    try:
        cur.execute("""
            SELECT healthcare.save_calendar_integration(
                'e0c84f56-235d-49f2-9a44-37c1be579afc'::uuid,
                '4e8ddba1-ad52-4613-9a03-ec64636b3f6c'::uuid,
                'google',
                'primary',
                'test_vault_ref',
                'Test Calendar',
                '1',
                NULL
            );
        """)

        result = cur.fetchone()[0]
        conn.commit()

        print(f"✅ RPC call succeeded!")
        print(f"Result: {result}")

    except Exception as e:
        print(f"❌ RPC call failed: {e}")
        conn.rollback()
else:
    print("\n❌ Function NOT found in healthcare schema")
    print("\nLet me check what functions do exist:")

    cur.execute("""
        SELECT proname
        FROM pg_proc p
        JOIN pg_namespace n ON p.pronamespace = n.oid
        WHERE n.nspname = 'healthcare'
        AND proname LIKE '%calendar%'
        LIMIT 10;
    """)

    functions = cur.fetchall()
    for func in functions:
        print(f"  - {func[0]}")

cur.close()
conn.close()
