#!/usr/bin/env python3
"""Check recent appointments and their calendar events"""
import os
import sys
from dotenv import load_dotenv
from datetime import datetime, timedelta

load_dotenv()
sys.path.insert(0, '/Users/dmitrymolchanov/Programs/Plaintalk/apps/healthcare-backend')

from app.db.supabase_client import get_supabase_client

def check_appointments():
    supabase = get_supabase_client()
    
    # Get organization's clinic
    clinics = supabase.from_('clinics').select('id, name').eq(
        'organization_id', '4e8ddba1-ad52-4613-9a03-ec64636b3f6c'
    ).execute()
    
    if not clinics.data:
        print("No clinics found")
        return
    
    clinic_id = clinics.data[0]['id']
    print(f"Clinic: {clinics.data[0]['name']} ({clinic_id})\n")
    
    # Get recent appointments for today
    today = datetime.now().date()
    appointments = supabase.from_('appointments').select(
        'id, patient_id, doctor_id, appointment_date, start_time, status, appointment_type, google_calendar_event_id, created_at'
    ).eq('clinic_id', clinic_id).gte(
        'appointment_date', str(today)
    ).order('created_at', desc=True).limit(5).execute()
    
    if not appointments.data:
        print("No recent appointments found")
        return
    
    print(f"Recent appointments (last 5 for today or later):\n")
    
    for appt in appointments.data:
        # Get doctor info
        doctor = supabase.from_('doctors').select('first_name, last_name, google_calendar_id').eq('id', appt['doctor_id']).single().execute()
        doctor_name = f"{doctor.data['first_name']} {doctor.data['last_name']}" if doctor.data else 'Unknown'
        doctor_cal = doctor.data.get('google_calendar_id', 'None') if doctor.data else 'None'
        
        print(f"📅 {appt['appointment_date']} {appt['start_time']} - {appt['appointment_type']}")
        print(f"   Doctor: {doctor_name}")
        print(f"   Doctor's Calendar: {doctor_cal[:50]}..." if doctor_cal != 'None' else f"   Doctor's Calendar: {doctor_cal}")
        print(f"   Calendar Event ID: {appt.get('google_calendar_event_id', 'NOT CREATED')}")
        print(f"   Created: {appt['created_at']}")
        print(f"   Status: {appt['status']}\n")

if __name__ == '__main__':
    check_appointments()
