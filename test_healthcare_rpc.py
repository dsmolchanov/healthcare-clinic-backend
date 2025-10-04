#!/usr/bin/env python3
"""
Test the healthcare schema insert_appointment RPC function
"""

import asyncio
import os
from supabase import create_client
from dotenv import load_dotenv
import uuid
from datetime import datetime, date, time

load_dotenv()

async def test_healthcare_insert_appointment_rpc():
    """Test the healthcare schema insert_appointment RPC function"""
    print("🧪 Testing Healthcare insert_appointment RPC Function")
    print("=" * 50)

    # Initialize Supabase with healthcare schema
    supabase_url = os.getenv("SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_SERVICE_KEY") or os.getenv("SUPABASE_ANON_KEY")

    if not supabase_url or not supabase_key:
        print("❌ Supabase credentials not found")
        return False

    from supabase.client import ClientOptions
    options = ClientOptions(schema='healthcare')
    supabase = create_client(supabase_url, supabase_key, options=options)
    print("✅ Healthcare schema connection established")

    try:
        # Get a test clinic and doctor
        clinics = supabase.table("clinics").select("id").limit(1).execute()
        doctors = supabase.table("doctors").select("id").limit(1).execute()

        if not clinics.data or not doctors.data:
            print("❌ No clinic or doctor found for testing")
            return False

        clinic_id = clinics.data[0]['id']
        doctor_id = doctors.data[0]['id']

        # Create test patient
        test_patient_id = str(uuid.uuid4())
        patient_data = {
            "id": test_patient_id,
            "clinic_id": clinic_id,
            "first_name": "Healthcare",
            "last_name": "RPCTest",
            "date_of_birth": "1990-01-01",
            "gender": "other",
            "phone": f"+1555{test_patient_id[:7]}",
            "email": f"healthcare.rpc+{test_patient_id[:8]}@example.com"
        }

        try:
            supabase.table("patients").insert(patient_data).execute()
            print("✅ Test patient created in healthcare schema")
        except Exception as e:
            if "duplicate key" not in str(e).lower():
                print(f"⚠️ Patient creation: {str(e)}")

        # Test RPC function with appointment data (healthcare schema structure)
        appointment_data = {
            "clinic_id": clinic_id,
            "patient_id": test_patient_id,
            "doctor_id": doctor_id,
            "appointment_type": "consultation",
            "appointment_date": "2025-10-01",
            "start_time": "14:00:00",
            "end_time": "14:30:00",
            "duration_minutes": 30,
            "status": "scheduled",
            "reason_for_visit": "Test healthcare RPC function",
            "chief_complaint": "Testing healthcare insert_appointment RPC",
            "notes": "This is a test appointment created via healthcare RPC"
        }

        print("\n📝 Testing healthcare RPC function...")
        result = supabase.rpc('insert_appointment', {'appointment_data': appointment_data}).execute()

        if result.data:
            appointment_id = result.data['id']
            print("✅ Healthcare RPC function executed successfully!")
            print(f"   - Appointment ID: {appointment_id}")
            print(f"   - Type: {result.data['appointment_type']}")
            print(f"   - Date: {result.data['appointment_date']}")
            print(f"   - Time: {result.data['start_time']} - {result.data['end_time']}")
            print(f"   - Duration: {result.data['duration_minutes']} minutes")
            print(f"   - Status: {result.data['status']}")
            print(f"   - Reason: {result.data['reason_for_visit']}")

            # Clean up test appointment
            supabase.table("appointments").delete().eq("id", appointment_id).execute()
            print("🧹 Test appointment cleaned up")

            # Clean up test patient
            supabase.table("patients").delete().eq("id", test_patient_id).execute()
            print("🧹 Test patient cleaned up")

            print("\n🎉 Healthcare RPC Function Test PASSED!")
            return True
        else:
            print("❌ Healthcare RPC function failed to return data")
            if result.error:
                print(f"   Error: {result.error}")
            return False

    except Exception as e:
        print(f"❌ Test failed: {str(e)}")
        return False

if __name__ == "__main__":
    success = asyncio.run(test_healthcare_insert_appointment_rpc())
    exit(0 if success else 1)