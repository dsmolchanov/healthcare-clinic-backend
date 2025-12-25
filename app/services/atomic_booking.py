"""
Atomic booking service using Postgres RPC.

Replaces the fake transaction manager that provided no actual transactional
guarantees. Supabase REST API does NOT support multi-table transactions.

This uses a Postgres stored procedure for true ACID guarantees:
- Slot availability check with row locking (prevents race conditions)
- Appointment creation
- Audit log creation
All in a single atomic transaction.

Usage:
    from app.services.atomic_booking import book_appointment_atomic

    result = await book_appointment_atomic(
        patient_id="...",
        doctor_id="...",
        service_id="...",
        slot_start=datetime.now(),
        slot_end=datetime.now() + timedelta(minutes=30),
        created_by="system",
    )

    if result["success"]:
        appointment_id = result["appointment_id"]
    else:
        error = result["error"]
"""
from datetime import datetime
from typing import TypedDict
import logging

from app.database import get_healthcare_client

logger = logging.getLogger(__name__)


class BookingResult(TypedDict):
    success: bool
    appointment_id: str | None
    error: str | None


async def book_appointment_atomic(
    patient_id: str,
    doctor_id: str,
    service_id: str,
    slot_start: datetime,
    slot_end: datetime,
    created_by: str = "system",
) -> BookingResult:
    """
    Book appointment atomically using Postgres RPC.

    This ensures appointment creation + audit logging happen in a single
    transaction with proper row-level locking to prevent double-booking.

    Args:
        patient_id: UUID of the patient
        doctor_id: UUID of the doctor
        service_id: UUID of the service
        slot_start: Start time of the appointment
        slot_end: End time of the appointment
        created_by: Who created this booking (for audit)

    Returns:
        BookingResult with success status and appointment_id or error
    """
    supabase = get_healthcare_client()

    try:
        result = supabase.rpc(
            "book_appointment_atomic",
            {
                "p_patient_id": patient_id,
                "p_doctor_id": doctor_id,
                "p_service_id": service_id,
                "p_slot_start": slot_start.isoformat(),
                "p_slot_end": slot_end.isoformat(),
                "p_created_by": created_by,
            }
        ).execute()

        if result.data:
            return result.data

        # RPC returned nothing - shouldn't happen
        logger.error("book_appointment_atomic RPC returned no data")
        return {
            "success": False,
            "appointment_id": None,
            "error": "Unexpected empty response from booking RPC",
        }

    except Exception as e:
        logger.exception(f"Error in atomic booking: {e}")
        return {
            "success": False,
            "appointment_id": None,
            "error": str(e),
        }


async def cancel_appointment_atomic(
    appointment_id: str,
    cancelled_by: str,
    reason: str = "",
) -> dict:
    """
    Cancel appointment atomically with audit logging.

    TODO: Create corresponding Postgres RPC for this.
    """
    # Placeholder - would use similar RPC pattern
    supabase = get_healthcare_client()

    try:
        # Update appointment status
        result = supabase.table("appointments").update({
            "status": "cancelled",
            "cancellation_reason": reason,
            "cancelled_by": cancelled_by,
            "cancelled_at": datetime.utcnow().isoformat(),
        }).eq("id", appointment_id).execute()

        return {"success": True, "data": result.data}

    except Exception as e:
        logger.exception(f"Error cancelling appointment: {e}")
        return {"success": False, "error": str(e)}
