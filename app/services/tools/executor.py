"""
Tool Executor with Enforcement

Tool execution with full enforcement via ToolStateGate.

IMPORTANT: prior_results must be passed in per-message,
NOT stored on the instance (which persists across messages).
"""

from typing import Any, Dict, List, Optional, Tuple
import logging
import json

from app.services.tools.base import ToolHandler
from app.services.tools.price_handler import PriceQueryHandler
from app.services.tools.clinic_info_handler import ClinicInfoHandler
from app.services.tools.availability_handler import AvailabilityHandler
from app.services.tools.booking_handler import BookingHandler, CancellationHandler, RescheduleHandler
from app.services.tools.history_handler import PreviousConversationsHandler, DetailedHistoryHandler
from app.services.tool_state_gate import ToolStateGate
from app.services.conversation_constraints import ConversationConstraints

logger = logging.getLogger(__name__)


class ToolExecutor:
    """
    Tool execution with enforcement.

    IMPORTANT: prior_results must be passed in per-message,
    NOT stored on the instance (which persists across messages).
    """

    def __init__(self):
        self.handlers: Dict[str, ToolHandler] = {}
        self.state_gate = ToolStateGate()
        # NOTE: NO self.prior_results here - it's per-message
        self._register_default_handlers()

    def _register_default_handlers(self):
        """Register all default tool handlers."""
        self.register_handler(PriceQueryHandler())
        self.register_handler(ClinicInfoHandler())
        self.register_handler(AvailabilityHandler())
        self.register_handler(BookingHandler())
        self.register_handler(CancellationHandler())
        self.register_handler(RescheduleHandler())
        self.register_handler(PreviousConversationsHandler())
        self.register_handler(DetailedHistoryHandler())

    def register_handler(self, handler: ToolHandler):
        """Register a new tool handler."""
        self.handlers[handler.tool_name] = handler

    async def execute(
        self,
        tool_call_id: str,
        tool_name: str,
        tool_args: Dict[str, Any],
        context: Dict[str, Any],
        constraints: Optional[ConversationConstraints] = None,
        current_state: str = "idle",
        tool_schemas: List[Dict] = None,
        prior_tool_results: Dict[str, Any] = None
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """
        Execute tool with full enforcement.

        Args:
            tool_call_id: Unique ID for this tool call
            tool_name: Name of tool to execute
            tool_args: Arguments for the tool
            context: Execution context (clinic_id, patient_id, etc.)
            constraints: Active conversation constraints
            current_state: Current FSM state value as string
            tool_schemas: List of tool schemas with x_meta for validation
            prior_tool_results: Results from prior tool calls in this message turn

        Returns:
            Tuple of (tool_result, updated_prior_results)
            Caller is responsible for tracking prior_results across calls.
        """
        logger.info(f"Executing tool: {tool_name} with args: {tool_args}")

        prior_results = prior_tool_results or {}

        # Validate with state, dependencies, and constraints
        is_valid, error_msg, suggested_fixes = self.state_gate.validate_tool_call(
            tool_name=tool_name,
            arguments=tool_args,
            constraints=constraints or ConversationConstraints(),
            current_state=current_state,
            tool_schemas=tool_schemas or [],
            prior_tool_results=prior_results
        )

        if not is_valid:
            logger.error(f"🚫 BLOCKED tool call: {error_msg}")
            return {
                "tool_call_id": tool_call_id,
                "role": "tool",
                "name": tool_name,
                "content": json.dumps({
                    "success": False,
                    "error": "enforcement_violation",
                    "message": error_msg,
                    "suggested_fixes": suggested_fixes
                })
            }, prior_results  # Return unchanged prior_results

        # Apply suggested fixes
        if suggested_fixes:
            logger.info(f"🔄 Applying suggested fixes: {suggested_fixes}")
            tool_args.update(suggested_fixes)

        # Check for calendar call budget (specific logic for check_availability)
        if tool_name == "check_availability":
            calendar_calls_made = context.get('calendar_calls_made', 0)
            max_calls = context.get('max_calendar_calls', 10)

            context['calendar_calls_made'] = calendar_calls_made + 1

            if calendar_calls_made >= max_calls:
                logger.error(f"🚨 BUDGET EXCEEDED: {calendar_calls_made + 1} calendar calls")
                return {
                    "tool_call_id": tool_call_id,
                    "role": "tool",
                    "name": tool_name,
                    "content": json.dumps({
                        "error": "too_many_calendar_queries",
                        "message": "I'm having trouble finding availability. Let me connect you with our team to help directly.",
                        "requires_escalation": True,
                        "calls_attempted": calendar_calls_made + 1
                    })
                }, prior_results

        handler = self.handlers.get(tool_name)
        if not handler:
            logger.error(f"Unknown tool: {tool_name}")
            return {
                "tool_call_id": tool_call_id,
                "role": "tool",
                "name": tool_name,
                "content": f"Error: Unknown tool '{tool_name}'"
            }, prior_results

        try:
            result_text = await handler.execute(tool_args, context)
        except Exception as e:
            logger.error(f"Error executing tool {tool_name}: {e}")
            result_text = json.dumps({
                "success": False,
                "error": "execution_error",
                "message": str(e)
            })

        # Update prior_results for dependency tracking
        try:
            prior_results[tool_name] = json.loads(result_text)
        except:
            prior_results[tool_name] = {"raw": result_text}

        return {
            "tool_call_id": tool_call_id,
            "role": "tool",
            "name": tool_name,
            "content": result_text
        }, prior_results  # Return updated prior_results
