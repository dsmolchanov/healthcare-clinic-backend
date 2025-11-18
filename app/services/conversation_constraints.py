"""
Conversation Constraints Management
Manages constraints that MUST be enforced in all tool calls (guardrails, not hints)
"""

from typing import Optional, List, Dict, Any, Set
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
import json
import logging

logger = logging.getLogger(__name__)


@dataclass
class ConversationConstraints:
    """
    Conversation constraints that MUST be enforced in all tool calls.

    These are guardrails, not hints. They override all other context.
    """
    # Current intent
    desired_service: Optional[str] = None  # "виниры" (veneers)
    desired_service_id: Optional[str] = None  # UUID if known
    desired_doctor: Optional[str] = None  # "Андреа Ревилья"
    desired_doctor_id: Optional[str] = None  # UUID if known

    # Exclusions (NEVER suggest these)
    excluded_doctors: Set[str] = None  # {"Дан", "Dana"}
    excluded_doctor_ids: Set[str] = None  # {UUID}
    excluded_services: Set[str] = None  # {"пломба", "filling"}
    excluded_service_ids: Set[str] = None  # {UUID}

    # Time constraints
    time_window_start: Optional[str] = None  # "2025-11-24" (ISO date)
    time_window_end: Optional[str] = None  # "2025-11-30"
    time_window_display: Optional[str] = None  # "24–30 ноября"

    # Session tracking
    session_id: Optional[str] = None
    is_fresh_session: bool = False  # True if new session after boundary
    previous_session_id: Optional[str] = None

    # Metadata
    last_updated: Optional[datetime] = None
    confidence_source: str = "user_explicit"  # user_explicit | llm_inferred

    def __post_init__(self):
        # Initialize sets if None
        if self.excluded_doctors is None:
            self.excluded_doctors = set()
        if self.excluded_doctor_ids is None:
            self.excluded_doctor_ids = set()
        if self.excluded_services is None:
            self.excluded_services = set()
        if self.excluded_service_ids is None:
            self.excluded_service_ids = set()
        if self.last_updated is None:
            self.last_updated = datetime.utcnow()

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dict for Redis storage"""
        data = asdict(self)
        # Convert sets to lists for JSON serialization
        data['excluded_doctors'] = list(self.excluded_doctors)
        data['excluded_doctor_ids'] = list(self.excluded_doctor_ids)
        data['excluded_services'] = list(self.excluded_services)
        data['excluded_service_ids'] = list(self.excluded_service_ids)
        data['last_updated'] = self.last_updated.isoformat() if self.last_updated else None
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'ConversationConstraints':
        """Deserialize from dict"""
        # Convert lists back to sets (handles both None and list cases)
        if 'excluded_doctors' in data:
            data['excluded_doctors'] = set(data['excluded_doctors']) if data['excluded_doctors'] else set()
        if 'excluded_doctor_ids' in data:
            data['excluded_doctor_ids'] = set(data['excluded_doctor_ids']) if data['excluded_doctor_ids'] else set()
        if 'excluded_services' in data:
            data['excluded_services'] = set(data['excluded_services']) if data['excluded_services'] else set()
        if 'excluded_service_ids' in data:
            data['excluded_service_ids'] = set(data['excluded_service_ids']) if data['excluded_service_ids'] else set()
        if 'last_updated' in data and data['last_updated']:
            data['last_updated'] = datetime.fromisoformat(data['last_updated'])
        return cls(**data)

    def should_exclude_doctor(self, doctor_name: Optional[str], doctor_id: Optional[str]) -> bool:
        """Check if doctor should be excluded"""
        if doctor_name and doctor_name.lower() in {d.lower() for d in self.excluded_doctors}:
            return True
        if doctor_id and doctor_id in self.excluded_doctor_ids:
            return True
        return False

    def should_exclude_service(self, service_name: Optional[str], service_id: Optional[str]) -> bool:
        """Check if service should be excluded"""
        if service_name and service_name.lower() in {s.lower() for s in self.excluded_services}:
            return True
        if service_id and service_id in self.excluded_service_ids:
            return True
        return False


class ConstraintsManager:
    """Manages conversation constraints in Redis"""

    def __init__(self, redis_client):
        self.redis = redis_client
        self.default_ttl = 3600  # 1 hour, same as session TTL

    def _make_key(self, session_id: str) -> str:
        """Generate Redis key for constraints"""
        return f"constraints:{session_id}"

    async def get_constraints(self, session_id: str) -> ConversationConstraints:
        """Get constraints for session (returns empty if not found)"""
        key = self._make_key(session_id)
        data = self.redis.get(key)

        if not data:
            return ConversationConstraints()

        try:
            parsed = json.loads(data)
            return ConversationConstraints.from_dict(parsed)
        except Exception as e:
            logger.error(f"Failed to parse constraints for {session_id}: {e}")
            return ConversationConstraints()

    async def set_constraints(self, session_id: str, constraints: ConversationConstraints) -> bool:
        """Store constraints with TTL"""
        key = self._make_key(session_id)
        constraints.last_updated = datetime.utcnow()

        try:
            data = json.dumps(constraints.to_dict())
            self.redis.setex(key, self.default_ttl, data)
            logger.info(f"✅ Updated constraints for {session_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to store constraints for {session_id}: {e}")
            return False

    async def update_constraints(
        self,
        session_id: str,
        desired_service: Optional[str] = None,
        desired_doctor: Optional[str] = None,
        exclude_doctor: Optional[str] = None,
        exclude_service: Optional[str] = None,
        time_window: Optional[tuple[str, str, str]] = None  # (start, end, display)
    ) -> ConversationConstraints:
        """Update constraints atomically"""
        constraints = await self.get_constraints(session_id)

        # Update desired items (these REPLACE previous values)
        if desired_service is not None:
            constraints.desired_service = desired_service
        if desired_doctor is not None:
            constraints.desired_doctor = desired_doctor

        # Add to exclusions (these ACCUMULATE)
        if exclude_doctor is not None:
            constraints.excluded_doctors.add(exclude_doctor)
        if exclude_service is not None:
            constraints.excluded_services.add(exclude_service)

        # Update time window
        if time_window is not None:
            constraints.time_window_start, constraints.time_window_end, constraints.time_window_display = time_window

        await self.set_constraints(session_id, constraints)
        return constraints

    async def clear_constraints(self, session_id: str) -> bool:
        """Clear all constraints for session"""
        key = self._make_key(session_id)
        self.redis.delete(key)
        logger.info(f"🗑️ Cleared constraints for {session_id}")
        return True
