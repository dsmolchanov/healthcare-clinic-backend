#!/usr/bin/env python3
"""
Test Google Calendar sync for Shtern clinic using demo mode
This tests the calendar functionality without requiring OAuth
"""

import os
import sys
import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent))

# Load environment
from dotenv import load_dotenv
load_dotenv('.env')

# Import after env is loaded
from supabase import create_client
from supabase.client import ClientOptions
from app.services.external_calendar_service import ExternalCalendarService


async def test_shtern_calendar():
    """Test calendar operations for Shtern clinic"""

    print("=" * 60)
    print("🧪 Testing Calendar Operations for Shtern Clinic")
    print("=" * 60)

    # Configure client to use healthcare schema
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

    # Initialize calendar service
    calendar_service = ExternalCalendarService(supabase)

    # Get Shtern clinic details
    print("\n1. Getting Shtern clinic details...")
    clinic_result = supabase.table('clinics').select('*').eq(
        'name', 'Shtern Dental Clinic'
    ).execute()

    if not clinic_result.data:
        print("❌ Shtern clinic not found!")
        return

    clinic = clinic_result.data[0]
    clinic_id = clinic['id']
    print(f"✅ Found Shtern clinic: {clinic_id}")

    # Get a doctor from Shtern clinic
    print("\n2. Getting doctors from Shtern clinic...")
    doctor_result = supabase.table('doctors').select('*').eq(
        'clinic_id', clinic_id
    ).limit(1).execute()

    if not doctor_result.data:
        print("❌ No doctors found for Shtern clinic!")
        return

    doctor = doctor_result.data[0]
    doctor_id = doctor['id']
    print(f"✅ Using doctor: {doctor_id}")
    print(f"   Specialization: {doctor.get('specialization')}")

    # Test appointment availability check
    print("\n3. Testing appointment availability...")
    start_time = datetime.now(timezone.utc) + timedelta(days=1, hours=10)
    end_time = start_time + timedelta(hours=1)

    appointment_data = {
        "patient_id": "test_patient_001",
        "patient_name": "Test Patient",
        "type": "consultation",
        "notes": "Test appointment for calendar integration",
        "clinic_id": clinic_id
    }

    try:
        success, result = await calendar_service.ask_hold_reserve(
            doctor_id=doctor_id,
            start_time=start_time,
            end_time=end_time,
            appointment_data=appointment_data
        )

        if success:
            print(f"✅ Appointment slot available and reserved!")
            print(f"   Reservation ID: {result.get('reservation_id')}")
            print(f"   Status: {result.get('status')}")

            # Clean up the test reservation
            if result.get('reservation_id'):
                print("\n4. Cleaning up test reservation...")
                await calendar_service._rollback_holds(result.get('reservation_id'))
                print("✅ Test reservation cleaned up")
        else:
            print(f"⚠️ Appointment slot not available or reservation failed")
            print(f"   Error: {result.get('error')}")
    except Exception as e:
        print(f"❌ Error testing appointment: {e}")

    # Check existing appointments
    print("\n5. Checking existing appointments...")
    appointments_result = supabase.table('appointments').select('*').eq(
        'clinic_id', clinic_id
    ).limit(5).order('appointment_date', desc=True).execute()

    if appointments_result.data:
        print(f"✅ Found {len(appointments_result.data)} recent appointments:")
        for apt in appointments_result.data:
            print(f"   - Date: {apt.get('appointment_date')}")
            print(f"     Doctor: {apt.get('doctor_id')}")
            print(f"     Status: {apt.get('status')}")
            print(f"     External Calendar: {apt.get('external_calendar_event_id') or 'Not synced'}")
    else:
        print("ℹ️ No appointments found")

    print("\n" + "=" * 60)
    print("📊 Summary")
    print("=" * 60)
    print(f"Clinic: {clinic['name']}")
    print(f"Clinic ID: {clinic_id}")
    print(f"Number of doctors: {len(doctor_result.data)}")
    print(f"Calendar sync: Not configured (OAuth needed)")
    print("\nTo enable Google Calendar sync:")
    print("1. Run the OAuth flow to connect Google Calendar")
    print("2. Grant calendar permissions")
    print("3. Tokens will be stored for automatic sync")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(test_shtern_calendar())