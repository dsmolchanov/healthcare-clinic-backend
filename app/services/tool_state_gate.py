"""
Tool Call State Gate
Hard enforcement layer for tool calls.

LLM proposes → ToolStateGate enforces → Handler executes

IMPORTANT: This is the SOLE AUTHORITY on tool permissions.
All permission logic reads from x_meta in tool schemas.
"""

from typing import Dict, Any, Tuple, List, Optional
from app.services.conversation_constraints import ConversationConstraints
import logging

logger = logging.getLogger(__name__)


class ToolStateGate:
    """
    Hard enforcement layer for tool calls.

    LLM proposes → ToolStateGate enforces → Handler executes

    IMPORTANT: This is the SOLE AUTHORITY on tool permissions.
    All permission logic reads from x_meta in tool schemas.

    Validates:
    1. State gating (allowed_states from x_meta)
    2. Dependency enforcement (depends_on from x_meta)
    3. Prior result requirements (requires_prior_result from x_meta)
    4. Constraint validation (excluded doctors/services/time windows)
    5. Budget validation (max_calls_per_turn from x_meta)
    """

    def __init__(self):
        # Track tool call counts per turn (reset by caller per message)
        self._call_counts: Dict[str, int] = {}

    def reset_turn_counters(self):
        """Reset per-turn call counters. Call at start of each message."""
        self._call_counts = {}

    def validate_tool_call(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        constraints: ConversationConstraints,
        current_state: str = "idle",
        tool_schemas: List[Dict] = None,
        prior_tool_results: Dict[str, Any] = None
    ) -> Tuple[bool, Optional[str], Optional[Dict[str, Any]]]:
        """
        Validate tool call against all enforcement rules.

        Args:
            tool_name: Name of tool being called
            arguments: Tool parameters
            constraints: Active conversation constraints
            current_state: Current FSM state value as string
            tool_schemas: List of tool schemas with x_meta
            prior_tool_results: Results from prior tool calls in this message

        Returns:
            Tuple of (is_valid, error_message, suggested_fixes)
            - is_valid: True if call passes validation
            - error_message: Explanation if invalid
            - suggested_fixes: Dict of parameter corrections
        """
        errors = []
        fixes = {}

        # Get tool metadata from schema (SINGLE SOURCE OF TRUTH)
        tool_meta = self._get_tool_meta(tool_name, tool_schemas or [])

        # 1. State validation (reads from x_meta.allowed_states)
        state_result = self._validate_state(tool_name, current_state, tool_meta)
        if state_result[0] is False:
            errors.append(state_result[1])

        # 2. Dependency validation (reads from x_meta.depends_on)
        dep_result = self._validate_dependencies(
            tool_name, arguments, tool_meta, prior_tool_results or {}
        )
        if dep_result[0] is False:
            errors.append(dep_result[1])
        if dep_result[2]:
            fixes.update(dep_result[2])

        # 3. Constraint validation (existing logic for exclusions)
        if tool_name in ('check_availability', 'book_appointment', 'reschedule_appointment'):
            constraint_result = self._validate_scheduling_constraints(arguments, constraints)
            if constraint_result[0] is False:
                errors.append(constraint_result[1])
            if constraint_result[2]:
                fixes.update(constraint_result[2])

        # 4. Budget validation (reads from x_meta.max_calls_per_turn)
        budget_result = self._validate_budget(tool_name, tool_meta)
        if budget_result[0] is False:
            errors.append(budget_result[1])

        # Increment call count for this tool
        self._call_counts[tool_name] = self._call_counts.get(tool_name, 0) + 1

        # Return result
        if errors:
            return False, "; ".join(errors), fixes if fixes else None
        elif fixes:
            # No hard errors, but suggest fixes
            logger.info(f"✅ Validation passed with suggested fixes: {fixes}")
            return True, None, fixes
        else:
            logger.info(f"✅ Validation passed with no issues for {tool_name}")
            return True, None, None

    def _get_tool_meta(self, tool_name: str, schemas: List[Dict]) -> Optional[Dict]:
        """Extract x_meta from tool schema."""
        for schema in schemas:
            if schema.get("function", {}).get("name") == tool_name:
                return schema.get("x_meta", {})
        return None

    def _validate_state(
        self,
        tool_name: str,
        current_state: str,
        tool_meta: Optional[Dict]
    ) -> Tuple[bool, Optional[str]]:
        """Validate tool is allowed in current state."""
        if not tool_meta:
            # No metadata means no state restrictions
            return True, None

        allowed_states = tool_meta.get("allowed_states", [])
        if not allowed_states:
            # Empty list means allowed everywhere
            return True, None

        if current_state not in allowed_states:
            return False, (
                f"❌ Tool '{tool_name}' not allowed in state '{current_state}'. "
                f"Allowed states: {allowed_states}"
            )

        return True, None

    def _validate_dependencies(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        tool_meta: Optional[Dict],
        prior_tool_results: Dict[str, Any]
    ) -> Tuple[bool, Optional[str], Optional[Dict]]:
        """Validate tool dependencies are satisfied."""
        if not tool_meta:
            return True, None, None

        depends_on = tool_meta.get("depends_on", [])
        required_results = tool_meta.get("requires_prior_result", {})
        fixes = {}

        # Check each dependency was called
        for dep in depends_on:
            if dep not in prior_tool_results:
                return False, (
                    f"❌ Tool '{tool_name}' requires '{dep}' to be called first "
                    f"in the same message turn"
                ), None

        # Check required fields from prior results
        for dep, fields in required_results.items():
            if dep not in prior_tool_results:
                # Already caught above if in depends_on
                continue

            prior = prior_tool_results.get(dep, {})
            if isinstance(prior, str):
                # Try to parse as JSON
                try:
                    import json
                    prior = json.loads(prior)
                except:
                    prior = {}

            for field in fields:
                if field not in arguments:
                    # Field missing from arguments, try to auto-fill from prior result
                    if isinstance(prior, dict) and field in prior:
                        fixes[field] = prior[field]
                        logger.info(
                            f"🔄 Auto-filling '{field}' from {dep} result: {prior[field]}"
                        )
                    else:
                        # Check if prior has a 'slots' or 'availability' structure
                        if isinstance(prior, dict):
                            # Common patterns: slots array with datetime, doctor_id, service_id
                            slots = prior.get('slots', prior.get('availability', []))
                            if isinstance(slots, list) and len(slots) > 0:
                                first_slot = slots[0]
                                if isinstance(first_slot, dict) and field in first_slot:
                                    fixes[field] = first_slot[field]
                                    logger.info(
                                        f"🔄 Auto-filling '{field}' from {dep} first slot: {first_slot[field]}"
                                    )

        return True, None, fixes if fixes else None

    def _validate_scheduling_constraints(
        self,
        arguments: Dict[str, Any],
        constraints: ConversationConstraints
    ) -> Tuple[bool, Optional[str], Optional[Dict[str, Any]]]:
        """Validate scheduling tools against constraints."""
        errors = []
        fixes = {}

        # 1. Check service against constraints
        service_name = arguments.get('service_name', '').lower()
        service_id = arguments.get('service_id')

        # Validate against exclusions
        if constraints.should_exclude_service(service_name, service_id):
            errors.append(
                f"❌ Service '{service_name}' is EXCLUDED by user. "
                f"User explicitly said to forget about this service."
            )

            # Suggest correction
            if constraints.desired_service:
                fixes['service_name'] = constraints.desired_service
                logger.warning(
                    f"🔄 Rewriting service: {service_name} → {constraints.desired_service}"
                )

        # Suggest desired service if set - SOFT SUGGESTION (not blocking)
        elif constraints.desired_service and service_name:
            if constraints.desired_service.lower() not in service_name:
                # Suggest correction but don't block
                fixes['service_name'] = constraints.desired_service
                logger.warning(
                    f"⚠️  Service mismatch: '{service_name}' != '{constraints.desired_service}' (suggesting fix)"
                )

        # 2. Check doctor against constraints
        doctor_name = arguments.get('doctor_name', '').lower()
        doctor_id = arguments.get('doctor_id')

        # Validate against exclusions
        if constraints.should_exclude_doctor(doctor_name, doctor_id):
            errors.append(
                f"❌ Doctor '{doctor_name or doctor_id}' is EXCLUDED by user. "
                f"User explicitly said to forget about this doctor."
            )

            # Suggest correction
            if constraints.desired_doctor:
                fixes['doctor_name'] = constraints.desired_doctor
                logger.warning(
                    f"🔄 Rewriting doctor: {doctor_name} → {constraints.desired_doctor}"
                )

        # 3. Check date against time window
        preferred_date = arguments.get('preferred_date')

        if constraints.time_window_start and preferred_date:
            if preferred_date < constraints.time_window_start or preferred_date > constraints.time_window_end:
                logger.warning(
                    f"⚠️  Date {preferred_date} outside user's window "
                    f"{constraints.time_window_display}"
                )
                fixes['preferred_date'] = constraints.time_window_start

        # Return validation result
        if errors:
            return False, "; ".join(errors), fixes if fixes else None
        elif fixes:
            return True, None, fixes
        else:
            return True, None, None

    def _validate_budget(
        self,
        tool_name: str,
        tool_meta: Optional[Dict]
    ) -> Tuple[bool, Optional[str]]:
        """Validate tool call against per-turn budget."""
        if not tool_meta:
            return True, None

        max_calls = tool_meta.get("max_calls_per_turn")
        if max_calls is None:
            return True, None

        current_count = self._call_counts.get(tool_name, 0)
        if current_count >= max_calls:
            return False, (
                f"❌ Tool '{tool_name}' budget exceeded: "
                f"{current_count + 1} calls (max: {max_calls} per turn)"
            )

        return True, None
