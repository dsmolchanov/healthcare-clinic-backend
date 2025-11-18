"""
Tool Call State Gate
Pre-tool validation that enforces conversation constraints
"""

from typing import Dict, Any, Tuple, List, Optional
from app.services.conversation_constraints import ConversationConstraints
import logging

logger = logging.getLogger(__name__)


class ToolStateGate:
    """
    Pre-tool validation gate that enforces conversation constraints.

    Validates tool parameters against Constraints Block BEFORE execution.
    Can BLOCK invalid calls or REWRITE parameters to match constraints.
    """

    def validate_tool_call(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        constraints: ConversationConstraints
    ) -> Tuple[bool, Optional[str], Optional[Dict[str, Any]]]:
        """
        Validate tool call against constraints.

        Args:
            tool_name: Name of tool being called
            arguments: Tool parameters
            constraints: Active conversation constraints

        Returns:
            Tuple of (is_valid, error_message, suggested_fixes)
            - is_valid: True if call passes validation
            - error_message: Explanation if invalid
            - suggested_fixes: Dict of parameter corrections
        """

        if tool_name in ('check_availability', 'book_appointment', 'reschedule_appointment'):
            return self._validate_scheduling_tool(arguments, constraints)

        # Other tools pass by default
        return True, None, None

    def _validate_scheduling_tool(
        self,
        arguments: Dict[str, Any],
        constraints: ConversationConstraints
    ) -> Tuple[bool, Optional[str], Optional[Dict[str, Any]]]:
        """Validate scheduling tools against constraints"""

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

        # Enforce desired service if set - HARD BLOCKING
        elif constraints.desired_service and service_name:
            if constraints.desired_service.lower() not in service_name:
                errors.append(
                    f"❌ Service mismatch: tool uses '{service_name}' "
                    f"but user wants '{constraints.desired_service}'. BLOCKING."
                )
                fixes['service_name'] = constraints.desired_service
                logger.error(
                    f"🚫 BLOCKING service mismatch: '{service_name}' != '{constraints.desired_service}'"
                )

        # 2. Check doctor against constraints
        doctor_name = arguments.get('doctor_name', '').lower()
        doctor_id = arguments.get('doctor_id')

        # Validate against exclusions
        if constraints.should_exclude_doctor(doctor_name, doctor_id):
            errors.append(
                f"❌ Doctor '{doctor_name}' is EXCLUDED by user. "
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
            # No hard errors, but suggest fixes
            logger.info(f"✅ Validation passed with suggested fixes: {fixes}")
            return True, None, fixes
        else:
            logger.info(f"✅ Validation passed with no issues")
            return True, None, None
