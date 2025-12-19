"""
PipelineContext - Mutable shared context passed through pipeline steps.

This is explicitly MUTABLE. Each step modifies the context in place.
This matches existing code patterns and is simpler than returning new copies.

Phase 2A of the Agentic Flow Architecture Refactor.
"""

from dataclasses import dataclass, field
from typing import Optional, Dict, List, Any, Set, Tuple, TYPE_CHECKING
from datetime import datetime

from app.services.conversation_constraints import ConversationConstraints

if TYPE_CHECKING:
    from app.domain.preferences.narrowing import NarrowingInstruction


@dataclass
class PipelineContext:
    """
    Mutable shared context passed through pipeline steps.

    NOTE: This is explicitly MUTABLE. Each step modifies the context
    in place. This matches the existing code patterns and is simpler
    than returning new copies.

    Attributes are organized by when they're set:
    - Request data: Set at initialization (immutable after init)
    - Session data: Set by SessionManagementStep
    - Hydrated context: Set by ContextHydrationStep
    - Routing data: Set by RoutingStep
    - Response data: Set by LLMGenerationStep / FastPathStep
    """

    # ===== Request data (immutable after init) =====
    message: str
    from_phone: str
    to_phone: str
    message_sid: str
    clinic_id: str  # May be organization_id, resolved by SessionStep
    clinic_name: str
    message_type: str = "text"
    media_url: Optional[str] = None
    channel: str = "whatsapp"
    profile_name: str = "Usuario"
    request_metadata: Dict[str, Any] = field(default_factory=dict)

    # ===== Resolved identifiers (set by SessionManagementStep) =====
    resolved_clinic_id: Optional[str] = None
    session_id: Optional[str] = None
    patient_id: Optional[str] = None
    correlation_id: Optional[str] = None

    # ===== Session state (set by SessionManagementStep) =====
    session: Optional[Dict[str, Any]] = None
    is_new_session: bool = False
    previous_session_summary: Optional[str] = None

    # ===== Flow state (set by routing/state steps) =====
    flow_state: str = "idle"  # FSM state value
    turn_status: str = "user_turn"  # user_turn, agent_action_pending, etc.
    last_agent_action: Optional[str] = None
    pending_since: Optional[str] = None

    # ===== Hydrated context (set by ContextHydrationStep) =====
    clinic_profile: Optional[Dict[str, Any]] = None
    patient_profile: Optional[Dict[str, Any]] = None
    patient_name: Optional[str] = None
    conversation_history: List[Dict[str, Any]] = field(default_factory=list)
    session_messages: List[Dict[str, str]] = field(default_factory=list)
    user_preferences: Dict[str, Any] = field(default_factory=dict)
    profile: Optional[Any] = None  # PatientProfile from ProfileManager
    conversation_state: Optional[Any] = None  # ConversationState from ProfileManager
    clinic_services: List[Any] = field(default_factory=list)
    clinic_doctors: List[Any] = field(default_factory=list)
    clinic_faqs: List[Any] = field(default_factory=list)

    # ===== Constraints (set by ConstraintEnforcementStep) =====
    constraints: Optional[ConversationConstraints] = None
    constraints_changed: bool = False

    # ===== Narrowing instruction (set by NarrowingStep) =====
    narrowing_instruction: Optional['NarrowingInstruction'] = None

    # ===== Routing decision (set by RoutingStep) =====
    lane: Optional[str] = None  # FAQ, PRICE, SERVICE_INFO, SCHEDULING, COMPLEX
    lane_metadata: Dict[str, Any] = field(default_factory=dict)

    # ===== Language (set by RoutingStep or LanguageStep) =====
    detected_language: str = "es"

    # ===== Response (set by FastPath/LLM steps) =====
    response: Optional[str] = None
    response_metadata: Dict[str, Any] = field(default_factory=dict)
    fast_path_handled: bool = False

    # ===== Additional context for LLM =====
    additional_context: str = ""
    knowledge_context: List[str] = field(default_factory=list)

    # ===== Extracted data =====
    extracted_first_name: Optional[str] = None
    extracted_last_name: Optional[str] = None

    # ===== Metrics =====
    start_time: float = field(default_factory=lambda: datetime.now().timestamp())
    step_timings: Dict[str, float] = field(default_factory=dict)
    llm_metrics: Dict[str, Any] = field(default_factory=dict)

    # ===== Flags =====
    should_escalate: bool = False
    escalation_reason: Optional[str] = None
    escalation_result: Optional[Dict[str, Any]] = None
    requires_followup: bool = False
    is_meta_reset: bool = False

    def snapshot(self) -> Dict[str, Any]:
        """
        Create a snapshot of current state for debugging.
        Call before risky steps to aid error diagnosis.
        """
        return {
            'session_id': self.session_id,
            'flow_state': self.flow_state,
            'turn_status': self.turn_status,
            'lane': self.lane,
            'has_response': self.response is not None,
            'detected_language': self.detected_language,
            'step_timings': dict(self.step_timings),
            'constraints_active': bool(
                self.constraints and (
                    self.constraints.excluded_doctors or
                    self.constraints.excluded_services
                )
            ),
        }

    @property
    def effective_clinic_id(self) -> str:
        """Return the resolved clinic ID or fallback to original."""
        return self.resolved_clinic_id or self.clinic_id

    @property
    def masked_phone(self) -> str:
        """Return masked phone number for PII-safe logging."""
        if len(self.from_phone) > 7:
            return f"{self.from_phone[:3]}***{self.from_phone[-4:]}"
        return f"{self.from_phone[:3]}***"
