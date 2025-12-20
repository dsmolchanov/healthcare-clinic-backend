#!/usr/bin/env python3
"""
Connect Shtern Clinic to Google Calendar
Generates the OAuth URL for calendar integration
"""

import os
import sys
import asyncio
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent))

# Load environment - try both locations
from dotenv import load_dotenv
if not load_dotenv('.env'):
    load_dotenv('../.env')

from app.calendar.oauth_manager import CalendarOAuthManager
from supabase import create_client
from supabase.client import ClientOptions


async def connect_shtern_google_calendar():
    """Generate OAuth URL for Shtern clinic Google Calendar integration"""

    print("=" * 60)
    print("🔗 Google Calendar Connection for Shtern Dental Clinic")
    print("=" * 60)

    # Initialize OAuth manager
    oauth_manager = CalendarOAuthManager()

    # Get Shtern clinic details
    options = ClientOptions(
        schema='healthcare',
        auto_refresh_token=True,
        persist_session=False
    )

    supabase = create_client(
        os.getenv("SUPABASE_URL"),
        os.getenv("SUPABASE_SERVICE_ROLE_KEY"),
        options=options
    )

    # Get clinic
    clinic_result = supabase.table('clinics').select('*').eq(
        'name', 'Shtern Dental Clinic'
    ).execute()

    if not clinic_result.data:
        print("❌ Shtern clinic not found!")
        return

    clinic = clinic_result.data[0]
    clinic_id = clinic['id']

    # Get first doctor
    doctor_result = supabase.table('doctors').select('*').eq(
        'clinic_id', clinic_id
    ).limit(1).execute()

    if not doctor_result.data:
        print("❌ No doctors found!")
        return

    doctor = doctor_result.data[0]
    doctor_id = doctor['id']

    print(f"\n📋 Clinic Details:")
    print(f"   Name: {clinic['name']}")
    print(f"   ID: {clinic_id}")
    print(f"   Doctor: {doctor.get('specialization', 'Doctor')}")
    print(f"   Doctor ID: {doctor_id}")

    # Generate OAuth URL
    print(f"\n🔐 Generating OAuth URL...")
    auth_url = await oauth_manager.initiate_google_oauth(
        clinic_id=clinic_id,
        doctor_id=doctor_id
    )

    print(f"\n✅ OAuth URL Generated!")
    print("=" * 60)
    print("\n📝 INSTRUCTIONS TO CONNECT GOOGLE CALENDAR:")
    print("=" * 60)
    print("\n1. Copy this URL and open it in your browser:")
    print(f"\n{auth_url}\n")
    print("2. Log in with your Google account")
    print("3. Grant these permissions:")
    print("   - View and edit calendar events")
    print("   - Access calendar list")
    print("4. After authorization, you'll be redirected")
    print("5. The calendar will be automatically connected!")
    print("\n" + "=" * 60)

    # Save URL to file
    with open("shtern_google_calendar_url.txt", "w") as f:
        f.write(auth_url)
    print("\n📁 URL also saved to: shtern_google_calendar_url.txt")

    print("\n✨ After connecting, appointments will sync bidirectionally:")
    print("   - New appointments in the system → Google Calendar")
    print("   - Events created in Google Calendar → System via webhook")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(connect_shtern_google_calendar())