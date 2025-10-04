#!/usr/bin/env python3
"""
Simulate the exact frontend call that was failing
"""

import os
from supabase import create_client
from dotenv import load_dotenv

load_dotenv()

def test_frontend_simulation():
    """Test with the exact data from frontend error"""
    print("🎯 Frontend Simulation Test")
    print("Testing with exact data that was failing in frontend")
    print("=" * 60)

    # Initialize Supabase exactly like frontend (no explicit schema)
    supabase_url = os.getenv("SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_ANON_KEY")  # Frontend uses anon key

    if not supabase_url or not supabase_key:
        print("❌ Supabase credentials not found")
        return False

    supabase = create_client(supabase_url, supabase_key)
    print("✅ Supabase connection established (anon key)")

    # Test data from the frontend error log
    frontend_data = {
        'id': 'a2cffa7a-c474-4636-95bb-b78a063669fe',
        'clinic_id': 'e0c84f56-235d-49f2-9a44-37c1be579afc',
        'patient_id': '0ba4d0d4-5c93-4178-ace0-0fc8cf7b3225',
        'doctor_id': 'ac687c61-3d0c-4a7f-a4a5-fe57d7499e3a',
        'service_id': '486f1342-afe0-4f4a-9038-e519209c8118',
        'appointment_date': '2025-09-27',
        'start_time': '17:30:00',
        'end_time': '18:00:00',
        'duration_minutes': 30,
        'status': 'scheduled',
        'notes': 'Frontend test appointment'
    }

    print("📱 Simulating frontend call:")
    print(f"   POST /rest/v1/rpc/insert_appointment")
    print(f"   Data: {frontend_data}")

    try:
        # This is exactly what the frontend is doing
        result = supabase.rpc('insert_appointment', {'appointment_data': frontend_data}).execute()

        if result.data:
            if result.data.get('success', True):  # Handle both success formats
                appointment_id = result.data.get('id')
                print("\n✅ Frontend simulation SUCCESSFUL!")
                print(f"   🆔 Appointment ID: {appointment_id}")
                print(f"   📅 Date: {result.data.get('appointment_date')}")
                print(f"   ⏰ Time: {result.data.get('start_time')} - {result.data.get('end_time')}")
                print(f"   📋 Status: {result.data.get('status')}")
                print(f"   🏥 Type: {result.data.get('appointment_type', 'consultation')}")

                # Clean up
                try:
                    supabase.table("appointments").delete().eq("id", appointment_id).execute()
                    print("   🧹 Test appointment cleaned up")
                except Exception as cleanup_error:
                    print(f"   ⚠️ Cleanup note: {cleanup_error}")

                print("\n🎉 FRONTEND ISSUE RESOLVED!")
                print("   The insert_appointment RPC function is now working correctly")
                print("   Frontend should be able to create appointments without errors")
                return True

            else:
                print(f"\n❌ RPC returned error: {result.data.get('error')}")
                return False

        else:
            print(f"\n❌ RPC call failed: {result.error}")
            return False

    except Exception as e:
        print(f"\n❌ Simulation failed: {str(e)}")
        return False

if __name__ == "__main__":
    success = test_frontend_simulation()
    exit(0 if success else 1)