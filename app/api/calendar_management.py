"""
Calendar Management API
Endpoints for managing multi-doctor calendar setup
"""
import logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

from ..services.doctor_calendar_manager import DoctorCalendarManager
from ..security.compliance_vault import ComplianceVault
from ..db.supabase_client import get_supabase_client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/calendar-management", tags=["Calendar Management"])


class SetupMultiDoctorRequest(BaseModel):
    organization_id: str


class SetupMultiDoctorResponse(BaseModel):
    success: bool
    total: Optional[int] = None
    created: Optional[int] = None
    skipped: Optional[int] = None
    failed: Optional[int] = None
    message: Optional[str] = None
    error: Optional[str] = None


@router.post("/setup-multi-doctor", response_model=SetupMultiDoctorResponse)
async def setup_multi_doctor_calendars(request: SetupMultiDoctorRequest):
    """
    Setup individual sub-calendars for each doctor in an organization

    This will:
    1. Create a secondary Google Calendar for each doctor
    2. Assign unique colors to each doctor's calendar
    3. Enable multi-doctor mode for the organization
    4. Future appointments will sync to doctor-specific calendars

    Benefits:
    - Better visual organization (color-coded per doctor)
    - Toggle doctors on/off in Google Calendar
    - Each doctor can manage their own calendar permissions
    """
    try:
        logger.info(f"Setting up multi-doctor calendars for org {request.organization_id}")

        # Get calendar credentials from vault
        vault = ComplianceVault()
        credentials = await vault.retrieve_calendar_credentials(
            organization_id=request.organization_id,
            provider='google'
        )

        if not credentials:
            raise HTTPException(
                status_code=400,
                detail="No Google Calendar credentials found. Please connect calendar first."
            )

        # Setup multi-doctor calendars
        manager = DoctorCalendarManager()
        result = await manager.setup_multi_doctor_calendars(
            organization_id=request.organization_id,
            credentials=credentials
        )

        if not result.get('success'):
            raise HTTPException(status_code=500, detail=result.get('error', 'Setup failed'))

        return SetupMultiDoctorResponse(**result)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to setup multi-doctor calendars: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/status/{organization_id}")
async def get_multi_doctor_status(organization_id: str):
    """
    Get the status of multi-doctor calendar setup

    Returns:
    - multi_doctor_mode: Whether multi-doctor mode is enabled
    - doctors: List of doctors with their calendar status
    """
    try:
        supabase = get_supabase_client()

        # Check if multi-doctor mode is enabled
        integration = supabase.from_('calendar_integrations').select(
            'multi_doctor_mode, provider'
        ).eq('organization_id', organization_id).eq('provider', 'google').execute()

        multi_doctor_mode = False
        if integration.data and len(integration.data) > 0:
            multi_doctor_mode = integration.data[0].get('multi_doctor_mode', False)

        # Get all doctors and their calendar status
        # First get clinics for this organization
        clinics = supabase.from_('clinics').select('id').eq('organization_id', organization_id).execute()
        clinic_ids = [c['id'] for c in (clinics.data or [])]

        if not clinic_ids:
            return {
                'success': True,
                'organization_id': organization_id,
                'multi_doctor_mode': multi_doctor_mode,
                'total_doctors': 0,
                'doctors_with_calendars': 0,
                'doctors': []
            }

        # Get doctors for these clinics
        doctors = supabase.from_('doctors').select(
            'id, first_name, last_name, google_calendar_id, google_calendar_color_id'
        ).in_('clinic_id', clinic_ids).execute()

        doctor_list = []
        for doctor in (doctors.data or []):
            doctor_list.append({
                'id': doctor['id'],
                'name': f"{doctor['first_name']} {doctor['last_name']}",
                'has_calendar': bool(doctor.get('google_calendar_id')),
                'calendar_id': doctor.get('google_calendar_id'),
                'color_id': doctor.get('google_calendar_color_id')
            })

        return {
            'success': True,
            'organization_id': organization_id,
            'multi_doctor_mode': multi_doctor_mode,
            'total_doctors': len(doctor_list),
            'doctors_with_calendars': sum(1 for d in doctor_list if d['has_calendar']),
            'doctors': doctor_list
        }

    except Exception as e:
        logger.error(f"Failed to get multi-doctor status: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/disable-multi-doctor/{organization_id}")
async def disable_multi_doctor_mode(organization_id: str):
    """
    Disable multi-doctor mode and revert to using primary calendar

    Note: This does NOT delete the sub-calendars, just stops using them
    """
    try:
        supabase = get_supabase_client()

        # Disable multi-doctor mode
        supabase.from_('calendar_integrations').update({
            'multi_doctor_mode': False
        }).eq('organization_id', organization_id).eq('provider', 'google').execute()

        logger.info(f"Disabled multi-doctor mode for org {organization_id}")

        return {
            'success': True,
            'message': 'Multi-doctor mode disabled. Future appointments will use primary calendar.'
        }

    except Exception as e:
        logger.error(f"Failed to disable multi-doctor mode: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
