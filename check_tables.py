import asyncio
import asyncpg
from dotenv import load_dotenv
import os

load_dotenv()

async def check_tables():
    # Get database URL from environment
    database_url = os.getenv('DATABASE_URL')
    if not database_url:
        # Build from components
        db_host = os.getenv('DB_HOST', 'localhost')
        db_port = os.getenv('DB_PORT', '5432')
        db_name = os.getenv('DB_NAME', 'postgres')
        db_user = os.getenv('DB_USER', 'postgres')
        db_password = os.getenv('DB_PASSWORD', '')

        database_url = f"postgresql://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}"

    # Connect to database
    conn = await asyncpg.connect(database_url)

    try:
        # Get all tables
        tables = await conn.fetch("""
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'public'
            AND table_type = 'BASE TABLE'
            ORDER BY table_name
        """)

        print("📊 Tables in database:")
        for table in tables:
            print(f"  - {table['table_name']}")

        # Check for specific tables we're looking for
        print("\n🔍 Checking for specialty/assignment tables:")
        specialty_tables = await conn.fetch("""
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'public'
            AND table_type = 'BASE TABLE'
            AND (table_name LIKE '%specialty%' OR table_name LIKE '%assignment%')
            ORDER BY table_name
        """)

        if specialty_tables:
            for table in specialty_tables:
                print(f"  ✅ Found: {table['table_name']}")

                # Get columns for this table
                columns = await conn.fetch("""
                    SELECT column_name, data_type
                    FROM information_schema.columns
                    WHERE table_name = $1
                    ORDER BY ordinal_position
                """, table['table_name'])

                print(f"     Columns:")
                for col in columns:
                    print(f"       - {col['column_name']} ({col['data_type']})")
        else:
            print("  ❌ No specialty or assignment tables found")

        # Check for doctor-related tables
        print("\n🔍 Checking doctor-related tables:")
        doctor_tables = await conn.fetch("""
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'public'
            AND table_type = 'BASE TABLE'
            AND table_name LIKE '%doctor%'
            ORDER BY table_name
        """)

        for table in doctor_tables:
            print(f"  - {table['table_name']}")

        # Check for service-related tables
        print("\n🔍 Checking service-related tables:")
        service_tables = await conn.fetch("""
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'public'
            AND table_type = 'BASE TABLE'
            AND table_name LIKE '%service%'
            ORDER BY table_name
        """)

        for table in service_tables:
            print(f"  - {table['table_name']}")

    finally:
        await conn.close()

if __name__ == "__main__":
    asyncio.run(check_tables())