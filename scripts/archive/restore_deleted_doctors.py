import os
from dotenv import load_dotenv
from supabase import create_client, Client
import uuid

load_dotenv()

def restore_doctors():
    # Get Supabase credentials
    supabase_url = os.getenv('SUPABASE_URL')
    supabase_key = os.getenv('SUPABASE_SERVICE_KEY') or os.getenv('SUPABASE_ANON_KEY')

    if not supabase_url or not supabase_key:
        print("❌ Missing SUPABASE_URL or SUPABASE_KEY")
        return

    # Create Supabase client
    supabase: Client = create_client(supabase_url, supabase_key)

    print("🔍 Checking current doctors...")

    # Get current doctors to see what's missing
    current_doctors = supabase.table('doctors').select("*").execute()
    current_names = [(d['first_name'], d['last_name']) for d in current_doctors.data]
    print(f"✅ Found {len(current_doctors.data)} existing doctors")

    # Get clinic ID
    clinics = supabase.table('clinics').select("*").limit(1).execute()
    if not clinics.data:
        print("❌ No clinic found")
        return

    clinic_id = clinics.data[0]['id']
    clinic_name = clinics.data[0]['name']
    print(f"📌 Using clinic: {clinic_name} ({clinic_id})")

    # List of doctors that were likely deleted (based on your test output)
    doctors_to_restore = [
        ('Gilberto', 'Gálvez', 'gilberto.galvez@dental.com', 'General Dentistry'),
        ('Kevin', 'Ishibashi', 'kevin.ishibashi@dental.com', 'Orthodontics'),
        ('Maria Virginia', 'Parra', 'maria.parra@dental.com', 'Endodontics'),
    ]

    # Also check if any of the original sample doctors are missing
    original_sample_doctors = [
        ('John', 'Smith', 'john.smith@dental.com', 'Orthodontics'),
        ('Jane', 'Doe', 'jane.doe@dental.com', 'Endodontics'),
        ('Robert', 'Johnson', 'robert.johnson@dental.com', 'Periodontics'),
        ('Emily', 'Williams', 'emily.williams@dental.com', 'General'),
        ('Michael', 'Brown', 'michael.brown@dental.com', 'Oral Surgery')
    ]

    doctors_to_restore.extend(original_sample_doctors)

    restored_count = 0
    for first_name, last_name, email, specialization in doctors_to_restore:
        # Check if this doctor already exists
        if (first_name, last_name) in current_names:
            print(f"   ✓ Dr. {first_name} {last_name} already exists")
            continue

        # Restore the doctor
        try:
            doctor_data = {
                'id': str(uuid.uuid4()),
                'clinic_id': clinic_id,
                'first_name': first_name,
                'last_name': last_name,
                'email': email,
                'phone': f'+1-555-{str(uuid.uuid4().int)[:4]}',
                'specialization': specialization,
                'license_number': f'LIC{str(uuid.uuid4().int)[:6]}',
                'is_active': True
            }

            result = supabase.table('doctors').insert(doctor_data).execute()

            if result.data:
                print(f"   ✅ Restored Dr. {first_name} {last_name}")
                restored_count += 1
        except Exception as e:
            print(f"   ❌ Failed to restore Dr. {first_name} {last_name}: {e}")

    # Final count
    final_doctors = supabase.table('doctors').select("id").execute()
    print(f"\n✅ Restoration complete!")
    print(f"   - Restored {restored_count} doctors")
    print(f"   - Total doctors now: {len(final_doctors.data)}")

if __name__ == "__main__":
    restore_doctors()