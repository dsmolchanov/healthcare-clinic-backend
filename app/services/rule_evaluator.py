"""
Rule Evaluator Service  
Two-phase evaluation pipeline for scheduling rules with explainability
"""

import time
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any, Tuple
from enum import Enum
from uuid import uuid4

from supabase import Client
from .policy_cache import PolicyCache

logger = logging.getLogger(__name__)


class EvaluationResult:
    """Result of rule evaluation with explanations"""
    
    def __init__(
        self,
        slot_id: str,
        is_valid: bool,
        score: float = 0.0,
        violated_rules: List[Dict] = None,
        applied_preferences: List[Dict] = None,
        explanations: List[str] = None,
        execution_time_ms: float = 0.0
    ):
        self.slot_id = slot_id
        self.is_valid = is_valid
        self.score = score
        self.violated_rules = violated_rules or []
        self.applied_preferences = applied_preferences or []
        self.explanations = explanations or []
        self.execution_time_ms = execution_time_ms

    def to_dict(self):
        return {
            "slot_id": self.slot_id,
            "is_valid": self.is_valid,
            "score": self.score,
            "violated_rules": self.violated_rules,
            "applied_preferences": self.applied_preferences,
            "explanations": self.explanations,
            "execution_time_ms": self.execution_time_ms
        }


class TimeSlot:
    """Represents an appointment time slot"""
    
    def __init__(
        self,
        id: str,
        doctor_id: str,
        room_id: str,
        service_id: str,
        start_time: datetime,
        end_time: datetime,
        is_available: bool = True,
        metadata: Dict = None
    ):
        self.id = id
        self.doctor_id = doctor_id
        self.room_id = room_id
        self.service_id = service_id
        self.start_time = start_time
        self.end_time = end_time
        self.is_available = is_available
        self.metadata = metadata or {}

    def to_dict(self):
        return {
            "id": self.id,
            "doctor_id": self.doctor_id,
            "room_id": self.room_id,
            "service_id": self.service_id,
            "start_time": self.start_time.isoformat(),
            "end_time": self.end_time.isoformat(),
            "is_available": self.is_available,
            "metadata": self.metadata
        }


class EvaluationContext:
    """Context for rule evaluation"""
    
    def __init__(
        self,
        clinic_id: str,
        patient_id: str,
        requested_service: str,
        preferences: Dict = None,
        metadata: Dict = None
    ):
        self.clinic_id = clinic_id
        self.patient_id = patient_id
        self.requested_service = requested_service
        self.preferences = preferences or {}
        self.metadata = metadata or {}
        self.session_id = str(uuid4())

    def to_dict(self):
        return {
            "clinic_id": self.clinic_id,
            "patient_id": self.patient_id,
            "requested_service": self.requested_service,
            "preferences": self.preferences,
            "metadata": self.metadata,
            "session_id": self.session_id
        }


class RuleEvaluator:
    """
    Two-phase evaluation pipeline:
    1. Hard constraints (fail-fast)
    2. Soft preferences (scoring)
    """
    
    # Performance budget
    MAX_EVAL_TIME_MS = 50
    
    def __init__(self, supabase: Client, policy_cache: PolicyCache):
        self.supabase = supabase
        self.policy_cache = policy_cache
        self.stats = {
            "total_evaluations": 0,
            "valid_slots": 0,
            "invalid_slots": 0,
            "avg_execution_time_ms": 0.0
        }

    async def evaluate_slot(
        self,
        context: EvaluationContext,
        slot: TimeSlot
    ) -> EvaluationResult:
        """
        Evaluate a single slot against all applicable rules
        """
        start_time = time.perf_counter()
        session_id = context.session_id
        
        logger.debug(f"Evaluating slot {slot.id} for session {session_id}")
        
        # Get compiled policy from cache
        policy = await self.policy_cache.get(context.clinic_id)
        if not policy:
            logger.warning(f"No policy found for clinic {context.clinic_id}")
            return EvaluationResult(
                slot_id=slot.id,
                is_valid=True,
                score=0.0,
                explanations=["No rules configured"]
            )
        
        # Phase 1: Evaluate hard constraints
        constraint_result = await self._evaluate_constraints(
            context, slot, policy.get("constraints", [])
        )
        
        if not constraint_result["is_valid"]:
            # Slot violates constraints, fail fast
            execution_time = (time.perf_counter() - start_time) * 1000
            
            # Log evaluation for audit
            await self._log_evaluation(
                session_id, slot.id, context, constraint_result, execution_time
            )
            
            self.stats["invalid_slots"] += 1
            
            return EvaluationResult(
                slot_id=slot.id,
                is_valid=False,
                violated_rules=constraint_result["violated_rules"],
                explanations=constraint_result["explanations"],
                execution_time_ms=execution_time
            )
        
        # Check if we're within time budget
        elapsed = (time.perf_counter() - start_time) * 1000
        if elapsed > self.MAX_EVAL_TIME_MS:
            logger.warning(f"Evaluation exceeding time budget: {elapsed}ms")
        
        # Phase 2: Evaluate soft preferences
        preference_result = await self._evaluate_preferences(
            context, slot, policy.get("preferences", [])
        )
        
        execution_time = (time.perf_counter() - start_time) * 1000
        
        # Build final result
        result = EvaluationResult(
            slot_id=slot.id,
            is_valid=True,
            score=preference_result["score"],
            applied_preferences=preference_result["applied_preferences"],
            explanations=[
                *constraint_result["explanations"],
                *preference_result["explanations"]
            ],
            execution_time_ms=execution_time
        )
        
        # Log evaluation
        await self._log_evaluation(
            session_id, slot.id, context, result.to_dict(), execution_time
        )
        
        # Update statistics
        self._update_stats(execution_time, True)
        
        return result

    async def evaluate_slots(
        self,
        context: EvaluationContext,
        slots: List[TimeSlot]
    ) -> List[EvaluationResult]:
        """
        Evaluate multiple slots and return sorted by score
        """
        logger.info(f"Evaluating {len(slots)} slots for session {context.session_id}")
        
        results = []
        for slot in slots:
            # Check time budget for batch operations
            if len(results) > 0 and results[-1].execution_time_ms > self.MAX_EVAL_TIME_MS:
                logger.warning("Batch evaluation exceeding time budget, using fast mode")
                # Use simplified evaluation for remaining slots
                result = await self._fast_evaluate(context, slot)
            else:
                result = await self.evaluate_slot(context, slot)
            
            if result.is_valid:
                results.append(result)
        
        # Sort by score (highest first)
        results.sort(key=lambda r: r.score, reverse=True)
        
        return results

    async def _evaluate_constraints(
        self,
        context: EvaluationContext,
        slot: TimeSlot,
        constraints: List[Dict]
    ) -> Dict:
        """Evaluate hard constraints with fail-fast behavior"""
        violated_rules = []
        explanations = []
        
        for constraint in constraints:
            try:
                passed, explanation = await self._evaluate_rule(
                    context, slot, constraint
                )
                
                if not passed:
                    violated_rules.append({
                        "id": constraint.get("id"),
                        "name": constraint.get("name"),
                        "explanation": explanation
                    })
                    explanations.append(f"❌ {explanation}")
                    
                    # Fail fast on first violation
                    return {
                        "is_valid": False,
                        "violated_rules": violated_rules,
                        "explanations": explanations
                    }
                else:
                    explanations.append(f"✅ {constraint.get('name', 'Constraint')} passed")
                    
            except Exception as e:
                logger.error(f"Error evaluating constraint {constraint.get('id')}: {e}")
        
        return {
            "is_valid": True,
            "violated_rules": [],
            "explanations": explanations
        }

    async def _evaluate_preferences(
        self,
        context: EvaluationContext,
        slot: TimeSlot,
        preferences: List[Dict]
    ) -> Dict:
        """Evaluate soft preferences and calculate score"""
        score = 100.0  # Base score
        applied_preferences = []
        explanations = []
        
        for preference in preferences:
            try:
                passed, explanation = await self._evaluate_rule(
                    context, slot, preference
                )
                
                if passed:
                    # Apply positive scoring
                    modifier = preference.get("actions", {}).get("score_modifier", 10)
                    score += modifier
                    
                    applied_preferences.append({
                        "id": preference.get("id"),
                        "name": preference.get("name"),
                        "modifier": modifier
                    })
                    
                    explanations.append(f"➕ {explanation} (+{modifier} points)")
                else:
                    # Apply negative scoring if preference not met
                    penalty = preference.get("actions", {}).get("penalty", 5)
                    score -= penalty
                    
                    explanations.append(f"➖ {explanation} (-{penalty} points)")
                    
            except Exception as e:
                logger.error(f"Error evaluating preference {preference.get('id')}: {e}")
        
        return {
            "score": max(0, score),  # Don't go below 0
            "applied_preferences": applied_preferences,
            "explanations": explanations
        }

    async def _evaluate_rule(
        self,
        context: EvaluationContext,
        slot: TimeSlot,
        rule: Dict
    ) -> Tuple[bool, str]:
        """
        Evaluate a single rule against the slot
        Returns (passed, explanation)
        """
        conditions = rule.get("conditions", {})
        rule_type = conditions.get("type")
        
        # Common rule evaluations
        if rule_type == "doctor_room":
            # Check if doctor is authorized for the room
            allowed_rooms = conditions.get("allowed_rooms", [])
            if slot.room_id not in allowed_rooms:
                return False, f"{rule.get('name')}: Doctor not authorized for room {slot.room_id}"
            return True, f"{rule.get('name')}: Doctor authorized for room"
        
        elif rule_type == "time_range":
            # Check if slot is within allowed time range
            min_hour = conditions.get("min_hour", 0)
            max_hour = conditions.get("max_hour", 24)
            slot_hour = slot.start_time.hour
            
            if slot_hour < min_hour or slot_hour >= max_hour:
                return False, f"{rule.get('name')}: Slot outside allowed hours ({min_hour}-{max_hour})"
            return True, f"{rule.get('name')}: Slot within allowed hours"
        
        elif rule_type == "workload":
            # Check doctor's workload
            max_daily = conditions.get("max_appointments_per_day", 20)
            current_count = await self._get_doctor_appointment_count(
                slot.doctor_id, slot.start_time.date()
            )
            
            if current_count >= max_daily:
                return False, f"{rule.get('name')}: Doctor at capacity ({current_count}/{max_daily})"
            
            # Preference scoring based on workload
            score_per_appointment = conditions.get("score_per_appointment", -5)
            return True, f"{rule.get('name')}: Doctor has {current_count} appointments"
        
        elif rule_type == "equipment":
            # Check equipment availability
            required_equipment = conditions.get("required_equipment", [])
            available_equipment = await self._get_room_equipment(slot.room_id)
            
            for equipment in required_equipment:
                if equipment not in available_equipment:
                    return False, f"{rule.get('name')}: Required equipment '{equipment}' not available"
            return True, f"{rule.get('name')}: All required equipment available"
        
        elif rule_type == "buffer_time":
            # Check for buffer time between appointments
            buffer_minutes = conditions.get("buffer_minutes", 15)
            has_buffer = await self._check_buffer_time(
                slot.doctor_id, slot.start_time, slot.end_time, buffer_minutes
            )

            if not has_buffer:
                return False, f"{rule.get('name')}: Insufficient buffer time ({buffer_minutes} min required)"
            return True, f"{rule.get('name')}: Adequate buffer time"

        elif rule_type == "room_type_match":
            # Validate that room type matches service requirements
            required_types = conditions.get("required_room_types", [])

            # Get room's type
            room_type = await self._get_room_type(slot.room_id)

            if room_type not in required_types:
                return False, f"{rule.get('name')}: Service requires room type: {required_types}, got {room_type}"
            return True, f"{rule.get('name')}: Room type {room_type} matches requirements"

        elif rule_type == "cleaning_buffer":
            # Enforce cleaning time between appointments in same room
            # Get room's cleaning duration
            cleaning_minutes = await self._get_room_cleaning_duration(slot.room_id)
            buffer_minutes = conditions.get("buffer_minutes", cleaning_minutes)

            # Check if there's adequate buffer time in the ROOM (not just doctor)
            has_buffer = await self._check_room_buffer_time(
                slot.room_id, slot.start_time, slot.end_time, buffer_minutes
            )

            if not has_buffer:
                return False, f"{rule.get('name')}: Room needs {buffer_minutes} min cleaning buffer"
            return True, f"{rule.get('name')}: Adequate cleaning buffer in room"

        elif rule_type == "doctor_room_preference":
            # Score rooms higher if doctor prefers them
            # Get doctor's preferred rooms from metadata or database
            preferred_rooms = conditions.get("preferred_rooms", [])

            if slot.room_id in preferred_rooms:
                modifier = conditions.get("score_modifier", 10)
                return True, f"{rule.get('name')}: Doctor's preferred room (+{modifier} points)"
            else:
                penalty = conditions.get("penalty", 5)
                return False, f"{rule.get('name')}: Not doctor's preferred room (-{penalty} points)"

        elif rule_type == "utilization_balancing":
            # Prefer less-utilized rooms for load balancing
            # Get room's appointment count for today
            daily_count = await self._get_room_appointment_count(
                slot.room_id, slot.start_time.date()
            )

            # Score based on utilization (fewer appointments = higher score)
            max_daily = conditions.get("max_daily_appointments", 20)
            utilization_ratio = daily_count / max_daily if max_daily > 0 else 0

            if utilization_ratio < 0.5:
                # Room is underutilized (< 50% capacity)
                modifier = conditions.get("underutilized_bonus", 10)
                return True, f"{rule.get('name')}: Room underutilized ({daily_count}/{max_daily}) (+{modifier} points)"
            elif utilization_ratio > 0.8:
                # Room is heavily utilized (> 80% capacity)
                penalty = conditions.get("overutilized_penalty", 10)
                return False, f"{rule.get('name')}: Room heavily utilized ({daily_count}/{max_daily}) (-{penalty} points)"
            else:
                # Normal utilization
                return True, f"{rule.get('name')}: Room utilization normal ({daily_count}/{max_daily})"

        # Default: rule passes if type not recognized
        logger.warning(f"Unknown rule type: {rule_type}")
        return True, f"{rule.get('name')}: Rule type '{rule_type}' not implemented"

    async def _get_doctor_appointment_count(
        self,
        doctor_id: str,
        date: datetime.date
    ) -> int:
        """Get the number of appointments for a doctor on a specific date"""
        try:
            response = self.supabase.from_("appointments")\
                .select("id", count="exact")\
                .eq("doctor_id", doctor_id)\
                .eq("appointment_date", date.isoformat())\
                .eq("status", "confirmed")\
                .execute()
            
            return response.count or 0
        except Exception as e:
            logger.error(f"Error getting appointment count: {e}")
            return 0

    async def _get_room_equipment(self, room_id: str) -> List[str]:
        """Get available equipment in a room"""
        try:
            response = self.supabase.from_("rooms")\
                .select("equipment")\
                .eq("id", room_id)\
                .single()\
                .execute()
            
            if response.data:
                return response.data.get("equipment", [])
        except Exception as e:
            logger.error(f"Error getting room equipment: {e}")
        
        return []

    async def _check_buffer_time(
        self,
        doctor_id: str,
        start_time: datetime,
        end_time: datetime,
        buffer_minutes: int
    ) -> bool:
        """Check if there's adequate buffer time around the slot"""
        try:
            # Check appointments before and after
            response = self.supabase.from_("appointments")\
                .select("start_time, end_time")\
                .eq("doctor_id", doctor_id)\
                .eq("appointment_date", start_time.date().isoformat())\
                .eq("status", "confirmed")\
                .execute()

            for appointment in response.data:
                apt_start = datetime.fromisoformat(appointment["start_time"])
                apt_end = datetime.fromisoformat(appointment["end_time"])

                # Check if appointments are too close
                if apt_end <= start_time:
                    # Appointment before this slot
                    buffer = (start_time - apt_end).total_seconds() / 60
                    if buffer < buffer_minutes:
                        return False
                elif apt_start >= end_time:
                    # Appointment after this slot
                    buffer = (apt_start - end_time).total_seconds() / 60
                    if buffer < buffer_minutes:
                        return False

            return True

        except Exception as e:
            logger.error(f"Error checking buffer time: {e}")
            return True  # Default to allowing if check fails

    async def _get_room_type(self, room_id: str) -> str:
        """Get room type from database"""
        try:
            response = self.supabase.from_("rooms")\
                .select("room_type")\
                .eq("id", room_id)\
                .single()\
                .execute()
            return response.data.get("room_type", "") if response.data else ""
        except Exception as e:
            logger.error(f"Error getting room type: {e}")
            return ""

    async def _get_room_cleaning_duration(self, room_id: str) -> int:
        """Get room's cleaning duration from database"""
        try:
            response = self.supabase.from_("rooms")\
                .select("cleaning_duration_minutes")\
                .eq("id", room_id)\
                .single()\
                .execute()
            return response.data.get("cleaning_duration_minutes", 15) if response.data else 15
        except Exception as e:
            logger.error(f"Error getting cleaning duration: {e}")
            return 15

    async def _check_room_buffer_time(
        self,
        room_id: str,
        start_time: datetime,
        end_time: datetime,
        buffer_minutes: int
    ) -> bool:
        """Check if room has adequate buffer time (similar to _check_buffer_time but for rooms)"""
        try:
            response = self.supabase.from_("appointments")\
                .select("start_time, end_time")\
                .eq("room_id", room_id)\
                .eq("appointment_date", start_time.date().isoformat())\
                .eq("status", "confirmed")\
                .execute()

            for appointment in response.data:
                apt_start = datetime.fromisoformat(appointment["start_time"])
                apt_end = datetime.fromisoformat(appointment["end_time"])

                if apt_end <= start_time:
                    buffer = (start_time - apt_end).total_seconds() / 60
                    if buffer < buffer_minutes:
                        return False
                elif apt_start >= end_time:
                    buffer = (apt_start - end_time).total_seconds() / 60
                    if buffer < buffer_minutes:
                        return False

            return True
        except Exception as e:
            logger.error(f"Error checking room buffer time: {e}")
            return True

    async def _get_room_appointment_count(
        self,
        room_id: str,
        date: datetime.date
    ) -> int:
        """Get number of appointments for room on specific date"""
        try:
            response = self.supabase.from_("appointments")\
                .select("id", count="exact")\
                .eq("room_id", room_id)\
                .eq("appointment_date", date.isoformat())\
                .eq("status", "confirmed")\
                .execute()
            return response.count or 0
        except Exception as e:
            logger.error(f"Error getting room appointment count: {e}")
            return 0

    async def _fast_evaluate(
        self,
        context: EvaluationContext,
        slot: TimeSlot
    ) -> EvaluationResult:
        """Simplified fast evaluation for batch processing"""
        # Only check basic availability
        if not slot.is_available:
            return EvaluationResult(
                slot_id=slot.id,
                is_valid=False,
                explanations=["Slot not available"],
                execution_time_ms=0.5
            )
        
        return EvaluationResult(
            slot_id=slot.id,
            is_valid=True,
            score=50.0,  # Neutral score
            explanations=["Fast evaluation mode"],
            execution_time_ms=0.5
        )

    async def _log_evaluation(
        self,
        session_id: str,
        slot_id: str,
        context: Dict,
        result: Dict,
        execution_time_ms: float
    ):
        """Log evaluation for audit and analysis"""
        try:
            self.supabase.from_("rule_evaluations").insert({
                "session_id": session_id,
                "slot_id": slot_id,
                "input_context": context.to_dict() if hasattr(context, 'to_dict') else context,
                "evaluation_result": result,
                "execution_time_ms": execution_time_ms,
                "created_at": datetime.now(timezone.utc).isoformat()
            }).execute()
        except Exception as e:
            logger.error(f"Error logging evaluation: {e}")

    def _update_stats(self, execution_time_ms: float, is_valid: bool):
        """Update evaluation statistics"""
        self.stats["total_evaluations"] += 1
        if is_valid:
            self.stats["valid_slots"] += 1
        else:
            self.stats["invalid_slots"] += 1
        
        # Update average execution time
        current_avg = self.stats["avg_execution_time_ms"]
        total = self.stats["total_evaluations"]
        self.stats["avg_execution_time_ms"] = ((current_avg * (total - 1)) + execution_time_ms) / total

    def get_stats(self) -> Dict:
        """Get evaluation statistics"""
        return self.stats

    def reset_stats(self):
        """Reset statistics"""
        self.stats = {
            "total_evaluations": 0,
            "valid_slots": 0,
            "invalid_slots": 0,
            "avg_execution_time_ms": 0.0
        }