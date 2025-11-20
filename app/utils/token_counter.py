"""Token counting utilities for context budgeting."""

import logging
from typing import List, Dict

logger = logging.getLogger(__name__)

# Approximation: 1 token ≈ 4 characters for English, 2-3 for others
CHARS_PER_TOKEN = 4


def estimate_tokens(text: str) -> int:
    """Estimate token count for text (conservative approximation)."""
    if not text:
        return 0
    return len(text) // CHARS_PER_TOKEN


def count_message_tokens(messages: List[Dict]) -> int:
    """Count total tokens in message list."""
    total = 0
    for msg in messages:
        content = msg.get('content', '') or msg.get('message_content', '')
        if content:
            total += estimate_tokens(str(content))
    return total


def truncate_to_budget(
    messages: List[Dict],
    max_tokens: int,
    preserve_recent: int = 5
) -> List[Dict]:
    """
    Truncate message list to fit within token budget.

    Args:
        messages: List of messages (oldest first)
        max_tokens: Maximum allowed tokens
        preserve_recent: Always keep last N messages (default 5)

    Returns:
        Truncated message list that fits budget
    """
    if not messages:
        return []

    # Always preserve last N messages
    recent_messages = messages[-preserve_recent:] if len(messages) >= preserve_recent else messages
    older_messages = messages[:-preserve_recent] if len(messages) > preserve_recent else []

    # Count tokens in recent messages
    recent_tokens = count_message_tokens(recent_messages)

    if recent_tokens >= max_tokens:
        # Recent messages alone exceed budget - return them anyway (safety)
        logger.warning(f"Recent {preserve_recent} messages exceed budget ({recent_tokens} > {max_tokens})")
        return recent_messages

    # Add older messages until budget exhausted
    remaining_budget = max_tokens - recent_tokens
    included_older = []

    # Iterate from most recent older messages backwards
    for msg in reversed(older_messages):
        content = msg.get('content', '') or msg.get('message_content', '')
        msg_tokens = estimate_tokens(str(content)) if content else 0
        if msg_tokens <= remaining_budget:
            included_older.insert(0, msg)  # Prepend to maintain order
            remaining_budget -= msg_tokens
        else:
            break  # Stop adding messages

    result = included_older + recent_messages
    total_tokens = count_message_tokens(result)

    logger.info(
        f"Context budget: {len(result)}/{len(messages)} messages, "
        f"{total_tokens}/{max_tokens} tokens"
    )

    return result
