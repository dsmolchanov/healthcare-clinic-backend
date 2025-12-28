"""State definitions for FSM-based orchestration.

Each flow (booking, pricing, cancel) has its own state class
with its own stages. States are immutable - use dataclasses.replace()
to create new states.
"""

from dataclasses import dataclass, field, replace
from typing import Optional, List, Dict, Any, TYPE_CHECKING
from enum import Enum, auto

if TYPE_CHECKING:
    from .types import RouterOutput


class BookingStage(Enum):
    """Stages in the booking flow."""
    INTENT = auto()           # Initial state
    COLLECT_SERVICE = auto()  # Need service type
    COLLECT_DATE = auto()     # Need date/time
    CHECK_AVAILABILITY = auto()  # Calling check_availability
    PRESENT_SLOTS = auto()    # Showing available slots
    AWAIT_SLOT_SELECTION = auto()  # Waiting for user to pick
    COLLECT_PATIENT_INFO = auto()  # Need name/phone
    AWAIT_CONFIRM = auto()    # Confirming booking
    BOOK = auto()             # Calling book_appointment
    COMPLETE = auto()         # Done


class PricingStage(Enum):
    """Stages in the pricing flow."""
    QUERY = auto()
    RESPOND = auto()
    COMPLETE = auto()


class CancelStage(Enum):
    """Stages in the cancellation flow."""
    IDENTIFY_APPOINTMENT = auto()
    CONFIRM_CANCEL = auto()
    CANCEL = auto()
    COMPLETE = auto()


@dataclass
class BookingState:
    """State for booking flow - updated via dataclasses.replace().

    IMPORTANT: Never mutate fields directly. Always use:
        new_state = replace(state, field=new_value)

    Attributes:
        stage: Current stage in the booking flow
        service_type: Type of service being booked
        target_date: Target date (raw string from user)
        time_of_day: Preferred time of day
        doctor_name: Requested doctor name
        doctor_id: Resolved doctor ID
        patient_name: Patient's name
        patient_phone: Patient's phone number
        patient_id: Resolved patient ID
        available_slots: List of available time slots
        selected_slot: The slot the user selected
        appointment_id: ID of the booked appointment
        confirmation_message: Confirmation message from booking
        has_pain: Whether user mentioned pain (for empathy)
        language: Detected language code
        clarification_count: Number of times we've asked for clarification
        last_tool_call_id: For idempotency - prevent duplicate tool calls
    """
    stage: BookingStage = BookingStage.INTENT

    # Extracted info
    service_type: Optional[str] = None
    target_date: Optional[str] = None
    time_of_day: Optional[str] = None
    doctor_name: Optional[str] = None
    doctor_id: Optional[str] = None

    # Patient info
    patient_name: Optional[str] = None
    patient_phone: Optional[str] = None
    patient_id: Optional[str] = None

    # Availability results
    available_slots: List[Dict[str, Any]] = field(default_factory=list)
    selected_slot: Optional[Dict[str, Any]] = None

    # Booking result
    appointment_id: Optional[str] = None
    confirmation_message: Optional[str] = None

    # Flow control
    has_pain: bool = False
    language: str = "en"
    clarification_count: int = 0

    # Idempotency - prevent duplicate bookings
    last_tool_call_id: Optional[str] = None  # Track to skip duplicate tool calls

    def __post_init__(self) -> None:
        """Validate state consistency."""
        # Ensure we don't have appointment_id without being COMPLETE
        if self.appointment_id and self.stage != BookingStage.COMPLETE:
            # Auto-correct to COMPLETE stage
            object.__setattr__(self, 'stage', BookingStage.COMPLETE)

    def merge_router_output(self, router: 'RouterOutput') -> 'BookingState':
        """Merge new info from router without overwriting existing values.

        IMPORTANT: Existing values are preserved. This prevents accidental
        overwrites when the router extracts partial info. For intentional
        changes (like backtracking), use explicit replace() in the FSM logic.

        Args:
            router: RouterOutput with newly extracted entities

        Returns:
            New BookingState with merged values (existing preserved, gaps filled)
        """
        return replace(
            self,
            # Prioritize Self (existing) -> Fallback to Router (new)
            service_type=self.service_type or router.service_type,
            target_date=self.target_date or router.target_date,
            time_of_day=self.time_of_day or router.time_of_day,
            doctor_name=self.doctor_name or router.doctor_name,
            patient_name=self.patient_name or router.patient_name,
            patient_phone=self.patient_phone or router.patient_phone,
            # Pain is additive - once mentioned, stays true
            has_pain=router.has_pain or self.has_pain,
        )


@dataclass
class PricingState:
    """State for pricing queries.

    Attributes:
        stage: Current stage in the pricing flow
        query: The user's pricing query
        results: List of pricing results
        language: Detected language code
    """
    stage: PricingStage = PricingStage.QUERY
    query: Optional[str] = None
    results: List[Dict[str, Any]] = field(default_factory=list)
    language: str = "en"


@dataclass
class CancelState:
    """State for cancellation flow.

    Attributes:
        stage: Current stage in the cancellation flow
        appointment_id: ID of appointment to cancel
        patient_phone: Patient's phone for verification
        language: Detected language code
    """
    stage: CancelStage = CancelStage.IDENTIFY_APPOINTMENT
    appointment_id: Optional[str] = None
    patient_phone: Optional[str] = None
    language: str = "en"
