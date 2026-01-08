"""
Outbox Service - Write outbound WhatsApp messages to database outbox

This service provides functions for writing messages to the outbox table,
which will be processed by the OutboxProcessor worker for async delivery.

Supports message types: text, location, buttons, template
"""

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
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


async def write_location_to_outbox(
    instance_name: str,
    to_number: str,
    lat: float,
    lng: float,
    name: str,
    address: str,
    conversation_id: str,
    clinic_id: str,
    message_id: Optional[str] = None
) -> bool:
    """
    Write a location message to the outbox table.

    Args:
        instance_name: WhatsApp instance name
        to_number: Recipient phone number
        lat: Latitude
        lng: Longitude
        name: Location name
        address: Location address
        conversation_id: Conversation identifier
        clinic_id: UUID of the clinic
        message_id: Optional custom message ID

    Returns:
        True if message was written successfully, False otherwise
    """
    try:
        supabase = get_supabase_client()

        if not message_id:
            message_id = str(uuid.uuid4())

        payload = {
            "lat": lat,
            "lng": lng,
            "name": name,
            "address": address
        }

        result = supabase.schema('healthcare').table('outbound_messages').insert({
            'message_id': message_id,
            'conversation_id': conversation_id,
            'clinic_id': clinic_id,
            'instance_name': instance_name,
            'to_number': to_number,
            'message_text': f"Location: {name}",  # Fallback text
            'message_type': 'location',
            'message_payload': payload,
            'delivery_status': 'pending',
            'retry_count': 0
        }).execute()

        if result.data:
            logger.info(f"✅ Location message written to outbox: {message_id}")
            return True
        else:
            logger.error(f"❌ Failed to write location to outbox")
            return False

    except Exception as e:
        logger.error(f"❌ Error writing location to outbox: {e}", exc_info=True)
        return False


async def write_buttons_to_outbox(
    instance_name: str,
    to_number: str,
    text: str,
    buttons: List[Dict[str, str]],
    conversation_id: str,
    clinic_id: str,
    title: Optional[str] = None,
    footer: Optional[str] = None,
    message_id: Optional[str] = None
) -> bool:
    """
    Write a button message to the outbox table.

    Args:
        instance_name: WhatsApp instance name
        to_number: Recipient phone number
        text: Message body text
        buttons: List of buttons [{"id": "confirm", "text": "Confirm"}, ...]
        conversation_id: Conversation identifier
        clinic_id: UUID of the clinic
        title: Optional title
        footer: Optional footer
        message_id: Optional custom message ID

    Returns:
        True if message was written successfully, False otherwise
    """
    try:
        supabase = get_supabase_client()

        if not message_id:
            message_id = str(uuid.uuid4())

        payload = {
            "title": title,
            "text": text,
            "buttons": buttons,
            "footer": footer
        }

        result = supabase.schema('healthcare').table('outbound_messages').insert({
            'message_id': message_id,
            'conversation_id': conversation_id,
            'clinic_id': clinic_id,
            'instance_name': instance_name,
            'to_number': to_number,
            'message_text': text,  # Fallback text
            'message_type': 'buttons',
            'message_payload': payload,
            'delivery_status': 'pending',
            'retry_count': 0
        }).execute()

        if result.data:
            logger.info(f"✅ Buttons message written to outbox: {message_id}")
            return True
        else:
            logger.error(f"❌ Failed to write buttons to outbox")
            return False

    except Exception as e:
        logger.error(f"❌ Error writing buttons to outbox: {e}", exc_info=True)
        return False


async def write_template_to_outbox(
    instance_name: str,
    to_number: str,
    template_name: str,
    conversation_id: str,
    clinic_id: str,
    language: str = "en",
    components: Optional[List[Dict[str, Any]]] = None,
    message_id: Optional[str] = None
) -> bool:
    """
    Write a template message to the outbox table.

    Args:
        instance_name: WhatsApp instance name
        to_number: Recipient phone number
        template_name: WhatsApp-approved template name
        conversation_id: Conversation identifier
        clinic_id: UUID of the clinic
        language: Language code (default "en")
        components: Template variable components
        message_id: Optional custom message ID

    Returns:
        True if message was written successfully, False otherwise
    """
    try:
        supabase = get_supabase_client()

        if not message_id:
            message_id = str(uuid.uuid4())

        payload = {
            "template_name": template_name,
            "language": language,
            "components": components or []
        }

        result = supabase.schema('healthcare').table('outbound_messages').insert({
            'message_id': message_id,
            'conversation_id': conversation_id,
            'clinic_id': clinic_id,
            'instance_name': instance_name,
            'to_number': to_number,
            'message_text': f"Template: {template_name}",  # Fallback text
            'message_type': 'template',
            'message_payload': payload,
            'delivery_status': 'pending',
            'retry_count': 0
        }).execute()

        if result.data:
            logger.info(f"✅ Template message written to outbox: {message_id}")
            return True
        else:
            logger.error(f"❌ Failed to write template to outbox")
            return False

    except Exception as e:
        logger.error(f"❌ Error writing template to outbox: {e}", exc_info=True)
        return False
