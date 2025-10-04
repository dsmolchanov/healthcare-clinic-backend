#!/usr/bin/env python3
"""
Fix clinic_id issue for bulk upload
"""

import os
import sys
import asyncio
from pathlib import Path
from dotenv import load_dotenv
from supabase import create_client

# Load from parent directory .env file
env_path = Path(__file__).parent.parent.parent / '.env'
load_dotenv(dotenv_path=env_path)

async def main():
    # Get Supabase client
    url = os.environ.get('SUPABASE_URL')
    key = os.environ.get('SUPABASE_SERVICE_KEY')

    if not url or not key:
        print("❌ Missing SUPABASE_URL or SUPABASE_SERVICE_KEY")
        return

    client = create_client(url, key)

    # The clinic_id that's causing issues
    problem_clinic_id = "3e411ecb-3411-4add-91e2-8fa897310cb0"

    # Check if it exists
    result = client.table('clinics').select('*').eq('id', problem_clinic_id).execute()

    if result.data:
        print(f"✅ Clinic {problem_clinic_id} already exists")
        print(f"   Name: {result.data[0]['name']}")
    else:
        print(f"❌ Clinic {problem_clinic_id} does not exist")

        # Get all existing clinics to see what we have
        all_clinics = client.table('clinics').select('id, name, organization_id').execute()
        print("\n📋 Existing clinics in database:")
        for clinic in all_clinics.data:
            print(f"   • ID: {clinic['id']}")
            print(f"     Name: {clinic['name']}")
            print(f"     Org: {clinic['organization_id']}")
            print()

        # Create the missing clinic
        print(f"\n🔧 Creating clinic {problem_clinic_id}...")

        new_clinic = {
            'id': problem_clinic_id,
            'name': 'Shtern Dental Clinic',
            'organization_id': problem_clinic_id,  # Use same ID for org
            'phone': '+1234567890',
            'email': 'info@shterndental.com',
            'address': '123 Dental Street',
            'city': 'Tel Aviv',
            'country': 'Israel',
            'settings': {
                'business_hours': {
                    'monday': {'open': '09:00', 'close': '18:00'},
                    'tuesday': {'open': '09:00', 'close': '18:00'},
                    'wednesday': {'open': '09:00', 'close': '18:00'},
                    'thursday': {'open': '09:00', 'close': '18:00'},
                    'friday': {'open': '09:00', 'close': '15:00'},
                    'saturday': {'closed': True},
                    'sunday': {'open': '09:00', 'close': '18:00'}
                }
            }
        }

        try:
            result = client.table('clinics').insert(new_clinic).execute()
            print(f"✅ Successfully created clinic {problem_clinic_id}")
        except Exception as e:
            print(f"❌ Failed to create clinic: {e}")

            # Try without the id field (let it auto-generate)
            print("\n🔧 Trying alternative: Find or use existing clinic...")

            # Use the first available clinic
            if all_clinics.data:
                first_clinic = all_clinics.data[0]
                print(f"\n✅ Use this clinic_id instead: {first_clinic['id']}")
                print(f"   Name: {first_clinic['name']}")
                print("\n📝 Update your frontend to use this clinic_id!")

if __name__ == "__main__":
    asyncio.run(main())