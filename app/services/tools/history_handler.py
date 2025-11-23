from typing import Any, Dict
import logging
from app.services.tools.base import ToolHandler
from app.tools import conversation_history_tools

logger = logging.getLogger(__name__)

class PreviousConversationsHandler(ToolHandler):
    @property
    def tool_name(self) -> str:
        return "get_previous_conversations_summary"

    async def execute(self, args: Dict[str, Any], context: Dict[str, Any]) -> str:
        # Set context for history tools
        conversation_history_tools.set_context(
            phone_number=context.get('phone_number'),
            clinic_id=context.get('clinic_id')
        )
        
        result = await conversation_history_tools.get_previous_conversations_summary(**args)

        if result.get('found'):
            # Format summaries for LLM
            summaries = result['summaries']
            formatted_summaries = []
            for summary in summaries:
                date_str = summary['date'][:10]  # Just the date part
                formatted_summaries.append(
                    f"• {date_str}: {summary['summary']}"
                )

            result_text = f"Found {len(summaries)} previous conversation(s):\n\n" + "\n\n".join(formatted_summaries)
        else:
            result_text = result['message']

        logger.info(f"✅ get_previous_conversations_summary returned: {result.get('count', 0)} results")
        return result_text


class DetailedHistoryHandler(ToolHandler):
    @property
    def tool_name(self) -> str:
        return "search_detailed_conversation_history"

    async def execute(self, args: Dict[str, Any], context: Dict[str, Any]) -> str:
        # Set context for history tools
        conversation_history_tools.set_context(
            phone_number=context.get('phone_number'),
            clinic_id=context.get('clinic_id')
        )

        result = await conversation_history_tools.search_detailed_conversation_history(**args)

        if result.get('found'):
            # Format messages for LLM
            messages = result['messages']
            formatted_messages = []
            for msg in messages:
                formatted_messages.append(
                    f"[{msg['date']} {msg['time']}] {msg['role'].upper()}: {msg['content']}"
                )

            result_text = f"Found {result['count']} message(s)"
            if result.get('has_more'):
                result_text += f" (showing first {result['count']} of {result['total']} total)"
            result_text += ":\n\n" + "\n\n".join(formatted_messages)
        else:
            result_text = result['message']

        logger.info(f"✅ search_detailed_conversation_history returned: {result.get('count', 0)} results")
        return result_text
