#!/usr/bin/env python3
"""
Fix visibility of existing doctor calendars
Makes all doctor calendars visible in Google Calendar sidebar
"""
import os
import sys
import asyncio
from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, '/Users/dmitrymolchanov/Programs/Plaintalk/apps/healthcare-backend')

from app.db.supabase_client import get_supabase_client
from app.security.compliance_vault import ComplianceVault
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

async def fix_calendar_visibility(organization_id: str):
    """Make all doctor calendars visible in Google Calendar"""
    
    supabase = get_supabase_client()
    vault = ComplianceVault()
    
    # Get calendar credentials
    credentials = await vault.retrieve_calendar_credentials(
        organization_id=organization_id,
        provider='google'
    )
    
    if not credentials:
        print("❌ No Google Calendar credentials found")
        return
    
    # Build Google Calendar service
    creds = Credentials(
        token=credentials['access_token'],
        refresh_token=credentials.get('refresh_token'),
        token_uri='https://oauth2.googleapis.com/token',
        client_id=os.getenv('GOOGLE_CLIENT_ID'),
        client_secret=os.getenv('GOOGLE_CLIENT_SECRET')
    )
    service = build('calendar', 'v3', credentials=creds)
    
    # Get all doctors with calendars
    clinics = supabase.from_('clinics').select('id').eq('organization_id', organization_id).execute()
    clinic_ids = [c['id'] for c in (clinics.data or [])]
    
    if not clinic_ids:
        print("❌ No clinics found")
        return
    
    doctors = supabase.from_('doctors').select(
        'id, first_name, last_name, google_calendar_id, google_calendar_color_id'
    ).in_('clinic_id', clinic_ids).execute()
    
    if not doctors.data:
        print("❌ No doctors found")
        return
    
    print(f"Found {len(doctors.data)} doctors with calendars")
    
    updated = 0
    failed = 0
    
    for doctor in doctors.data:
        calendar_id = doctor.get('google_calendar_id')
        if not calendar_id:
            continue
        
        doctor_name = f"{doctor['first_name']} {doctor['last_name']}"
        color_id = doctor.get('google_calendar_color_id', '1')
        
        try:
            # Update calendar list entry
            calendar_list_entry = service.calendarList().get(calendarId=calendar_id).execute()
            calendar_list_entry['selected'] = True
            calendar_list_entry['hidden'] = False
            calendar_list_entry['colorId'] = str(color_id)
            
            service.calendarList().update(
                calendarId=calendar_id,
                body=calendar_list_entry
            ).execute()
            
            print(f"✅ Updated visibility for Dr. {doctor_name} (color: {color_id})")
            updated += 1
            
        except Exception as e:
            print(f"❌ Failed to update Dr. {doctor_name}: {e}")
            failed += 1
    
    print(f"\n✅ Updated: {updated}")
    print(f"❌ Failed: {failed}")

if __name__ == '__main__':
    organization_id = '4e8ddba1-ad52-4613-9a03-ec64636b3f6c'
    asyncio.run(fix_calendar_visibility(organization_id))
