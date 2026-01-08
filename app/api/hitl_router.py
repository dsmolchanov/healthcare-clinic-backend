"""
HITL (Human-in-the-Loop) Control Mode API

Endpoints for managing session control modes between agent and human operators.

Phase 6 of the HITL Control Mode System implementation.
"""

from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime, timezone
import logging

from app.db.supabase_client import get_supabase_client

logger = logging.getLogger(__name__)


# Auth dependency - optional, returns empty dict if auth module not available
def get_current_user():
    """
    Get current user dependency.

    Falls back to empty dict if auth module not available.
    TODO: Implement proper auth when app.auth.dependencies is created.
    """
    return {}

router = APIRouter(prefix="/hitl", tags=["hitl"])


# ============================================================================
# Request/Response Models
# ============================================================================

class UnlockSessionRequest(BaseModel):
    """Request body for unlocking a session."""
    reason: Optional[str] = Field(
        None,
        description="Optional reason for releasing the session back to agent"
    )


class SessionControlStatus(BaseModel):
    """Response model for session control status."""
    session_id: str
    control_mode: str
    locked_by: Optional[str] = None
    locked_at: Optional[str] = None
    lock_reason: Optional[str] = None
    lock_source: Optional[str] = None
    unread_for_human_count: int = 0
    last_human_message_at: Optional[str] = None


class UnlockSessionResponse(BaseModel):
    """Response model for unlock operation."""
    success: bool
    session_id: str
    previous_control_mode: str
    new_control_mode: str
    message: str


class HumanControlledSessionsResponse(BaseModel):
    """Response model for listing human-controlled sessions."""
    sessions: List[SessionControlStatus]
    total: int


# ============================================================================
# API Endpoints
# ============================================================================

@router.post("/unlock/{session_id}", response_model=UnlockSessionResponse)
async def unlock_session(
    session_id: str,
    request: Optional[UnlockSessionRequest] = None,
    current_user: dict = Depends(get_current_user)
):
    """
    Release a session back to agent control.

    This endpoint is called when a human operator finishes handling a session
    and wants to return control to the AI agent.

    The session's control_mode is set back to 'agent', and the lock fields
    are cleared.

    Args:
        session_id: UUID of the session to unlock
        request: Optional request body with unlock reason

    Returns:
        UnlockSessionResponse with operation result

    Raises:
        HTTPException 404: If session not found
        HTTPException 400: If session is not currently under human control
    """
    try:
        supabase = get_supabase_client()

        # Get current session state
        result = supabase.schema('healthcare').table('conversation_sessions').select(
            'id, control_mode, locked_by, lock_reason, phone_number'
        ).eq('id', session_id).single().execute()

        if not result.data:
            raise HTTPException(status_code=404, detail=f"Session {session_id} not found")

        session = result.data
        current_mode = session.get('control_mode', 'agent')

        if current_mode == 'agent':
            # Already under agent control
            return UnlockSessionResponse(
                success=True,
                session_id=session_id,
                previous_control_mode='agent',
                new_control_mode='agent',
                message="Session is already under agent control"
            )

        # Update session to return to agent control
        unlock_reason = request.reason if request else "Manually released by operator"
        user_id = current_user.get('id') if current_user else None

        update_data = {
            'control_mode': 'agent',
            'locked_by': None,
            'locked_at': None,
            'lock_reason': None,
            'lock_source': None,
            'unread_for_human_count': 0,  # Reset unread count
            'updated_at': datetime.now(timezone.utc).isoformat()
        }

        supabase.schema('healthcare').table('conversation_sessions').update(
            update_data
        ).eq('id', session_id).execute()

        logger.info(
            f"✅ Session {session_id[:8]}... unlocked by user {user_id}, "
            f"control mode: {current_mode} → agent, "
            f"reason: {unlock_reason}"
        )

        return UnlockSessionResponse(
            success=True,
            session_id=session_id,
            previous_control_mode=current_mode,
            new_control_mode='agent',
            message=f"Session released to agent control. Reason: {unlock_reason}"
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error unlocking session {session_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to unlock session: {str(e)}")


@router.get("/sessions", response_model=HumanControlledSessionsResponse)
async def list_human_controlled_sessions(
    clinic_id: Optional[str] = Query(None, description="Filter by clinic ID"),
    limit: int = Query(50, ge=1, le=200, description="Max results to return"),
    offset: int = Query(0, ge=0, description="Offset for pagination"),
    current_user: dict = Depends(get_current_user)
):
    """
    List sessions currently under human control.

    Returns sessions where control_mode is 'human' or 'paused', ordered by
    most recent lock time.

    Args:
        clinic_id: Optional clinic ID filter
        limit: Maximum number of results (default 50)
        offset: Pagination offset (default 0)

    Returns:
        HumanControlledSessionsResponse with list of sessions
    """
    try:
        supabase = get_supabase_client()

        # Build query
        query = supabase.schema('healthcare').table('conversation_sessions').select(
            'id, control_mode, locked_by, locked_at, lock_reason, lock_source, '
            'unread_for_human_count, last_human_message_at, phone_number, clinic_id'
        ).in_('control_mode', ['human', 'paused'])

        if clinic_id:
            query = query.eq('clinic_id', clinic_id)

        # Order by most recently locked first
        query = query.order('locked_at', desc=True).range(offset, offset + limit - 1)

        result = query.execute()

        sessions = []
        for row in result.data or []:
            sessions.append(SessionControlStatus(
                session_id=row['id'],
                control_mode=row['control_mode'],
                locked_by=row.get('locked_by'),
                locked_at=row.get('locked_at'),
                lock_reason=row.get('lock_reason'),
                lock_source=row.get('lock_source'),
                unread_for_human_count=row.get('unread_for_human_count', 0),
                last_human_message_at=row.get('last_human_message_at')
            ))

        # Get total count
        count_query = supabase.schema('healthcare').table('conversation_sessions').select(
            'id', count='exact'
        ).in_('control_mode', ['human', 'paused'])

        if clinic_id:
            count_query = count_query.eq('clinic_id', clinic_id)

        count_result = count_query.execute()
        total = count_result.count if hasattr(count_result, 'count') else len(sessions)

        return HumanControlledSessionsResponse(
            sessions=sessions,
            total=total
        )

    except Exception as e:
        logger.error(f"Error listing human-controlled sessions: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to list sessions: {str(e)}"
        )


@router.get("/session/{session_id}/status", response_model=SessionControlStatus)
async def get_session_control_status(
    session_id: str,
    current_user: dict = Depends(get_current_user)
):
    """
    Get the control status of a specific session.

    Args:
        session_id: UUID of the session

    Returns:
        SessionControlStatus with current control mode and lock info
    """
    try:
        supabase = get_supabase_client()

        result = supabase.schema('healthcare').table('conversation_sessions').select(
            'id, control_mode, locked_by, locked_at, lock_reason, lock_source, '
            'unread_for_human_count, last_human_message_at'
        ).eq('id', session_id).single().execute()

        if not result.data:
            raise HTTPException(status_code=404, detail=f"Session {session_id} not found")

        row = result.data
        return SessionControlStatus(
            session_id=row['id'],
            control_mode=row.get('control_mode', 'agent'),
            locked_by=row.get('locked_by'),
            locked_at=row.get('locked_at'),
            lock_reason=row.get('lock_reason'),
            lock_source=row.get('lock_source'),
            unread_for_human_count=row.get('unread_for_human_count', 0),
            last_human_message_at=row.get('last_human_message_at')
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting session status: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get session status: {str(e)}"
        )


@router.post("/lock/{session_id}")
async def lock_session_for_human(
    session_id: str,
    reason: Optional[str] = Query(None, description="Reason for locking"),
    current_user: dict = Depends(get_current_user)
):
    """
    Manually lock a session for human control.

    This endpoint allows an operator to take control of a session
    that is currently under agent control.

    Args:
        session_id: UUID of the session to lock
        reason: Optional reason for taking control

    Returns:
        Updated session control status
    """
    try:
        supabase = get_supabase_client()
        user_id = current_user.get('id') if current_user else None

        # Get current session state
        result = supabase.schema('healthcare').table('conversation_sessions').select(
            'id, control_mode'
        ).eq('id', session_id).single().execute()

        if not result.data:
            raise HTTPException(status_code=404, detail=f"Session {session_id} not found")

        current_mode = result.data.get('control_mode', 'agent')

        if current_mode == 'human':
            return {
                "success": True,
                "message": "Session is already under human control",
                "session_id": session_id,
                "control_mode": "human"
            }

        # Lock the session
        lock_reason = reason or "Manually locked by operator"
        update_data = {
            'control_mode': 'human',
            'locked_by': user_id,
            'locked_at': datetime.now(timezone.utc).isoformat(),
            'lock_reason': lock_reason,
            'lock_source': 'ui',
            'updated_at': datetime.now(timezone.utc).isoformat()
        }

        supabase.schema('healthcare').table('conversation_sessions').update(
            update_data
        ).eq('id', session_id).execute()

        logger.info(
            f"✅ Session {session_id[:8]}... locked by user {user_id}, "
            f"control mode: {current_mode} → human, "
            f"reason: {lock_reason}"
        )

        return {
            "success": True,
            "message": f"Session locked for human control. Reason: {lock_reason}",
            "session_id": session_id,
            "control_mode": "human",
            "locked_by": user_id
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error locking session {session_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to lock session: {str(e)}")
