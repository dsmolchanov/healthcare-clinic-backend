"""
Calendar Sync API
Endpoints for syncing appointments with external calendars
"""

import logging
from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime

from app.services.external_calendar_service import ExternalCalendarService

router = APIRouter(prefix="/api/calendar", tags=["calendar-sync"])
logger = logging.getLogger(__name__)

class SyncAppointmentRequest(BaseModel):
    appointment_id: str

class BulkSyncRequest(BaseModel):
    clinic_id: Optional[str] = None
    organization_id: Optional[str] = None
    doctor_id: Optional[str] = None
    from_date: Optional[str] = None
    to_date: Optional[str] = None
    async_mode: Optional[bool] = False  # Run in background if True


async def trigger_bulk_sync_background(
    clinic_id: str,
    organization_id: str
):
    """
    Trigger bulk sync in background (used by OAuth callback)
    Does not wait for completion
    """
    try:
        logger.info(f"Background bulk sync triggered for clinic {clinic_id}")

        calendar_service = ExternalCalendarService()

        # Get unsynced appointments
        result = calendar_service.supabase.rpc('get_unsynced_appointments', {
            'p_clinic_id': clinic_id
        }).execute()

        if not result.data:
            logger.info(f"No unsynced appointments for clinic {clinic_id}")
            return

        logger.info(f"Syncing {len(result.data)} appointments for clinic {clinic_id}")

        # Sync each appointment (fire and forget)
        for appointment in result.data:
            try:
                await calendar_service.sync_appointment_to_calendar(appointment['id'])
            except Exception as e:
                logger.error(f"Failed to sync appointment {appointment['id']}: {e}")

        logger.info(f"Background bulk sync completed for clinic {clinic_id}")

    except Exception as e:
        logger.error(f"Background bulk sync failed: {e}", exc_info=True)


async def _bulk_sync_task(
    clinic_id: str,
    organization_id: str,
    from_date: str = None,
    to_date: str = None
):
    """Background task for bulk sync"""
    try:
        calendar_service = ExternalCalendarService()

        # Get unsynced appointments
        result = calendar_service.supabase.rpc('get_unsynced_appointments', {
            'p_clinic_id': clinic_id,
            'p_from_date': from_date,
            'p_to_date': to_date
        }).execute()

        if not result.data:
            logger.info(f"No unsynced appointments for clinic {clinic_id}")
            return

        logger.info(f"Background task syncing {len(result.data)} appointments for clinic {clinic_id}")

        # Sync each appointment
        synced_count = 0
        failed_count = 0
        for appointment in result.data:
            try:
                sync_result = await calendar_service.sync_appointment_to_calendar(appointment['id'])
                if sync_result.get('success'):
                    synced_count += 1
                else:
                    failed_count += 1
            except Exception as e:
                logger.error(f"Failed to sync appointment {appointment['id']}: {e}")
                failed_count += 1

        logger.info(
            f"Background bulk sync completed for clinic {clinic_id}: "
            f"{synced_count} synced, {failed_count} failed"
        )

    except Exception as e:
        logger.error(f"Background bulk sync task failed: {e}", exc_info=True)


@router.post("/sync/appointment")
async def sync_appointment(request: SyncAppointmentRequest):
    """
    Sync a specific appointment to external calendar
    """
    try:
        calendar_service = ExternalCalendarService()
        result = await calendar_service.sync_appointment_to_calendar(request.appointment_id)

        if not result.get('success'):
            raise HTTPException(status_code=500, detail=result.get('error', 'Sync failed'))

        return {
            'success': True,
            'appointment_id': request.appointment_id,
            'google_event_id': result.get('google_event_id'),
            'event_link': result.get('event_link'),
            'message': 'Appointment synced to Google Calendar'
        }

    except Exception as e:
        logger.error(f"Failed to sync appointment: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/sync/bulk")
async def bulk_sync_appointments(request: BulkSyncRequest, background_tasks: BackgroundTasks):
    """
    Bulk sync appointments to external calendar using RPC functions
    Can run in background for large batches
    """
    try:
        # Validate clinic_id or organization_id
        if not request.clinic_id and not request.organization_id:
            raise HTTPException(status_code=400, detail='clinic_id or organization_id is required')

        # If async mode requested, run in background
        if request.async_mode:
            clinic_id = request.clinic_id or request.organization_id
            background_tasks.add_task(
                _bulk_sync_task,
                clinic_id,
                request.organization_id or clinic_id,
                request.from_date,
                request.to_date
            )
            return {
                'success': True,
                'message': 'Bulk sync started in background',
                'async_mode': True
            }

        # Otherwise run synchronously (existing logic)
        calendar_service = ExternalCalendarService()

        # Get all clinics for the organization if organization_id provided
        clinic_ids = []
        if request.organization_id:
            # Get all clinics for this organization
            clinics_result = calendar_service.supabase.from_('clinics').select('id').eq(
                'organization_id', request.organization_id
            ).execute()
            if clinics_result.data:
                clinic_ids = [clinic['id'] for clinic in clinics_result.data]
        elif request.clinic_id:
            clinic_ids = [request.clinic_id]

        if not clinic_ids:
            return {
                'success': True,
                'synced_count': 0,
                'message': 'No clinics found for organization'
            }

        # Get unsynced appointments for all clinics
        appointments = []
        for clinic_id in clinic_ids:
            result = calendar_service.supabase.rpc(
                'get_unsynced_appointments',  # Don't prefix with healthcare. since client already uses healthcare schema
                {
                    'p_clinic_id': clinic_id,
                    'p_from_date': request.from_date,
                    'p_to_date': request.to_date
                }
            ).execute()
            if result.data:
                appointments.extend(result.data)

        if not appointments:
            return {
                'success': True,
                'synced_count': 0,
                'message': 'No appointments to sync'
            }

        # Sync each appointment
        synced_count = 0
        failed_count = 0
        results = []

        for appointment in appointments:
            try:
                result = await calendar_service.sync_appointment_to_calendar(appointment['id'])
                if result.get('success'):
                    synced_count += 1
                    results.append({
                        'appointment_id': appointment['id'],
                        'status': 'synced',
                        'google_event_id': result.get('google_event_id')
                    })
                else:
                    failed_count += 1
                    results.append({
                        'appointment_id': appointment['id'],
                        'status': 'failed',
                        'error': result.get('error')
                    })
            except Exception as e:
                logger.error(f"Failed to sync appointment {appointment['id']}: {e}")
                failed_count += 1
                results.append({
                    'appointment_id': appointment['id'],
                    'status': 'failed',
                    'error': str(e)
                })

        return {
            'success': True,
            'total_appointments': len(appointments),
            'synced_count': synced_count,
            'failed_count': failed_count,
            'results': results,
            'message': f'Synced {synced_count} of {len(appointments)} appointments'
        }

    except Exception as e:
        logger.error(f"Bulk sync failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/sync/appointment/{appointment_id}")
async def sync_appointment_by_id(appointment_id: str):
    """
    Sync appointment by ID (URL parameter)
    """
    try:
        calendar_service = ExternalCalendarService()
        result = await calendar_service.sync_appointment_to_calendar(appointment_id)

        if not result.get('success'):
            raise HTTPException(status_code=500, detail=result.get('error', 'Sync failed'))

        return {
            'success': True,
            'appointment_id': appointment_id,
            'google_event_id': result.get('google_event_id'),
            'event_link': result.get('event_link'),
            'message': 'Appointment synced to Google Calendar'
        }

    except Exception as e:
        logger.error(f"Failed to sync appointment: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/sync/status/{appointment_id}")
async def get_sync_status(appointment_id: str):
    """
    Check if an appointment has been synced to external calendar
    """
    try:
        calendar_service = ExternalCalendarService()

        appointment_result = calendar_service.supabase.table('appointments').select(
            'id, external_calendar_event_id, calendar_synced_at'
        ).eq('id', appointment_id).execute()

        if not appointment_result.data:
            raise HTTPException(status_code=404, detail='Appointment not found')

        appointment = appointment_result.data[0]

        return {
            'success': True,
            'appointment_id': appointment_id,
            'synced': bool(appointment.get('external_calendar_event_id')),
            'google_event_id': appointment.get('external_calendar_event_id'),
            'synced_at': appointment.get('calendar_synced_at'),
            'message': 'Synced' if appointment.get('external_calendar_event_id') else 'Not synced'
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get sync status: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))