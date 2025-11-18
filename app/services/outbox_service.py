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
    message_text: str,
    conversation_id: str,
    clinic_id: str,
    message_id: Optional[str] = None
) -> bool:
    """
    Write an outbound message to the outbox table for async processing.

    Args:
        instance_name: WhatsApp instance name
        to_number: Recipient phone number
        message_text: Message text
        conversation_id: Conversation identifier for replay capability
        clinic_id: UUID of the clinic
        message_id: Optional custom message ID (auto-generated if not provided)

    Returns:
        True if message was written successfully, False otherwise
    """
    try:
        supabase = get_supabase_client()

        # Generate message ID if not provided
        if not message_id:
            message_id = str(uuid.uuid4())

        # Insert into outbox table
        result = supabase.schema('healthcare').table('outbound_messages').insert({
            'message_id': message_id,
            'conversation_id': conversation_id,
            'clinic_id': clinic_id,
            'instance_name': instance_name,
            'to_number': to_number,
            'message_text': message_text,
            'delivery_status': 'pending',
            'retry_count': 0
        }).execute()

        if result.data:
            logger.info(
                f"✅ Message written to outbox: {message_id} "
                f"(to={to_number}, instance={instance_name}, conversation={conversation_id})"
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
        Dictionary with counts by delivery status
    """
    try:
        supabase = get_supabase_client()

        result = supabase.schema('healthcare').table('outbound_messages').select(
            'delivery_status',
            count='exact'
        ).execute()

        # Count by delivery status (matching migration schema)
        stats = {
            'pending': 0,
            'queued': 0,
            'delivered': 0,
            'failed': 0,
            'total': result.count if result.count else 0
        }

        # Get counts for each status
        for status in ['pending', 'queued', 'delivered', 'failed']:
            status_result = supabase.schema('healthcare').table('outbound_messages').select(
                '*',
                count='exact'
            ).eq('delivery_status', status).execute()
            stats[status] = status_result.count if status_result.count else 0

        return stats

    except Exception as e:
        logger.error(f"Error getting outbox stats: {e}")
        return {
            'error': str(e),
            'pending': -1,
            'queued': -1,
            'delivered': -1,
            'failed': -1,
            'total': -1
        }
