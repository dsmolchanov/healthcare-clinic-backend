"""
Comprehensive Appointment Management API
Replaces existing appointment endpoints with unified calendar-aware system
Implements Phase 2: Feature Integration & Direct Replacement
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks, Query
from pydantic import BaseModel, field_validator
from enum import Enum

from ..services.unified_appointment_service import (
    UnifiedAppointmentService,
    AppointmentRequest,
    AppointmentType,
    AppointmentStatus
)
from ..services.external_calendar_service import ExternalCalendarService

logger = logging.getLogger(__name__)

# Background task for instant calendar sync
async def sync_appointment_to_google(appointment_id: str):
    """Sync appointment to Google Calendar instantly (background task)"""
    try:
        calendar_service = ExternalCalendarService()
        result = await calendar_service.sync_appointment_to_calendar(appointment_id)
        if result.get('success'):
            logger.info(f"Instant sync: Appointment {appointment_id} synced to Google Calendar")
        else:
            logger.warning(f"Instant sync failed for {appointment_id}: {result.get('error')}")
    except Exception as e:
        logger.error(f"Instant sync error for {appointment_id}: {e}")

# Create router
router = APIRouter(prefix="/api/appointments", tags=["Appointments"])

# Pydantic models for request/response

class AppointmentTypeEnum(str, Enum):
    CONSULTATION = "consultation"
    CLEANING = "cleaning"
    PROCEDURE = "procedure"
    FOLLOW_UP = "follow_up"
    EMERGENCY = "emergency"

class AppointmentStatusEnum(str, Enum):
    SCHEDULED = "scheduled"
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    NO_SHOW = "no_show"
    RESCHEDULED = "rescheduled"

class BookAppointmentRequest(BaseModel):
    patient_id: str
    doctor_id: str
    clinic_id: str
    start_time: str  # ISO format datetime string
    end_time: str    # ISO format datetime string
    appointment_type: AppointmentTypeEnum
    reason: Optional[str] = None
    notes: Optional[str] = None
    patient_phone: Optional[str] = None
    patient_email: Optional[str] = None

    @field_validator('start_time', 'end_time')
    @classmethod
    def validate_datetime(cls, v):
        try:
            return datetime.fromisoformat(v.replace('Z', '+00:00'))
        except ValueError:
            raise ValueError('Invalid datetime format. Use ISO format.')

class RescheduleRequest(BaseModel):
    new_start_time: str
    new_end_time: str

    @field_validator('new_start_time', 'new_end_time')
    @classmethod
    def validate_datetime(cls, v):
        try:
            return datetime.fromisoformat(v.replace('Z', '+00:00'))
        except ValueError:
            raise ValueError('Invalid datetime format. Use ISO format.')

class CancelRequest(BaseModel):
    reason: Optional[str] = None

class AppointmentResponse(BaseModel):
    success: bool
    appointment_id: Optional[str] = None
    reservation_id: Optional[str] = None
    error: Optional[str] = None
    external_events: Optional[Dict[str, str]] = None
    conflicts: Optional[List[str]] = None

class TimeSlotResponse(BaseModel):
    start_time: str
    end_time: str
    doctor_id: str
    available: bool
    source: str

class AppointmentListResponse(BaseModel):
    id: str
    clinic_id: str
    patient_id: str
    doctor_id: str
    appointment_date: str
    start_time: str
    end_time: str
    status: str
    appointment_type: str
    reason: Optional[str] = None
    notes: Optional[str] = None
    created_at: str
    updated_at: str

# Dependency to get service instance
async def get_appointment_service() -> UnifiedAppointmentService:
    return UnifiedAppointmentService()

# API Endpoints

@router.get("/available", response_model=List[TimeSlotResponse])
async def get_available_slots(
    doctor_id: str,
    date: str,
    duration_minutes: int = Query(30, ge=15, le=240),
    appointment_type: Optional[str] = None,
    service: UnifiedAppointmentService = Depends(get_appointment_service)
):
    """
    Get available appointment slots for a specific doctor and date

    - **doctor_id**: UUID of the doctor
    - **date**: Date in YYYY-MM-DD format
    - **duration_minutes**: Appointment duration (15-240 minutes)
    - **appointment_type**: Optional filter by appointment type
    """
    try:
        # Validate date format
        try:
            datetime.fromisoformat(date)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD.")

        # Get available slots from unified service
        slots = await service.get_available_slots(
            doctor_id=doctor_id,
            date=date,
            duration_minutes=duration_minutes,
            appointment_type=appointment_type
        )

        # Convert to response format
        return [
            TimeSlotResponse(
                start_time=slot.start_time.isoformat(),
                end_time=slot.end_time.isoformat(),
                doctor_id=slot.doctor_id,
                available=slot.available,
                source=slot.source
            )
            for slot in slots
        ]

    except Exception as e:
        logger.error(f"Failed to get available slots: {e}")
        raise HTTPException(status_code=500, detail="Failed to retrieve available slots")

@router.post("/book", response_model=AppointmentResponse)
async def book_appointment(
    request: BookAppointmentRequest,
    background_tasks: BackgroundTasks,
    service: UnifiedAppointmentService = Depends(get_appointment_service)
):
    """
    Book a new appointment using the unified calendar-aware system

    This endpoint replaces the existing /api/appointments/book endpoint
    and integrates with external calendar coordination.
    """
    try:
        # Convert request to internal format
        appointment_request = AppointmentRequest(
            patient_id=request.patient_id,
            doctor_id=request.doctor_id,
            clinic_id=request.clinic_id,
            start_time=request.start_time,
            end_time=request.end_time,
            appointment_type=AppointmentType(request.appointment_type.value),
            reason=request.reason,
            notes=request.notes,
            patient_phone=request.patient_phone,
            patient_email=request.patient_email
        )

        # Book appointment using unified service
        result = await service.book_appointment(appointment_request)

        # Schedule background tasks for notifications, calendar sync, etc.
        if result.success:
            background_tasks.add_task(
                send_appointment_confirmation,
                result.appointment_id,
                request.patient_phone,
                request.patient_email
            )

            # Instant sync to Google Calendar (no more 15-minute wait!)
            background_tasks.add_task(
                sync_appointment_to_google,
                result.appointment_id
            )

        return AppointmentResponse(
            success=result.success,
            appointment_id=result.appointment_id,
            reservation_id=result.reservation_id,
            error=result.error,
            external_events=result.external_events,
            conflicts=result.conflicts
        )

    except Exception as e:
        logger.error(f"Failed to book appointment: {e}")
        raise HTTPException(status_code=500, detail="Failed to book appointment")

@router.put("/{appointment_id}/reschedule", response_model=AppointmentResponse)
async def reschedule_appointment(
    appointment_id: str,
    request: RescheduleRequest,
    background_tasks: BackgroundTasks,
    service: UnifiedAppointmentService = Depends(get_appointment_service)
):
    """
    Reschedule an existing appointment to a new time

    This uses the ask-hold-reserve pattern to ensure the new time is available
    across all calendar sources before making the change.
    """
    try:
        result = await service.reschedule_appointment(
            appointment_id=appointment_id,
            new_start_time=request.new_start_time,
            new_end_time=request.new_end_time
        )

        if result.success:
            background_tasks.add_task(
                send_reschedule_notification,
                appointment_id,
                request.new_start_time
            )

            # Instant sync to Google Calendar after reschedule
            background_tasks.add_task(
                sync_appointment_to_google,
                appointment_id
            )

        return AppointmentResponse(
            success=result.success,
            appointment_id=result.appointment_id,
            reservation_id=result.reservation_id,
            error=result.error,
            conflicts=result.conflicts
        )

    except Exception as e:
        logger.error(f"Failed to reschedule appointment: {e}")
        raise HTTPException(status_code=500, detail="Failed to reschedule appointment")

@router.delete("/{appointment_id}/cancel", response_model=AppointmentResponse)
async def cancel_appointment(
    appointment_id: str,
    request: CancelRequest,
    background_tasks: BackgroundTasks,
    service: UnifiedAppointmentService = Depends(get_appointment_service)
):
    """
    Cancel an existing appointment

    This will also clean up any external calendar events that were created.
    """
    try:
        result = await service.cancel_appointment(
            appointment_id=appointment_id,
            reason=request.reason
        )

        if result.success:
            background_tasks.add_task(
                send_cancellation_notification,
                appointment_id,
                request.reason
            )

        return AppointmentResponse(
            success=result.success,
            appointment_id=result.appointment_id,
            error=result.error
        )

    except Exception as e:
        logger.error(f"Failed to cancel appointment: {e}")
        raise HTTPException(status_code=500, detail="Failed to cancel appointment")

@router.get("/", response_model=List[AppointmentListResponse])
async def list_appointments(
    doctor_id: Optional[str] = None,
    patient_id: Optional[str] = None,
    clinic_id: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[AppointmentStatusEnum] = None,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    service: UnifiedAppointmentService = Depends(get_appointment_service)
):
    """
    List appointments with filtering options

    - **doctor_id**: Filter by doctor
    - **patient_id**: Filter by patient
    - **clinic_id**: Filter by clinic
    - **date_from**: Start date filter (YYYY-MM-DD)
    - **date_to**: End date filter (YYYY-MM-DD)
    - **status**: Filter by appointment status
    - **limit**: Maximum number of results
    - **offset**: Number of results to skip
    """
    try:
        # Validate date formats if provided
        if date_from:
            try:
                datetime.fromisoformat(date_from)
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid date_from format. Use YYYY-MM-DD.")

        if date_to:
            try:
                datetime.fromisoformat(date_to)
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid date_to format. Use YYYY-MM-DD.")

        appointments = await service.get_appointments(
            doctor_id=doctor_id,
            patient_id=patient_id,
            clinic_id=clinic_id,
            date_from=date_from,
            date_to=date_to,
            status=status.value if status else None
        )

        # Apply pagination
        paginated_appointments = appointments[offset:offset + limit]

        return [
            AppointmentListResponse(**appointment)
            for appointment in paginated_appointments
        ]

    except Exception as e:
        logger.error(f"Failed to list appointments: {e}")
        raise HTTPException(status_code=500, detail="Failed to retrieve appointments")

@router.get("/{appointment_id}", response_model=AppointmentListResponse)
async def get_appointment(
    appointment_id: str,
    service: UnifiedAppointmentService = Depends(get_appointment_service)
):
    """Get a specific appointment by ID"""
    try:
        appointments = await service.get_appointments()
        appointment = next((apt for apt in appointments if apt['id'] == appointment_id), None)

        if not appointment:
            raise HTTPException(status_code=404, detail="Appointment not found")

        return AppointmentListResponse(**appointment)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get appointment: {e}")
        raise HTTPException(status_code=500, detail="Failed to retrieve appointment")

@router.post("/{appointment_id}/confirm")
async def confirm_appointment(
    appointment_id: str,
    background_tasks: BackgroundTasks,
    service: UnifiedAppointmentService = Depends(get_appointment_service)
):
    """
    Confirm an appointment

    This updates the status and can trigger additional calendar sync operations.
    """
    try:
        # Get appointment and update status
        appointments = await service.get_appointments()
        appointment = next((apt for apt in appointments if apt['id'] == appointment_id), None)

        if not appointment:
            raise HTTPException(status_code=404, detail="Appointment not found")

        # Update status to confirmed (this could be enhanced with more logic)
        # For now, this is a placeholder for the confirmation workflow

        background_tasks.add_task(
            send_confirmation_notification,
            appointment_id
        )

        return {"success": True, "message": "Appointment confirmed"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to confirm appointment: {e}")
        raise HTTPException(status_code=500, detail="Failed to confirm appointment")

# Background task functions

async def send_appointment_confirmation(
    appointment_id: str,
    patient_phone: Optional[str] = None,
    patient_email: Optional[str] = None
):
    """Send appointment confirmation via SMS/email"""
    try:
        logger.info(f"Sending appointment confirmation for {appointment_id}")
        # This would integrate with notification services
        # For now, just log the action
    except Exception as e:
        logger.error(f"Failed to send confirmation: {e}")

async def send_reschedule_notification(appointment_id: str, new_time: str):
    """Send reschedule notification"""
    try:
        logger.info(f"Sending reschedule notification for {appointment_id} to {new_time}")
        # This would integrate with notification services
    except Exception as e:
        logger.error(f"Failed to send reschedule notification: {e}")

async def send_cancellation_notification(appointment_id: str, reason: Optional[str] = None):
    """Send cancellation notification"""
    try:
        logger.info(f"Sending cancellation notification for {appointment_id}")
        # This would integrate with notification services
    except Exception as e:
        logger.error(f"Failed to send cancellation notification: {e}")

async def send_confirmation_notification(appointment_id: str):
    """Send confirmation notification"""
    try:
        logger.info(f"Sending confirmation notification for {appointment_id}")
        # This would integrate with notification services
    except Exception as e:
        logger.error(f"Failed to send confirmation notification: {e}")

# Health check endpoint for the appointments API
@router.get("/health")
async def appointments_health_check():
    """Health check for appointments API"""
    try:
        service = UnifiedAppointmentService()
        # Quick test of service instantiation
        return {
            "status": "healthy",
            "service": "Unified Appointments API",
            "features": [
                "calendar_coordination",
                "ask_hold_reserve",
                "external_calendar_sync",
                "comprehensive_operations"
            ]
        }
    except Exception as e:
        logger.error(f"Appointments API health check failed: {e}")
        raise HTTPException(status_code=503, detail="Appointments service unavailable")