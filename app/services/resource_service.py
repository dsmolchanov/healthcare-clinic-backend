"""
Resource Reservation Service
Implements unified resource reservation system using the new schema
"""

import os
import logging
from datetime import datetime, date, time
from typing import Dict, List, Optional, Any
from dataclasses import dataclass
from enum import Enum
from uuid import UUID

from supabase import create_client, Client

from app.models.scheduling import HardConstraints
from app.policies.compiler import RuleEffectType
from app.services.policy_manager import PolicyManager, ActivePolicy
from app.services.policy_adapter import build_slot_context, context_field_truthy

logger = logging.getLogger(__name__)


class ReservationStatus(Enum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    NO_SHOW = "no_show"


class ResourceType(Enum):
    DOCTOR = "doctor"
    ROOM = "room"
    EQUIPMENT = "equipment"
    FACILITY = "facility"


@dataclass
class ResourceCombination:
    """Available resource combination (doctor + room)"""
    doctor_resource_id: str
    doctor_name: str
    doctor_specialization: Optional[str]
    room_resource_id: str
    room_name: str
    room_type: Optional[str]
    combination_score: int
    available: bool


@dataclass
class ReservationRequest:
    """Request to create a resource reservation"""
    clinic_id: str
    patient_id: str
    service_id: str
    reservation_date: date
    start_time: time
    end_time: time
    doctor_resource_id: str
    room_resource_id: Optional[str] = None
    appointment_type: str = "consultation"
    reason: str = ""
    notes: str = ""


@dataclass
class ReservationResult:
    """Result of reservation operation"""
    success: bool
    reservation_id: Optional[str] = None
    error: Optional[str] = None
    data: Optional[Dict[str, Any]] = None


class PolicyViolationError(Exception):
    """Raised when a reservation violates clinic policy."""

    def __init__(self, message: str, messages: Optional[List[str]] = None):
        super().__init__(message)
        self.messages = messages or []


class ResourceService:
    """
    Unified resource reservation service using the new resource model.
    This replaces the old appointment-based system with a flexible resource reservation system.
    """

    def __init__(self, supabase: Client = None):
        if supabase:
            self.supabase = supabase
        else:
            self.supabase: Client = create_client(
                os.environ.get("SUPABASE_URL"),
                os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
            )
        self.policy_manager = PolicyManager(self.supabase)

    async def get_available_combinations(
        self,
        clinic_id: str,
        date: date,
        start_time: time,
        end_time: time,
        service_id: Optional[str] = None
    ) -> List[ResourceCombination]:
        """
        Get available resource combinations (doctor + room) for a given time slot.

        Args:
            clinic_id: Clinic UUID
            date: Reservation date
            start_time: Start time
            end_time: End time
            service_id: Optional service UUID for filtering

        Returns:
            List of available resource combinations
        """
        try:
            logger.info(f"Getting available combinations for clinic {clinic_id} on {date} {start_time}-{end_time}")

            # Call RPC function
            result = self.supabase.rpc(
                'get_available_resource_combinations',
                {
                    'p_clinic_id': clinic_id,
                    'p_date': date.isoformat(),
                    'p_start_time': start_time.isoformat(),
                    'p_end_time': end_time.isoformat(),
                    'p_service_id': service_id
                }
            ).execute()

            if not result.data:
                logger.info("No available combinations found")
                return []

            # Convert to dataclass
            combinations = [
                ResourceCombination(
                    doctor_resource_id=row['doctor_resource_id'],
                    doctor_name=row['doctor_name'],
                    doctor_specialization=row.get('doctor_specialization'),
                    room_resource_id=row['room_resource_id'],
                    room_name=row['room_name'],
                    room_type=row.get('room_type'),
                    combination_score=row.get('combination_score', 50),
                    available=row.get('available', True)
                )
                for row in result.data
            ]

            logger.info(f"Found {len(combinations)} available combinations")
            return combinations

        except Exception as e:
            logger.error(f"Failed to get available combinations: {e}")
            return []

    async def create_reservation(
        self,
        request: ReservationRequest
    ) -> ReservationResult:
        """
        Create a new resource reservation.

        Uses the unified RPC function which leverages EXCLUDE constraints
        to prevent race conditions atomically at the database level.

        Args:
            request: ReservationRequest with all booking details

        Returns:
            ReservationResult with success/failure status
        """
        try:
            logger.info(f"Creating reservation for patient {request.patient_id} on {request.reservation_date}")

            clinic_uuid = UUID(request.clinic_id)
            doctor_uuid = UUID(request.doctor_resource_id)
            room_uuid = UUID(request.room_resource_id) if request.room_resource_id else None

            policy_entry = await self.policy_manager.get_active_policy(clinic_uuid)
            settings = await self._get_clinic_hours(clinic_uuid)
            doctor_appointments = await self._get_doctor_reservations(clinic_uuid)

            hard_constraints = HardConstraints(doctor_id=doctor_uuid)
            patient_preferences = {
                "is_emergency": request.appointment_type.lower() == "emergency",
                "preferred_doctors": [request.doctor_resource_id],
                "notes": request.notes
            }

            start_dt = datetime.combine(request.reservation_date, request.start_time)
            end_dt = datetime.combine(request.reservation_date, request.end_time)

            slot = {
                "doctor_id": doctor_uuid,
                "room_id": room_uuid,
                "start_time": start_dt,
                "end_time": end_dt,
                "duration_minutes": int((end_dt - start_dt).total_seconds() / 60) or 1,
                "score": 0.0
            }

            try:
                policy_info = await self._validate_policy_for_reservation(
                    policy_entry,
                    slot,
                    settings,
                    doctor_appointments,
                    patient_preferences,
                    hard_constraints
                )
            except PolicyViolationError as exc:
                logger.info("Reservation blocked by policy: %s", exc)
                return ReservationResult(
                    success=False,
                    error=str(exc),
                    data={"policy_messages": exc.messages}
                )

            result = self.supabase.rpc(
                'create_resource_reservation',
                {
                    'p_clinic_id': request.clinic_id,
                    'p_patient_id': request.patient_id,
                    'p_service_id': request.service_id,
                    'p_reservation_date': request.reservation_date.isoformat(),
                    'p_start_time': request.start_time.isoformat(),
                    'p_end_time': request.end_time.isoformat(),
                    'p_doctor_resource_id': request.doctor_resource_id,
                    'p_room_resource_id': request.room_resource_id,
                    'p_appointment_type': request.appointment_type,
                    'p_reason': request.reason,
                    'p_notes': request.notes
                }
            ).execute()

            if not result.data:
                return ReservationResult(
                    success=False,
                    error="RPC function returned no data"
                )

            # RPC returns JSON, check success field
            response = result.data if isinstance(result.data, dict) else result.data[0]

            if response.get('success'):
                reservation_id = response.get('reservation_id')
                logger.info(f"Reservation created successfully: {reservation_id}")

                try:
                    await self._validate_policy_for_reservation(
                        policy_entry,
                        slot,
                        settings,
                        doctor_appointments,
                        patient_preferences,
                        hard_constraints
                    )
                except PolicyViolationError as exc:
                    logger.warning("Post-insert policy validation failed: %s", exc)

                if reservation_id and policy_entry.snapshot_id:
                    try:
                        self.supabase.table("resource_reservations")\
                            .update({
                                "policy_snapshot_id": str(policy_entry.snapshot_id),
                                "policy_version": policy_entry.version,
                                "policy_bundle_sha256": policy_entry.bundle_sha
                            })\
                            .eq("id", reservation_id)\
                            .execute()
                    except Exception as exc:
                        logger.warning("Failed to persist policy metadata: %s", exc)

                response = response if isinstance(response, dict) else {}
                if policy_entry.snapshot_id:
                    response.setdefault("policy_snapshot_id", str(policy_entry.snapshot_id))
                if policy_entry.version is not None:
                    response.setdefault("policy_version", policy_entry.version)
                response.setdefault("policy_bundle_sha256", policy_entry.bundle_sha)
                if policy_info.get("messages"):
                    response.setdefault("policy_messages", policy_info["messages"])

                return ReservationResult(
                    success=True,
                    reservation_id=reservation_id,
                    data=response
                )
            else:
                error = response.get('error', 'Unknown error')
                logger.error(f"Reservation creation failed: {error}")

                return ReservationResult(
                    success=False,
                    error=error
                )

        except Exception as e:
            logger.error(f"Failed to create reservation: {e}")
            return ReservationResult(
                success=False,
                error=str(e)
            )

    async def _validate_policy_for_reservation(
        self,
        policy_entry: ActivePolicy,
        slot: Dict[str, Any],
        settings: Dict[str, Any],
        doctor_appointments: Dict[UUID, List[Dict]],
        patient_preferences: Dict[str, Any],
        hard_constraints: HardConstraints
    ) -> Dict[str, Any]:
        policy = policy_entry.policy if policy_entry else None
        if not policy or not policy.hard_rules:
            return {"messages": []}

        context = build_slot_context(
            slot,
            settings,
            doctor_appointments,
            patient_preferences,
            hard_constraints
        )

        notes: List[str] = []

        for rule in policy.hard_rules:
            try:
                if not rule.matches(context):
                    continue
            except Exception as exc:
                logger.warning(f"Error evaluating reservation rule {rule.rule_id}: {exc}")
                continue

            effect = rule.effect_payload
            explanation = effect.get("explain_template") or rule.metadata.get("explain_template")

            if rule.effect_type == RuleEffectType.DENY:
                raise PolicyViolationError(explanation or "Reservation blocked by policy.", notes)

            if rule.effect_type == RuleEffectType.ESCALATE:
                raise PolicyViolationError(explanation or "Reservation requires manual approval.", notes)

            if rule.effect_type == RuleEffectType.REQUIRE_FIELD:
                required_field = effect.get("field")
                if required_field and not context_field_truthy(context, required_field):
                    raise PolicyViolationError(explanation or "Reservation missing required data.", notes)

            if rule.effect_type == RuleEffectType.LIMIT_OCCURRENCE:
                if explanation:
                    notes.append(explanation)

        return {"messages": notes}

    async def _get_clinic_hours(self, clinic_id: UUID) -> Dict[str, int]:
        try:
            result = self.supabase.table("sched_settings")\
                .select("open_hour, close_hour")\
                .eq("clinic_id", str(clinic_id))\
                .limit(1)\
                .execute()
        except Exception as exc:
            logger.warning(f"Failed to fetch clinic hours for {clinic_id}: {exc}")
            result = None

        if result and result.data:
            row = result.data[0]
            return {
                "open_hour": row.get("open_hour", 8),
                "close_hour": row.get("close_hour", 20)
            }

        return {"open_hour": 8, "close_hour": 20}

    async def _get_doctor_reservations(self, clinic_id: UUID) -> Dict[UUID, List[Dict]]:
        try:
            result = self.supabase.table("resource_reservations")\
                .select("doctor_resource_id, start_time, end_time")\
                .eq("clinic_id", str(clinic_id))\
                .neq("status", "cancelled")\
                .execute()
        except Exception as exc:
            logger.warning(f"Failed to fetch doctor reservations for {clinic_id}: {exc}")
            return {}

        appointments: Dict[UUID, List[Dict]] = {}
        for row in result.data or []:
            try:
                doctor_id = UUID(row["doctor_resource_id"])
            except (TypeError, ValueError, KeyError):
                continue
            appointments.setdefault(doctor_id, []).append(row)

        return appointments

    async def update_reservation(
        self,
        reservation_id: str,
        new_date: Optional[date] = None,
        new_start_time: Optional[time] = None,
        new_end_time: Optional[time] = None,
        new_doctor_resource_id: Optional[str] = None,
        new_room_resource_id: Optional[str] = None,
        reason: Optional[str] = None
    ) -> ReservationResult:
        """
        Update an existing reservation (reschedule or reassign resources).

        Args:
            reservation_id: UUID of reservation to update
            new_date: New reservation date (optional)
            new_start_time: New start time (optional)
            new_end_time: New end time (optional)
            new_doctor_resource_id: New doctor resource ID (optional)
            new_room_resource_id: New room resource ID (optional)
            reason: Reason for update (required for manual overrides)

        Returns:
            ReservationResult with success/failure status
        """
        try:
            logger.info(f"Updating reservation {reservation_id}")

            # Call RPC function
            result = self.supabase.rpc(
                'update_resource_reservation',
                {
                    'p_reservation_id': reservation_id,
                    'p_new_date': new_date.isoformat() if new_date else None,
                    'p_new_start_time': new_start_time.isoformat() if new_start_time else None,
                    'p_new_end_time': new_end_time.isoformat() if new_end_time else None,
                    'p_new_doctor_resource_id': new_doctor_resource_id,
                    'p_new_room_resource_id': new_room_resource_id,
                    'p_reason': reason
                }
            ).execute()

            if not result.data:
                return ReservationResult(
                    success=False,
                    error="RPC function returned no data"
                )

            response = result.data if isinstance(result.data, dict) else result.data[0]

            if response.get('success'):
                logger.info(f"Reservation updated successfully: {reservation_id}")
                return ReservationResult(
                    success=True,
                    reservation_id=reservation_id,
                    data=response
                )
            else:
                error = response.get('error', 'Unknown error')
                logger.error(f"Reservation update failed: {error}")
                return ReservationResult(
                    success=False,
                    error=error
                )

        except Exception as e:
            logger.error(f"Failed to update reservation: {e}")
            return ReservationResult(
                success=False,
                error=str(e)
            )

    async def cancel_reservation(
        self,
        reservation_id: str,
        reason: Optional[str] = None
    ) -> ReservationResult:
        """
        Cancel a reservation.

        Args:
            reservation_id: UUID of reservation to cancel
            reason: Cancellation reason (optional)

        Returns:
            ReservationResult with success/failure status
        """
        try:
            logger.info(f"Cancelling reservation {reservation_id}")

            # Call RPC function
            result = self.supabase.rpc(
                'cancel_resource_reservation',
                {
                    'p_reservation_id': reservation_id,
                    'p_reason': reason
                }
            ).execute()

            if not result.data:
                return ReservationResult(
                    success=False,
                    error="RPC function returned no data"
                )

            response = result.data if isinstance(result.data, dict) else result.data[0]

            if response.get('success'):
                logger.info(f"Reservation cancelled successfully: {reservation_id}")
                return ReservationResult(
                    success=True,
                    reservation_id=reservation_id,
                    data=response
                )
            else:
                error = response.get('error', 'Unknown error')
                logger.error(f"Reservation cancellation failed: {error}")
                return ReservationResult(
                    success=False,
                    error=error
                )

        except Exception as e:
            logger.error(f"Failed to cancel reservation: {e}")
            return ReservationResult(
                success=False,
                error=str(e)
            )

    async def get_reservation_details(
        self,
        reservation_id: str
    ) -> Optional[Dict[str, Any]]:
        """
        Get complete details of a reservation including all resources.

        Args:
            reservation_id: UUID of reservation

        Returns:
            Dictionary with reservation details or None if not found
        """
        try:
            logger.info(f"Getting details for reservation {reservation_id}")

            # Call RPC function
            result = self.supabase.rpc(
                'get_reservation_details',
                {
                    'p_reservation_id': reservation_id
                }
            ).execute()

            if not result.data:
                logger.warning(f"Reservation {reservation_id} not found")
                return None

            response = result.data if isinstance(result.data, dict) else result.data[0]

            if response.get('error'):
                logger.warning(f"Error getting reservation details: {response['error']}")
                return None

            return response

        except Exception as e:
            logger.error(f"Failed to get reservation details: {e}")
            return None

    async def get_reservations_by_date_range(
        self,
        clinic_id: str,
        start_date: date,
        end_date: date,
        patient_id: Optional[str] = None,
        status: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Get reservations within a date range with optional filtering.

        Args:
            clinic_id: Clinic UUID
            start_date: Start of date range
            end_date: End of date range
            patient_id: Optional patient UUID for filtering
            status: Optional status for filtering

        Returns:
            List of reservation dictionaries
        """
        try:
            logger.info(f"Getting reservations for clinic {clinic_id} from {start_date} to {end_date}")

            # Build query
            query = self.supabase.table('resource_reservations')\
                .select('*')\
                .eq('clinic_id', clinic_id)\
                .gte('reservation_date', start_date.isoformat())\
                .lte('reservation_date', end_date.isoformat())

            if patient_id:
                query = query.eq('patient_id', patient_id)

            if status:
                query = query.eq('status', status)

            result = query.order('reservation_date').order('start_time').execute()

            return result.data or []

        except Exception as e:
            logger.error(f"Failed to get reservations: {e}")
            return []
