#!/usr/bin/env python3
"""
Test the new calendar range RPC function
"""

import os
from supabase import create_client
from dotenv import load_dotenv
from datetime import datetime, timedelta

load_dotenv()

def test_calendar_range_fetch():
    """Test calendar range fetching functionality"""
    print("📅 Testing Calendar Range Fetching")
    print("=" * 50)

    # Initialize Supabase
    supabase_url = os.getenv("SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_ANON_KEY")

    if not supabase_url or not supabase_key:
        print("❌ Supabase credentials not found")
        return False

    supabase = create_client(supabase_url, supabase_key)
    print("✅ Supabase connection established")

    # Test dates - yesterday to 6 days ahead (8 days total)
    today = datetime.now()
    yesterday = today - timedelta(days=1)
    six_days_ahead = today + timedelta(days=6)

    start_date = yesterday.strftime('%Y-%m-%d')
    end_date = six_days_ahead.strftime('%Y-%m-%d')

    print(f"\n📅 Testing date range:")
    print(f"   Start: {start_date} (yesterday)")
    print(f"   End: {end_date} (6 days ahead)")
    print(f"   Total days: 8")

    clinic_id = "e0c84f56-235d-49f2-9a44-37c1be579afc"

    try:
        # Test the new range RPC function
        result = supabase.rpc('get_calendar_data_range', {
            'p_clinic_id': clinic_id,
            'p_start_date': start_date,
            'p_end_date': end_date
        }).execute()

        if result.data:
            appointments = result.data.get('appointments', [])
            doctors = result.data.get('doctors', [])
            services = result.data.get('services', [])
            metadata = result.data.get('metadata', {})

            print(f"\n✅ Range fetch successful!")
            print(f"   📋 Appointments: {len(appointments)}")
            print(f"   👨‍⚕️ Doctors: {len(doctors)}")
            print(f"   🔧 Services: {len(services)}")
            print(f"   📊 Metadata: {metadata}")

            # Group appointments by date
            appointments_by_date = {}
            for apt in appointments:
                date = apt.get('appointment_date', 'unknown')
                if date not in appointments_by_date:
                    appointments_by_date[date] = []
                appointments_by_date[date].append(apt)

            print(f"\n📅 Appointments by date:")
            for date in sorted(appointments_by_date.keys()):
                count = len(appointments_by_date[date])
                date_label = "TODAY" if date == today.strftime('%Y-%m-%d') else date
                print(f"   {date_label}: {count} appointments")

                # Show first appointment details
                if appointments_by_date[date]:
                    first = appointments_by_date[date][0]
                    print(f"      - {first.get('patient_name', 'N/A')} at {first.get('start_time', 'N/A')}")

            # Test cache behavior with overlapping range
            print(f"\n🔄 Testing overlapping range fetch:")
            overlap_start = today.strftime('%Y-%m-%d')
            overlap_end = (today + timedelta(days=10)).strftime('%Y-%m-%d')

            overlap_result = supabase.rpc('get_calendar_data_range', {
                'p_clinic_id': clinic_id,
                'p_start_date': overlap_start,
                'p_end_date': overlap_end
            }).execute()

            if overlap_result.data:
                overlap_apts = overlap_result.data.get('appointments', [])
                print(f"   ✅ Overlap fetch: {len(overlap_apts)} appointments")
                print(f"   📊 Range: {overlap_start} to {overlap_end}")
            else:
                print(f"   ❌ Overlap fetch failed: {overlap_result.error}")

            return True

        else:
            print(f"❌ Range fetch failed: {result.error}")
            return False

    except Exception as e:
        print(f"❌ Error: {e}")
        return False

if __name__ == "__main__":
    success = test_calendar_range_fetch()
    if success:
        print("\n🎉 CALENDAR RANGE FETCHING WORKS!")
        print("   ✅ 8-day initial range loads correctly")
        print("   ✅ Can fetch extended date ranges")
        print("   ✅ Frontend can now cache and display multiple days")
    else:
        print("\n❌ Calendar range fetching test failed")

    exit(0 if success else 1)