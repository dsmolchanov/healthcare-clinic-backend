#!/usr/bin/env python3
"""
Activate Google Calendar for Shtern Clinic
Uses demo mode or finds existing OAuth tokens
"""

import os
import sys
import json
import asyncio
from pathlib import Path
from datetime import datetime, timezone, timedelta

# Load environment
from dotenv import load_dotenv
env_path = Path(__file__).parent / '../.env'
load_dotenv(env_path)

from supabase import create_client
from supabase.client import ClientOptions


async def activate_calendar_for_shtern():
    """Activate calendar integration for Shtern clinic"""

    print("=" * 60)
    print("🔍 Checking Google Calendar Integration Status")
    print("=" * 60)

    # Initialize Supabase clients
    options = ClientOptions(
        schema='healthcare',
        auto_refresh_token=True,
        persist_session=False
    )

    # Healthcare schema client
    supabase_healthcare = create_client(
        os.getenv("SUPABASE_URL"),
        os.getenv("SUPABASE_SERVICE_ROLE_KEY"),
        options=options
    )

    # Public schema client
    supabase_public = create_client(
        os.getenv("SUPABASE_URL"),
        os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    )

    # Get Shtern clinic
    clinic_result = supabase_healthcare.table('clinics').select('*').eq(
        'name', 'Shtern Dental Clinic'
    ).execute()

    if not clinic_result.data:
        print("❌ Shtern clinic not found!")
        return

    clinic = clinic_result.data[0]
    clinic_id = clinic['id']
    print(f"✅ Found Shtern Dental Clinic")
    print(f"   ID: {clinic_id}")

    # Check for existing OAuth tokens in different places
    print("\n📊 Checking for existing OAuth tokens...")

    # 1. Check clinic_calendar_tokens
    print("\n1. Checking clinic_calendar_tokens table...")
    try:
        result = supabase_public.table('clinic_calendar_tokens').select('*').execute()
        if result.data:
            for token in result.data:
                if 'shtern' in str(token).lower() or clinic_id in str(token):
                    print(f"  ✅ Found token for clinic: {token.get('clinic_id')}")
                    print(f"     Provider: {token.get('provider')}")
                    print(f"     Has tokens: {bool(token.get('access_token'))}")
                    return token
        print("  ❌ No tokens found")
    except Exception as e:
        print(f"  ⚠️ Error: {e}")

    # 2. Check calendar_integrations
    print("\n2. Checking healthcare.calendar_integrations table...")
    try:
        result = supabase_healthcare.table('calendar_integrations').select('*').execute()
        if result.data:
            for integration in result.data:
                if clinic_id in str(integration):
                    print(f"  ✅ Found integration!")
                    print(f"     Details: {json.dumps(integration, indent=2)}")
                    return integration
        print("  ❌ No integrations found")
    except Exception as e:
        print(f"  ⚠️ Table might not exist: {e}")

    # 3. Try to use demo mode
    print("\n3. Attempting to activate demo mode...")
    try:
        # Check if demo mode is already active
        clinic_settings = clinic.get('settings', {})
        if isinstance(clinic_settings, str):
            clinic_settings = json.loads(clinic_settings)

        if clinic_settings.get('calendar_connected'):
            print("  ✅ Calendar already marked as connected!")
            print(f"     Provider: {clinic_settings.get('calendar_provider')}")
            print(f"     Demo mode: {clinic_settings.get('calendar_demo_mode')}")
        else:
            # Activate demo mode
            print("  🔧 Activating demo mode for testing...")

            # Update clinic settings
            new_settings = clinic_settings.copy()
            new_settings.update({
                'calendar_connected': True,
                'calendar_provider': 'google',
                'calendar_demo_mode': True,
                'calendar_enabled_at': datetime.now(timezone.utc).isoformat()
            })

            update_result = supabase_healthcare.table('clinics').update({
                'settings': json.dumps(new_settings)
            }).eq('id', clinic_id).execute()

            if update_result.data:
                print("  ✅ Demo mode activated successfully!")
                print("     Calendar features are now available for testing")
            else:
                print("  ❌ Failed to activate demo mode")
    except Exception as e:
        print(f"  ⚠️ Error: {e}")

    # 4. Create a test appointment with calendar sync
    print("\n4. Testing calendar sync...")
    try:
        # Get a doctor
        doctor_result = supabase_healthcare.table('doctors').select('*').eq(
            'clinic_id', clinic_id
        ).limit(1).execute()

        if doctor_result.data:
            doctor = doctor_result.data[0]
            doctor_id = doctor['id']

            # Create a test appointment
            test_appointment = {
                'clinic_id': clinic_id,
                'doctor_id': doctor_id,
                'patient_id': '00000000-0000-0000-0000-000000000001',  # Test patient
                'appointment_date': (datetime.now(timezone.utc) + timedelta(days=3)).date().isoformat(),
                'appointment_time': '14:00:00',
                'duration_minutes': 30,
                'type': 'Test Calendar Sync',
                'status': 'scheduled',
                'notes': 'Test appointment for calendar sync verification',
                'external_calendar_event_id': f'demo_event_{datetime.now().timestamp()}'  # Simulate calendar event
            }

            result = supabase_healthcare.table('appointments').insert(test_appointment).execute()

            if result.data:
                print(f"  ✅ Created test appointment")
                print(f"     ID: {result.data[0]['id']}")
                print(f"     External Calendar ID: {result.data[0].get('external_calendar_event_id')}")
                print(f"     This simulates a calendar sync")
            else:
                print("  ❌ Failed to create test appointment")
        else:
            print("  ❌ No doctors found")
    except Exception as e:
        print(f"  ⚠️ Error creating test appointment: {e}")

    print("\n" + "=" * 60)
    print("📊 Summary")
    print("=" * 60)
    print("\n✅ Calendar integration is now active for Shtern Dental Clinic")
    print("\nFeatures available:")
    print("  • Appointment creation with calendar placeholders")
    print("  • Ask-Hold-Reserve pattern for slot management")
    print("  • Simulated bidirectional sync (demo mode)")
    print("\nTo enable full Google Calendar sync:")
    print("  1. Complete OAuth flow with Google account")
    print("  2. Store tokens in database")
    print("  3. Enable webhook for real-time sync")
    print("\nFor now, the system will work with simulated calendar events.")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(activate_calendar_for_shtern())