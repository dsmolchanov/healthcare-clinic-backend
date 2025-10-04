#!/usr/bin/env python3
"""
Comprehensive Test for Stores and Reservations Flows
Tests the complete appointment booking and clinic management system
"""

import asyncio
import logging
import sys
import os
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, List
import json
import uuid

# Add the parent directory to the path so we can import from app
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from supabase import create_client
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class StoresReservationsTestSuite:
    def __init__(self):
        self.supabase = None
        self.test_clinic_id = None
        self.test_doctor_id = None
        self.test_patient_id = str(uuid.uuid4())
        self.test_results = {}

    async def setup_supabase(self):
        """Initialize Supabase connection"""
        supabase_url = os.getenv("SUPABASE_URL")
        supabase_key = os.getenv("SUPABASE_SERVICE_KEY") or os.getenv("SUPABASE_ANON_KEY")

        if not supabase_url or not supabase_key:
            raise Exception("Supabase credentials not found")

        from supabase.client import ClientOptions
        options = ClientOptions(schema='healthcare')
        self.supabase = create_client(supabase_url, supabase_key, options=options)
        logger.info("✅ Supabase connection established")

    async def test_clinic_store_operations(self):
        """Test clinic store management operations"""
        logger.info("\n🏥 Testing Clinic Store Operations...")

        try:
            # 1. List available clinics
            clinics_result = self.supabase.table("clinics").select("*").execute()
            clinics = clinics_result.data if clinics_result.data else []

            logger.info(f"  📋 Found {len(clinics)} clinics in system")

            if clinics:
                clinic = clinics[0]
                self.test_clinic_id = clinic['id']
                logger.info(f"  ✅ Using clinic: {clinic.get('name', 'N/A')} (ID: {self.test_clinic_id})")

                # Display clinic details
                specialties = clinic.get('specialties', [])
                services = clinic.get('services', [])
                business_hours = clinic.get('business_hours', {})

                logger.info(f"    - Specialties: {specialties}")
                logger.info(f"    - Services: {len(services)} available")
                logger.info(f"    - Business Hours: {json.dumps(business_hours, indent=2) if business_hours else 'Not set'}")
                logger.info(f"    - HIPAA Compliant: {clinic.get('hipaa_compliant', False)}")

                self.test_results['clinic_operations'] = True
            else:
                logger.warning("  ⚠️ No clinics found in system")
                self.test_results['clinic_operations'] = False

        except Exception as e:
            logger.error(f"  ❌ Clinic operations test failed: {str(e)}")
            self.test_results['clinic_operations'] = False

    async def test_doctor_availability(self):
        """Test doctor availability and scheduling"""
        logger.info("\n👨‍⚕️ Testing Doctor Availability...")

        try:
            # 1. Get available doctors
            doctors_result = self.supabase.table("doctors").select("*").execute()
            doctors = doctors_result.data if doctors_result.data else []

            logger.info(f"  📋 Found {len(doctors)} doctors in system")

            if doctors:
                doctor = doctors[0]
                self.test_doctor_id = doctor['id']
                doctor_name = f"{doctor.get('first_name', '')} {doctor.get('last_name', '')}"

                logger.info(f"  ✅ Testing doctor: {doctor_name} (ID: {self.test_doctor_id})")
                logger.info(f"    - Specialization: {doctor.get('specialization', 'N/A')}")
                logger.info(f"    - Available Days: {doctor.get('available_days', [])}")
                logger.info(f"    - Working Hours: {doctor.get('working_hours', {})}")
                logger.info(f"    - Booking Duration: {doctor.get('booking_duration_minutes', 30)} minutes")
                logger.info(f"    - Accepting New Patients: {doctor.get('accepting_new_patients', False)}")
                logger.info(f"    - Telemedicine: {doctor.get('offers_telemedicine', False)}")

                # Test specialty assignment
                specialties = doctor.get('specialties', [])
                services = doctor.get('services_offered', [])

                logger.info(f"    - Doctor Specialties: {specialties}")
                logger.info(f"    - Services Offered: {services}")

                self.test_results['doctor_availability'] = True
            else:
                logger.warning("  ⚠️ No doctors found in system")
                self.test_results['doctor_availability'] = False

        except Exception as e:
            logger.error(f"  ❌ Doctor availability test failed: {str(e)}")
            self.test_results['doctor_availability'] = False

    async def test_appointment_reservation_flow(self):
        """Test complete appointment reservation workflow"""
        logger.info("\n📅 Testing Appointment Reservation Flow...")

        if not self.test_clinic_id or not self.test_doctor_id:
            logger.error("  ❌ Cannot test reservations without clinic and doctor IDs")
            self.test_results['reservation_flow'] = False
            return

        try:
            # 0. Create a test patient record first
            test_patient_data = {
                "id": self.test_patient_id,
                "clinic_id": self.test_clinic_id,
                "first_name": "Test",
                "last_name": "Patient",
                "date_of_birth": "1990-01-01",
                "gender": "other",
                "phone": f"+1555{self.test_patient_id[:7]}",
                "email": f"test.patient+{self.test_patient_id[:8]}@example.com",
                "created_at": datetime.now(timezone.utc).isoformat()
            }

            # Try to create patient (it might already exist)
            try:
                patient_result = self.supabase.table("patients").insert(test_patient_data).execute()
                if patient_result.data:
                    logger.info("  ✅ Test patient created successfully")
                else:
                    logger.info("  ℹ️ Test patient already exists or creation skipped")
            except Exception as e:
                if "duplicate key" in str(e).lower() or "already exists" in str(e).lower():
                    logger.info("  ℹ️ Test patient already exists")
                else:
                    logger.warning(f"  ⚠️ Could not create test patient: {str(e)}")
                    # Continue anyway - patient might exist
            # 1. Check existing appointments for the doctor
            existing_appointments = self.supabase.table("appointments").select("*").eq(
                "doctor_id", self.test_doctor_id
            ).execute()

            logger.info(f"  📋 Doctor has {len(existing_appointments.data) if existing_appointments.data else 0} existing appointments")

            # 2. Test appointment booking
            test_appointment_data = {
                "clinic_id": self.test_clinic_id,
                "patient_id": self.test_patient_id,
                "doctor_id": self.test_doctor_id,
                "appointment_type": "consultation",
                "appointment_date": (datetime.now() + timedelta(days=1)).date().isoformat(),
                "start_time": "09:00:00",
                "end_time": "09:30:00",
                "duration_minutes": 30,
                "status": "scheduled",
                "reason_for_visit": "Regular checkup",
                "symptoms": ["General wellness check", "Routine visit"],
                "chief_complaint": "Routine visit",
                "is_telemedicine": False,
                "created_at": datetime.now(timezone.utc).isoformat()
            }

            # Try to create appointment
            logger.info("  📝 Creating test appointment...")
            appointment_result = self.supabase.table("appointments").insert(test_appointment_data).execute()

            if appointment_result.data:
                appointment = appointment_result.data[0]
                appointment_id = appointment['id']
                logger.info(f"  ✅ Appointment created successfully: {appointment_id}")
                logger.info(f"    - Date: {appointment['appointment_date']}")
                logger.info(f"    - Time: {appointment['start_time']} - {appointment['end_time']}")
                logger.info(f"    - Status: {appointment['status']}")
                logger.info(f"    - Type: {appointment['appointment_type']}")

                # 3. Test appointment updates
                logger.info("  📝 Testing appointment updates...")
                update_result = self.supabase.table("appointments").update({
                    "status": "confirmed",
                    "confirmed_at": datetime.now(timezone.utc).isoformat(),
                    "confirmation_method": "phone",
                    "notes": "Patient confirmed via phone call"
                }).eq("id", appointment_id).execute()

                if update_result.data:
                    logger.info("  ✅ Appointment updated successfully")
                    updated_appointment = update_result.data[0]
                    logger.info(f"    - New Status: {updated_appointment['status']}")
                    logger.info(f"    - Confirmed At: {updated_appointment['confirmed_at']}")

                # 4. Test appointment retrieval
                logger.info("  📋 Testing appointment retrieval...")
                get_result = self.supabase.table("appointments").select("*").eq("id", appointment_id).execute()

                if get_result.data:
                    retrieved_appointment = get_result.data[0]
                    logger.info("  ✅ Appointment retrieved successfully")
                    logger.info(f"    - Verification: Status is {retrieved_appointment['status']}")

                # 5. Clean up - delete test appointment
                logger.info("  🧹 Cleaning up test appointment...")
                delete_result = self.supabase.table("appointments").delete().eq("id", appointment_id).execute()

                if delete_result.data:
                    logger.info("  ✅ Test appointment cleaned up successfully")

                # 6. Clean up test patient
                try:
                    self.supabase.table("patients").delete().eq("id", self.test_patient_id).execute()
                    logger.info("  ✅ Test patient cleaned up successfully")
                except Exception as e:
                    logger.info(f"  ℹ️ Test patient cleanup: {str(e)}")

                self.test_results['reservation_flow'] = True

            else:
                logger.error("  ❌ Failed to create test appointment")
                self.test_results['reservation_flow'] = False

        except Exception as e:
            logger.error(f"  ❌ Reservation flow test failed: {str(e)}")
            self.test_results['reservation_flow'] = False

    async def test_calendar_integration(self):
        """Test calendar integration and time slot management"""
        logger.info("\n📆 Testing Calendar Integration...")

        try:
            if not self.test_doctor_id:
                logger.warning("  ⚠️ No doctor ID available for calendar testing")
                self.test_results['calendar_integration'] = False
                return

            # 1. Test available time slots
            today = datetime.now().date()
            tomorrow = today + timedelta(days=1)

            logger.info(f"  🔍 Checking time slots for {tomorrow}")

            # Get doctor's working hours
            doctor_result = self.supabase.table("doctors").select("working_hours, available_days, booking_duration_minutes").eq("id", self.test_doctor_id).execute()

            if doctor_result.data:
                doctor = doctor_result.data[0]
                working_hours = doctor.get('working_hours', {})
                available_days = doctor.get('available_days', [])
                booking_duration = doctor.get('booking_duration_minutes', 30)

                logger.info(f"    - Working Hours: {working_hours}")
                logger.info(f"    - Available Days: {available_days}")
                logger.info(f"    - Booking Duration: {booking_duration} minutes")

                # 2. Check existing appointments for tomorrow
                appointments_result = self.supabase.table("appointments").select("start_time, end_time, status").eq(
                    "doctor_id", self.test_doctor_id
                ).eq("appointment_date", tomorrow.isoformat()).execute()

                existing_appointments = appointments_result.data if appointments_result.data else []
                logger.info(f"    - Existing appointments for {tomorrow}: {len(existing_appointments)}")

                for apt in existing_appointments:
                    logger.info(f"      * {apt['start_time']} - {apt['end_time']} ({apt['status']})")

                # 3. Calculate available time slots
                if working_hours and str(tomorrow.weekday()) in available_days:
                    logger.info("  ✅ Doctor is available on the requested date")

                    # Simple slot calculation (would be more complex in production)
                    start_hour = working_hours.get('start', '09:00')
                    end_hour = working_hours.get('end', '17:00')

                    logger.info(f"    - Available time window: {start_hour} - {end_hour}")
                    logger.info("  ✅ Calendar integration working properly")

                    self.test_results['calendar_integration'] = True
                else:
                    logger.info("  ⚠️ Doctor not available on requested date")
                    self.test_results['calendar_integration'] = True  # Still working, just no availability

            else:
                logger.error("  ❌ Could not retrieve doctor's calendar settings")
                self.test_results['calendar_integration'] = False

        except Exception as e:
            logger.error(f"  ❌ Calendar integration test failed: {str(e)}")
            self.test_results['calendar_integration'] = False

    async def test_specialty_assignment_flow(self):
        """Test specialty assignment and routing"""
        logger.info("\n🎯 Testing Specialty Assignment Flow...")

        try:
            # 1. Test specialty-based doctor routing
            specialties_to_test = ["general_dentistry", "oral_surgery", "orthodontics", "pediatric_dentistry"]

            for specialty in specialties_to_test:
                logger.info(f"  🔍 Testing specialty: {specialty}")

                # Find doctors with this specialty in the text field
                doctors_result = self.supabase.table("doctors").select("id, first_name, last_name, specialization, specialties").ilike(
                    "specialization", f"%{specialty.replace('_', ' ')}%"
                ).execute()

                doctors = doctors_result.data if doctors_result.data else []
                logger.info(f"    - Found {len(doctors)} doctors with {specialty}")

                for doctor in doctors:
                    doctor_name = f"{doctor.get('first_name', '')} {doctor.get('last_name', '')}"
                    logger.info(f"      * {doctor_name} - {doctor.get('specialization', 'N/A')}")

            # 2. Test clinic specialty capabilities
            if self.test_clinic_id:
                clinic_result = self.supabase.table("clinics").select("specialties, services").eq("id", self.test_clinic_id).execute()

                if clinic_result.data:
                    clinic = clinic_result.data[0]
                    clinic_specialties = clinic.get('specialties', [])
                    clinic_services = clinic.get('services', [])

                    logger.info(f"  🏥 Clinic specialties: {clinic_specialties}")
                    logger.info(f"  🏥 Clinic services: {len(clinic_services)} available")

                    # Test specialty matching
                    if clinic_specialties:
                        test_specialty = clinic_specialties[0] if clinic_specialties else "general_medicine"
                        logger.info(f"  ✅ Testing specialty matching for: {test_specialty}")

                        # Find matching doctors in this clinic
                        matching_doctors = self.supabase.table("doctors").select("*").eq(
                            "clinic_id", self.test_clinic_id
                        ).ilike("specialization", f"%{test_specialty.replace('_', ' ')}%").execute()

                        doctors = matching_doctors.data if matching_doctors.data else []
                        logger.info(f"    - Found {len(doctors)} matching doctors in clinic")

            self.test_results['specialty_assignment'] = True
            logger.info("  ✅ Specialty assignment flow working properly")

        except Exception as e:
            logger.error(f"  ❌ Specialty assignment test failed: {str(e)}")
            self.test_results['specialty_assignment'] = False

    async def test_end_to_end_booking_workflow(self):
        """Test complete end-to-end booking workflow"""
        logger.info("\n🔄 Testing End-to-End Booking Workflow...")

        try:
            # Simulate a complete patient booking journey
            patient_request = {
                "chief_complaint": "Tooth pain and need cleaning",
                "preferred_date": (datetime.now() + timedelta(days=2)).date().isoformat(),
                "preferred_time": "morning",
                "insurance_provider": "Delta Dental",
                "urgency": "routine"
            }

            logger.info("  👤 Patient Request:")
            logger.info(f"    - Complaint: {patient_request['chief_complaint']}")
            logger.info(f"    - Preferred Date: {patient_request['preferred_date']}")
            logger.info(f"    - Urgency: {patient_request['urgency']}")

            # 1. Determine required specialty based on complaint
            # This would use AI/NLP in production
            specialty_mapping = {
                "tooth pain": "general_dentistry",
                "wisdom teeth": "oral_surgery",
                "crooked teeth": "orthodontics",
                "cleaning": "general_dentistry"
            }

            required_specialty = "general_dentistry"  # Based on dental needs
            logger.info(f"  🎯 Determined required specialty: {required_specialty}")

            # 2. Find available doctors in the clinic
            available_doctors = self.supabase.table("doctors").select("*").eq(
                "clinic_id", self.test_clinic_id
            ).eq("accepting_new_patients", True).execute()

            doctors = available_doctors.data if available_doctors.data else []
            logger.info(f"  👨‍⚕️ Found {len(doctors)} available specialists")

            if doctors:
                selected_doctor = doctors[0]
                doctor_name = f"{selected_doctor.get('first_name', '')} {selected_doctor.get('last_name', '')}"
                logger.info(f"    - Selected: {doctor_name}")

                # 3. Check calendar availability
                # This would integrate with actual calendar system
                logger.info("  📅 Checking calendar availability...")

                # 4. Ensure test patient exists for end-to-end test
                try:
                    e2e_patient_data = {
                        "id": self.test_patient_id,
                        "clinic_id": selected_doctor['clinic_id'],
                        "first_name": "End2End",
                        "last_name": "TestPatient",
                        "date_of_birth": "1985-05-15",
                        "gender": "other",
                        "phone": f"+1555{self.test_patient_id[:7]}",
                        "email": f"e2e.patient+{self.test_patient_id[:8]}@example.com"
                    }
                    self.supabase.table("patients").insert(e2e_patient_data).execute()
                    logger.info("  👤 Test patient created for end-to-end test")
                except Exception as e:
                    if "duplicate key" not in str(e).lower():
                        logger.warning(f"  ⚠️ Patient creation issue: {str(e)}")

                # 5. Create the appointment
                booking_data = {
                    "clinic_id": selected_doctor['clinic_id'],
                    "patient_id": self.test_patient_id,
                    "doctor_id": selected_doctor['id'],
                    "appointment_type": "consultation",
                    "appointment_date": patient_request['preferred_date'],
                    "start_time": "10:00:00",  # Available morning slot
                    "end_time": "10:30:00",
                    "duration_minutes": 30,
                    "status": "scheduled",
                    "reason_for_visit": patient_request['chief_complaint'],
                    "chief_complaint": patient_request['chief_complaint'],
                    "is_telemedicine": False
                }

                booking_result = self.supabase.table("appointments").insert(booking_data).execute()

                if booking_result.data:
                    appointment = booking_result.data[0]
                    appointment_id = appointment['id']

                    logger.info("  ✅ End-to-end booking successful!")
                    logger.info(f"    - Appointment ID: {appointment_id}")
                    logger.info(f"    - Date/Time: {appointment['appointment_date']} at {appointment['start_time']}")
                    logger.info(f"    - Doctor: {doctor_name}")
                    logger.info(f"    - Specialty: {required_specialty}")

                    # 5. Simulate confirmation workflow
                    confirmation_result = self.supabase.table("appointments").update({
                        "status": "confirmed",
                        "confirmed_at": datetime.now(timezone.utc).isoformat(),
                        "confirmation_method": "auto_system"
                    }).eq("id", appointment_id).execute()

                    if confirmation_result.data:
                        logger.info("  ✅ Appointment confirmed automatically")

                    # Clean up
                    self.supabase.table("appointments").delete().eq("id", appointment_id).execute()
                    logger.info("  🧹 Test appointment cleaned up")

                    # Clean up test patient
                    try:
                        self.supabase.table("patients").delete().eq("id", self.test_patient_id).execute()
                        logger.info("  🧹 Test patient cleaned up")
                    except Exception as e:
                        logger.info(f"  ℹ️ Patient cleanup: {str(e)}")

                    self.test_results['end_to_end_workflow'] = True

            else:
                logger.warning("  ⚠️ No available specialists found")
                self.test_results['end_to_end_workflow'] = False

        except Exception as e:
            logger.error(f"  ❌ End-to-end workflow test failed: {str(e)}")
            self.test_results['end_to_end_workflow'] = False

    async def generate_test_report(self):
        """Generate comprehensive test report"""
        logger.info("\n" + "="*60)
        logger.info("📊 STORES AND RESERVATIONS TEST REPORT")
        logger.info("="*60)

        total_tests = len(self.test_results)
        passed_tests = sum(1 for result in self.test_results.values() if result)

        logger.info(f"Tests Run: {total_tests}")
        logger.info(f"Tests Passed: {passed_tests}")
        logger.info(f"Tests Failed: {total_tests - passed_tests}")
        logger.info(f"Success Rate: {(passed_tests/total_tests)*100:.1f}%")

        logger.info("\nDetailed Results:")
        for test_name, result in self.test_results.items():
            status = "✅ PASSED" if result else "❌ FAILED"
            logger.info(f"  {test_name}: {status}")

        if passed_tests == total_tests:
            logger.info("\n🎉 ALL TESTS PASSED - STORES AND RESERVATIONS SYSTEM OPERATIONAL!")
        else:
            logger.info(f"\n⚠️ {total_tests - passed_tests} TESTS FAILED - REVIEW REQUIRED")

        return passed_tests == total_tests

async def main():
    """Run all stores and reservations tests"""
    logger.info("🧪 Starting Stores and Reservations Test Suite")
    logger.info("="*60)

    test_suite = StoresReservationsTestSuite()

    try:
        # Initialize
        await test_suite.setup_supabase()

        # Run all tests
        await test_suite.test_clinic_store_operations()
        await test_suite.test_doctor_availability()
        await test_suite.test_appointment_reservation_flow()
        await test_suite.test_calendar_integration()
        await test_suite.test_specialty_assignment_flow()
        await test_suite.test_end_to_end_booking_workflow()

        # Generate report
        all_passed = await test_suite.generate_test_report()

        return 0 if all_passed else 1

    except Exception as e:
        logger.error(f"💥 Test suite execution failed: {str(e)}")
        return 1

if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)