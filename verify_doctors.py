#!/usr/bin/env python3
"""Verify doctors were imported successfully"""

import asyncio
from app.database import get_supabase

async def verify_doctors():
    supabase = await get_supabase()

    # Get clinic ID
    clinic = supabase.table("clinics").select("id").eq("name", "Shtern Dental Clinic").execute()
    clinic_id = clinic.data[0]['id']

    # Get all doctors for the clinic, ordered by created_at to see newest first
    doctors = supabase.table("doctors").select("*").eq("clinic_id", clinic_id).order("created_at", desc=True).execute()

    print(f"=== DOCTORS IN SHTERN DENTAL CLINIC ===\n")
    print(f"Total doctors: {len(doctors.data)}\n")

    for doctor in doctors.data:
        print(f"Doctor: {doctor['first_name']} {doctor['last_name']}")
        print(f"  Specialization: {doctor.get('specialization', 'N/A')}")
        print(f"  Email: {doctor.get('email', 'N/A')}")
        print(f"  Phone: {doctor.get('phone', 'N/A')}")
        print(f"  License: {doctor.get('license_number', 'Not provided')}")
        print()

if __name__ == "__main__":
    asyncio.run(verify_doctors())