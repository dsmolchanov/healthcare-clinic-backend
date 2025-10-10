"""
Appointment Calendar Sync Webhook
Automatically syncs appointments to Google Calendar when they're created/updated
"""
import logging
from fastapi import APIRouter, Request, HTTPException, BackgroundTasks
from typing import Dict, Any

from app.services.external_calendar_service import ExternalCalendarService
from app.db.supabase_client import get_supabase_client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks/appointment-sync", tags=["Webhooks"])


async def sync_appointment_to_calendar(appointment_id: str, operation: str):
    """Background task to sync appointment to calendar"""
    try:
        logger.info(f"Syncing appointment {appointment_id} to calendar (operation: {operation})")
        
        supabase = get_supabase_client()
        service = ExternalCalendarService()
        
        # Get appointment details
        appointment = supabase.from_('appointments').select(
            '*'
        ).eq('id', appointment_id).single().execute()
        
        if not appointment.data:
            logger.warning(f"Appointment {appointment_id} not found")
            return
        
        appt = appointment.data
        
        # Skip if no doctor assigned
        if not appt.get('doctor_id'):
            logger.info(f"No doctor assigned to appointment {appointment_id}, skipping sync")
            return
        
        # Get doctor details
        doctor = supabase.from_('doctors').select('first_name, last_name').eq(
            'id', appt['doctor_id']
        ).single().execute()
        
        if not doctor.data:
            logger.warning(f"Doctor not found for appointment {appointment_id}")
            return
        
        doctor_name = f"{doctor.data['first_name']} {doctor.data['last_name']}"

        # Handle DELETE/CANCEL operations
        if operation == 'DELETE' or appt.get('status') == 'cancelled':
            # TODO: Implement calendar event deletion
            logger.info(f"Appointment {appointment_id} cancelled/deleted - calendar event deletion not yet implemented")
            return

        # Get service duration if available
        duration_minutes = 30  # Default
        appointment_type = appt.get('appointment_type')

        if appointment_type:
            try:
                # Try to find matching service by name
                service = supabase.from_('services').select(
                    'duration_minutes'
                ).eq('clinic_id', appt['clinic_id']).eq('name', appointment_type).execute()

                if service.data and len(service.data) > 0:
                    duration_minutes = service.data[0].get('duration_minutes', 30)
                    logger.info(f"Using service duration: {duration_minutes} minutes for {appointment_type}")
                else:
                    logger.info(f"No service found for '{appointment_type}', using default 30 minutes")
            except Exception as e:
                logger.warning(f"Error fetching service duration: {e}, using default 30 minutes")

        # Prepare appointment data
        appointment_data = {
            'id': appt['id'],
            'clinic_id': appt['clinic_id'],
            'doctor_id': appt['doctor_id'],
            'doctor_name': doctor_name,
            'patient_id': appt.get('patient_id'),
            'patient_name': appt.get('patient_name', 'Unknown Patient'),
            'patient_phone': appt.get('patient_phone'),
            'appointment_date': appt['appointment_date'],
            'start_time': appt['start_time'],
            'end_time': appt.get('end_time'),
            'duration_minutes': duration_minutes,
            'appointment_type': appointment_type,
            'reason_for_visit': appt.get('reason_for_visit'),
            'notes': appt.get('notes'),
            'google_event_id': appt.get('google_event_id')
        }

        # Check if appointment already has a google_event_id
        if appt.get('google_event_id'):
            logger.info(f"Appointment {appointment_id} already synced, updating existing event {appt['google_event_id']}")
            result = await service.update_calendar_event(appointment_data)
        else:
            logger.info(f"Appointment {appointment_id} not yet synced, creating new event")
            result = await service.create_calendar_event(appointment_data)

        if result.get('success'):
            # Update appointment with google_event_id if this was a new event
            if result.get('google_event_id') and not appt.get('google_event_id'):
                try:
                    supabase.from_('appointments').update({
                        'google_event_id': result['google_event_id'],
                        'calendar_synced_at': 'now()'
                    }).eq('id', appointment_id).execute()
                    logger.info(f"Updated appointment {appointment_id} with google_event_id: {result['google_event_id']}")
                except Exception as update_error:
                    logger.warning(f"Failed to update appointment with google_event_id: {update_error}")

            logger.info(f"✅ Successfully synced appointment {appointment_id} to calendar")
        else:
            logger.error(f"❌ Failed to sync appointment {appointment_id}: {result.get('error')}")
            
    except Exception as e:
        logger.error(f"Error syncing appointment {appointment_id} to calendar: {e}", exc_info=True)


@router.post("/supabase")
async def supabase_appointment_webhook(
    request: Request,
    background_tasks: BackgroundTasks
):
    """
    Webhook endpoint for Supabase Database Webhooks
    Receives notifications when appointments are created/updated/deleted
    """
    try:
        # Get webhook payload
        payload = await request.json()

        logger.info(f"Received appointment sync webhook: {payload.get('type')}")

        # Supabase webhook format
        webhook_type = payload.get('type')  # INSERT, UPDATE, DELETE
        record = payload.get('record', {})
        old_record = payload.get('old_record', {})

        appointment_id = record.get('id') or old_record.get('id')

        if not appointment_id:
            logger.warning("No appointment ID in webhook payload")
            return {'success': False, 'error': 'Missing appointment ID'}

        # Skip if this update is from calendar sync worker (prevent loops)
        # Check if google_event_id was just set (meaning this is a sync update)
        if webhook_type == 'UPDATE':
            old_google_id = old_record.get('google_event_id')
            new_google_id = record.get('google_event_id')

            # If google_event_id was just added, this is a sync operation - skip to prevent loop
            if not old_google_id and new_google_id:
                logger.info(f"Skipping webhook for appointment {appointment_id} - sync operation detected")
                return {'success': True, 'note': 'Sync operation - skipping to prevent loop'}

        # Only process scheduled/confirmed appointments
        status = record.get('status')
        if webhook_type in ['INSERT', 'UPDATE'] and status not in ['scheduled', 'confirmed']:
            logger.info(f"Skipping sync for appointment {appointment_id} with status {status}")
            return {'success': True, 'note': 'Appointment not in scheduled/confirmed status'}
        
        # Add background task to sync to calendar
        background_tasks.add_task(
            sync_appointment_to_calendar,
            appointment_id=appointment_id,
            operation=webhook_type
        )
        
        return {
            'success': True,
            'appointment_id': appointment_id,
            'operation': webhook_type
        }
        
    except Exception as e:
        logger.error(f"Error processing appointment sync webhook: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/pg-notify")
async def pg_notify_appointment_webhook(
    request: Request,
    background_tasks: BackgroundTasks
):
    """
    Webhook endpoint for pg_notify events
    Alternative to Supabase webhooks using PostgreSQL NOTIFY
    """
    try:
        # Get notification payload
        payload = await request.json()
        
        logger.info(f"Received pg_notify appointment sync: {payload}")
        
        appointment_id = payload.get('appointment_id')
        operation = payload.get('operation', 'INSERT')
        
        if not appointment_id:
            return {'success': False, 'error': 'Missing appointment ID'}
        
        # Add background task
        background_tasks.add_task(
            sync_appointment_to_calendar,
            appointment_id=appointment_id,
            operation=operation
        )
        
        return {
            'success': True,
            'appointment_id': appointment_id
        }
        
    except Exception as e:
        logger.error(f"Error processing pg_notify webhook: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
