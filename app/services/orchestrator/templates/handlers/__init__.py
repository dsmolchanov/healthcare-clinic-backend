"""Healthcare orchestrator handlers."""
from .emergency_detector import (
    EMERGENCY_KEYWORDS,
    is_emergency_message,
    get_emergency_response,
    check_audit_trail_for_emergency,
)
from .phi_handler import (
    PHI_PATTERNS,
    detect_phi_basic,
    redact_phi_basic,
    create_phi_check_audit_entry,
    create_phi_redact_audit_entry,
    apply_empathy_prefix,
)
from .guardrails import (
    EMERGENCY_PATTERNS,
    PHI_SSN_PATTERNS,
    ALL_TOOLS,
    ESCALATED_BLOCKED_TOOLS,
    detect_emergency,
    detect_phi_ssn,
    get_emergency_response_by_language,
    get_pii_response_by_language,
    calculate_allowed_tools,
    get_blocked_tools_for_state,
    create_guardrail_audit_entry,
    route_by_guardrail_action,
)

__all__ = [
    'EMERGENCY_KEYWORDS',
    'is_emergency_message',
    'get_emergency_response',
    'check_audit_trail_for_emergency',
    'PHI_PATTERNS',
    'detect_phi_basic',
    'redact_phi_basic',
    'create_phi_check_audit_entry',
    'create_phi_redact_audit_entry',
    'apply_empathy_prefix',
    'EMERGENCY_PATTERNS',
    'PHI_SSN_PATTERNS',
    'ALL_TOOLS',
    'ESCALATED_BLOCKED_TOOLS',
    'detect_emergency',
    'detect_phi_ssn',
    'get_emergency_response_by_language',
    'get_pii_response_by_language',
    'calculate_allowed_tools',
    'get_blocked_tools_for_state',
    'create_guardrail_audit_entry',
    'route_by_guardrail_action',
]
