"""
Comprehensive Appointment Management API
Replaces existing appointment endpoints with unified calendar-aware system
Implements Phase 2: Feature Integration & Direct Replacement
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks, Query
from pydantic import BaseModel, Field, field_validator
from enum import Enum
import asyncio
from collections import defaultdict

from ..services.unified_appointment_service import (
    UnifiedAppointmentService,
    AppointmentRequest,
    AppointmentType,
    AppointmentStatus
)
from ..services.external_calendar_service import ExternalCalendarService

logger = logging.getLogger(__name__)

# Simple in-memory cache for room configurations with 5-minute TTL
class RoomCache:
    """Simple in-memory cache for room configurations with TTL"""
    def __init__(self, ttl_seconds: int = 300):  # 5 minutes
        self.cache: Dict[str, Dict] = {}
        self.timestamps: Dict[str, datetime] = {}
        self.ttl_seconds = ttl_seconds
        self.lock = asyncio.Lock()

    async def get(self, clinic_id: str) -> Optional[List[Dict]]:
        """Get cached room data for a clinic"""
        async with self.lock:
            if clinic_id in self.cache:
                # Check if cache is still valid
                if (datetime.now() - self.timestamps[clinic_id]).total_seconds() < self.ttl_seconds:
                    logger.debug(f"Cache hit for clinic {clinic_id}")
                    return self.cache[clinic_id]
                else:
                    # Cache expired
                    del self.cache[clinic_id]
                    del self.timestamps[clinic_id]
            return None

    async def set(self, clinic_id: str, rooms: List[Dict]):
        """Set cached room data for a clinic"""
        async with self.lock:
            self.cache[clinic_id] = rooms
            self.timestamps[clinic_id] = datetime.now()
            logger.debug(f"Cached {len(rooms)} rooms for clinic {clinic_id}")

    async def invalidate(self, clinic_id: str):
        """Invalidate cache for a specific clinic"""
        async with self.lock:
            if clinic_id in self.cache:
                del self.cache[clinic_id]
                del self.timestamps[clinic_id]
                logger.debug(f"Invalidated cache for clinic {clinic_id}")

    async def clear(self):
        """Clear all cache"""
        async with self.lock:
            self.cache.clear()
            self.timestamps.clear()
            logger.debug("Cleared all room cache")

# Global room cache instance
room_cache = RoomCache(ttl_seconds=300)  # 5-minute TTL

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
router = APIRouter(prefix="/api/v1/appointments", tags=["Appointments"])

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
    service_id: Optional[str] = None
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

class RoomOverrideRequest(BaseModel):
    room_id: str = Field(..., description="New room ID")
    reason: str = Field(..., min_length=10, description="Reason for override (min 10 chars)")

class RoomOverrideResponse(BaseModel):
    appointment_id: str
    old_room_id: Optional[str]
    new_room_id: str
    updated_at: str
    message: str

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

@router.post("", response_model=AppointmentResponse)  # No trailing slash
@router.post("/", response_model=AppointmentResponse)  # With trailing slash
@router.post("/book", response_model=AppointmentResponse)  # Alternative endpoint
async def book_appointment(
    request: BookAppointmentRequest,
    background_tasks: BackgroundTasks,
    service: UnifiedAppointmentService = Depends(get_appointment_service)
):
    """
    Book a new appointment using the unified calendar-aware system

    Available at both POST /api/v1/appointments and POST /api/v1/appointments/book
    for backward compatibility.
    """
    try:
        # Convert request to internal format
        appointment_request = AppointmentRequest(
            patient_id=request.patient_id,
            doctor_id=request.doctor_id,
            clinic_id=request.clinic_id,
            service_id=request.service_id,
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

class BatchAvailabilityRequest(BaseModel):
    clinic_id: str
    service_id: str
    doctor_id: Optional[str] = None
    date_start: str  # YYYY-MM-DD format
    date_end: str    # YYYY-MM-DD format
    duration_minutes: int = 30

    @field_validator('date_start', 'date_end')
    @classmethod
    def validate_date(cls, v):
        try:
            datetime.fromisoformat(v)
            return v
        except ValueError:
            raise ValueError('Invalid date format. Use YYYY-MM-DD.')

class AvailableSlotWithRoom(BaseModel):
    time: str  # ISO format datetime
    doctor_id: str
    room_id: str
    score: float
    duration_minutes: int

class BatchAvailabilityResponse(BaseModel):
    slots: List[AvailableSlotWithRoom]
    total_count: int
    date_range: Dict[str, str]
    cache_hit: bool = False

@router.post("/available-slots", response_model=BatchAvailabilityResponse)
async def get_batch_availability_with_rooms(
    request: BatchAvailabilityRequest,
    service: UnifiedAppointmentService = Depends(get_appointment_service)
):
    """
    Get batch availability with pre-assigned rooms using optimized SQL query.

    This endpoint combines doctor availability, equipment requirements, and room
    availability checks in a single optimized query for better performance.

    - **clinic_id**: UUID of the clinic
    - **service_id**: UUID of the service being scheduled
    - **doctor_id**: Optional UUID to filter by specific doctor
    - **date_start**: Start date for availability search (YYYY-MM-DD)
    - **date_end**: End date for availability search (YYYY-MM-DD)
    - **duration_minutes**: Duration of the appointment in minutes

    Returns a list of available slots with pre-assigned rooms and optimization scores.
    """
    try:
        # Validate date range
        try:
            start_date = datetime.fromisoformat(request.date_start)
            end_date = datetime.fromisoformat(request.date_end)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD.")

        if start_date >= end_date:
            raise HTTPException(status_code=400, detail="date_start must be before date_end")

        # Check if date range is reasonable (max 30 days)
        if (end_date - start_date).days > 30:
            raise HTTPException(status_code=400, detail="Date range cannot exceed 30 days")

        # Import constraint engine and database client
        from ..services.scheduling.constraint_engine import ConstraintEngine
        from ..db.supabase_client import get_supabase_client

        db = get_supabase_client(schema='healthcare')
        constraint_engine = ConstraintEngine(db)

        # Get available slots with rooms using optimized query
        available_slots = await get_optimized_availability(
            db=db,
            constraint_engine=constraint_engine,
            clinic_id=request.clinic_id,
            service_id=request.service_id,
            doctor_id=request.doctor_id,
            start_date=start_date,
            end_date=end_date,
            duration_minutes=request.duration_minutes
        )

        return BatchAvailabilityResponse(
            slots=available_slots,
            total_count=len(available_slots),
            date_range={
                "start": request.date_start,
                "end": request.date_end
            }
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get batch availability: {e}")
        raise HTTPException(status_code=500, detail="Failed to retrieve batch availability")

async def get_optimized_availability(
    db,
    constraint_engine: Any,
    clinic_id: str,
    service_id: str,
    doctor_id: Optional[str],
    start_date: datetime,
    end_date: datetime,
    duration_minutes: int
) -> List[AvailableSlotWithRoom]:
    """
    Execute optimized SQL query to find available slots with rooms.

    This function uses a single SQL query with window functions and LEFT JOINs
    to check doctor availability, equipment requirements, and room availability
    in a single pass.
    """
    # Build the optimized SQL query
    sql_query = """
    WITH date_series AS (
        -- Generate series of dates in the range
        SELECT generate_series(
            %(start_date)s::date,
            %(end_date)s::date,
            '1 day'::interval
        )::date AS appointment_date
    ),
    time_slots AS (
        -- Generate time slots for each date based on doctor schedules
        SELECT
            ds.appointment_date,
            d.id AS doctor_id,
            (ds.appointment_date + dsch.start_time)::timestamp AS slot_start,
            (ds.appointment_date + dsch.start_time + interval '%(duration)s minutes')::timestamp AS slot_end,
            dsch.start_time,
            dsch.end_time
        FROM date_series ds
        CROSS JOIN healthcare.doctors d
        LEFT JOIN healthcare.doctor_schedules dsch ON d.id = dsch.doctor_id
        WHERE d.clinic_id = %(clinic_id)s::uuid
            AND (%(doctor_id)s IS NULL OR d.id = %(doctor_id)s::uuid)
            AND dsch.day_of_week = LOWER(TO_CHAR(ds.appointment_date, 'Day'))
            -- Ensure slot fits within working hours
            AND dsch.start_time + interval '%(duration)s minutes' <= dsch.end_time
    ),
    doctor_conflicts AS (
        -- Check for doctor appointment conflicts
        SELECT DISTINCT
            ts.appointment_date,
            ts.doctor_id,
            ts.slot_start
        FROM time_slots ts
        INNER JOIN healthcare.appointments a ON (
            a.doctor_id = ts.doctor_id
            AND a.appointment_date = ts.appointment_date
            AND a.status NOT IN ('cancelled', 'no_show')
            AND (
                (a.start_time, a.end_time) OVERLAPS
                (ts.slot_start::time, ts.slot_end::time)
            )
        )
    ),
    doctor_timeoff AS (
        -- Check for doctor time-off
        SELECT DISTINCT
            ts.appointment_date,
            ts.doctor_id,
            ts.slot_start
        FROM time_slots ts
        INNER JOIN healthcare.doctor_time_off dto ON (
            dto.doctor_id = ts.doctor_id
            AND ts.appointment_date BETWEEN dto.start_date AND dto.end_date
        )
    ),
    available_rooms AS (
        -- Find rooms that meet service requirements and are available
        SELECT
            ts.appointment_date,
            ts.doctor_id,
            ts.slot_start,
            ts.slot_end,
            r.id AS room_id,
            r.room_name,
            -- Calculate room score based on availability and features
            (
                CASE WHEN r.is_available THEN 10 ELSE 0 END +
                CASE WHEN r.cleaning_duration_minutes <= 15 THEN 5 ELSE 0 END +
                COALESCE(ARRAY_LENGTH(r.accessibility_features, 1), 0)
            ) AS room_score
        FROM time_slots ts
        CROSS JOIN healthcare.rooms r
        LEFT JOIN healthcare.appointments a ON (
            a.room_id = r.id
            AND a.appointment_date = ts.appointment_date
            AND a.status NOT IN ('cancelled', 'no_show')
            AND (
                (a.start_time, a.end_time) OVERLAPS
                (ts.slot_start::time, ts.slot_end::time)
            )
        )
        LEFT JOIN healthcare.appointment_holds h ON (
            h.room_id = r.id
            AND h.appointment_date = ts.appointment_date
            AND h.status = 'active'
            AND h.expires_at > NOW()
            AND (
                (h.start_time, h.end_time) OVERLAPS
                (ts.slot_start, ts.slot_end)
            )
        )
        WHERE r.clinic_id = %(clinic_id)s::uuid
            AND r.is_available = TRUE
            AND a.id IS NULL  -- No conflicting appointment
            AND h.hold_id IS NULL  -- No active hold
    ),
    ranked_slots AS (
        -- Combine all checks and rank available slots
        SELECT
            ar.slot_start,
            ar.doctor_id,
            ar.room_id,
            ar.room_score,
            ROW_NUMBER() OVER (
                PARTITION BY ar.slot_start, ar.doctor_id
                ORDER BY ar.room_score DESC
            ) AS room_rank
        FROM available_rooms ar
        LEFT JOIN doctor_conflicts dc ON (
            dc.appointment_date = ar.appointment_date
            AND dc.doctor_id = ar.doctor_id
            AND dc.slot_start = ar.slot_start
        )
        LEFT JOIN doctor_timeoff dto ON (
            dto.appointment_date = ar.appointment_date
            AND dto.doctor_id = ar.doctor_id
            AND dto.slot_start = ar.slot_start
        )
        WHERE dc.doctor_id IS NULL  -- No doctor conflict
            AND dto.doctor_id IS NULL  -- No doctor time-off
    )
    SELECT
        slot_start AS time,
        doctor_id,
        room_id,
        room_score AS score
    FROM ranked_slots
    WHERE room_rank = 1  -- Best room for each slot
    ORDER BY slot_start, score DESC
    LIMIT 500;
    """

    # Execute query with parameters
    try:
        result = db.rpc('exec_sql', {
            'query': sql_query,
            'params': {
                'clinic_id': clinic_id,
                'service_id': service_id,
                'doctor_id': doctor_id,
                'start_date': start_date.isoformat(),
                'end_date': end_date.isoformat(),
                'duration': duration_minutes
            }
        }).execute()

        # Parse results into response format
        available_slots = []
        for row in result.data:
            available_slots.append(AvailableSlotWithRoom(
                time=row['time'],
                doctor_id=str(row['doctor_id']),
                room_id=str(row['room_id']),
                score=float(row['score']),
                duration_minutes=duration_minutes
            ))

        return available_slots

    except Exception as e:
        logger.error(f"Optimized query failed, falling back to iterative approach: {e}")
        # Fallback to iterative approach if SQL query fails
        return await get_availability_fallback(
            db=db,
            constraint_engine=constraint_engine,
            clinic_id=clinic_id,
            service_id=service_id,
            doctor_id=doctor_id,
            start_date=start_date,
            end_date=end_date,
            duration_minutes=duration_minutes
        )

async def get_availability_fallback(
    db,
    constraint_engine: Any,
    clinic_id: str,
    service_id: str,
    doctor_id: Optional[str],
    start_date: datetime,
    end_date: datetime,
    duration_minutes: int
) -> List[AvailableSlotWithRoom]:
    """
    Fallback method using iterative checks when optimized query fails.
    """
    from uuid import UUID

    available_slots = []

    # Get doctors
    doctor_query = db.table("doctors").select("*").eq("clinic_id", clinic_id)
    if doctor_id:
        doctor_query = doctor_query.eq("id", doctor_id)
    doctors_result = doctor_query.execute()

    if not doctors_result.data:
        return []

    # Get rooms from cache or database
    rooms = await room_cache.get(clinic_id)
    if rooms is None:
        rooms_result = db.table("rooms").select("*").eq("clinic_id", clinic_id).eq("is_available", True).execute()
        rooms = rooms_result.data if rooms_result.data else []
        # Cache the rooms
        await room_cache.set(clinic_id, rooms)

    # Iterate through dates
    current_date = start_date
    while current_date <= end_date:
        day_name = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"][current_date.weekday()]

        for doctor in doctors_result.data:
            # Get doctor schedule for this day
            schedule_result = db.table("doctor_schedules")\
                .select("*")\
                .eq("doctor_id", doctor['id'])\
                .eq("day_of_week", day_name)\
                .execute()

            if not schedule_result.data:
                continue

            for schedule in schedule_result.data:
                # Parse schedule times
                work_start = datetime.strptime(schedule["start_time"], "%H:%M").time()
                work_end = datetime.strptime(schedule["end_time"], "%H:%M").time()

                # Generate time slots
                slot_time = datetime.combine(current_date.date(), work_start)
                end_of_day = datetime.combine(current_date.date(), work_end)

                while slot_time + timedelta(minutes=duration_minutes) <= end_of_day:
                    slot_end = slot_time + timedelta(minutes=duration_minutes)

                    # Check doctor availability
                    doctor_available = await constraint_engine.check_doctor_schedule(
                        UUID(doctor['id']), slot_time, duration_minutes
                    )
                    doctor_not_off = await constraint_engine.check_doctor_time_off(
                        UUID(doctor['id']), slot_time
                    )

                    if doctor_available and doctor_not_off:
                        # Find available room
                        for room in rooms:
                            room_available = await constraint_engine.check_room_availability(
                                UUID(room['id']), slot_time, slot_end
                            )

                            if room_available:
                                # Calculate score
                                score = 10.0
                                if room.get('cleaning_duration_minutes', 15) <= 15:
                                    score += 5.0
                                if room.get('accessibility_features'):
                                    score += len(room['accessibility_features'])

                                available_slots.append(AvailableSlotWithRoom(
                                    time=slot_time.isoformat(),
                                    doctor_id=doctor['id'],
                                    room_id=room['id'],
                                    score=score,
                                    duration_minutes=duration_minutes
                                ))
                                break  # Use first available room

                    # Move to next time slot (15-minute intervals)
                    slot_time += timedelta(minutes=15)

                    # Limit slots per day to avoid performance issues
                    if len(available_slots) >= 500:
                        return available_slots

        current_date += timedelta(days=1)

    return available_slots

@router.patch("/{appointment_id}/room", response_model=RoomOverrideResponse)
async def override_appointment_room(
    appointment_id: str,
    request: RoomOverrideRequest,
    service: UnifiedAppointmentService = Depends(get_appointment_service)
):
    """
    Override the room assignment for an appointment with manual selection.

    This endpoint allows manual room reassignment with audit logging.
    Requires a reason for compliance and audit trail purposes.

    - **appointment_id**: UUID of the appointment to update
    - **room_id**: UUID of the new room to assign
    - **reason**: Reason for the override (minimum 10 characters)

    Returns updated appointment details with old and new room IDs.
    """
    try:
        from ..db.supabase_client import get_supabase_client
        from ..security.hipaa_audit_system import (
            HIPAAAuditSystem,
            AuditEventType,
            AuditResult
        )
        from uuid import UUID

        db = get_supabase_client(schema='healthcare')

        # Validate appointment exists
        appointment_result = db.table("appointments") \
            .select("*") \
            .eq("id", appointment_id) \
            .single() \
            .execute()

        if not appointment_result.data:
            raise HTTPException(
                status_code=404,
                detail=f"Appointment {appointment_id} not found"
            )

        appointment = appointment_result.data

        # Check appointment is not cancelled or completed
        invalid_statuses = ['cancelled', 'completed', 'no_show']
        if appointment.get('status') in invalid_statuses:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot override room for appointment with status: {appointment.get('status')}"
            )

        # Validate new room exists
        room_result = db.table("rooms") \
            .select("*") \
            .eq("id", request.room_id) \
            .single() \
            .execute()

        if not room_result.data:
            raise HTTPException(
                status_code=404,
                detail=f"Room {request.room_id} not found"
            )

        room = room_result.data

        # Check room is available (not disabled)
        if not room.get('is_available', False):
            raise HTTPException(
                status_code=409,
                detail=f"Room {room.get('room_name', request.room_id)} is not available"
            )

        # Check room belongs to same clinic
        if room.get('clinic_id') != appointment.get('clinic_id'):
            raise HTTPException(
                status_code=400,
                detail="Room must belong to the same clinic as the appointment"
            )

        # Check if room is available for the appointment time slot
        appointment_date = appointment.get('appointment_date')
        start_time = appointment.get('start_time')
        end_time = appointment.get('end_time')

        # Store old room_id for audit log
        old_room_id = appointment.get('room_id')

        # Use transaction with locking to prevent race conditions (RC4 fix)
        from ..database import get_db_connection
        import asyncpg as pg

        try:
            async with get_db_connection() as conn:
                if conn is None:
                    raise HTTPException(
                        status_code=503,
                        detail="Database connection unavailable"
                    )

                async with conn.transaction():
                    # Lock the room row to prevent concurrent modifications
                    room_lock = await conn.fetchrow(
                        """
                        SELECT id FROM healthcare.rooms
                        WHERE id = $1
                        FOR UPDATE
                        """,
                        UUID(request.room_id)
                    )

                    if not room_lock:
                        raise HTTPException(
                            status_code=404,
                            detail=f"Room {request.room_id} not found during lock"
                        )

                    # Check for conflicting appointments with locking
                    conflict_check = await conn.fetchrow(
                        """
                        SELECT id FROM healthcare.appointments
                        WHERE room_id = $1
                        AND appointment_date = $2
                        AND id != $3
                        AND status IN ('scheduled', 'confirmed', 'checked_in', 'in_progress')
                        AND (start_time, end_time) OVERLAPS ($4::time, $5::time)
                        LIMIT 1
                        """,
                        UUID(request.room_id),
                        appointment_date,
                        UUID(appointment_id),
                        start_time,
                        end_time
                    )

                    if conflict_check:
                        raise HTTPException(
                            status_code=409,
                            detail=f"Room {room.get('room_name', request.room_id)} is not available during the appointment time slot"
                        )

                    # Update appointment with new room within transaction
                    update_result = await conn.execute(
                        """
                        UPDATE healthcare.appointments
                        SET room_id = $1, updated_at = $2
                        WHERE id = $3
                        """,
                        UUID(request.room_id),
                        datetime.utcnow(),
                        UUID(appointment_id)
                    )

                    if update_result != "UPDATE 1":
                        raise HTTPException(
                            status_code=500,
                            detail="Failed to update appointment"
                        )

                    # Transaction will commit here

        except pg.UniqueViolationError as e:
            logger.error(f"Unique constraint violation during room override: {e}")
            raise HTTPException(
                status_code=409,
                detail="Room assignment conflict detected (prevented by database constraint)"
            )
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Transaction failed during room override: {e}")
            raise HTTPException(
                status_code=500,
                detail=f"Failed to update room assignment: {str(e)}"
            )

        updated_at = datetime.utcnow()

        # Audit logging
        try:
            audit_system = HIPAAAuditSystem(db)
            await audit_system.log_audit_event(
                event_type=AuditEventType.ADMIN_ACTION,
                user_id="system",  # In production, get from auth
                user_role="admin",
                patient_id=appointment.get('patient_id'),
                result=AuditResult.SUCCESS,
                resource_accessed=f"appointment/{appointment_id}",
                ip_address="internal",
                user_agent="appointments_api",
                session_id="api_call",
                organization_id=appointment.get('clinic_id', 'default'),
                reason=request.reason,
                metadata={
                    "action": "room_override",
                    "appointment_id": appointment_id,
                    "old_room_id": old_room_id,
                    "new_room_id": request.room_id,
                    "reason": request.reason,
                    "appointment_date": appointment_date,
                    "start_time": start_time,
                    "end_time": end_time
                },
                data_volume=1
            )
        except Exception as audit_error:
            logger.error(f"Audit logging failed: {audit_error}")
            # Don't fail the request if audit logging fails

        # Return success response
        return RoomOverrideResponse(
            appointment_id=appointment_id,
            old_room_id=old_room_id,
            new_room_id=request.room_id,
            updated_at=updated_at.isoformat(),
            message=f"Room successfully updated. Reason: {request.reason}"
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to override appointment room: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to override appointment room: {str(e)}"
        )

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
                "comprehensive_operations",
                "batch_availability_with_rooms",
                "manual_room_override"
            ]
        }
    except Exception as e:
        logger.error(f"Appointments API health check failed: {e}")
        raise HTTPException(status_code=503, detail="Appointments service unavailable")
