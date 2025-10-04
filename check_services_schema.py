import asyncio
import asyncpg
import os
from dotenv import load_dotenv

load_dotenv()

async def check_schema():
    conn = await asyncpg.connect(os.getenv('SUPABASE_DB_URL'))
    
    print("Checking existing tables and columns in healthcare schema...")
    
    # Check if services table exists and its columns
    services_exists = await conn.fetchval("""
        SELECT EXISTS (
            SELECT 1 FROM information_schema.tables 
            WHERE table_schema = 'healthcare' 
            AND table_name = 'services'
        )
    """)
    
    if services_exists:
        print("\n✅ Services table exists. Columns:")
        columns = await conn.fetch("""
            SELECT column_name, data_type, is_nullable
            FROM information_schema.columns
            WHERE table_schema = 'healthcare' 
            AND table_name = 'services'
            ORDER BY ordinal_position
        """)
        for col in columns:
            print(f"  - {col['column_name']}: {col['data_type']} (nullable: {col['is_nullable']})")
    else:
        print("\n❌ Services table does not exist")
    
    # Check all tables in healthcare schema
    print("\n All tables in healthcare schema:")
    tables = await conn.fetch("""
        SELECT table_name 
        FROM information_schema.tables 
        WHERE table_schema = 'healthcare'
        ORDER BY table_name
    """)
    for table in tables:
        print(f"  - {table['table_name']}")
    
    await conn.close()

asyncio.run(check_schema())