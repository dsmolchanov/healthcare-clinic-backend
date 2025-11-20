"""Tools for accessing conversation history."""

from typing import Dict, Any
from app.services.summary_search_service import SummarySearchService
from app.services.full_history_search_service import FullHistorySearchService
import logging

logger = logging.getLogger(__name__)

summary_search = SummarySearchService()
full_history_search = FullHistorySearchService()

# Context variables (set by processor)
_current_phone_number = None
_current_clinic_id = None


def set_context(phone_number: str, clinic_id: str):
    """Set current request context (called by processor)."""
    global _current_phone_number, _current_clinic_id
    _current_phone_number = phone_number
    _current_clinic_id = clinic_id


def get_current_phone_number() -> str:
    """Get phone number from context."""
    if _current_phone_number is None:
        raise RuntimeError("Phone number not set in context")
    return _current_phone_number


def get_current_clinic_id() -> str:
    """Get clinic ID from context."""
    if _current_clinic_id is None:
        raise RuntimeError("Clinic ID not set in context")
    return _current_clinic_id


async def get_previous_conversations_summary(
    query: str = "",
    days_back: int = 90
) -> Dict[str, Any]:
    """
    Search summaries of user's previous conversations with the clinic.

    Use this when the user asks about past interactions, like:
    - "What did we discuss last week?"
    - "Did I already book an appointment?"
    - "What did the doctor say about my allergies?"

    Args:
        query: Search keywords (optional, searches all summaries if empty)
        days_back: How many days back to search (default 90)

    Returns:
        List of conversation summaries with dates and key information
    """

    # Get phone and clinic from context (injected by processor)
    phone_number = get_current_phone_number()
    clinic_id = get_current_clinic_id()

    summaries = await summary_search.search_summaries(
        phone_number=phone_number,
        clinic_id=clinic_id,
        query=query if query else None,
        days_back=days_back
    )

    if not summaries:
        return {
            "found": False,
            "message": f"No previous conversations found in the last {days_back} days.",
            "summaries": []
        }

    return {
        "found": True,
        "count": len(summaries),
        "summaries": summaries,
        "message": f"Found {len(summaries)} previous conversation(s)"
    }


# Tool schema for LLM function calling
get_previous_conversations_summary.tool_schema = {
    "type": "function",
    "function": {
        "name": "get_previous_conversations_summary",
        "description": "Search summaries of user's previous conversations. Use when user asks about past interactions.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search keywords (e.g., 'appointment', 'doctor', 'allergies'). Leave empty to get all recent conversations."
                },
                "days_back": {
                    "type": "integer",
                    "description": "How many days back to search (default 90)",
                    "default": 90
                }
            },
            "required": []
        }
    }
}


async def search_detailed_conversation_history(
    query: str,
    days_back: int = 90,
    limit: int = 20
) -> Dict[str, Any]:
    """
    Deep search of full conversation history (messages, not summaries).

    Use this when:
    - User asks for specific details not in summaries
    - get_previous_conversations_summary didn't find enough info
    - User wants exact quotes or details from past messages

    Args:
        query: Search keywords (required for deep search)
        days_back: How many days back to search (default 90)
        limit: Max results to return (default 20)

    Returns:
        Detailed message results with timestamps and context
    """

    if not query or not query.strip():
        return {
            "found": False,
            "message": "Deep search requires a search query. Please provide keywords.",
            "messages": []
        }

    # Get phone and clinic from context
    phone_number = get_current_phone_number()
    clinic_id = get_current_clinic_id()

    result = await full_history_search.search_full_history(
        phone_number=phone_number,
        clinic_id=clinic_id,
        query=query,
        days_back=days_back,
        limit=limit
    )

    if not result['found']:
        return {
            "found": False,
            "message": f"No messages found matching '{query}' in the last {days_back} days.",
            "messages": []
        }

    # Format messages for LLM
    messages = result['messages']
    formatted_messages = []
    for msg in messages:
        date_str = msg['created_at'][:10]
        time_str = msg['created_at'][11:16]
        formatted_messages.append({
            'date': date_str,
            'time': time_str,
            'role': msg['role'],
            'content': msg['message_content']
        })

    return {
        "found": True,
        "count": len(messages),
        "total": result.get('total', len(messages)),
        "has_more": result.get('has_more', False),
        "messages": formatted_messages,
        "message": f"Found {len(messages)} message(s) matching '{query}'"
    }


# Tool schema for deep search
search_detailed_conversation_history.tool_schema = {
    "type": "function",
    "function": {
        "name": "search_detailed_conversation_history",
        "description": "Deep search of full message history. Use when summaries don't have enough detail or user wants exact quotes.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search keywords (required - e.g., 'allergic to penicillin', 'Dr. Smith said')"
                },
                "days_back": {
                    "type": "integer",
                    "description": "How many days back to search (default 90)",
                    "default": 90
                },
                "limit": {
                    "type": "integer",
                    "description": "Max results (default 20)",
                    "default": 20
                }
            },
            "required": ["query"]
        }
    }
}
