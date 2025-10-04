#!/usr/bin/env python3
"""
Test what calendar data is returned for today's date
"""

import os
from supabase import create_client
from dotenv import load_dotenv
from datetime import datetime

load_dotenv()

def test_calendar_data_for_today():
    """Test calendar data fetch for today"""
    print("📅 Testing Calendar Data for Today")
    print("=" * 50)

    # Initialize Supabase (default schema like frontend)
    supabase_url = os.getenv("SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_ANON_KEY")

    if not supabase_url or not supabase_key:
        print("❌ Supabase credentials not found")
        return False

    supabase = create_client(supabase_url, supabase_key)
    print("✅ Supabase connection established")

    # Test dates
    today = datetime.now()
    today_local = today.strftime('%Y-%m-%d')
    today_utc = today.utcnow().strftime('%Y-%m-%d')
    yesterday = (today.replace(day=today.day-1)).strftime('%Y-%m-%d')

    print(f"\n📅 Date variations:")
    print(f"   Today (local): {today_local}")
    print(f"   Today (UTC): {today_utc}")
    print(f"   Yesterday: {yesterday}")

    clinic_id = "e0c84f56-235d-49f2-9a44-37c1be579afc"

    for test_date, date_label in [(today_local, "Today Local"), (today_utc, "Today UTC"), (yesterday, "Yesterday")]:
        print(f"\n🔍 Testing {date_label} ({test_date}):")

        try:
            result = supabase.rpc('get_calendar_data', {
                'p_clinic_id': clinic_id,
                'p_date': test_date
            }).execute()

            if result.data:
                appointments = result.data.get('appointments', [])
                print(f"   ✅ Found {len(appointments)} appointments")

                for i, apt in enumerate(appointments[:3]):  # Show first 3
                    print(f"     {i+1}. {apt.get('patient_name', 'N/A')} - {apt.get('appointment_date', 'N/A')} at {apt.get('time', apt.get('start_time', 'N/A'))}")

                if len(appointments) > 3:
                    print(f"     ... and {len(appointments) - 3} more")
            else:
                print(f"   ❌ No data returned: {result.error}")

        except Exception as e:
            print(f"   ❌ Error: {e}")

    # Also check raw appointments table
    print(f"\n📊 Raw appointments check:")
    try:
        from supabase.client import ClientOptions
        healthcare_options = ClientOptions(schema='healthcare')
        healthcare_supabase = create_client(supabase_url, supabase_key, options=healthcare_options)

        # Check today's appointments directly
        today_apts = healthcare_supabase.table('appointments').select('*').eq('appointment_date', today_local).execute()
        print(f"   📅 Today ({today_local}): {len(today_apts.data) if today_apts.data else 0} appointments")

        yesterday_apts = healthcare_supabase.table('appointments').select('*').eq('appointment_date', yesterday).execute()
        print(f"   📅 Yesterday ({yesterday}): {len(yesterday_apts.data) if yesterday_apts.data else 0} appointments")

        if today_apts.data:
            print(f"   📋 Today's appointments:")
            for apt in today_apts.data:
                print(f"     - {apt.get('id')} at {apt.get('start_time')} (status: {apt.get('status')})")

    except Exception as e:
        print(f"   ❌ Raw check error: {e}")

if __name__ == "__main__":
    test_calendar_data_for_today()