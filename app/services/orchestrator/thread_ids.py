"""
Standardized thread ID and namespace generation.
Used across checkpointer, observability, and evals.

IMPORTANT: Thread IDs are session-scoped. The session_id changes on:
- Soft reset (4+ hour gap): New session, previous summary available
- Hard reset (72+ hour gap): New session, clean slate

The session_id is determined by SessionManager.check_and_manage_boundary()
BEFORE the graph is invoked. This ensures consistent thread_id across
the entire request lifecycle.
"""
from typing import Optional
import logging

logger = logging.getLogger(__name__)


def make_thread_id(clinic_id: str, patient_id: str, session_id: str) -> str:
    """
    Generate standardized thread ID for LangGraph.

    Args:
        clinic_id: Clinic identifier (tenant)
        patient_id: Patient/user identifier (can be phone number hash)
        session_id: Session UUID from session boundary management

    Returns:
        Thread ID in format: tenant-{clinic}:patient-{patient}:session-{session}

    Example:
        >>> make_thread_id("clinic123", "patient456", "abc-def-123")
        'tenant-clinic123:patient-patient456:session-abc-def-123'
    """
    return f"tenant-{clinic_id}:patient-{patient_id}:session-{session_id}"


def make_checkpoint_ns(clinic_id: str) -> str:
    """
    Generate checkpoint namespace for tenant isolation.

    All sessions for a clinic share the same namespace, enabling
    cross-session queries within tenant boundary.
    """
    return f"tenant-{clinic_id}"


def parse_thread_id(thread_id: str) -> dict:
    """
    Parse thread ID back to components.

    Returns:
        Dict with clinic_id, patient_id, session_id (or None if malformed)
    """
    if not thread_id or not isinstance(thread_id, str):
        logger.warning(f"Invalid thread_id: {thread_id}")
        return {"clinic_id": None, "patient_id": None, "session_id": None}

    parts = thread_id.split(":")

    # Validate expected format
    if len(parts) != 3:
        logger.warning(f"Malformed thread_id (expected 3 parts): {thread_id[:50]}...")
        return {"clinic_id": None, "patient_id": None, "session_id": None}

    try:
        return {
            "clinic_id": parts[0].replace("tenant-", "") if parts[0].startswith("tenant-") else None,
            "patient_id": parts[1].replace("patient-", "") if parts[1].startswith("patient-") else None,
            "session_id": parts[2].replace("session-", "") if parts[2].startswith("session-") else None,
        }
    except Exception as e:
        logger.error(f"Error parsing thread_id {thread_id[:50]}...: {e}")
        return {"clinic_id": None, "patient_id": None, "session_id": None}


def is_same_session(thread_id_a: str, thread_id_b: str) -> bool:
    """Check if two thread IDs belong to the same session."""
    parsed_a = parse_thread_id(thread_id_a)
    parsed_b = parse_thread_id(thread_id_b)
    return (
        parsed_a.get("session_id") is not None
        and parsed_a.get("session_id") == parsed_b.get("session_id")
    )


def is_same_patient(thread_id_a: str, thread_id_b: str) -> bool:
    """Check if two thread IDs belong to the same patient (across sessions)."""
    parsed_a = parse_thread_id(thread_id_a)
    parsed_b = parse_thread_id(thread_id_b)
    return (
        parsed_a.get("patient_id") is not None
        and parsed_a.get("patient_id") == parsed_b.get("patient_id")
        and parsed_a.get("clinic_id") == parsed_b.get("clinic_id")
    )


def is_same_tenant(thread_id_a: str, thread_id_b: str) -> bool:
    """Check if two thread IDs belong to the same tenant/clinic."""
    parsed_a = parse_thread_id(thread_id_a)
    parsed_b = parse_thread_id(thread_id_b)
    return (
        parsed_a.get("clinic_id") is not None
        and parsed_a.get("clinic_id") == parsed_b.get("clinic_id")
    )


def make_thread_id_from_phone(clinic_id: str, phone: str, session_id: str) -> str:
    """
    Create thread ID using phone number as patient identifier.

    Normalizes phone number to remove non-digits for consistency.
    """
    # Normalize phone - strip non-digits
    normalized_phone = "".join(c for c in phone if c.isdigit())
    return make_thread_id(clinic_id, normalized_phone, session_id)
