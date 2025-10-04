"""
Pattern Evaluator Service
Multi-visit pattern scheduling with atomic reservation
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple
from uuid import uuid4
import asyncio

from supabase import Client
from .rule_evaluator import RuleEvaluator, EvaluationContext, TimeSlot

logger = logging.getLogger(__name__)


class PatternSlot:
    """Represents a slot in a multi-visit pattern"""
    
    def __init__(
        self,
        visit_number: int,
        visit_name: str,
        slot: TimeSlot,
        offset_days: int = 0
    ):
        self.visit_number = visit_number
        self.visit_name = visit_name
        self.slot = slot
        self.offset_days = offset_days

    def to_dict(self):
        return {
            "visit_number": self.visit_number,
            "visit_name": self.visit_name,
            "slot": self.slot.to_dict(),
            "offset_days": self.offset_days
        }


class PatternSlotSet:
    """A complete set of slots for a pattern"""
    
    def __init__(
        self,
        pattern_id: str,
        pattern_name: str,
        slots: List[PatternSlot],
        total_score: float = 0.0,
        constraints_met: bool = True
    ):
        self.pattern_id = pattern_id
        self.pattern_name = pattern_name
        self.slots = slots
        self.total_score = total_score
        self.constraints_met = constraints_met
        self.group_token = str(uuid4())

    def to_dict(self):
        return {
            "pattern_id": self.pattern_id,
            "pattern_name": self.pattern_name,
            "slots": [s.to_dict() for s in self.slots],
            "total_score": self.total_score,
            "constraints_met": self.constraints_met,
            "group_token": self.group_token
        }


class PatternReservation:
    """Represents a reserved pattern"""
    
    def __init__(
        self,
        reservation_id: str,
        pattern_id: str,
        patient_id: str,
        group_token: str,
        slots: List[Dict],
        status: str = "held",
        expires_at: datetime = None
    ):
        self.reservation_id = reservation_id
        self.pattern_id = pattern_id
        self.patient_id = patient_id
        self.group_token = group_token
        self.slots = slots
        self.status = status
        self.expires_at = expires_at or datetime.now(timezone.utc) + timedelta(minutes=5)

    def to_dict(self):
        return {
            "reservation_id": self.reservation_id,
            "pattern_id": self.pattern_id,
            "patient_id": self.patient_id,
            "group_token": self.group_token,
            "slots": self.slots,
            "status": self.status,
            "expires_at": self.expires_at.isoformat()
        }


class PatternEvaluator:
    """
    Evaluates and reserves multi-visit patterns atomically
    """
    
    def __init__(self, supabase: Client, rule_evaluator: RuleEvaluator):
        self.supabase = supabase
        self.rule_evaluator = rule_evaluator

    async def find_pattern_slots(
        self,
        pattern_id: str,
        context: EvaluationContext,
        start_date: datetime,
        end_date: datetime,
        max_results: int = 10
    ) -> List[PatternSlotSet]:
        """
        Find available slot sets for a multi-visit pattern
        """
        logger.info(f"Finding slots for pattern {pattern_id}")
        
        # Get pattern definition
        pattern = await self._get_pattern(pattern_id)
        if not pattern:
            logger.error(f"Pattern {pattern_id} not found")
            return []
        
        visits = pattern.get("visits", [])
        constraints = pattern.get("constraints", {})
        
        # Find slots for first visit
        first_visit = visits[0]
        first_slots = await self._find_visit_slots(
            context,
            first_visit,
            start_date,
            end_date,
            max_slots=max_results * 2  # Get extra to account for filtering
        )
        
        # For each first visit slot, try to build complete pattern
        pattern_sets = []
        
        for first_slot in first_slots:
            pattern_slots = [PatternSlot(
                visit_number=1,
                visit_name=first_visit["name"],
                slot=first_slot,
                offset_days=0
            )]
            
            # Try to find slots for remaining visits
            valid_pattern = True
            total_score = first_slot.metadata.get("score", 0)
            
            for i, visit in enumerate(visits[1:], start=2):
                # Calculate date range for this visit
                offset = visit.get("offset_from_previous", {})
                min_days = offset.get("min_days", 1)
                max_days = offset.get("max_days", 30)
                
                visit_start = first_slot.start_time + timedelta(days=min_days)
                visit_end = first_slot.start_time + timedelta(days=max_days)
                
                # Find slots for this visit
                visit_slots = await self._find_visit_slots(
                    context,
                    visit,
                    visit_start,
                    min(visit_end, end_date),
                    doctor_id=first_slot.doctor_id if constraints.get("same_doctor") else None,
                    max_slots=5
                )
                
                if not visit_slots:
                    valid_pattern = False
                    break
                
                # Use best scoring slot
                best_slot = visit_slots[0]
                pattern_slots.append(PatternSlot(
                    visit_number=i,
                    visit_name=visit["name"],
                    slot=best_slot,
                    offset_days=(best_slot.start_time - first_slot.start_time).days
                ))
                
                total_score += best_slot.metadata.get("score", 0)
            
            if valid_pattern and self._validate_pattern_constraints(pattern_slots, constraints):
                pattern_sets.append(PatternSlotSet(
                    pattern_id=pattern_id,
                    pattern_name=pattern["name"],
                    slots=pattern_slots,
                    total_score=total_score,
                    constraints_met=True
                ))
                
                if len(pattern_sets) >= max_results:
                    break
        
        # Sort by total score
        pattern_sets.sort(key=lambda ps: ps.total_score, reverse=True)
        
        logger.info(f"Found {len(pattern_sets)} valid pattern slot sets")
        return pattern_sets

    async def reserve_pattern_set(
        self,
        pattern_set: PatternSlotSet,
        patient_id: str,
        hold_duration_seconds: int = 300,
        client_hold_id: Optional[str] = None
    ) -> PatternReservation:
        """
        Atomically reserve all slots in a pattern
        """
        logger.info(f"Reserving pattern {pattern_set.pattern_id} for patient {patient_id}")
        
        # Check for idempotent request
        if client_hold_id:
            existing = await self._check_existing_hold(client_hold_id)
            if existing:
                logger.info(f"Returning existing reservation for hold {client_hold_id}")
                return existing
        
        group_token = pattern_set.group_token
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=hold_duration_seconds)
        reservation_id = str(uuid4())
        
        # Begin transaction
        try:
            # Create holds for each slot
            hold_ids = []
            for pattern_slot in pattern_set.slots:
                slot = pattern_slot.slot
                
                # Create hold
                hold_data = {
                    "id": str(uuid4()),
                    "pattern_reservation_id": reservation_id,
                    "visit_number": pattern_slot.visit_number,
                    "group_token": group_token,
                    "client_hold_id": client_hold_id,
                    "slot_id": slot.id,
                    "doctor_id": slot.doctor_id,
                    "room_id": slot.room_id,
                    "service_id": slot.service_id,
                    "start_time": slot.start_time.isoformat(),
                    "end_time": slot.end_time.isoformat(),
                    "appointment_date": slot.start_time.date().isoformat(),
                    "patient_id": patient_id,
                    "status": "held",
                    "expires_at": expires_at.isoformat()
                }
                
                response = self.supabase.from_("appointment_holds").insert(hold_data).execute()
                
                if not response.data:
                    # Rollback on failure
                    await self._rollback_holds(hold_ids)
                    raise RuntimeError(f"Failed to create hold for visit {pattern_slot.visit_number}")
                
                hold_ids.append(response.data[0]["id"])
            
            # Create pattern reservation record
            reservation_data = {
                "id": reservation_id,
                "pattern_id": pattern_set.pattern_id,
                "patient_id": patient_id,
                "group_token": group_token,
                "slots": [ps.to_dict() for ps in pattern_set.slots],
                "status": "held",
                "expires_at": expires_at.isoformat()
            }
            
            response = self.supabase.from_("pattern_reservations").insert(reservation_data).execute()
            
            if not response.data:
                await self._rollback_holds(hold_ids)
                raise RuntimeError("Failed to create pattern reservation")
            
            logger.info(f"Successfully reserved pattern {reservation_id}")
            
            return PatternReservation(
                reservation_id=reservation_id,
                pattern_id=pattern_set.pattern_id,
                patient_id=patient_id,
                group_token=group_token,
                slots=[ps.to_dict() for ps in pattern_set.slots],
                status="held",
                expires_at=expires_at
            )
            
        except Exception as e:
            logger.error(f"Error reserving pattern: {e}")
            raise

    async def confirm_pattern_reservation(
        self,
        reservation_id: str
    ) -> bool:
        """
        Confirm a pattern reservation and create actual appointments
        """
        logger.info(f"Confirming reservation {reservation_id}")
        
        # Get reservation
        response = self.supabase.from_("pattern_reservations")\
            .select("*")\
            .eq("id", reservation_id)\
            .eq("status", "held")\
            .single()\
            .execute()
        
        if not response.data:
            logger.error(f"Reservation {reservation_id} not found or not held")
            return False
        
        reservation = response.data
        
        # Check if not expired
        expires_at = datetime.fromisoformat(reservation["expires_at"])
        if expires_at < datetime.now(timezone.utc):
            logger.warning(f"Reservation {reservation_id} has expired")
            await self.cancel_pattern_reservation(reservation_id, "expired")
            return False
        
        # Create appointments
        appointment_ids = []
        try:
            for slot_data in reservation["slots"]:
                slot = slot_data["slot"]
                
                appointment_data = {
                    "id": str(uuid4()),
                    "clinic_id": reservation.get("clinic_id"),
                    "patient_id": reservation["patient_id"],
                    "doctor_id": slot["doctor_id"],
                    "room_id": slot.get("room_id"),
                    "service_id": slot["service_id"],
                    "pattern_group_token": reservation["group_token"],
                    "visit_number": slot_data["visit_number"],
                    "appointment_date": slot["start_time"][:10],
                    "start_time": slot["start_time"],
                    "end_time": slot["end_time"],
                    "status": "confirmed"
                }
                
                response = self.supabase.from_("appointments").insert(appointment_data).execute()
                
                if not response.data:
                    # Rollback appointments
                    await self._rollback_appointments(appointment_ids)
                    raise RuntimeError(f"Failed to create appointment for visit {slot_data['visit_number']}")
                
                appointment_ids.append(response.data[0]["id"])
            
            # Update reservation status
            self.supabase.from_("pattern_reservations")\
                .update({"status": "confirmed", "confirmed_at": datetime.now(timezone.utc).isoformat()})\
                .eq("id", reservation_id)\
                .execute()
            
            # Release holds
            self.supabase.from_("appointment_holds")\
                .update({"status": "converted"})\
                .eq("pattern_reservation_id", reservation_id)\
                .execute()
            
            logger.info(f"Successfully confirmed reservation {reservation_id}")
            return True
            
        except Exception as e:
            logger.error(f"Error confirming reservation: {e}")
            return False

    async def cancel_pattern_reservation(
        self,
        reservation_id: str,
        reason: str = "user_cancelled"
    ) -> bool:
        """
        Cancel a pattern reservation and release holds
        """
        logger.info(f"Cancelling reservation {reservation_id}: {reason}")
        
        try:
            # Update reservation status
            self.supabase.from_("pattern_reservations")\
                .update({
                    "status": "cancelled",
                    "cancelled_at": datetime.now(timezone.utc).isoformat(),
                    "cancellation_reason": reason
                })\
                .eq("id", reservation_id)\
                .execute()
            
            # Release holds
            self.supabase.from_("appointment_holds")\
                .update({"status": "cancelled"})\
                .eq("pattern_reservation_id", reservation_id)\
                .execute()
            
            logger.info(f"Successfully cancelled reservation {reservation_id}")
            return True
            
        except Exception as e:
            logger.error(f"Error cancelling reservation: {e}")
            return False

    async def _get_pattern(self, pattern_id: str) -> Optional[Dict]:
        """Get pattern definition from database"""
        try:
            response = self.supabase.from_("visit_patterns")\
                .select("*")\
                .eq("id", pattern_id)\
                .eq("active", True)\
                .single()\
                .execute()
            
            return response.data
        except Exception as e:
            logger.error(f"Error fetching pattern: {e}")
            return None

    async def _find_visit_slots(
        self,
        context: EvaluationContext,
        visit: Dict,
        start_date: datetime,
        end_date: datetime,
        doctor_id: Optional[str] = None,
        max_slots: int = 10
    ) -> List[TimeSlot]:
        """Find available slots for a single visit"""
        # Get available slots from appointment service
        # This is simplified - in production would query actual availability
        
        slots = []
        current_date = start_date.date()
        
        while current_date <= end_date.date() and len(slots) < max_slots:
            # Generate sample slots for the date
            for hour in [9, 10, 11, 14, 15, 16]:
                slot_start = datetime.combine(current_date, datetime.min.time().replace(hour=hour))
                slot_end = slot_start + timedelta(minutes=visit.get("duration_minutes", 30))
                
                slot = TimeSlot(
                    id=str(uuid4()),
                    doctor_id=doctor_id or f"doctor_{hour % 3}",
                    room_id=f"room_{hour % 2}",
                    service_id=visit.get("service_id", "default"),
                    start_time=slot_start,
                    end_time=slot_end,
                    is_available=True
                )
                
                # Evaluate slot
                result = await self.rule_evaluator.evaluate_slot(context, slot)
                
                if result.is_valid:
                    slot.metadata["score"] = result.score
                    slots.append(slot)
                    
                    if len(slots) >= max_slots:
                        break
            
            current_date += timedelta(days=1)
        
        # Sort by score
        slots.sort(key=lambda s: s.metadata.get("score", 0), reverse=True)
        
        return slots

    def _validate_pattern_constraints(
        self,
        pattern_slots: List[PatternSlot],
        constraints: Dict
    ) -> bool:
        """Validate pattern-level constraints"""
        if constraints.get("same_doctor"):
            # All visits must be with same doctor
            doctors = set(ps.slot.doctor_id for ps in pattern_slots)
            if len(doctors) > 1:
                return False
        
        if constraints.get("same_location"):
            # All visits must be at same location
            rooms = set(ps.slot.room_id for ps in pattern_slots)
            if len(rooms) > 1:
                return False
        
        # Check minimum/maximum spacing between visits
        for i in range(1, len(pattern_slots)):
            days_between = pattern_slots[i].offset_days - pattern_slots[i-1].offset_days
            
            min_spacing = constraints.get(f"visit_{i}_min_spacing", 1)
            max_spacing = constraints.get(f"visit_{i}_max_spacing", 365)
            
            if days_between < min_spacing or days_between > max_spacing:
                return False
        
        return True

    async def _check_existing_hold(self, client_hold_id: str) -> Optional[PatternReservation]:
        """Check for existing reservation with client hold ID"""
        try:
            response = self.supabase.from_("appointment_holds")\
                .select("pattern_reservation_id")\
                .eq("client_hold_id", client_hold_id)\
                .eq("status", "held")\
                .limit(1)\
                .execute()
            
            if response.data:
                reservation_id = response.data[0]["pattern_reservation_id"]
                
                # Get full reservation
                res_response = self.supabase.from_("pattern_reservations")\
                    .select("*")\
                    .eq("id", reservation_id)\
                    .single()\
                    .execute()
                
                if res_response.data:
                    res = res_response.data
                    return PatternReservation(
                        reservation_id=res["id"],
                        pattern_id=res["pattern_id"],
                        patient_id=res["patient_id"],
                        group_token=res["group_token"],
                        slots=res["slots"],
                        status=res["status"],
                        expires_at=datetime.fromisoformat(res["expires_at"])
                    )
        except Exception as e:
            logger.error(f"Error checking existing hold: {e}")
        
        return None

    async def _rollback_holds(self, hold_ids: List[str]):
        """Rollback created holds in case of error"""
        if hold_ids:
            try:
                self.supabase.from_("appointment_holds")\
                    .delete()\
                    .in_("id", hold_ids)\
                    .execute()
            except Exception as e:
                logger.error(f"Error rolling back holds: {e}")

    async def _rollback_appointments(self, appointment_ids: List[str]):
        """Rollback created appointments in case of error"""
        if appointment_ids:
            try:
                self.supabase.from_("appointments")\
                    .delete()\
                    .in_("id", appointment_ids)\
                    .execute()
            except Exception as e:
                logger.error(f"Error rolling back appointments: {e}")

    async def cleanup_expired_holds(self):
        """Clean up expired holds (can be run periodically)"""
        try:
            now = datetime.now(timezone.utc).isoformat()
            
            # Update expired holds
            self.supabase.from_("appointment_holds")\
                .update({"status": "expired"})\
                .eq("status", "held")\
                .lt("expires_at", now)\
                .execute()
            
            # Update expired reservations
            self.supabase.from_("pattern_reservations")\
                .update({"status": "expired"})\
                .eq("status", "held")\
                .lt("expires_at", now)\
                .execute()
            
            logger.info("Cleaned up expired holds")
            
        except Exception as e:
            logger.error(f"Error cleaning up expired holds: {e}")