#!/usr/bin/env python3
"""
Check existing Google Calendar integration for Shtern clinic
"""

import os
import sys
from pathlib import Path
from datetime import datetime, timedelta
import asyncio

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent))

# Load environment
from dotenv import load_dotenv
load_dotenv('.env')

from supabase import create_client
from supabase.client import ClientOptions


async def check_shtern_calendar_integration():
    """Check all calendar-related data for Shtern clinic"""

    # Initialize Supabase with healthcare schema
    options = ClientOptions(
        schema='healthcare',
        auto_refresh_token=True,
        persist_session=False
    )

    supabase = create_client(
        os.getenv("SUPABASE_URL"),
        os.getenv("SUPABASE_SERVICE_ROLE_KEY"),
        options=options
    )

    print("=" * 60)
    print("🔍 Checking Shtern Clinic Calendar Integration")
    print("=" * 60)

    # Check clinic_calendar_tokens table
    print("\n1. Checking clinic_calendar_tokens...")
    try:
        # Switch to public schema for this table
        public_supabase = create_client(
            os.getenv("SUPABASE_URL"),
            os.getenv("SUPABASE_SERVICE_ROLE_KEY")
        )

        # Try multiple queries to find Shtern clinic data
        results = []

        # Query 1: exact match
        try:
            r1 = public_supabase.table('clinic_calendar_tokens').select('*').eq(
                'clinic_id', 'shtern_dental_clinic'
            ).execute()
            if r1.data:
                results.extend(r1.data)
        except:
            pass

        # Query 2: with hyphen
        try:
            r2 = public_supabase.table('clinic_calendar_tokens').select('*').eq(
                'clinic_id', 'shtern-dental-clinic'
            ).execute()
            if r2.data:
                results.extend(r2.data)
        except:
            pass

        # Query 3: all tokens
        try:
            r3 = public_supabase.table('clinic_calendar_tokens').select('*').execute()
            if r3.data:
                for token in r3.data:
                    if 'shtern' in str(token.get('clinic_id', '')).lower():
                        results.append(token)
        except:
            pass

        result = type('obj', (object,), {'data': results})

        if result.data:
            print(f"  ✅ Found {len(result.data)} calendar token(s):")
            for token in result.data:
                print(f"    - Provider: {token.get('provider')}")
                print(f"      Clinic ID: {token.get('clinic_id')}")
                print(f"      Created: {token.get('created_at')}")
                print(f"      Expires: {token.get('expires_at')}")
                print(f"      Has Access Token: {'Yes' if token.get('access_token') else 'No'}")
                print(f"      Has Refresh Token: {'Yes' if token.get('refresh_token') else 'No'}")
        else:
            print("  ❌ No calendar tokens found")
    except Exception as e:
        print(f"  ⚠️ Error checking clinic_calendar_tokens: {e}")

    # Check calendar_connections table
    print("\n2. Checking calendar_connections...")
    try:
        # Check all calendar connections
        result = public_supabase.table('calendar_connections').select('*').execute()

        if result.data:
            print(f"  ✅ Found {len(result.data)} calendar connection(s):")
            for conn in result.data:
                print(f"    - Provider: {conn.get('provider')}")
                print(f"      Doctor ID: {conn.get('doctor_id')}")
                print(f"      Calendar ID: {conn.get('calendar_id')}")
                print(f"      Last Sync: {conn.get('last_sync_at')}")
                print(f"      Active: {conn.get('is_active')}")
        else:
            print("  ❌ No calendar connections found")
    except Exception as e:
        print(f"  ⚠️ Error checking calendar_connections: {e}")

    # Check calendar_integrations in healthcare schema
    print("\n3. Checking healthcare.calendar_integrations...")
    try:
        # Check all calendar integrations
        result = supabase.table('calendar_integrations').select('*').execute()

        if result.data:
            print(f"  ✅ Found {len(result.data)} integration(s):")
            for integ in result.data:
                print(f"    - Provider: {integ.get('provider')}")
                print(f"      Calendar Name: {integ.get('calendar_name')}")
                print(f"      User Email: {integ.get('user_email')}")
                print(f"      Created: {integ.get('created_at')}")
        else:
            print("  ❌ No calendar integrations found")
    except Exception as e:
        print(f"  ⚠️ Error checking calendar_integrations: {e}")

    # Check if Shtern clinic exists
    print("\n4. Checking if Shtern clinic exists...")
    try:
        # Check all clinics
        result = supabase.table('clinics').select('*').execute()

        if result.data:
            print(f"  ✅ Found {len(result.data)} clinic(s):")
            for clinic in result.data:
                print(f"    - ID: {clinic.get('id')}")
                print(f"      Name: {clinic.get('name')}")
                print(f"      Phone: {clinic.get('phone')}")
        else:
            print("  ❌ Shtern clinic not found")
    except Exception as e:
        print(f"  ⚠️ Error checking clinics: {e}")

    # Check for any doctors in Shtern clinic
    print("\n5. Checking doctors in Shtern clinic...")
    try:
        # Check all doctors
        result = supabase.table('doctors').select('*').limit(10).execute()

        if result.data:
            print(f"  ✅ Found {len(result.data)} doctor(s):")
            for doctor in result.data:
                print(f"    - ID: {doctor.get('id')}")
                print(f"      Name: {doctor.get('name')}")
                print(f"      Specialization: {doctor.get('specialization')}")
        else:
            print("  ❌ No doctors found for Shtern clinic")
    except Exception as e:
        print(f"  ⚠️ Error checking doctors: {e}")

    # Check recent calendar sync logs
    print("\n6. Checking recent calendar sync logs...")
    try:
        # Check recent sync logs
        result = supabase.table('calendar_sync_log').select('*').limit(5).order('created_at', desc=True).execute()

        if result.data:
            print(f"  ✅ Found {len(result.data)} recent log entries:")
            for log in result.data:
                print(f"    - Action: {log.get('action')}")
                print(f"      Provider: {log.get('provider')}")
                print(f"      Success: {log.get('success')}")
                print(f"      Created: {log.get('created_at')}")
        else:
            print("  ℹ️ No sync logs found")
    except Exception as e:
        print(f"  ⚠️ Error checking sync logs: {e}")

    # Check for any appointments with Google Calendar event IDs
    print("\n7. Checking appointments with Google Calendar events...")
    try:
        # Check recent appointments
        result = supabase.table('appointments').select('*').limit(5).order('created_at', desc=True).execute()

        if result.data:
            print(f"  ✅ Found {len(result.data)} appointment(s) with calendar events:")
            for apt in result.data:
                print(f"    - Appointment ID: {apt.get('id')}")
                print(f"      Event ID: {apt.get('external_calendar_event_id')}")
                print(f"      Doctor ID: {apt.get('doctor_id')}")
                print(f"      Date: {apt.get('appointment_date')}")
        else:
            print("  ℹ️ No appointments linked to calendar events")
    except Exception as e:
        print(f"  ⚠️ Error checking appointments: {e}")

    print("\n" + "=" * 60)
    print("📊 Summary")
    print("=" * 60)

    # Check environment variables
    print("\n8. Environment Variables Check:")
    google_vars = {
        'GOOGLE_CLIENT_ID': os.getenv('GOOGLE_CLIENT_ID'),
        'GOOGLE_CLIENT_SECRET': os.getenv('GOOGLE_CLIENT_SECRET'),
        'GOOGLE_REDIRECT_URI': os.getenv('GOOGLE_REDIRECT_URI'),
    }

    for key, value in google_vars.items():
        if value:
            print(f"  ✅ {key}: {'*' * 10} (configured)")
        else:
            print(f"  ❌ {key}: Not configured")

    print("\n" + "=" * 60)
    print("If you have already granted OAuth permissions, the tokens should")
    print("appear in one of the tables above. If not, you may need to:")
    print("1. Re-authorize the calendar connection")
    print("2. Check if tokens expired and need refresh")
    print("3. Verify the correct clinic_id is being used")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(check_shtern_calendar_integration())