from typing import Any, Dict, List, Optional
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
    Service to execute tools requested by the LLM.
    """
    def __init__(self):
        self.handlers: Dict[str, ToolHandler] = {}
        self.state_gate = ToolStateGate()
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
        constraints: Optional[ConversationConstraints] = None
    ) -> Dict[str, Any]:
        """
        Execute a tool call with validation and error handling.
        
        Returns:
            A dict representing the tool result message to be sent back to the LLM.
        """
        logger.info(f"Executing tool: {tool_name} with args: {tool_args}")

        # Validate tool call against constraints
        is_valid, error_msg, suggested_fixes = self.state_gate.validate_tool_call(
            tool_name=tool_name,
            arguments=tool_args,
            constraints=constraints or ConversationConstraints()
        )

        if not is_valid:
            logger.error(f"🚫 BLOCKED tool call: {error_msg}")
            return {
                "tool_call_id": tool_call_id,
                "role": "tool",
                "name": tool_name,
                "content": json.dumps({
                    "success": False,
                    "error": "constraint_violation",
                    "message": error_msg,
                    "suggested_fixes": suggested_fixes
                })
            }

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
                }

        handler = self.handlers.get(tool_name)
        if not handler:
            logger.error(f"Unknown tool: {tool_name}")
            return {
                "tool_call_id": tool_call_id,
                "role": "tool",
                "name": tool_name,
                "content": f"Error: Unknown tool '{tool_name}'"
            }

        try:
            result_text = await handler.execute(tool_args, context)
        except Exception as e:
            logger.error(f"Error executing tool {tool_name}: {e}")
            result_text = f"Error executing tool: {str(e)}"

        return {
            "tool_call_id": tool_call_id,
            "role": "tool",
            "name": tool_name,
            "content": result_text
        }
