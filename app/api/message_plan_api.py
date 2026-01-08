"""
Message Plan API Endpoints (Phase 6)

Admin endpoints for viewing and managing appointment message plans.
"""
from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query

from app.db.supabase_client import get_supabase_client

router = APIRouter(prefix="/api/message-plan", tags=["Message Plan"])


@router.get("/appointments/{appointment_id}")
async def get_appointment_message_plan(appointment_id: str):
    """
    Get the message plan for an appointment.

    Returns all scheduled, processing, sent, and cancelled messages.
    """
    try:
        supabase = get_supabase_client()
        result = supabase.schema('healthcare').table('appointment_message_plan').select(
            '*'
        ).eq('appointment_id', appointment_id).order('scheduled_at').execute()

        return {"appointment_id": appointment_id, "messages": result.data or []}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/appointments/{appointment_id}/{message_id}/cancel")
async def cancel_scheduled_message(appointment_id: str, message_id: str):
    """
    Cancel a scheduled message.

    Only messages with status='scheduled' can be cancelled.
    """
    try:
        supabase = get_supabase_client()
        result = supabase.schema('healthcare').table('appointment_message_plan').update({
            'status': 'cancelled',
            'updated_at': datetime.now(timezone.utc).isoformat()
        }).eq('id', message_id).eq('appointment_id', appointment_id).eq(
            'status', 'scheduled'
        ).execute()

        if result.data:
            return {"success": True, "message_id": message_id, "status": "cancelled"}
        else:
            return {"success": False, "error": "Message not found or already processed"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/pending")
async def get_pending_messages(
    limit: int = Query(default=50, le=200),
    clinic_id: Optional[str] = None
):
    """
    Get pending (scheduled) messages across all appointments.

    Useful for monitoring what's queued to send.
    """
    try:
        supabase = get_supabase_client()
        query = supabase.schema('healthcare').table('appointment_message_plan').select(
            '*, appointments(patient_name, scheduled_at, clinic_id)'
        ).eq('status', 'scheduled').order('scheduled_at').limit(limit)

        # Filter by clinic if provided
        if clinic_id:
            query = query.eq('appointments.clinic_id', clinic_id)

        result = query.execute()

        return {"pending_count": len(result.data or []), "messages": result.data or []}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/stats")
async def get_message_plan_stats(
    hours: int = Query(default=24, le=168)  # Up to 1 week
):
    """
    Get message plan statistics for the last N hours.

    Returns counts by status and message type.
    """
    try:
        supabase = get_supabase_client()
        now = datetime.now(timezone.utc)
        since = (now - timezone.utc.utcoffset(now) if timezone.utc.utcoffset(now) else now)

        # Get all messages in time range
        result = supabase.schema('healthcare').table('appointment_message_plan').select(
            'status, message_type'
        ).gte('created_at', since.isoformat()).execute()

        # Aggregate stats
        stats = {
            'by_status': {},
            'by_type': {},
            'total': len(result.data or [])
        }

        for msg in (result.data or []):
            status = msg.get('status', 'unknown')
            msg_type = msg.get('message_type', 'unknown')

            stats['by_status'][status] = stats['by_status'].get(status, 0) + 1
            stats['by_type'][msg_type] = stats['by_type'].get(msg_type, 0) + 1

        return stats
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
