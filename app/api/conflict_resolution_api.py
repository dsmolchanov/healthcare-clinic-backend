"""
Conflict Resolution API Endpoints
Provides REST API for the human-in-the-loop dashboard
"""

from fastapi import APIRouter, Depends, HTTPException, Query, BackgroundTasks
from fastapi.responses import JSONResponse
from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta
from pydantic import BaseModel, Field
import logging

from ..services.conflict_resolution_enhanced import (
    EnhancedConflictResolver,
    ResolutionStatus,
    HumanInterventionReason
)
from ..services.realtime_conflict_detector import (
    RealtimeConflictDetector,
    ConflictEvent,
    ConflictType,
    ConflictSeverity
)
from ..services.websocket_manager import websocket_manager
from ..auth.dependencies import get_current_user
from ..database import get_db_connection

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/conflicts", tags=["conflict-resolution"])

# Initialize services (these would typically be dependency injected)
conflict_detector = None
conflict_resolver = None


def get_conflict_detector():
    """Get or create conflict detector instance"""
    global conflict_detector
    if not conflict_detector:
        conflict_detector = RealtimeConflictDetector()
    return conflict_detector


def get_conflict_resolver():
    """Get or create conflict resolver instance"""
    global conflict_resolver
    if not conflict_resolver:
        import redis.asyncio as redis
        from supabase import create_client
        import os

        redis_client = redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379"))
        supabase = create_client(
            os.getenv("SUPABASE_URL"),
            os.getenv("SUPABASE_SERVICE_KEY")
        )
        conflict_resolver = EnhancedConflictResolver(redis_client, supabase, websocket_manager)
    return conflict_resolver


# Request/Response Models
class ResolveConflictRequest(BaseModel):
    """Request to resolve a conflict"""
    action: str = Field(..., description="Resolution action to take")
    parameters: Dict[str, Any] = Field(default_factory=dict, description="Action parameters")
    notes: Optional[str] = Field(None, description="Resolution notes")


class ConflictInterventionResponse(BaseModel):
    """Response for conflict intervention"""
    resolution_id: str
    conflict_id: str
    conflict_type: str
    severity: str
    intervention_reason: str
    suggestions: List[Dict[str, Any]]
    created_at: str
    urgency_score: float
    assigned_to: Optional[str] = None


class MetricsResponse(BaseModel):
    """Response for conflict metrics"""
    total_conflicts: int
    auto_resolved: int
    human_resolved: int
    escalated: int
    failed: int
    avg_resolution_time: float
    automation_rate: float
    pending_interventions: int
    by_type: Dict[str, int]
    by_severity: Dict[str, int]


class ConflictHistoryResponse(BaseModel):
    """Response for conflict history"""
    conflicts: List[Dict[str, Any]]
    total: int
    page: int
    page_size: int


# API Endpoints

@router.get("/health")
async def health_check():
    """Check conflict resolution service health"""
    return {
        "status": "healthy",
        "service": "conflict-resolution",
        "timestamp": datetime.utcnow().isoformat()
    }


@router.get("/interventions", response_model=List[ConflictInterventionResponse])
async def get_pending_interventions(
    current_user: dict = Depends(get_current_user),
    assigned_to_me: bool = Query(False, description="Filter interventions assigned to current user")
):
    """
    Get list of conflicts requiring human intervention

    Args:
        current_user: Authenticated user
        assigned_to_me: Filter for interventions assigned to current user

    Returns:
        List of pending interventions
    """
    try:
        resolver = get_conflict_resolver()
        user_id = current_user["id"] if assigned_to_me else None

        interventions = await resolver.get_pending_interventions(user_id)

        return JSONResponse(
            content={"interventions": interventions},
            status_code=200
        )
    except Exception as e:
        logger.error(f"Error fetching interventions: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch interventions")


@router.post("/resolve/{resolution_id}")
async def resolve_conflict(
    resolution_id: str,
    request: ResolveConflictRequest,
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(get_current_user)
):
    """
    Resolve a conflict through human intervention

    Args:
        resolution_id: ID of the resolution to handle
        request: Resolution action and parameters
        current_user: User performing the resolution

    Returns:
        Resolution result
    """
    try:
        resolver = get_conflict_resolver()

        success = await resolver.handle_human_resolution(
            resolution_id=resolution_id,
            user_id=current_user["id"],
            action=request.action,
            parameters=request.parameters,
            notes=request.notes
        )

        if not success:
            raise HTTPException(status_code=400, detail="Resolution failed")

        # Log audit trail
        background_tasks.add_task(
            log_resolution_audit,
            resolution_id,
            current_user["id"],
            request.action
        )

        return {
            "success": True,
            "resolution_id": resolution_id,
            "action": request.action,
            "resolved_by": current_user["id"],
            "timestamp": datetime.utcnow().isoformat()
        }

    except Exception as e:
        logger.error(f"Error resolving conflict: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to resolve conflict: {str(e)}")


@router.get("/metrics", response_model=MetricsResponse)
async def get_conflict_metrics(
    time_range: int = Query(7, description="Time range in days"),
    current_user: dict = Depends(get_current_user)
):
    """
    Get conflict resolution metrics

    Args:
        time_range: Number of days to include in metrics
        current_user: Authenticated user

    Returns:
        Conflict resolution metrics
    """
    try:
        resolver = get_conflict_resolver()
        metrics = await resolver.get_resolution_metrics(timedelta(days=time_range))

        return metrics

    except Exception as e:
        logger.error(f"Error fetching metrics: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch metrics")


@router.get("/history", response_model=ConflictHistoryResponse)
async def get_conflict_history(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    conflict_type: Optional[str] = None,
    severity: Optional[str] = None,
    status: Optional[str] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    current_user: dict = Depends(get_current_user)
):
    """
    Get historical conflicts with filtering

    Args:
        page: Page number
        page_size: Items per page
        conflict_type: Filter by conflict type
        severity: Filter by severity
        status: Filter by resolution status
        start_date: Start of date range
        end_date: End of date range
        current_user: Authenticated user

    Returns:
        Paginated conflict history
    """
    try:
        # Get database connection
        db = await get_db_connection()

        # Build query
        query = "SELECT * FROM conflict_resolutions WHERE 1=1"
        params = []

        if conflict_type:
            query += " AND conflict_type = %s"
            params.append(conflict_type)

        if severity:
            query += " AND severity = %s"
            params.append(severity)

        if status:
            query += " AND status = %s"
            params.append(status)

        if start_date:
            query += " AND created_at >= %s"
            params.append(start_date)

        if end_date:
            query += " AND created_at <= %s"
            params.append(end_date)

        # Add pagination
        offset = (page - 1) * page_size
        query += " ORDER BY created_at DESC LIMIT %s OFFSET %s"
        params.extend([page_size, offset])

        # Execute query
        conflicts = await db.fetch(query, *params)

        # Get total count
        count_query = "SELECT COUNT(*) as total FROM conflict_resolutions WHERE 1=1"
        if conflict_type or severity or status or start_date or end_date:
            # Add same filters for count
            count_params = params[:-2]  # Exclude limit and offset
            total_result = await db.fetchrow(count_query, *count_params)
        else:
            total_result = await db.fetchrow(count_query)

        total = total_result["total"]

        return {
            "conflicts": [dict(c) for c in conflicts],
            "total": total,
            "page": page,
            "page_size": page_size
        }

    except Exception as e:
        logger.error(f"Error fetching conflict history: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch conflict history")


@router.post("/simulate")
async def simulate_conflict(
    conflict_type: str = Query(..., description="Type of conflict to simulate"),
    severity: str = Query("medium", description="Conflict severity"),
    current_user: dict = Depends(get_current_user)
):
    """
    Simulate a conflict for testing (admin only)

    Args:
        conflict_type: Type of conflict
        severity: Severity level
        current_user: Authenticated admin user

    Returns:
        Created conflict details
    """
    # Check admin permission
    if not current_user.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admin access required")

    try:
        import uuid

        # Create simulated conflict
        conflict = ConflictEvent(
            conflict_id=f"sim_{uuid.uuid4().hex[:12]}",
            conflict_type=ConflictType(conflict_type),
            severity=ConflictSeverity(severity),
            doctor_id="sim_doctor_001",
            start_time=datetime.utcnow() + timedelta(hours=2),
            end_time=datetime.utcnow() + timedelta(hours=3),
            sources=["internal", "google"],
            details={
                "simulated": True,
                "created_by": current_user["id"],
                "patient_id": "sim_patient_001"
            },
            detected_at=datetime.utcnow()
        )

        # Process through conflict resolver
        resolver = get_conflict_resolver()
        resolution = await resolver.create_resolution(conflict)

        return {
            "success": True,
            "conflict_id": conflict.conflict_id,
            "resolution_id": resolution.resolution_id,
            "requires_human": resolution.requires_human,
            "message": "Simulated conflict created successfully"
        }

    except Exception as e:
        logger.error(f"Error simulating conflict: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to simulate conflict: {str(e)}")


@router.get("/suggestions/{conflict_id}")
async def get_resolution_suggestions(
    conflict_id: str,
    current_user: dict = Depends(get_current_user)
):
    """
    Get AI-generated resolution suggestions for a specific conflict

    Args:
        conflict_id: ID of the conflict
        current_user: Authenticated user

    Returns:
        List of resolution suggestions
    """
    try:
        # This would typically fetch the conflict and generate suggestions
        # For now, returning mock suggestions
        suggestions = [
            {
                "strategy": "reschedule_patient",
                "description": "Reschedule patient to next available slot",
                "confidence": 0.85,
                "impact": "Patient will be notified of new time",
                "automatic": True
            },
            {
                "strategy": "merge_appointments",
                "description": "Merge both appointments if they're for the same patient",
                "confidence": 0.65,
                "impact": "Requires verification of patient identity",
                "automatic": False
            },
            {
                "strategy": "contact_patient",
                "description": "Contact patient to confirm preference",
                "confidence": 0.95,
                "impact": "Manual intervention required",
                "automatic": False
            }
        ]

        return {"conflict_id": conflict_id, "suggestions": suggestions}

    except Exception as e:
        logger.error(f"Error getting suggestions: {e}")
        raise HTTPException(status_code=500, detail="Failed to get suggestions")


@router.post("/assign/{resolution_id}")
async def assign_resolution(
    resolution_id: str,
    assignee_id: str = Query(..., description="User ID to assign to"),
    current_user: dict = Depends(get_current_user)
):
    """
    Assign a resolution to a specific user

    Args:
        resolution_id: Resolution to assign
        assignee_id: User to assign to
        current_user: User making the assignment (must be admin)

    Returns:
        Assignment confirmation
    """
    if not current_user.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admin access required")

    try:
        # Update assignment in database
        db = await get_db_connection()
        await db.execute(
            "UPDATE conflict_resolutions SET assigned_to = %s, updated_at = %s WHERE id = %s",
            assignee_id,
            datetime.utcnow(),
            resolution_id
        )

        # Notify assignee via WebSocket
        await websocket_manager.send_to_user(
            assignee_id,
            {
                "type": "resolution_assigned",
                "resolution_id": resolution_id,
                "assigned_by": current_user["id"],
                "timestamp": datetime.utcnow().isoformat()
            }
        )

        return {
            "success": True,
            "resolution_id": resolution_id,
            "assigned_to": assignee_id,
            "assigned_by": current_user["id"]
        }

    except Exception as e:
        logger.error(f"Error assigning resolution: {e}")
        raise HTTPException(status_code=500, detail="Failed to assign resolution")


@router.get("/export")
async def export_conflicts(
    format: str = Query("csv", description="Export format (csv, json)"),
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    current_user: dict = Depends(get_current_user)
):
    """
    Export conflict data for reporting

    Args:
        format: Export format
        start_date: Start of date range
        end_date: End of date range
        current_user: Authenticated user

    Returns:
        Exported data
    """
    if not current_user.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admin access required")

    try:
        # Fetch data
        db = await get_db_connection()
        query = "SELECT * FROM conflict_resolutions WHERE 1=1"
        params = []

        if start_date:
            query += " AND created_at >= %s"
            params.append(start_date)

        if end_date:
            query += " AND created_at <= %s"
            params.append(end_date)

        conflicts = await db.fetch(query, *params)

        if format == "csv":
            import csv
            import io

            output = io.StringIO()
            if conflicts:
                writer = csv.DictWriter(output, fieldnames=conflicts[0].keys())
                writer.writeheader()
                for conflict in conflicts:
                    writer.writerow(dict(conflict))

            return JSONResponse(
                content=output.getvalue(),
                media_type="text/csv",
                headers={
                    "Content-Disposition": f"attachment; filename=conflicts_{datetime.utcnow().date()}.csv"
                }
            )
        else:
            return {"conflicts": [dict(c) for c in conflicts]}

    except Exception as e:
        logger.error(f"Error exporting conflicts: {e}")
        raise HTTPException(status_code=500, detail="Failed to export conflicts")


async def log_resolution_audit(resolution_id: str, user_id: str, action: str):
    """Log audit trail for resolution actions"""
    try:
        db = await get_db_connection()
        await db.execute(
            """
            INSERT INTO audit_log (
                event_type, entity_type, entity_id, user_id, action, timestamp
            ) VALUES (%s, %s, %s, %s, %s, %s)
            """,
            "conflict_resolution",
            "resolution",
            resolution_id,
            user_id,
            action,
            datetime.utcnow()
        )
    except Exception as e:
        logger.error(f"Error logging audit: {e}")