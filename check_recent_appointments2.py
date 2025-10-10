#!/usr/bin/env python3
"""Check recent appointments"""
import os
import sys
from dotenv import load_dotenv
from datetime import datetime

load_dotenv()
sys.path.insert(0, '/Users/dmitrymolchanov/Programs/Plaintalk/apps/healthcare-backend')

from app.db.supabase_client import get_supabase_client

def check_appointments():
    supabase = get_supabase_client()
    
    clinic_id = 'e0c84f56-235d-49f2-9a44-37c1be579afc'
    today = datetime.now().date()
    
    # Get recent appointments for today
    appointments = supabase.from_('appointments').select(
        '*'
    ).eq('clinic_id', clinic_id).gte(
        'appointment_date', str(today)
    ).order('created_at', desc=True).limit(3).execute()
    
    if not appointments.data:
        print("No recent appointments found")
        return
    
    print(f"Recent appointments:\n")
    
    for appt in appointments.data:
        # Get doctor info
        try:
            doctor = supabase.from_('doctors').select('first_name, last_name, google_calendar_id').eq('id', appt['doctor_id']).single().execute()
            doctor_name = f"{doctor.data['first_name']} {doctor.data['last_name']}" if doctor.data else 'Unknown'
            doctor_cal = doctor.data.get('google_calendar_id', 'None') if doctor.data else 'None'
        except:
            doctor_name = 'Unknown'
            doctor_cal = 'None'
        
        print(f"📅 {appt['appointment_date']} {appt['start_time']}")
        print(f"   Type: {appt.get('appointment_type', 'N/A')}")
        print(f"   Reason: {appt.get('reason_for_visit', 'N/A')}")
        print(f"   Doctor: {doctor_name}")
        print(f"   Doctor's Calendar: {doctor_cal[:50] if doctor_cal != 'None' else doctor_cal}")
        print(f"   Created: {appt['created_at']}")
        print(f"   Status: {appt['status']}\n")

if __name__ == '__main__':
    check_appointments()
