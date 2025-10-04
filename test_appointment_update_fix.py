#!/usr/bin/env python3
"""
Test the enhanced appointment update RPC function
Tests doctor change scenario specifically
"""

import os
from supabase import create_client
from dotenv import load_dotenv
import uuid

load_dotenv()

def test_appointment_update_with_doctor_change():
    """Test appointment update when changing doctors"""
    print("🧪 Testing Enhanced Appointment Update (Doctor Change)")
    print("=" * 60)

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
        # Get clinic and doctors for testing
        clinics = supabase.table("clinics").select("id").limit(1).execute()
        doctors = supabase.table("doctors").select("id, first_name, last_name, specialization").limit(2).execute()

        if not clinics.data or len(doctors.data) < 2:
            print("❌ Need at least 1 clinic and 2 doctors for testing")
            return False

        clinic_id = clinics.data[0]['id']
        doctor1 = doctors.data[0]
        doctor2 = doctors.data[1]

        print(f"🏥 Using clinic: {clinic_id}")
        print(f"👨‍⚕️ Doctor 1: {doctor1['first_name']} {doctor1['last_name']} ({doctor1['specialization']})")
        print(f"👨‍⚕️ Doctor 2: {doctor2['first_name']} {doctor2['last_name']} ({doctor2['specialization']})")

        # Create test patient
        test_patient_id = str(uuid.uuid4())
        patient_data = {
            "id": test_patient_id,
            "clinic_id": clinic_id,
            "first_name": "Test",
            "last_name": "Patient",
            "date_of_birth": "1990-01-01",
            "gender": "other",
            "phone": f"+1555{test_patient_id[:7]}",
            "email": f"test+{test_patient_id[:8]}@example.com"
        }

        supabase.table("patients").insert(patient_data).execute()
        print("✅ Test patient created")

        # Create test appointment with first doctor
        appointment_data = {
            "clinic_id": clinic_id,
            "patient_id": test_patient_id,
            "doctor_id": doctor1['id'],
            "appointment_type": "consultation",
            "appointment_date": "2025-12-31",
            "start_time": "23:00:00",
            "end_time": "23:30:00",
            "duration_minutes": 30,
            "status": "scheduled",
            "reason_for_visit": "Test appointment for doctor change",
            "notes": "Initial appointment with doctor 1"
        }

        result = supabase.rpc('insert_appointment', {'appointment_data': appointment_data}).execute()
        if not result.data:
            print("❌ Failed to create test appointment")
            return False

        appointment_id = result.data['id']
        print(f"✅ Test appointment created: {appointment_id}")
        print(f"   - Initial doctor: {doctor1['first_name']} {doctor1['last_name']}")

        # Test updating appointment to change doctor
        print("\n📝 Testing doctor change update...")
        update_data = {
            "doctor_id": doctor2['id'],
            "notes": "Updated to doctor 2",
            "reason_for_visit": "Moved to specialist"
        }

        update_result = supabase.rpc('update_healthcare_appointment', {
            'p_appointment_id': appointment_id,
            'p_updates': update_data
        }).execute()

        if hasattr(update_result, 'error') and update_result.error:
            print(f"❌ Update failed: {update_result.error}")
            return False

        response_data = update_result.data if hasattr(update_result, 'data') else update_result
        print("✅ Appointment update successful!")

        # Verify the response contains complete data
        if response_data.get('appointment'):
            apt_data = response_data['appointment']
            print(f"   📋 Updated appointment data:")
            print(f"      - ID: {apt_data['id']}")
            print(f"      - Doctor ID: {apt_data['doctor_id']}")
            print(f"      - Notes: {apt_data['notes']}")
            print(f"      - Reason: {apt_data['reason_for_visit']}")

        if response_data.get('doctor'):
            doctor_data = response_data['doctor']
            print(f"   👨‍⚕️ New doctor information:")
            print(f"      - Name: {doctor_data['name']}")
            print(f"      - Specialization: {doctor_data['specialization']}")
            print(f"      - Email: {doctor_data.get('email', 'N/A')}")

        if response_data.get('patient'):
            patient_data = response_data['patient']
            print(f"   👤 Patient information:")
            print(f"      - Name: {patient_data['name']}")

        # Verify the doctor was actually changed
        if (response_data.get('appointment', {}).get('doctor_id') == doctor2['id'] and
            response_data.get('doctor', {}).get('id') == doctor2['id']):
            print("\n🎉 DOCTOR CHANGE TEST PASSED!")
            print("   ✅ Appointment updated with new doctor")
            print("   ✅ Complete doctor information returned")
            print("   ✅ Frontend will now have all data needed to update UI")
        else:
            print("\n❌ Doctor change verification failed")
            return False

        # Clean up
        supabase.table("appointments").delete().eq("id", appointment_id).execute()
        supabase.table("patients").delete().eq("id", test_patient_id).execute()
        print("\n🧹 Test data cleaned up")

        return True

    except Exception as e:
        print(f"❌ Test failed: {str(e)}")
        return False

if __name__ == "__main__":
    success = test_appointment_update_with_doctor_change()
    exit(0 if success else 1)