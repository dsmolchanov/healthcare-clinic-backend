"""
Core Scheduling Service.

Implements slot suggestion, hold management, and appointment confirmation
with rule-based optimization.
"""

import os
import logging
import asyncio
from datetime import datetime, time, timedelta
from typing import Dict, List, Optional, Any, Tuple
from uuid import UUID, uuid4
from functools import lru_cache

from supabase import create_client, Client
from supabase.client import ClientOptions

from ..models.scheduling import (
    DateRange,
    HardConstraints,
    Slot,
    SuggestedSlots,
    HoldResponse,
    AppointmentResponse,
    NoSlotsAvailableError,
    HoldExpiredError,
    HoldNotFoundError,
    InvalidConstraintsError
)
from .scheduling.constraint_engine import ConstraintEngine
from .scheduling.preference_scorer import PreferenceScorer
from .scheduling.escalation_manager import EscalationManager
from .external_calendar_service import ExternalCalendarService
from .scheduling.query_profiler import profile_query, PerformanceMonitor
from .scheduling.cache_monitor import CacheMonitor, global_cache_monitor
from app.policies.compiler import RuleEffectType, CompiledPolicy
from app.services.limit_counter import LimitCounterStore, LimitReservationToken
from app.services.policy_errors import PolicyViolationError
from app.services.policy_manager import PolicyManager, ActivePolicy
from app.services.policy_adapter import (
    build_slot_context,
    context_field_truthy,
)

logger = logging.getLogger(__name__)

# Cache for settings (60-second TTL)
_settings_cache = {}
_cache_timestamp = {}
_settings_cache_monitor = CacheMonitor()

# Register settings cache with global monitor
global_cache_monitor.register("settings_cache", _settings_cache_monitor)


class SchedulingService:
    """
    Main scheduling service for appointment slot management.

    Implements the core scheduler with:
    - Slot suggestion with constraint checking and preference scoring
    - Temporary slot holds with expiration
    - Hold confirmation and appointment creation
    - Calendar synchronization
    - Escalation on zero slots
    """

    def __init__(self, supabase: Client = None):
        """
        Initialize scheduling service.

        Args:
            supabase: Optional Supabase client (creates new if not provided)
        """
        if supabase:
            self.db = supabase
        else:
            # Configure client to use healthcare schema
            options = ClientOptions(
                schema='healthcare',
                auto_refresh_token=True,
                persist_session=False
            )
            self.db = create_client(
                os.environ.get("SUPABASE_URL"),
                os.environ.get("SUPABASE_SERVICE_ROLE_KEY"),
                options=options
            )

        self.constraint_engine = ConstraintEngine(self.db)
        self.escalation_manager = EscalationManager(self.db)
        self.calendar_service = ExternalCalendarService(supabase=self.db)
        self.perf_monitor = PerformanceMonitor()
        self.policy_manager = PolicyManager(self.db)
        self.limit_counter = LimitCounterStore()

    @profile_query("suggest_slots")
    async def suggest_slots(
        self,
        clinic_id: UUID,
        service_id: UUID,
        date_range: DateRange,
        hard_constraints: Optional[HardConstraints] = None,
        patient_id: Optional[UUID] = None,
        patient_preferences: Optional[Dict[str, Any]] = None
    ) -> SuggestedSlots:
        """
        Suggest top appointment slots based on constraints and preferences.

        Steps:
        1. Load clinic settings (cached 60s)
        2. Enumerate candidate slots (15-min grid x date range)
        3. Filter hard constraints (fail-fast)
        4. Score soft preferences
        5. Sort and take top 10
        6. Generate explanations
        7. Escalate if zero results

        Args:
            clinic_id: Clinic UUID
            service_id: Service/appointment type UUID
            date_range: Search date range
            hard_constraints: Optional hard constraints (doctor, room, time)
            patient_id: Optional patient UUID (for escalation)
            patient_preferences: Optional patient preferences dict

        Returns:
            SuggestedSlots with top-scored slots

        Raises:
            NoSlotsAvailableError: If no slots found (includes escalation_id)
            InvalidConstraintsError: If constraints are contradictory
        """
        try:
            logger.info(
                f"Suggesting slots for clinic {clinic_id}, service {service_id}, "
                f"range {date_range.start_date} to {date_range.end_date}"
            )

            # Step 1: Load settings
            settings = await self._get_settings(clinic_id)
            grid_minutes = settings.get("grid_minutes", 15)
            max_days = settings.get("max_days_ahead", 3)

            # Step 2: Enumerate candidates
            candidates = await self._enumerate_candidates(
                clinic_id,
                service_id,
                date_range,
                grid_minutes,
                hard_constraints
            )
            logger.debug(f"Generated {len(candidates)} candidate slots")

            if not candidates:
                logger.warning("No candidate slots generated")
                raise InvalidConstraintsError(
                    "No slots match the provided constraints"
                )

            # Step 3: Filter hard constraints
            valid_slots = await self._filter_hard_constraints(
                candidates,
                clinic_id,
                service_id,
                hard_constraints
            )
            logger.info(f"{len(valid_slots)} slots passed constraint checks")

            # Step 3b: Apply policy constraints
            policy_entry = await self.policy_manager.get_active_policy(clinic_id)
            policy = policy_entry.policy if policy_entry else None
            doctor_appointments = await self._get_doctor_appointments(clinic_id)

            valid_slots = await self._apply_policy_constraints(
                valid_slots,
                clinic_id,
                service_id,
                policy_entry,
                settings,
                doctor_appointments,
                hard_constraints,
                patient_preferences,
                date_range,
                patient_id
            )

            # Step 4: Check if we need to escalate
            if not valid_slots:
                escalation_id = await self._escalate_no_slots(
                    clinic_id,
                    patient_id,
                    service_id,
                    date_range,
                    hard_constraints
                )
                raise NoSlotsAvailableError(
                    "No available slots found matching constraints",
                    escalation_id=escalation_id
                )

            # Step 5: Score soft preferences
            scored_slots = await self._score_soft_preferences(
                valid_slots,
                clinic_id,
                settings,
                patient_preferences,
                doctor_appointments=doctor_appointments,
                policy=policy,
                service_id=service_id,
                hard_constraints=hard_constraints
            )

            # Step 6: Sort and take top 10
            scored_slots.sort(key=lambda s: s["score"], reverse=True)
            top_slots = scored_slots[:10]

            # Step 7: Convert to Slot models
            slot_models = []
            for slot_data in top_slots:
                slot = Slot(
                    doctor_id=slot_data["doctor_id"],
                    room_id=slot_data["room_id"],
                    start_time=slot_data["start_time"],
                    end_time=slot_data["end_time"],
                    score=slot_data["score"],
                    explanations=slot_data.get("explanations", []),
                    doctor_name=slot_data.get("doctor_name"),
                    room_name=slot_data.get("room_name")
                )
                slot_models.append(slot)

            return SuggestedSlots(
                slots=slot_models,
                policy=settings,
                total_candidates_checked=len(candidates),
                date_range=date_range
            )

        except NoSlotsAvailableError:
            raise
        except InvalidConstraintsError:
            raise
        except Exception as e:
            logger.error(f"Error suggesting slots: {e}", exc_info=True)
            raise

    @profile_query("hold_slot")
    async def hold_slot(
        self,
        slot: Slot,
        client_hold_id: str,
        patient_id: UUID,
        clinic_id: UUID,
        service_id: UUID
    ) -> HoldResponse:
        """
        Create temporary hold on a slot (idempotent).

        Args:
            slot: Slot to hold
            client_hold_id: Client-side unique ID for idempotency
            patient_id: Patient UUID
            clinic_id: Clinic UUID
            service_id: Service UUID

        Returns:
            HoldResponse with hold details

        Raises:
            Exception: If slot is no longer available
        """
        try:
            logger.info(
                f"Creating hold for patient {patient_id}, "
                f"client_hold_id: {client_hold_id}"
            )

            # Check for existing hold with same client_hold_id (idempotency)
            existing = self.db.table("appointment_holds")\
                .select("*")\
                .eq("client_hold_id", client_hold_id)\
                .gte("expires_at", datetime.utcnow().isoformat())\
                .execute()

            if existing.data:
                logger.info(f"Returning existing hold for {client_hold_id}")
                hold = existing.data[0]
                return HoldResponse(
                    hold_id=UUID(hold["id"]),
                    expires_at=datetime.fromisoformat(hold["expires_at"]),
                    slot=slot,
                    client_hold_id=client_hold_id,
                    is_new=False
                )

            # Verify slot is still available
            is_available = await self.constraint_engine.check_room_availability(
                slot.room_id,
                slot.start_time,
                slot.end_time
            )

            if not is_available:
                raise Exception("Slot is no longer available")

            # Create hold with 5-minute expiration
            expires_at = datetime.utcnow() + timedelta(minutes=5)

            hold_data = {
                "id": str(uuid4()),
                "clinic_id": str(clinic_id),
                "patient_id": str(patient_id),
                "doctor_id": str(slot.doctor_id),
                "room_id": str(slot.room_id),
                "service_id": str(service_id),
                "start_time": slot.start_time.isoformat(),
                "end_time": slot.end_time.isoformat(),
                "expires_at": expires_at.isoformat(),
                "client_hold_id": client_hold_id,
                "slot_id": getattr(slot, "slot_id", str(uuid4())),
                "doctor_name": getattr(slot, "doctor_name", "Unknown"),
                "created_at": datetime.utcnow().isoformat()
            }

            result = self.db.table("appointment_holds")\
                .insert(hold_data)\
                .execute()

            logger.info(f"Created hold {hold_data['id']}")

            return HoldResponse(
                hold_id=UUID(hold_data["id"]),
                expires_at=expires_at,
                slot=slot,
                client_hold_id=client_hold_id,
                is_new=True
            )

        except Exception as e:
            logger.error(f"Error creating hold: {e}", exc_info=True)
            raise

    @profile_query("confirm_hold")
    async def confirm_hold(
        self,
        hold_id: UUID,
        patient_id: UUID,
        service_id: UUID,
        metadata: Optional[Dict[str, Any]] = None
    ) -> AppointmentResponse:
        """
        Confirm hold and create appointment.

        Triggers calendar sync asynchronously.

        Args:
            hold_id: Hold UUID to confirm
            patient_id: Patient UUID (must match hold)
            service_id: Service UUID
            metadata: Optional additional appointment data

        Returns:
            AppointmentResponse with appointment details

        Raises:
            HoldNotFoundError: If hold doesn't exist
            HoldExpiredError: If hold has expired
        """
        limit_tokens: List[LimitReservationToken] = []

        try:
            logger.info(f"Confirming hold {hold_id} for patient {patient_id}")

            # Fetch hold
            hold_result = self.db.table("appointment_holds")\
                .select("*")\
                .eq("id", str(hold_id))\
                .eq("patient_id", str(patient_id))\
                .execute()

            if not hold_result.data:
                raise HoldNotFoundError(f"Hold {hold_id} not found")

            hold = hold_result.data[0]

            # Check expiration
            expires_at = datetime.fromisoformat(hold["expires_at"])
            if datetime.utcnow() > expires_at:
                raise HoldExpiredError(f"Hold {hold_id} has expired")

            clinic_uuid = UUID(hold["clinic_id"])
            policy_entry = await self.policy_manager.get_active_policy(clinic_uuid)

            slot_for_policy = {
                "doctor_id": UUID(hold["doctor_id"]),
                "room_id": UUID(hold["room_id"]) if hold.get("room_id") else None,
                "start_time": datetime.fromisoformat(hold["start_time"]),
                "end_time": datetime.fromisoformat(hold["end_time"]),
                "duration_minutes": max(1, int((datetime.fromisoformat(hold["end_time"]) - datetime.fromisoformat(hold["start_time"])).total_seconds() / 60)),
                "score": 0.0
            }

            patient_preferences = metadata.copy() if isinstance(metadata, dict) else {}

            try:
                limit_tokens = await self._reserve_limit_counters(
                    clinic_uuid,
                    policy_entry,
                    slot_for_policy,
                    patient_id,
                    patient_preferences
                )
            except PolicyViolationError as exc:
                raise InvalidConstraintsError(str(exc)) from exc

            # Create appointment
            appointment_id = uuid4()
            appointment_data = {
                "id": str(appointment_id),
                "clinic_id": hold["clinic_id"],
                "patient_id": str(patient_id),
                "doctor_id": hold["doctor_id"],
                "room_id": hold["room_id"],
                "service_id": str(service_id),
                "start_time": hold["start_time"],
                "end_time": hold["end_time"],
                "status": "scheduled",
                "created_at": datetime.utcnow().isoformat(),
                "metadata": metadata or {}
            }

            try:
                apt_result = self.db.table("appointments")\
                    .insert(appointment_data)\
                    .execute()
            except Exception:
                for token in limit_tokens:
                    self.limit_counter.release(token)
                raise

            # Delete hold
            self.db.table("appointment_holds")\
                .delete()\
                .eq("id", str(hold_id))\
                .execute()

            logger.info(f"Created appointment {appointment_id}")

            # Trigger calendar sync asynchronously (don't block on errors)
            calendar_synced = False
            calendar_event_ids = None

            try:
                # Sync to external calendar
                sync_result = await self.calendar_service.sync_appointment_to_calendar(
                    appointment_id,
                    hold["doctor_id"],
                    datetime.fromisoformat(hold["start_time"]),
                    datetime.fromisoformat(hold["end_time"]),
                    appointment_data
                )
                calendar_synced = sync_result.get("success", False)
                calendar_event_ids = sync_result.get("event_ids")
                if isinstance(calendar_event_ids, list):
                    calendar_event_ids = {f"event_{idx}": event_id for idx, event_id in enumerate(calendar_event_ids)}
            except Exception as e:
                logger.warning(f"Calendar sync failed (non-blocking): {e}")

            # Create slot model for response
            slot = Slot(
                slot_id=hold.get("slot_id", hold["id"]),
                doctor_id=UUID(hold["doctor_id"]),
                doctor_name=hold.get("doctor_name", "Unknown"),
                room_id=UUID(hold["room_id"]) if hold.get("room_id") else None,
                service_id=UUID(hold.get("service_id", str(service_id))),
                start_time=datetime.fromisoformat(hold["start_time"]),
                end_time=datetime.fromisoformat(hold["end_time"]),
                score=0.0,
                explanation=[]
            )

            return AppointmentResponse(
                appointment_id=appointment_id,
                slot=slot,
                patient_id=patient_id,
                status="scheduled",
                created_at=datetime.utcnow(),
                calendar_synced=calendar_synced,
                calendar_event_ids=calendar_event_ids,
                metadata=metadata or {}
            )

        except (HoldNotFoundError, HoldExpiredError):
            for token in limit_tokens:
                self.limit_counter.release(token)
            raise
        except Exception as e:
            for token in limit_tokens:
                self.limit_counter.release(token)
            logger.error(f"Error confirming hold: {e}", exc_info=True)
            raise

    async def _get_settings(self, clinic_id: UUID) -> Dict[str, Any]:
        """
        Load scheduling settings with 60s cache.

        Args:
            clinic_id: Clinic UUID

        Returns:
            Settings dict from sched_settings table
        """
        cache_key = str(clinic_id)

        # Check cache
        if cache_key in _settings_cache:
            cached_time = _cache_timestamp.get(cache_key)
            if cached_time and datetime.utcnow() - cached_time < timedelta(seconds=60):
                _settings_cache_monitor.record_hit()
                logger.debug(f"Using cached settings for clinic {clinic_id}")
                return _settings_cache[cache_key]

        # Cache miss
        _settings_cache_monitor.record_miss()

        # Fetch from database
        result = self.db.table("sched_settings")\
            .select("*")\
            .eq("clinic_id", str(clinic_id))\
            .execute()

        if result.data:
            settings = result.data[0]
            settings.setdefault("open_hour", 8)
            settings.setdefault("close_hour", 20)
        else:
            # Default settings
            logger.warning(f"No settings found for clinic {clinic_id}, using defaults")
            settings = {
                "grid_minutes": 15,
                "max_days_ahead": 3,
                "hold_duration_minutes": 5,
                "open_hour": 8,
                "close_hour": 20,
                "preference_weights": {
                    "least_busy": 0.3,
                    "pack_schedule": 0.25,
                    "room_preference": 0.2,
                    "time_of_day": 0.15,
                    "patient_preference": 0.1
                }
            }

        # Update cache
        _settings_cache[cache_key] = settings
        _cache_timestamp[cache_key] = datetime.utcnow()

        return settings

    async def _enumerate_candidates(
        self,
        clinic_id: UUID,
        service_id: UUID,
        date_range: DateRange,
        grid_minutes: int,
        hard_constraints: Optional[HardConstraints] = None
    ) -> List[Dict[str, Any]]:
        """
        Generate candidate time slots.

        Args:
            clinic_id: Clinic UUID
            service_id: Service UUID
            date_range: Date range to search
            grid_minutes: Time grid interval (e.g., 15 minutes)
            hard_constraints: Optional hard constraints

        Returns:
            List of candidate slot dicts
        """
        candidates = []

        try:
            # Get service details for duration
            service_result = self.db.table("services")\
                .select("duration_minutes")\
                .eq("id", str(service_id))\
                .execute()

            duration_minutes = 30  # Default
            if service_result.data:
                duration_minutes = service_result.data[0].get("duration_minutes", 30)

            # Get eligible doctors and rooms
            doctors = await self._get_eligible_doctors(
                clinic_id,
                service_id,
                hard_constraints
            )
            rooms = await self._get_available_rooms(clinic_id, hard_constraints)

            if not doctors or not rooms:
                logger.warning("No eligible doctors or rooms found")
                return []

            # Generate time slots
            current = date_range.start_date
            while current <= date_range.end_date:
                # For each day, generate slots from 8 AM to 8 PM
                day_start = current.replace(hour=8, minute=0, second=0, microsecond=0)
                day_end = current.replace(hour=20, minute=0, second=0, microsecond=0)

                slot_time = day_start
                while slot_time < day_end:
                    # Create candidate for each doctor/room combination
                    for doctor in doctors:
                        for room in rooms:
                            candidates.append({
                                "doctor_id": doctor["id"],
                                "doctor_name": doctor.get("name", ""),
                                "room_id": room["id"],
                                "room_name": room.get("name", ""),
                                "start_time": slot_time,
                                "end_time": slot_time + timedelta(minutes=duration_minutes),
                                "duration_minutes": duration_minutes,
                                "score": 0.0
                            })

                    slot_time += timedelta(minutes=grid_minutes)

                current += timedelta(days=1)

            logger.debug(f"Generated {len(candidates)} candidate slots")
            return candidates

        except Exception as e:
            logger.error(f"Error enumerating candidates: {e}")
            return []

    async def _get_eligible_doctors(
        self,
        clinic_id: UUID,
        service_id: UUID,
        hard_constraints: Optional[HardConstraints] = None
    ) -> List[Dict[str, Any]]:
        """Get doctors eligible for this service."""
        query = self.db.table("doctor_services")\
            .select("doctor_id, doctors(id, name)")\
            .eq("service_id", str(service_id))

        if hard_constraints and hard_constraints.doctor_id:
            query = query.eq("doctor_id", str(hard_constraints.doctor_id))

        result = query.execute()

        doctors = []
        for row in result.data:
            doctor_data = row.get("doctors")
            if doctor_data:
                doctors.append({
                    "id": UUID(doctor_data["id"]),
                    "name": doctor_data.get("name", "")
                })

        return doctors

    async def _get_available_rooms(
        self,
        clinic_id: UUID,
        hard_constraints: Optional[HardConstraints] = None
    ) -> List[Dict[str, Any]]:
        """Get available rooms for clinic."""
        query = self.db.table("rooms")\
            .select("id, name")\
            .eq("clinic_id", str(clinic_id))

        if hard_constraints and hard_constraints.room_id:
            query = query.eq("id", str(hard_constraints.room_id))

        result = query.execute()

        rooms = []
        for row in result.data:
            rooms.append({
                "id": UUID(row["id"]),
                "name": row.get("name", "")
            })

        return rooms

    @profile_query("filter_constraints")
    async def _filter_hard_constraints(
        self,
        candidates: List[Dict[str, Any]],
        clinic_id: UUID,
        service_id: UUID,
        hard_constraints: Optional[HardConstraints] = None
    ) -> List[Dict[str, Any]]:
        """
        Filter candidates by hard constraints.

        Args:
            candidates: List of candidate slots
            clinic_id: Clinic UUID
            service_id: Service UUID
            hard_constraints: Optional hard constraints

        Returns:
            List of valid slots (passed all constraint checks)
        """
        valid_slots = []

        for candidate in candidates:
            # Check all constraints
            checks = await self.constraint_engine.check_all_constraints(
                candidate["doctor_id"],
                candidate["room_id"],
                service_id,
                candidate["start_time"],
                candidate["end_time"]
            )

            if self.constraint_engine.is_valid_slot(checks):
                valid_slots.append(candidate)

        return valid_slots

    async def _apply_policy_constraints(
        self,
        slots: List[Dict[str, Any]],
        clinic_id: UUID,
        service_id: UUID,
        policy_entry: Optional[ActivePolicy],
        settings: Dict[str, Any],
        doctor_appointments: Dict[UUID, List[Dict]],
        hard_constraints: Optional[HardConstraints],
        patient_preferences: Optional[Dict[str, Any]],
        date_range: DateRange,
        patient_id: Optional[UUID]
    ) -> List[Dict[str, Any]]:
        """Filter slots by policy hard rules."""
        policy = policy_entry.policy if policy_entry else None
        if not policy or not policy.hard_rules:
            return slots

        filtered: List[Dict[str, Any]] = []

        for slot in slots:
            tenant_id = None
            if policy_entry and policy_entry.bundle:
                tenant_id = policy_entry.bundle.get("tenant_id")

            context = build_slot_context(
                slot,
                settings,
                doctor_appointments,
                patient_preferences,
                hard_constraints,
                clinic_id=clinic_id,
                patient_id=patient_id,
                tenant_id=tenant_id
            )

            deny_slot = False
            policy_notes: List[str] = []

            for rule in policy.hard_rules:
                try:
                    if not rule.matches(context):
                        continue
                except Exception as exc:
                    logger.warning(f"Error evaluating rule {rule.rule_id}: {exc}")
                    continue

                effect = rule.effect_payload
                explanation = effect.get("explain_template") or rule.metadata.get("explain_template")

                if rule.effect_type == RuleEffectType.DENY:
                    deny_slot = True
                    if explanation:
                        logger.debug(f"Policy deny rule {rule.rule_id} matched for slot")
                    break

                if rule.effect_type == RuleEffectType.ESCALATE:
                    escalation_id = await self._escalate_no_slots(
                        clinic_id,
                        patient_id,
                        service_id,
                        date_range,
                        hard_constraints
                    )
                    raise NoSlotsAvailableError(
                        explanation or "Request escalated for manual review",
                        escalation_id=escalation_id
                    )

                if rule.effect_type == RuleEffectType.REQUIRE_FIELD:
                    required_field = effect.get("field")
                    if required_field and not context_field_truthy(context, required_field):
                        deny_slot = True
                        if explanation:
                            policy_notes.append(explanation)
                        break

                if rule.effect_type == RuleEffectType.LIMIT_OCCURRENCE:
                    logger.debug(
                        "Limit occurrence rule %s matched (not enforced in real-time)",
                        rule.rule_id
                    )
                    if explanation:
                        policy_notes.append(explanation)

            if deny_slot:
                continue

            if policy_notes:
                slot.setdefault("explanations", []).extend(policy_notes)

            filtered.append(slot)

        return filtered

    def _apply_policy_soft_rules(
        self,
        slot: Dict[str, Any],
        policy: CompiledPolicy,
        context: Dict[str, Any]
    ) -> List[str]:
        """Apply soft rules to adjust slot score/explanations."""
        explanations: List[str] = []

        for rule in policy.soft_rules:
            try:
                if not rule.matches(context):
                    continue
            except Exception as exc:
                logger.warning(f"Error evaluating soft rule {rule.rule_id}: {exc}")
                continue

            effect = rule.effect_payload
            explanation = (
                effect.get("explain_template")
                or effect.get("message")
                or rule.metadata.get("explain_template")
            )

            if rule.effect_type == RuleEffectType.ADJUST_SCORE:
                delta = effect.get("delta", 0)
                slot["score"] = slot.get("score", 0.0) + delta
                if explanation:
                    explanations.append(f"{explanation} (+{delta:.1f})")
            elif rule.effect_type == RuleEffectType.WARN:
                if explanation:
                    explanations.append(explanation)

        return explanations

    async def _reserve_limit_counters(
        self,
        clinic_id: UUID,
        policy_entry: ActivePolicy,
        slot: Dict[str, Any],
        patient_id: Optional[UUID],
        patient_preferences: Optional[Dict[str, Any]]
    ) -> List[LimitReservationToken]:
        tokens: List[LimitReservationToken] = []
        policy = policy_entry.policy if policy_entry else None
        if not policy or not policy.hard_rules:
            return tokens

        settings = await self._get_settings(clinic_id)
        doctor_appointments = await self._get_doctor_appointments(clinic_id)
        tenant_id = policy_entry.bundle.get("tenant_id") if policy_entry.bundle else None

        context = build_slot_context(
            slot,
            settings,
            doctor_appointments,
            patient_preferences or {},
            HardConstraints(doctor_id=slot["doctor_id"]),
            clinic_id=clinic_id,
            patient_id=patient_id,
            tenant_id=tenant_id
        )

        for rule in policy.hard_rules:
            if rule.effect_type != RuleEffectType.LIMIT_OCCURRENCE:
                continue

            try:
                if not rule.matches(context):
                    continue
            except Exception as exc:
                logger.warning(f"Error evaluating limit rule {rule.rule_id}: {exc}")
                continue

            metadata = self._compute_limit_metadata(
                rule,
                policy_entry,
                context
            )

            if not metadata:
                continue

            key, window_seconds, max_n = metadata
            allowed, token, count = self.limit_counter.reserve(key, window_seconds, max_n)
            if not allowed:
                explanation = (
                    rule.effect_payload.get("explain_template")
                    or rule.metadata.get("explain_template")
                    or "Reservation limit reached."
                )
                details = [
                    f"{rule.effect_payload.get('dimension')} limit reached ({count}/{max_n})"
                ]
                raise PolicyViolationError(explanation, details)

            if token:
                tokens.append(token)

        return tokens

    def _compute_limit_metadata(
        self,
        rule,
        policy_entry: ActivePolicy,
        context: Dict[str, Any]
    ) -> Optional[Tuple[str, int, int]]:
        effect = rule.effect_payload or {}
        dimension = effect.get("dimension")
        max_n = effect.get("max_n")
        if not dimension or max_n is None:
            return None

        window_hours_override = effect.get("window_hours")
        if window_hours_override is not None:
            window_hours = float(window_hours_override)
        else:
            if dimension.endswith("week"):
                window_hours = 24 * 7
            elif dimension.endswith("day"):
                window_hours = 24
            else:
                window_hours = 1

        entity = None
        if dimension.startswith("tenant"):
            entity = (
                context.get("tenant", {}).get("id")
                or context.get("clinic", {}).get("id")
                or str(policy_entry.clinic_id)
            )
        elif dimension.startswith("clinic"):
            entity = context.get("clinic", {}).get("id") or str(policy_entry.clinic_id)
        elif dimension.startswith("provider"):
            entity = context.get("doctor", {}).get("id")
        elif dimension.startswith("patient"):
            entity = context.get("patient", {}).get("id")

        if not entity:
            return None

        bundle_prefix = (
            policy_entry.bundle_sha
            or policy_entry.bundle.get("bundle_id")
            or str(policy_entry.clinic_id)
        )

        window_seconds = max(1, int(window_hours * 3600))
        key = f"policy:{bundle_prefix}:{dimension}:{entity}"
        return key, window_seconds, int(max_n)

    @profile_query("score_preferences")
    async def _score_soft_preferences(
        self,
        valid_slots: List[Dict[str, Any]],
        clinic_id: UUID,
        settings: Dict[str, Any],
        patient_preferences: Optional[Dict[str, Any]] = None,
        *,
        doctor_appointments: Optional[Dict[UUID, List[Dict]]] = None,
        policy: Optional[CompiledPolicy] = None,
        service_id: Optional[UUID] = None,
        hard_constraints: Optional[HardConstraints] = None
    ) -> List[Dict[str, Any]]:
        """
        Score slots based on soft preferences.

        Args:
            valid_slots: List of valid slots
            clinic_id: Clinic UUID
            settings: Scheduling settings
            patient_preferences: Optional patient preferences

        Returns:
            List of slots with scores and explanations
        """
        scorer = PreferenceScorer(settings)

        # Get all appointments for scoring context if not provided
        if doctor_appointments is None:
            doctor_appointments = await self._get_doctor_appointments(clinic_id)
        room_preferences = await self._get_room_preferences(clinic_id)

        for slot in valid_slots:
            components = {
                "least_busy": scorer.score_least_busy(
                    slot["doctor_id"],
                    slot["start_time"],
                    doctor_appointments
                ),
                "pack_schedule": scorer.score_pack_schedule(
                    slot["doctor_id"],
                    slot["start_time"],
                    doctor_appointments
                ),
                "room_preference": scorer.score_room_preference(
                    slot["doctor_id"],
                    slot["room_id"],
                    room_preferences
                ),
                "time_of_day": scorer.score_time_of_day(
                    slot["start_time"],
                    patient_preferences
                ),
                "patient_preference": scorer.score_patient_preference(
                    slot["doctor_id"],
                    patient_preferences
                )
            }

            slot["score"] = scorer.calculate_total_score(slot, components)
            slot["explanations"] = scorer.generate_explanations(components)

            if policy:
                context = build_slot_context(
                    slot,
                    settings,
                    doctor_appointments,
                    patient_preferences,
                    hard_constraints,
                    clinic_id=clinic_id,
                    patient_id=None,
                    tenant_id=None
                )
                policy_explanations = self._apply_policy_soft_rules(
                    slot,
                    policy,
                    context
                )
                if policy_explanations:
                    slot["explanations"].extend(policy_explanations)

        return valid_slots

    async def _get_doctor_appointments(
        self,
        clinic_id: UUID
    ) -> Dict[UUID, List[Dict]]:
        """Get all appointments grouped by doctor."""
        result = self.db.table("appointments")\
            .select("doctor_id, start_time, end_time")\
            .eq("clinic_id", str(clinic_id))\
            .neq("status", "cancelled")\
            .execute()

        appointments_by_doctor = {}
        for apt in result.data:
            doctor_id = UUID(apt["doctor_id"])
            if doctor_id not in appointments_by_doctor:
                appointments_by_doctor[doctor_id] = []
            appointments_by_doctor[doctor_id].append(apt)

        return appointments_by_doctor

    async def _get_room_preferences(self, clinic_id: UUID) -> Dict[UUID, UUID]:
        """Get doctor room preferences."""
        result = self.db.table("doctor_room_preference")\
            .select("doctor_id, room_id")\
            .eq("clinic_id", str(clinic_id))\
            .execute()

        preferences = {}
        for pref in result.data:
            preferences[UUID(pref["doctor_id"])] = UUID(pref["room_id"])

        return preferences

    async def _escalate_no_slots(
        self,
        clinic_id: UUID,
        patient_id: Optional[UUID],
        service_id: UUID,
        date_range: DateRange,
        hard_constraints: Optional[HardConstraints] = None
    ) -> Optional[UUID]:
        """Create escalation when no slots are available."""
        try:
            if not patient_id:
                logger.warning("No patient_id provided, skipping escalation")
                return None

            request_payload = {
                "service_id": str(service_id),
                "patient_id": str(patient_id),
                "date_range": {
                    "start_date": date_range.start_date.isoformat(),
                    "end_date": date_range.end_date.isoformat()
                },
                "hard_constraints": hard_constraints.dict() if hard_constraints else {}
            }

            escalation = await self.escalation_manager.create_escalation(
                clinic_id=clinic_id,
                request=request_payload,
                reason="no_available_slots"
            )

            escalation_id = escalation.get("id") if escalation else None
            logger.info(f"Created escalation {escalation_id}")
            return UUID(escalation_id) if escalation_id else None

        except Exception as e:
            logger.error(f"Error creating escalation: {e}")
            return None

    async def _log_decision(
        self,
        appointment_id: UUID,
        scores: Dict[str, Any],
        explanation: str
    ):
        """Log scheduling decision to sched_decisions table."""
        try:
            decision_data = {
                "id": str(uuid4()),
                "appointment_id": str(appointment_id),
                "scores": scores,
                "explanation": explanation,
                "created_at": datetime.utcnow().isoformat()
            }

            self.db.table("sched_decisions")\
                .insert(decision_data)\
                .execute()

        except Exception as e:
            logger.error(f"Error logging decision: {e}")
