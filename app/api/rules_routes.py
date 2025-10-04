"""
Rule Engine API Routes
Endpoints for managing and evaluating scheduling rules
"""

from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel, Field
from typing import Dict, List, Optional, Any
from datetime import datetime, date
import logging
import redis.asyncio as redis

from ..database import get_supabase
from ..services.policy_compiler import PolicyCompiler, PolicyStatus
from ..services.policy_cache import PolicyCache
from ..services.rule_evaluator import RuleEvaluator, EvaluationContext, TimeSlot
from ..services.pattern_evaluator import PatternEvaluator

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/rules", tags=["rules"])

# Initialize services (in production these would be dependency injected)
_policy_cache = None
_rule_evaluator = None
_pattern_evaluator = None
_policy_compiler = None


async def get_services():
    """Get or initialize services"""
    global _policy_cache, _rule_evaluator, _pattern_evaluator, _policy_compiler
    
    if not _policy_cache:
        supabase = await get_supabase()
        
        # Try to connect to Redis if available
        try:
            redis_client = redis.from_url("redis://localhost:6379", decode_responses=True)
            await redis_client.ping()
        except:
            redis_client = None
            logger.warning("Redis not available, using memory-only cache")
        
        _policy_cache = PolicyCache(supabase, redis_client)
        _policy_compiler = PolicyCompiler(supabase)
        _rule_evaluator = RuleEvaluator(supabase, _policy_cache)
        _pattern_evaluator = PatternEvaluator(supabase, _rule_evaluator)
    
    return {
        "policy_cache": _policy_cache,
        "policy_compiler": _policy_compiler,
        "rule_evaluator": _rule_evaluator,
        "pattern_evaluator": _pattern_evaluator
    }


# Request/Response Models
class CompilePolicyRequest(BaseModel):
    status: PolicyStatus = PolicyStatus.DRAFT
    compiled_by: Optional[str] = None


class DryRunRequest(BaseModel):
    clinic_id: str
    rules: List[Dict]
    slot: Dict
    context: Optional[Dict] = {}


class EvaluateSlotRequest(BaseModel):
    clinic_id: str
    patient_id: str
    requested_service: str
    slot: Dict
    preferences: Optional[Dict] = {}


class FindPatternSlotsRequest(BaseModel):
    pattern_id: str
    clinic_id: str
    patient_id: str
    requested_service: str
    start_date: date
    end_date: date
    max_results: int = 10


class ReservePatternRequest(BaseModel):
    pattern_set: Dict
    patient_id: str
    hold_duration_seconds: int = 300
    client_hold_id: Optional[str] = None


class ConfirmReservationRequest(BaseModel):
    reservation_id: str


class CancelReservationRequest(BaseModel):
    reservation_id: str
    reason: str = "user_cancelled"


# Endpoints

@router.get("/policy-snapshot/{clinic_id}")
async def get_policy_snapshot(
    clinic_id: str,
    version: Optional[int] = Query(None, description="Specific version to retrieve"),
    check_freshness: bool = Query(True, description="Check data freshness")
):
    """
    Get compiled policy snapshot for a clinic
    """
    services = await get_services()
    policy_cache = services["policy_cache"]
    
    try:
        policy = await policy_cache.get(clinic_id, version, check_freshness)
        
        if not policy:
            raise HTTPException(status_code=404, detail="Policy not found")
        
        return {
            "success": True,
            "policy": policy,
            "cache_stats": policy_cache.get_stats()
        }
        
    except Exception as e:
        logger.error(f"Error getting policy snapshot: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/compile/{clinic_id}")
async def compile_policy(
    clinic_id: str,
    request: CompilePolicyRequest
):
    """
    Compile rules into optimized policy snapshot
    """
    services = await get_services()
    policy_compiler = services["policy_compiler"]
    policy_cache = services["policy_cache"]
    
    try:
        # Compile policy
        result = await policy_compiler.compile_policy(
            clinic_id,
            request.status,
            request.compiled_by
        )
        
        # Cache the new policy
        await policy_cache.set(clinic_id, result)
        
        return {
            "success": True,
            "policy": result,
            "message": f"Policy compiled successfully (version {result['version']})"
        }
        
    except Exception as e:
        logger.error(f"Error compiling policy: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/activate/{clinic_id}/{version}")
async def activate_policy(
    clinic_id: str,
    version: int
):
    """
    Activate a specific policy version
    """
    services = await get_services()
    policy_compiler = services["policy_compiler"]
    policy_cache = services["policy_cache"]
    
    try:
        success = await policy_compiler.activate_policy(clinic_id, version)
        
        if not success:
            raise HTTPException(status_code=404, detail="Policy version not found")
        
        # Invalidate cache to force reload
        await policy_cache.invalidate(clinic_id)
        
        return {
            "success": True,
            "message": f"Policy version {version} activated for clinic {clinic_id}"
        }
        
    except Exception as e:
        logger.error(f"Error activating policy: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/dry-run")
async def dry_run_rules(request: DryRunRequest):
    """
    Test rules against sample data without persisting
    """
    services = await get_services()
    rule_evaluator = services["rule_evaluator"]
    
    try:
        # Create evaluation context
        context = EvaluationContext(
            clinic_id=request.clinic_id,
            patient_id=request.context.get("patient_id", "test_patient"),
            requested_service=request.context.get("service", "test_service"),
            preferences=request.context.get("preferences", {})
        )
        
        # Create time slot
        slot = TimeSlot(
            id=request.slot.get("id", "test_slot"),
            doctor_id=request.slot["doctor_id"],
            room_id=request.slot.get("room_id", "test_room"),
            service_id=request.slot.get("service_id", "test_service"),
            start_time=datetime.fromisoformat(request.slot["start_time"]),
            end_time=datetime.fromisoformat(request.slot["end_time"])
        )
        
        # Evaluate
        result = await rule_evaluator.evaluate_slot(context, slot)
        
        return {
            "success": True,
            "result": result.to_dict(),
            "message": "Dry run completed successfully"
        }
        
    except Exception as e:
        logger.error(f"Error in dry run: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/evaluate-slot")
async def evaluate_slot(request: EvaluateSlotRequest):
    """
    Evaluate a single slot against active rules
    """
    services = await get_services()
    rule_evaluator = services["rule_evaluator"]
    
    try:
        context = EvaluationContext(
            clinic_id=request.clinic_id,
            patient_id=request.patient_id,
            requested_service=request.requested_service,
            preferences=request.preferences
        )
        
        slot = TimeSlot(
            id=request.slot["id"],
            doctor_id=request.slot["doctor_id"],
            room_id=request.slot["room_id"],
            service_id=request.slot["service_id"],
            start_time=datetime.fromisoformat(request.slot["start_time"]),
            end_time=datetime.fromisoformat(request.slot["end_time"])
        )
        
        result = await rule_evaluator.evaluate_slot(context, slot)
        
        return {
            "success": True,
            "evaluation": result.to_dict()
        }
        
    except Exception as e:
        logger.error(f"Error evaluating slot: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/patterns/find-slots")
async def find_pattern_slots(request: FindPatternSlotsRequest):
    """
    Find available slots for a multi-visit pattern
    """
    services = await get_services()
    pattern_evaluator = services["pattern_evaluator"]
    
    try:
        context = EvaluationContext(
            clinic_id=request.clinic_id,
            patient_id=request.patient_id,
            requested_service=request.requested_service
        )
        
        pattern_sets = await pattern_evaluator.find_pattern_slots(
            request.pattern_id,
            context,
            datetime.combine(request.start_date, datetime.min.time()),
            datetime.combine(request.end_date, datetime.max.time()),
            request.max_results
        )
        
        return {
            "success": True,
            "pattern_sets": [ps.to_dict() for ps in pattern_sets],
            "count": len(pattern_sets)
        }
        
    except Exception as e:
        logger.error(f"Error finding pattern slots: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/patterns/reserve")
async def reserve_pattern(request: ReservePatternRequest):
    """
    Reserve all slots in a pattern atomically
    """
    services = await get_services()
    pattern_evaluator = services["pattern_evaluator"]
    
    try:
        # Convert dict back to PatternSlotSet
        from ..services.pattern_evaluator import PatternSlotSet, PatternSlot
        
        pattern_slots = []
        for slot_data in request.pattern_set["slots"]:
            slot = TimeSlot(
                id=slot_data["slot"]["id"],
                doctor_id=slot_data["slot"]["doctor_id"],
                room_id=slot_data["slot"]["room_id"],
                service_id=slot_data["slot"]["service_id"],
                start_time=datetime.fromisoformat(slot_data["slot"]["start_time"]),
                end_time=datetime.fromisoformat(slot_data["slot"]["end_time"])
            )
            pattern_slots.append(PatternSlot(
                visit_number=slot_data["visit_number"],
                visit_name=slot_data["visit_name"],
                slot=slot,
                offset_days=slot_data["offset_days"]
            ))
        
        pattern_set = PatternSlotSet(
            pattern_id=request.pattern_set["pattern_id"],
            pattern_name=request.pattern_set["pattern_name"],
            slots=pattern_slots,
            total_score=request.pattern_set.get("total_score", 0),
            constraints_met=request.pattern_set.get("constraints_met", True)
        )
        
        reservation = await pattern_evaluator.reserve_pattern_set(
            pattern_set,
            request.patient_id,
            request.hold_duration_seconds,
            request.client_hold_id
        )
        
        return {
            "success": True,
            "reservation": reservation.to_dict()
        }
        
    except Exception as e:
        logger.error(f"Error reserving pattern: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/patterns/confirm")
async def confirm_reservation(request: ConfirmReservationRequest):
    """
    Confirm a pattern reservation
    """
    services = await get_services()
    pattern_evaluator = services["pattern_evaluator"]
    
    try:
        success = await pattern_evaluator.confirm_pattern_reservation(
            request.reservation_id
        )
        
        if not success:
            raise HTTPException(status_code=400, detail="Failed to confirm reservation")
        
        return {
            "success": True,
            "message": f"Reservation {request.reservation_id} confirmed"
        }
        
    except Exception as e:
        logger.error(f"Error confirming reservation: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/patterns/cancel")
async def cancel_reservation(request: CancelReservationRequest):
    """
    Cancel a pattern reservation
    """
    services = await get_services()
    pattern_evaluator = services["pattern_evaluator"]
    
    try:
        success = await pattern_evaluator.cancel_pattern_reservation(
            request.reservation_id,
            request.reason
        )
        
        if not success:
            raise HTTPException(status_code=400, detail="Failed to cancel reservation")
        
        return {
            "success": True,
            "message": f"Reservation {request.reservation_id} cancelled"
        }
        
    except Exception as e:
        logger.error(f"Error cancelling reservation: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/stats")
async def get_statistics(
    clinic_id: Optional[str] = Query(None, description="Filter by clinic")
):
    """
    Get rule evaluation statistics
    """
    services = await get_services()
    policy_cache = services["policy_cache"]
    rule_evaluator = services["rule_evaluator"]
    
    try:
        stats = {
            "cache_stats": policy_cache.get_stats(),
            "evaluation_stats": rule_evaluator.get_stats()
        }
        
        if clinic_id:
            # Get clinic-specific stats from database
            supabase = await get_supabase()
            
            response = supabase.from_("rule_statistics")\
                .select("*")\
                .eq("clinic_id", clinic_id)\
                .order("created_at", desc=True)\
                .limit(1)\
                .execute()
            
            if response.data:
                stats["clinic_stats"] = response.data[0]
        
        return {
            "success": True,
            "statistics": stats
        }
        
    except Exception as e:
        logger.error(f"Error getting statistics: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/cache/warm")
async def warm_cache(clinic_ids: List[str]):
    """
    Pre-populate cache for specified clinics
    """
    services = await get_services()
    policy_cache = services["policy_cache"]
    
    try:
        await policy_cache.warm_cache(clinic_ids)
        
        return {
            "success": True,
            "message": f"Cache warmed for {len(clinic_ids)} clinics"
        }
        
    except Exception as e:
        logger.error(f"Error warming cache: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/cache/invalidate/{clinic_id}")
async def invalidate_cache(
    clinic_id: str,
    version: Optional[int] = Query(None, description="Specific version to invalidate")
):
    """
    Invalidate cache for a clinic
    """
    services = await get_services()
    policy_cache = services["policy_cache"]
    
    try:
        await policy_cache.invalidate(clinic_id, version)
        
        return {
            "success": True,
            "message": f"Cache invalidated for clinic {clinic_id}"
        }
        
    except Exception as e:
        logger.error(f"Error invalidating cache: {e}")
        raise HTTPException(status_code=500, detail=str(e))