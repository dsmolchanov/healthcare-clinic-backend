"""Healthcare conversation state models."""
from typing import Optional, Dict, Any, List, Annotated
from ...base_langgraph import BaseConversationState, last_value


class HealthcareConversationState(BaseConversationState):
    """
    Healthcare-specific conversation state.

    Extends BaseConversationState with healthcare/PHI fields.
    All fields use Annotated with last_value reducer for LangGraph checkpointing.
    """
    # PHI-related fields
    contains_phi: Annotated[bool, last_value]
    phi_tokens: Annotated[Optional[Dict[str, str]], last_value]
    de_identified_message: Annotated[Optional[str], last_value]

    # Appointment fields
    appointment_type: Annotated[Optional[str], last_value]
    preferred_date: Annotated[Optional[str], last_value]
    preferred_time: Annotated[Optional[str], last_value]
    doctor_id: Annotated[Optional[str], last_value]

    # Patient context
    patient_id: Annotated[Optional[str], last_value]
    patient_name: Annotated[Optional[str], last_value]
    insurance_verified: Annotated[bool, last_value]

    # Supervisor routing (Phase 3)
    flow_state: Annotated[str, last_value]  # FlowState.value
    active_task: Annotated[Optional[Dict[str, Any]], last_value]  # BookingTask as dict
    next_agent: Annotated[Optional[str], last_value]  # Supervisor routing decision

    # Phase 2: Guardrail fields
    is_emergency: Annotated[bool, last_value]
    phi_detected: Annotated[bool, last_value]
    allowed_tools: Annotated[List[str], last_value]
    blocked_tools: Annotated[List[str], last_value]
    guardrail_action: Annotated[Optional[str], last_value]  # 'escalate', 'restrict', 'allow'
    escalation_reason: Annotated[Optional[str], last_value]

    # Phase 2: Language detection
    detected_language: Annotated[str, last_value]

    # Phase 2: Context hydration
    context_hydrated: Annotated[bool, last_value]
    previous_session_summary: Annotated[Optional[Dict[str, Any]], last_value]

    # Phase 2: Fast path
    fast_path: Annotated[bool, last_value]
    lane: Annotated[Optional[str], last_value]

    # Phase 2: Plan-then-Execute
    action_plan: Annotated[Optional[Dict[str, Any]], last_value]
    plan_results: Annotated[Optional[Dict[str, Any]], last_value]
    plan_completed_steps: Annotated[List[str], last_value]
    plan_execution_error: Annotated[Optional[str], last_value]
    plan_failed_step: Annotated[Optional[str], last_value]
    plan_needs_replanning: Annotated[bool, last_value]

    # Phase 2: Action Proposal (HITL confirmation)
    action_proposal: Annotated[Optional[Dict[str, Any]], last_value]
    awaiting_confirmation: Annotated[bool, last_value]
    pending_action: Annotated[Optional[Dict[str, Any]], last_value]
    pending_action_timestamp: Annotated[Optional[str], last_value]
    pending_action_expired: Annotated[bool, last_value]
    user_confirmed: Annotated[bool, last_value]
    proposal_verified: Annotated[bool, last_value]
    verification_error: Annotated[Optional[str], last_value]

    # Phase 4: Routing control (booking flow fix)
    static_info_skipped_due_to_scheduling: Annotated[Optional[bool], last_value]
    force_reroute_to: Annotated[Optional[str], last_value]
    supervisor_overrode_to_scheduling: Annotated[Optional[bool], last_value]
    supervisor_forced_scheduling: Annotated[Optional[bool], last_value]

    # Phase 4: Extraction fields (booking flow fix)
    booking_intent: Annotated[Optional[str], last_value]
    extracted_booking_info: Annotated[Optional[Dict[str, Any]], last_value]
    preferred_date_raw: Annotated[Optional[str], last_value]
    doctor_preference: Annotated[Optional[str], last_value]
    is_urgent: Annotated[Optional[bool], last_value]
    patient_phone: Annotated[Optional[str], last_value]

    # Phase 4: Clarification flow (booking flow fix)
    awaiting_patient_identification: Annotated[Optional[bool], last_value]
    awaiting_datetime: Annotated[Optional[bool], last_value]
    clarification_count: Annotated[Optional[int], last_value]  # Track >2 → escalate
    needs_human_escalation: Annotated[Optional[bool], last_value]

    # Phase 4: Executor debugging & silent failure prevention
    tools_actually_called: Annotated[Optional[List[str]], last_value]  # Track internal tool execution
    tools_failed: Annotated[Optional[List[Dict[str, Any]]], last_value]  # Track failed tool calls with details
    executor_validation_errors: Annotated[Optional[List[str]], last_value]  # Track validation errors
    planner_validation_errors: Annotated[Optional[List[str]], last_value]  # Track planner validation errors
    booking_blocked_no_availability_check: Annotated[Optional[bool], last_value]  # Strict mode flag
    booking_blocked_no_verification: Annotated[Optional[bool], last_value]  # Strict mode flag
    preferred_date_iso: Annotated[Optional[str], last_value]  # Resolved ISO datetime from natural language
