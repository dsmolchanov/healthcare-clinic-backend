"""
Custom exceptions for the healthcare backend.
"""


class NoSlotsAvailableError(Exception):
    """Raised when no slots are available for scheduling."""

    def __init__(self, escalation_id: str, message: str = None):
        self.escalation_id = escalation_id
        self.message = message or f"No slots available. Escalation created: {escalation_id}"
        super().__init__(self.message)


class HoldExpiredError(Exception):
    """Raised when attempting to confirm an expired hold."""

    def __init__(self, hold_id: str = None):
        self.hold_id = hold_id
        message = f"Hold {hold_id} has expired" if hold_id else "Hold has expired"
        super().__init__(message)


class HoldNotFoundError(Exception):
    """Raised when a hold is not found."""

    def __init__(self, hold_id: str):
        self.hold_id = hold_id
        super().__init__(f"Hold {hold_id} not found")


class SlotNotAvailableError(Exception):
    """Raised when attempting to hold or book an unavailable slot."""

    def __init__(self, slot_id: str = None, message: str = None):
        self.slot_id = slot_id
        self.message = message or f"Slot {slot_id} is not available"
        super().__init__(self.message)


class EscalationNotFoundError(Exception):
    """Raised when an escalation is not found."""

    def __init__(self, escalation_id: str):
        self.escalation_id = escalation_id
        super().__init__(f"Escalation {escalation_id} not found")


class InvalidSchedulingRequestError(Exception):
    """Raised when a scheduling request is invalid."""

    def __init__(self, message: str):
        super().__init__(message)
