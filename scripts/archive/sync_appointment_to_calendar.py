#!/usr/bin/env python3
"""Sync a specific appointment to Google Calendar"""
import os
import sys
import asyncio
from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, '/Users/dmitrymolchanov/Programs/Plaintalk/apps/healthcare-backend')

from app.db.supabase_client import get_supabase_client
from app.services.external_calendar_service import ExternalCalendarService

async def sync_appointment():
    supabase = get_supabase_client()
    service = ExternalCalendarService()
    
    # Get the "Snap On" appointment for Dr. Andrea
    appointments = supabase.from_('appointments').select(
        '*'
    ).eq('appointment_type', 'Snap On').eq('appointment_date', '2025-10-09').execute()
    
    if not appointments.data:
        print("Appointment not found")
        return
    
    appt = appointments.data[0]
    print(f"Found appointment: {appt['appointment_type']} at {appt['start_time']}")
    
    # Get doctor details
    doctor = supabase.from_('doctors').select('first_name, last_name').eq('id', appt['doctor_id']).single().execute()
    doctor_name = f"{doctor.data['first_name']} {doctor.data['last_name']}"
    
    # Prepare appointment data
    appointment_data = {
        'id': appt['id'],
        'clinic_id': appt['clinic_id'],
        'doctor_id': appt['doctor_id'],
        'doctor_name': doctor_name,
        'patient_id': appt.get('patient_id'),
        'patient_name': appt.get('patient_name', 'Unknown Patient'),
        'appointment_date': appt['appointment_date'],
        'start_time': appt['start_time'],
        'end_time': appt.get('end_time'),
        'appointment_type': appt['appointment_type'],
        'reason_for_visit': appt.get('reason_for_visit'),
        'notes': appt.get('notes')
    }
    
    print(f"\nSyncing to Google Calendar for {doctor_name}...")
    result = await service.create_calendar_event(appointment_data)
    
    if result.get('success'):
        print(f"✅ Event created successfully!")
        print(f"Event ID: {result.get('event_id')}")
        print(f"Calendar ID: {result.get('calendar_id')}")
    else:
        print(f"❌ Failed: {result.get('error')}")

if __name__ == '__main__':
    asyncio.run(sync_appointment())
