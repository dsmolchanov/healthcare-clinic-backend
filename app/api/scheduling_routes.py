"""
Scheduling API Routes
RESTful API endpoints for the intelligent scheduling system.

This module provides endpoints for:
- Slot suggestions with AI scoring
- Hold management (idempotent)
- Appointment confirmation
- Escalation management

Implementation follows the task requirements from Task #15.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from typing import Optional, List
from uuid import UUID
from datetime import datetime
import logging

from ..models.scheduling import (
    DateRange, Slot, HardConstraints, SuggestedSlots,
    HoldResponse, AppointmentResponse
)
from ..models.escalation import (
    EscalationResponse, EscalationResolution, EscalationStatus
)
from ..database import get_supabase
from ..exceptions import (
    NoSlotsAvailableError,
    HoldExpiredError,
    HoldNotFoundError,
    EscalationNotFoundError
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/scheduling", tags=["scheduling"])

# Global service instances (will be initialized on first use)
_scheduling_service = None
_escalation_manager = None


async def get_scheduling_service():
    """
    Get or initialize SchedulingService.
    """
    global _scheduling_service

    from ..services.scheduling_service import SchedulingService

    if _scheduling_service is None:
        db = await get_supabase()
        _scheduling_service = SchedulingService(db)

    return _scheduling_service


async def get_escalation_manager():
    """
    Get or initialize EscalationManager.

    Note: This is a placeholder dependency. Task #17 will implement the actual
    EscalationManager class. For now, this returns None to allow the routes
    to be registered.
    """
    global _escalation_manager

    from ..services.scheduling.escalation_manager import EscalationManager

    if _escalation_manager is None:
        db = await get_supabase()
        _escalation_manager = EscalationManager(db)

    return _escalation_manager


# ============================================================================
# Slot Suggestion Endpoints
# ============================================================================

@router.post("/suggest-slots", response_model=SuggestedSlots)
async def suggest_slots(
    clinic_id: UUID,
    service_id: UUID,
    start_date: datetime,
    end_date: datetime,
    patient_id: Optional[UUID] = None,
    hard_constraints: Optional[HardConstraints] = None,
    service = Depends(get_scheduling_service)
):
    """
    Get suggested appointment slots with AI scoring.

    Returns top 10 slots sorted by score, or raises error with escalation_id
    if no slots are available.

    ## Request Parameters
    - **clinic_id**: Clinic UUID
    - **service_id**: Service type UUID
    - **start_date**: Search start date (ISO 8601)
    - **end_date**: Search end date (ISO 8601)
    - **patient_id**: Optional patient UUID for preference scoring
    - **hard_constraints**: Optional constraints (doctor_id, room_id, time_of_day)

    ## Response
    Returns top 10 slots sorted by score with explanations.

    ## Errors
    - **409 Conflict**: No slots available (returns escalation_id)
    - **500 Internal Server Error**: System error

    ## Example
    ```json
    {
      "clinic_id": "123e4567-e89b-12d3-a456-426614174000",
      "service_id": "123e4567-e89b-12d3-a456-426614174001",
      "start_date": "2025-10-15T00:00:00Z",
      "end_date": "2025-10-18T00:00:00Z",
      "patient_id": "123e4567-e89b-12d3-a456-426614174002",
      "hard_constraints": {
        "doctor_id": "123e4567-e89b-12d3-a456-426614174003",
        "time_of_day": "morning"
      }
    }
    ```
    """
    try:
        date_range = DateRange(start_date=start_date, end_date=end_date)

        slots = await service.suggest_slots(
            clinic_id=clinic_id,
            service_id=service_id,
            date_range=date_range,
            hard_constraints=hard_constraints,
            patient_id=patient_id
        )

        return slots

    except NoSlotsAvailableError as e:
        logger.info(f"No slots available for clinic={clinic_id}, escalation={e.escalation_id}")
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "no_slots_available",
                "escalation_id": str(e.escalation_id),
                "message": "No slots available. Escalation created for manual review."
            }
        )
    except Exception as e:
        logger.error(f"suggest_slots error: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to suggest slots: {str(e)}"
        )


# ============================================================================
# Hold Management Endpoints
# ============================================================================

@router.post("/hold-slot", response_model=HoldResponse)
async def hold_slot(
    slot: Slot,
    client_hold_id: str,
    patient_id: UUID,
    service = Depends(get_scheduling_service)
):
    """
    Hold a slot for 5 minutes (idempotent).

    Use client_hold_id for idempotency - same ID returns same hold without
    creating duplicates.

    ## Request Body
    - **slot**: Slot details to hold
    - **client_hold_id**: Idempotency key (e.g., "user123-slot456-1633024800")
    - **patient_id**: Patient UUID

    ## Response
    Returns hold details including expiration time.

    ## Idempotency
    Multiple calls with the same `client_hold_id` will return the same hold
    without creating duplicates. This is safe for retries.

    ## Example
    ```json
    {
      "slot": {
        "slot_id": "slot-123",
        "doctor_id": "123e4567-e89b-12d3-a456-426614174003",
        "doctor_name": "Dr. Smith",
        "service_id": "123e4567-e89b-12d3-a456-426614174001",
        "start_time": "2025-10-15T10:00:00Z",
        "end_time": "2025-10-15T10:30:00Z",
        "score": 95.5,
        "explanation": []
      },
      "client_hold_id": "patient-456-slot-123-1697371200",
      "patient_id": "123e4567-e89b-12d3-a456-426614174002"
    }
    ```
    """
    try:
        hold = await service.hold_slot(
            slot=slot,
            client_hold_id=client_hold_id,
            patient_id=patient_id
        )

        logger.info(f"Slot held: hold_id={hold.hold_id}, client_hold_id={client_hold_id}")
        return hold

    except Exception as e:
        logger.error(f"hold_slot error: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to hold slot: {str(e)}"
        )


@router.post("/confirm-hold", response_model=AppointmentResponse)
async def confirm_hold(
    hold_id: UUID,
    patient_id: UUID,
    metadata: Optional[dict] = None,
    service = Depends(get_scheduling_service)
):
    """
    Confirm a hold and create appointment.

    Triggers calendar sync automatically.

    ## Request Parameters
    - **hold_id**: Hold UUID to confirm
    - **patient_id**: Patient UUID (must match hold)
    - **metadata**: Optional metadata (notes, preferences, etc.)

    ## Response
    Returns created appointment details.

    ## Errors
    - **410 Gone**: Hold has expired
    - **404 Not Found**: Hold not found
    - **500 Internal Server Error**: System error

    ## Example
    ```json
    {
      "hold_id": "123e4567-e89b-12d3-a456-426614174004",
      "patient_id": "123e4567-e89b-12d3-a456-426614174002",
      "metadata": {
        "notes": "Patient requested morning appointment",
        "language": "es"
      }
    }
    ```
    """
    try:
        appointment = await service.confirm_hold(
            hold_id=hold_id,
            patient_id=patient_id,
            metadata=metadata or {}
        )

        logger.info(f"Hold confirmed: hold_id={hold_id}, appointment_id={appointment.appointment_id}")
        return appointment

    except HoldExpiredError as e:
        logger.warning(f"Hold expired: hold_id={hold_id}")
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="Hold has expired. Please request new slots."
        )
    except HoldNotFoundError as e:
        logger.warning(f"Hold not found: hold_id={hold_id}")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Hold {hold_id} not found"
        )
    except Exception as e:
        logger.error(f"confirm_hold error: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to confirm hold: {str(e)}"
        )


# ============================================================================
# Escalation Management Endpoints
# ============================================================================

@router.get("/escalations", response_model=List[EscalationResponse])
async def get_escalations(
    clinic_id: UUID,
    escalation_status: EscalationStatus = EscalationStatus.OPEN,
    limit: int = 50,
    offset: int = 0,
    manager = Depends(get_escalation_manager)
):
    """
    Get escalation queue for clinic.

    Filter by status: open, assigned, resolved, declined.

    ## Query Parameters
    - **clinic_id**: Clinic UUID
    - **escalation_status**: Filter by status (default: open)
    - **limit**: Max results (default: 50, max: 100)
    - **offset**: Pagination offset (default: 0)

    ## Response
    Returns list of escalations with details.

    ## Example
    ```
    GET /api/scheduling/escalations?clinic_id=123e4567-e89b-12d3-a456-426614174000&escalation_status=open
    ```
    """
    try:
        # TODO: Implement once EscalationManager is ready (Task #17)
        if manager is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="EscalationManager not yet implemented. Waiting for Task #17."
            )

        # Enforce limit cap
        limit = min(limit, 100)

        escalations = await manager.get_escalation_queue(
            clinic_id=clinic_id,
            status=escalation_status.value,
            limit=limit,
            offset=offset
        )

        logger.info(f"Retrieved {len(escalations)} escalations for clinic={clinic_id}, status={escalation_status}")
        return escalations

    except Exception as e:
        logger.error(f"get_escalations error: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve escalations: {str(e)}"
        )


@router.post("/escalations/{escalation_id}/resolve", response_model=AppointmentResponse)
async def resolve_escalation(
    escalation_id: UUID,
    resolution: EscalationResolution,
    manager = Depends(get_escalation_manager)
):
    """
    Resolve an escalation.

    Either pick a suggestion or provide manual slot.

    ## Path Parameters
    - **escalation_id**: Escalation UUID to resolve

    ## Request Body
    - **resolution**: Resolution details (type, selected suggestion, or manual slot)

    ## Response
    Returns created appointment details.

    ## Errors
    - **404 Not Found**: Escalation not found
    - **400 Bad Request**: Invalid resolution data
    - **500 Internal Server Error**: System error

    ## Example
    ```json
    {
      "escalation_id": "123e4567-e89b-12d3-a456-426614174005",
      "resolved_by": "123e4567-e89b-12d3-a456-426614174006",
      "resolution_type": "suggestion_accepted",
      "selected_suggestion_index": 0,
      "notes": "Patient accepted first suggestion"
    }
    ```
    """
    try:
        # TODO: Implement once EscalationManager is ready (Task #17)
        if manager is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="EscalationManager not yet implemented. Waiting for Task #17."
            )

        appointment = await manager.resolve_escalation(
            escalation_id=escalation_id,
            resolution=resolution.dict(),
            resolved_by=resolution.resolved_by
        )

        logger.info(f"Escalation resolved: escalation_id={escalation_id}, appointment_id={appointment.appointment_id}")
        return appointment

    except EscalationNotFoundError as e:
        logger.warning(f"Escalation not found: escalation_id={escalation_id}")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Escalation {escalation_id} not found"
        )
    except ValueError as e:
        logger.warning(f"Invalid escalation resolution: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        logger.error(f"resolve_escalation error: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to resolve escalation: {str(e)}"
        )


# ============================================================================
# Performance Monitoring Endpoints
# ============================================================================

@router.get("/performance/metrics")
async def get_performance_metrics(
    service = Depends(get_scheduling_service)
):
    """
    Get performance metrics for monitoring.

    Returns current performance statistics and cache hit rates to verify
    the system is meeting performance targets:
    - suggest_slots() p50 < 400ms, p95 < 800ms
    - hold_slot() p95 < 150ms
    - confirm_hold() p95 < 200ms
    - Cache hit rate > 90%

    ## Response
    Returns performance report with operation statistics and targets.

    ## Example Response
    ```json
    {
      "performance": {
        "suggest_slots": {
          "count": 150,
          "p50": 320.5,
          "p95": 650.2,
          "p99": 780.1,
          "min": 100.3,
          "max": 850.0,
          "avg": 380.5
        }
      },
      "cache": {
        "settings_cache": {
          "hits": 950,
          "misses": 50,
          "hit_rate": 0.95,
          "meets_target": true
        }
      },
      "targets": {
        "suggest_slots_p50_ms": 400,
        "suggest_slots_p95_ms": 800,
        "hold_slot_p95_ms": 150,
        "confirm_hold_p95_ms": 200,
        "cache_hit_rate": 0.90
      },
      "target_checks": {
        "suggest_slots_p50": true,
        "suggest_slots_p95": true,
        "hold_slot_p95": true,
        "confirm_hold_p95": true
      }
    }
    ```
    """
    from ..services.scheduling.cache_monitor import global_cache_monitor

    # Get performance stats
    perf_report = service.perf_monitor.report()
    cache_report = global_cache_monitor.report()
    target_checks = service.perf_monitor.check_targets()

    return {
        "performance": perf_report,
        "cache": cache_report,
        "targets": {
            "suggest_slots_p50_ms": 400,
            "suggest_slots_p95_ms": 800,
            "hold_slot_p95_ms": 150,
            "confirm_hold_p95_ms": 200,
            "cache_hit_rate": 0.90
        },
        "target_checks": target_checks
    }


@router.post("/performance/reset")
async def reset_performance_metrics(
    service = Depends(get_scheduling_service)
):
    """
    Reset performance metrics.

    Clears all recorded performance data and cache statistics.
    Useful for starting fresh performance measurement periods.

    ## Response
    Returns confirmation of reset.
    """
    from ..services.scheduling.cache_monitor import global_cache_monitor

    service.perf_monitor.reset()

    # Reset all cache monitors
    for monitor in global_cache_monitor.monitors.values():
        monitor.reset()

    return {
        "status": "reset",
        "message": "Performance metrics and cache statistics have been reset"
    }


# ============================================================================
# Health Check
# ============================================================================

@router.get("/health")
async def health_check():
    """
    Health check for scheduling API.

    Returns service status and feature availability.
    """
    return {
        "status": "healthy",
        "service": "Scheduling API",
        "features": [
            "slot_suggestions",
            "hold_management",
            "appointment_confirmation",
            "escalation_management",
            "performance_monitoring"
        ],
        "dependencies": {
            "scheduling_service": _scheduling_service is not None,
            "escalation_manager": _escalation_manager is not None
        }
    }
