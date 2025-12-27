"""PHI (Protected Health Information) handling for HIPAA compliance."""
from typing import Dict, Any, Optional, Tuple, List
from datetime import datetime
import logging
import re

logger = logging.getLogger(__name__)

# Common PHI patterns for basic detection (when no middleware available)
PHI_PATTERNS = {
    'ssn': r'\b\d{3}-\d{2}-\d{4}\b',
    'phone': r'\b\d{3}[-.]?\d{3}[-.]?\d{4}\b',
    'email': r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b',
    'dob': r'\b\d{1,2}/\d{1,2}/\d{2,4}\b',
    'mrn': r'\bMRN[:\s]?\d+\b',
}


def detect_phi_basic(text: str) -> Tuple[bool, Dict[str, List[str]]]:
    """
    Basic PHI detection using regex patterns.

    This is a fallback when no PHI middleware is available.
    Production should use a proper PHI detection service.

    Args:
        text: Text to check for PHI

    Returns:
        Tuple of (contains_phi, dict of found tokens by type)
    """
    found_tokens = {}

    for phi_type, pattern in PHI_PATTERNS.items():
        matches = re.findall(pattern, text, re.IGNORECASE)
        if matches:
            found_tokens[phi_type] = matches

    return bool(found_tokens), found_tokens


def redact_phi_basic(text: str, tokens: Dict[str, List[str]]) -> str:
    """
    Basic PHI redaction by replacing tokens with placeholders.

    Args:
        text: Text containing PHI
        tokens: Dict of PHI tokens by type

    Returns:
        Text with PHI redacted
    """
    redacted = text

    for phi_type, values in tokens.items():
        placeholder = f"[{phi_type.upper()}_REDACTED]"
        for value in values:
            redacted = redacted.replace(value, placeholder)

    return redacted


def create_phi_check_audit_entry(contains_phi: bool) -> Dict[str, Any]:
    """Create audit trail entry for PHI check."""
    return {
        "node": "phi_check",
        "timestamp": datetime.utcnow().isoformat(),
        "contains_phi": contains_phi
    }


def create_phi_redact_audit_entry() -> Dict[str, Any]:
    """Create audit trail entry for PHI redaction."""
    return {
        "node": "phi_redact",
        "timestamp": datetime.utcnow().isoformat()
    }


def apply_empathy_prefix(response: str, empathy_prefix: Optional[str]) -> str:
    """
    Apply empathy prefix to response if set.

    Phase 6: Prepend empathy prefix before PHI redaction.

    Args:
        response: Original response
        empathy_prefix: Prefix to prepend (e.g., "I understand your concern. ")

    Returns:
        Response with prefix applied
    """
    if empathy_prefix and response:
        logger.info("[phi_handler] Prepended empathy prefix to response")
        return empathy_prefix + response
    return response
