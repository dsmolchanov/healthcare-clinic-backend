import asyncio
from supabase import create_client, Client
from dotenv import load_dotenv
import os
import uuid
from datetime import datetime, timedelta
import random

load_dotenv()

def check_and_populate_clinic():
    # Get Supabase credentials
    supabase_url = os.getenv('SUPABASE_URL')
    supabase_key = os.getenv('SUPABASE_SERVICE_KEY') or os.getenv('SUPABASE_ANON_KEY')

    if not supabase_url or not supabase_key:
        print("❌ Missing SUPABASE_URL or SUPABASE_KEY")
        return

    # Create Supabase client
    supabase: Client = create_client(supabase_url, supabase_key)

    print("🔍 Checking existing clinics...")

    # First, let's see what clinics exist
    try:
        clinics = supabase.table('clinics').select("*").execute()
        if clinics.data:
            print(f"✅ Found {len(clinics.data)} clinics:")
            for clinic in clinics.data:
                print(f"   - {clinic['name']} (ID: {clinic['id']})")

            # Use the first clinic
            clinic_id = clinics.data[0]['id']
            clinic_name = clinics.data[0]['name']
            print(f"\n📌 Using clinic: {clinic_name} ({clinic_id})")
        else:
            print("❌ No clinics found. Creating one...")
            # Create a clinic
            new_clinic = supabase.table('clinics').insert({
                'id': str(uuid.uuid4()),
                'name': 'Demo Dental Clinic',
                'organization_id': str(uuid.uuid4())
            }).execute()
            clinic_id = new_clinic.data[0]['id']
            clinic_name = new_clinic.data[0]['name']
            print(f"✅ Created clinic: {clinic_name}")
    except Exception as e:
        print(f"❌ Error with clinics: {e}")
        return

    # Check if clinic has doctors
    print(f"\n🔍 Checking doctors in {clinic_name}...")
    try:
        doctors = supabase.table('doctors').select("*").eq('clinic_id', clinic_id).execute()

        if not doctors.data:
            print("❌ No doctors found. Adding sample doctors...")

            doctor_names = [
                ('John', 'Smith', 'john.smith@dental.com', 'Orthodontics'),
                ('Jane', 'Doe', 'jane.doe@dental.com', 'Endodontics'),
                ('Robert', 'Johnson', 'robert.johnson@dental.com', 'Periodontics'),
                ('Emily', 'Williams', 'emily.williams@dental.com', 'General'),
                ('Michael', 'Brown', 'michael.brown@dental.com', 'Oral Surgery')
            ]

            doctor_ids = []
            for first, last, email, spec in doctor_names:
                doctor = supabase.table('doctors').insert({
                    'clinic_id': clinic_id,
                    'first_name': first,
                    'last_name': last,
                    'email': email,
                    'phone': f'+1-555-{random.randint(1000, 9999)}',
                    'specialization': spec,
                    'license_number': f'LIC{random.randint(100000, 999999)}',
                    'is_active': True
                }).execute()
                doctor_ids.append(doctor.data[0]['id'])
                print(f"   ✅ Added Dr. {first} {last}")
        else:
            print(f"✅ Found {len(doctors.data)} doctors")
            doctor_ids = [d['id'] for d in doctors.data]
    except Exception as e:
        print(f"❌ Error with doctors: {e}")
        return

    # Check if clinic has services
    print(f"\n🔍 Checking services in {clinic_name}...")
    try:
        services = supabase.table('services').select("*").eq('clinic_id', clinic_id).execute()

        if not services.data:
            print("❌ No services found. Adding sample services...")

            service_list = [
                ('Dental Cleaning', 'Preventive', 60, 'CLEAN'),
                ('Tooth Extraction', 'Surgical', 90, 'EXTRACT'),
                ('Root Canal', 'Endodontics', 120, 'ROOT'),
                ('Crown Placement', 'Restorative', 90, 'CROWN'),
                ('Orthodontic Consultation', 'Orthodontics', 45, 'ORTHO'),
                ('Teeth Whitening', 'Cosmetic', 60, 'WHITE'),
                ('Filling', 'Restorative', 45, 'FILL'),
                ('X-Ray', 'Diagnostic', 15, 'XRAY'),
                ('Periodontal Treatment', 'Periodontics', 90, 'PERIO'),
                ('Dental Implant', 'Surgical', 120, 'IMPLANT')
            ]

            service_ids = []
            for name, category, duration, code in service_list:
                service = supabase.table('services').insert({
                    'clinic_id': clinic_id,
                    'name': name,
                    'category': category,
                    'code': code,
                    'duration_minutes': duration,
                    'price': random.randint(100, 500),
                    'is_active': True,
                    'requires_consultation': category == 'Surgical'
                }).execute()
                service_ids.append(service.data[0]['id'])
                print(f"   ✅ Added service: {name}")
        else:
            print(f"✅ Found {len(services.data)} services")
            service_ids = [s['id'] for s in services.data]
    except Exception as e:
        print(f"❌ Error with services: {e}")
        return

    # Check if clinic has patients
    print(f"\n🔍 Checking patients...")
    try:
        # First check if there are any patients
        patients = supabase.table('patients').select("*").limit(10).execute()

        if not patients.data:
            print("❌ No patients found. Adding sample patients...")

            patient_names = [
                ('Alice', 'Anderson'), ('Bob', 'Baker'), ('Carol', 'Carter'),
                ('David', 'Davis'), ('Eve', 'Evans'), ('Frank', 'Foster'),
                ('Grace', 'Green'), ('Henry', 'Hill'), ('Iris', 'Irving'),
                ('Jack', 'Jackson')
            ]

            patient_ids = []
            for first, last in patient_names:
                patient = supabase.table('patients').insert({
                    'first_name': first,
                    'last_name': last,
                    'email': f'{first.lower()}.{last.lower()}@email.com',
                    'phone': f'+1-555-{random.randint(1000, 9999)}',
                    'date_of_birth': f'{random.randint(1950, 2005)}-{random.randint(1, 12):02d}-{random.randint(1, 28):02d}'
                }).execute()
                patient_ids.append(patient.data[0]['id'])
                print(f"   ✅ Added patient: {first} {last}")
        else:
            print(f"✅ Found {len(patients.data)} patients")
            patient_ids = [p['id'] for p in patients.data]
    except Exception as e:
        print(f"❌ Error with patients: {e}")
        # Continue without patients - appointments can work without patient_id

    # Check and create appointments
    print(f"\n🔍 Checking appointments in {clinic_name}...")
    try:
        appointments = supabase.table('appointments').select("*").eq('clinic_id', clinic_id).execute()

        if not appointments.data:
            print("❌ No appointments found. Adding sample appointments...")

            # Create appointments for the past 3 months to establish history
            start_date = datetime.now() - timedelta(days=90)

            appointment_count = 0
            for days_ago in range(90, -30, -1):  # From 90 days ago to 30 days in future
                date = start_date + timedelta(days=days_ago)

                # Skip weekends
                if date.weekday() in [5, 6]:
                    continue

                # Create 3-8 appointments per day
                num_appointments = random.randint(3, 8)

                for _ in range(num_appointments):
                    doctor_id = random.choice(doctor_ids)
                    service_id = random.choice(service_ids)
                    hour = random.randint(9, 17)
                    minute = random.choice([0, 15, 30, 45])

                    # Determine status based on date
                    if date < datetime.now():
                        status = random.choice(['completed', 'completed', 'completed', 'no_show'])
                    elif date == datetime.now().date():
                        status = random.choice(['scheduled', 'confirmed'])
                    else:
                        status = 'scheduled'

                    try:
                        appointment = supabase.table('appointments').insert({
                            'clinic_id': clinic_id,
                            'doctor_id': doctor_id,
                            'service_id': service_id,
                            'patient_id': random.choice(patient_ids) if patient_ids else None,
                            'appointment_date': date.strftime('%Y-%m-%d'),
                            'start_time': f'{hour:02d}:{minute:02d}:00',
                            'end_time': f'{hour+1:02d}:{minute:02d}:00',
                            'status': status
                        }).execute()
                        appointment_count += 1
                    except Exception as e:
                        # Skip if there's a conflict
                        pass

            print(f"   ✅ Added {appointment_count} appointments")
        else:
            print(f"✅ Found {len(appointments.data)} appointments")
    except Exception as e:
        print(f"❌ Error with appointments: {e}")

    print(f"\n✅ Clinic {clinic_name} is now populated with sample data!")
    print(f"📋 Clinic ID to use in Medical Director Dashboard: {clinic_id}")

if __name__ == "__main__":
    check_and_populate_clinic()