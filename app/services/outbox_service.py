"""
Outbox Service - Write outbound WhatsApp messages to database outbox

This service provides functions for writing messages to the outbox table,
which will be processed by the OutboxProcessor worker for async delivery.
"""

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional
from app.db.supabase_client import get_supabase_client

logger = logging.getLogger(__name__)


async def write_to_outbox(
    instance_name: str,
    to_number: str,
    text_content: str,
    message_id: Optional[str] = None,
    scheduled_for: Optional[datetime] = None
) -> bool:
    """
    Write an outbound message to the outbox table for async processing.

    Args:
        instance_name: WhatsApp instance name
        to_number: Recipient phone number
        text_content: Message text
        message_id: Optional custom message ID (auto-generated if not provided)
        scheduled_for: Optional future datetime to send message (default: immediate)

    Returns:
        True if message was written successfully, False otherwise
    """
    try:
        supabase = get_supabase_client()

        # Generate message ID if not provided
        if not message_id:
            message_id = str(uuid.uuid4())

        # Use current time if no schedule specified
        if not scheduled_for:
            scheduled_for = datetime.now(timezone.utc)

        # Insert into outbox table
        result = supabase.schema('healthcare').table('outbound_messages').insert({
            'message_id': message_id,
            'instance_name': instance_name,
            'to_number': to_number,
            'text_content': text_content,
            'status': 'pending',
            'retry_count': 0,
            'scheduled_for': scheduled_for.isoformat()
        }).execute()

        if result.data:
            logger.info(
                f"✅ Message written to outbox: {message_id} "
                f"(to={to_number}, instance={instance_name})"
            )
            return True
        else:
            logger.error(f"❌ Failed to write message to outbox: no data returned")
            return False

    except Exception as e:
        logger.error(f"❌ Error writing to outbox: {e}", exc_info=True)
        return False


async def get_outbox_stats() -> dict:
    """
    Get statistics about messages in the outbox.

    Returns:
        Dictionary with counts by status
    """
    try:
        supabase = get_supabase_client()

        result = supabase.schema('healthcare').table('outbound_messages').select(
            'status',
            count='exact'
        ).execute()

        # Count by status
        stats = {
            'pending': 0,
            'processing': 0,
            'sent': 0,
            'failed': 0,
            'total': result.count if result.count else 0
        }

        # Get counts for each status
        for status in ['pending', 'processing', 'sent', 'failed']:
            status_result = supabase.schema('healthcare').table('outbound_messages').select(
                '*',
                count='exact'
            ).eq('status', status).execute()
            stats[status] = status_result.count if status_result.count else 0

        return stats

    except Exception as e:
        logger.error(f"Error getting outbox stats: {e}")
        return {
            'error': str(e),
            'pending': -1,
            'processing': -1,
            'sent': -1,
            'failed': -1,
            'total': -1
        }
