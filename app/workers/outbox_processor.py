"""
Authoritative Outbox Processor Worker
P1 Enhancement #3: Single source of truth for outbound message delivery

Processes messages from healthcare.outbound_messages table and delivers them
through the existing WhatsApp queue infrastructure with circuit breaker protection.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Dict, Any, Optional
import os

import httpx

logger = logging.getLogger(__name__)


class OutboxProcessor:
    """
    Processes outbound_messages table as authoritative queue
    Delivers messages through existing queue infrastructure
    """

    def __init__(self):
        self._supabase = None
        self.running = False
        self.poll_interval = float(os.getenv('OUTBOX_POLL_INTERVAL', '0.5'))  # 500ms
        self.batch_size = int(os.getenv('OUTBOX_BATCH_SIZE', '10'))
        self.max_retries = int(os.getenv('OUTBOX_MAX_RETRIES', '5'))

        logger.info(
            f"OutboxProcessor initialized: poll_interval={self.poll_interval}s, "
            f"batch_size={self.batch_size}, max_retries={self.max_retries}"
        )

    def _get_supabase(self, force_new: bool = False):
        """Get Supabase client, optionally forcing a fresh connection."""
        if self._supabase is None or force_new:
            # Import here to get fresh client
            from app.database import create_supabase_client, _supabase_clients

            if force_new:
                # Clear cached client to force reconnection
                _supabase_clients.pop('healthcare', None)
                logger.info("Cleared cached Supabase client, forcing reconnection")

            self._supabase = create_supabase_client('healthcare')

        return self._supabase

    async def start(self):
        """Start processing loop"""
        self.running = True
        logger.info("OutboxProcessor started")

        while self.running:
            try:
                # Fetch pending/failed messages with retry budget remaining
                messages = await self._fetch_pending_messages()

                if messages:
                    logger.debug(f"Processing {len(messages)} outbox messages")

                    # Process each message
                    for msg in messages:
                        await self._process_message(msg)

                # Poll interval
                await asyncio.sleep(self.poll_interval)

            except Exception as e:
                logger.error(f"Outbox processor error: {e}", exc_info=True)
                await asyncio.sleep(1)  # Brief pause on error

    async def stop(self):
        """Stop processing loop"""
        self.running = False
        logger.info("OutboxProcessor stopped")

    async def _fetch_pending_messages(self) -> list:
        """
        Fetch messages that need delivery

        Returns messages in pending or failed state with retry_count < max_retries
        Handles HTTP/2 connection termination by forcing client reconnection.
        """
        for attempt in range(2):  # Try twice: once with cached client, once with fresh
            try:
                supabase = self._get_supabase(force_new=(attempt > 0))
                result = supabase.table('outbound_messages').select('*').in_(
                    'delivery_status', ['pending', 'failed']
                ).lt(
                    'retry_count', self.max_retries
                ).order('created_at').limit(self.batch_size).execute()

                return result.data if result.data else []

            except (httpx.RemoteProtocolError, httpx.ConnectError) as e:
                # HTTP/2 GOAWAY or connection error - force reconnection on next attempt
                logger.warning(
                    f"Connection error (attempt {attempt + 1}/2): {e}. "
                    f"Will reconnect on next attempt."
                )
                self._supabase = None  # Clear cached reference
                if attempt == 1:
                    logger.error(f"Failed to fetch pending messages after reconnect: {e}")
                    return []

            except Exception as e:
                logger.error(f"Failed to fetch pending messages: {e}", exc_info=True)
                return []

        return []

    async def _process_message(self, msg: Dict[str, Any]):
        """
        Process single outbox message

        Args:
            msg: Message dict from outbound_messages table
        """
        message_id = msg['id']

        try:
            # Update to queued status (optimistic)
            await self._update_status(message_id, 'queued', queued_at=datetime.now(timezone.utc))

            # Send via Evolution API through existing infrastructure
            # Supports text, location, buttons, and template message types
            result = await self._send_via_evolution(
                instance_name=msg['instance_name'],
                to_number=msg['to_number'],
                text=msg['message_text'],
                message_id=msg['message_id'],
                message_type=msg.get('message_type', 'text'),
                message_payload=msg.get('message_payload')
            )

            success = result.get('success', False)
            provider_message_id = result.get('provider_message_id')
            text_hash = result.get('text_hash')
            remote_jid = result.get('remote_jid')

            if success:
                # Mark delivered with provider message ID for HITL correlation
                await self._update_status(
                    message_id,
                    'delivered',
                    delivered_at=datetime.now(timezone.utc),
                    provider_message_id=provider_message_id,
                    text_hash=text_hash,
                    remote_jid=remote_jid
                )
                logger.info(
                    f"✅ Outbox message {message_id} delivered successfully "
                    f"(provider_id: {provider_message_id})"
                )
            else:
                # Mark failed, increment retry
                await self._update_status(
                    message_id,
                    'failed',
                    failed_at=datetime.now(timezone.utc),
                    error_message=result.get('error', 'Send failed - Evolution API error'),
                    retry_count=msg['retry_count'] + 1
                )
                logger.warning(
                    f"❌ Outbox message {message_id} failed, "
                    f"retry {msg['retry_count'] + 1}/{self.max_retries}"
                )

        except Exception as e:
            logger.error(f"Error processing outbox message {message_id}: {e}", exc_info=True)

            # Mark failed with error details
            await self._update_status(
                message_id,
                'failed',
                failed_at=datetime.now(timezone.utc),
                error_message=str(e)[:500],  # Truncate long errors
                retry_count=msg['retry_count'] + 1
            )

    async def _send_via_evolution(
        self,
        instance_name: str,
        to_number: str,
        text: str,
        message_id: str,
        message_type: str = 'text',
        message_payload: Optional[Dict[str, Any]] = None
    ) -> dict:
        """
        Send message via Evolution API

        Uses Evolution client directly for actual delivery with provider_message_id capture.
        Supports text, location, buttons, and template message types.

        Args:
            instance_name: WhatsApp instance name
            to_number: Recipient phone number
            text: Message text (fallback for non-text types)
            message_id: Unique message ID
            message_type: Type of message (text|location|buttons|template)
            message_payload: JSONB payload with type-specific data

        Returns:
            Dict with 'success', 'provider_message_id', 'text_hash', 'remote_jid'
        """
        import hashlib

        try:
            from app.services.whatsapp_queue.evolution_client import (
                send_text, send_location, send_buttons, send_template
            )
            from app.services.whatsapp_queue.e164 import to_jid

            result = None
            payload = message_payload or {}

            if message_type == 'location':
                result = await send_location(
                    instance=instance_name,
                    to_number=to_number,
                    lat=payload.get('lat', 0),
                    lng=payload.get('lng', 0),
                    name=payload.get('name'),
                    address=payload.get('address')
                )
            elif message_type == 'buttons':
                result = await send_buttons(
                    instance=instance_name,
                    to_number=to_number,
                    text=payload.get('text', text),
                    buttons=payload.get('buttons', []),
                    title=payload.get('title'),
                    footer=payload.get('footer')
                )
            elif message_type == 'template':
                result = await send_template(
                    instance=instance_name,
                    to_number=to_number,
                    template_name=payload.get('template_name', ''),
                    language=payload.get('language', 'en'),
                    components=payload.get('components')
                )
            else:  # Default to text
                result = await send_text(
                    instance=instance_name,
                    to_number=to_number,
                    text=text
                )

            # Compute text hash for fallback matching
            text_hash = hashlib.sha256(text.strip().lower().encode()).hexdigest()[:32]

            # Get remote JID
            remote_jid = to_jid(to_number)

            # Handle both old (bool) and new (dict) return formats
            if isinstance(result, dict):
                return {
                    'success': result.get('success', False),
                    'provider_message_id': result.get('provider_message_id'),
                    'text_hash': text_hash,
                    'remote_jid': remote_jid,
                    'error': result.get('error')
                }
            else:
                return {
                    'success': bool(result),
                    'provider_message_id': None,
                    'text_hash': text_hash,
                    'remote_jid': remote_jid
                }

        except Exception as e:
            logger.error(f"Failed to send via Evolution: {e}", exc_info=True)
            return {'success': False, 'error': str(e)}

    async def _update_status(
        self,
        message_id: str,
        status: str,
        provider_message_id: str = None,
        text_hash: str = None,
        remote_jid: str = None,
        **kwargs
    ):
        """
        Update message status in outbound_messages table

        Args:
            message_id: Message UUID
            status: New status (pending|queued|delivered|failed)
            provider_message_id: WhatsApp message ID from Evolution API (for HITL correlation)
            text_hash: SHA256 hash of message text (for fallback matching)
            remote_jid: WhatsApp JID of recipient (for fallback matching)
            **kwargs: Additional fields to update (queued_at, delivered_at, etc.)
        """
        try:
            update_data = {'delivery_status': status}

            # Add HITL correlation fields if provided
            if provider_message_id:
                update_data['provider_message_id'] = provider_message_id
            if text_hash:
                update_data['text_hash'] = text_hash
            if remote_jid:
                update_data['remote_jid'] = remote_jid

            # Add timestamp fields
            if 'queued_at' in kwargs:
                update_data['queued_at'] = kwargs['queued_at'].isoformat()
            if 'delivered_at' in kwargs:
                update_data['delivered_at'] = kwargs['delivered_at'].isoformat()
            if 'failed_at' in kwargs:
                update_data['failed_at'] = kwargs['failed_at'].isoformat()

            # Add error/retry fields
            if 'error_message' in kwargs:
                update_data['error_message'] = kwargs['error_message']
            if 'retry_count' in kwargs:
                update_data['retry_count'] = kwargs['retry_count']

            supabase = self._get_supabase()
            supabase.table('outbound_messages').update(update_data).eq(
                'id', message_id
            ).execute()

        except (httpx.RemoteProtocolError, httpx.ConnectError) as e:
            # Connection error - try once more with fresh client
            logger.warning(f"Connection error updating status, retrying: {e}")
            self._supabase = None
            try:
                supabase = self._get_supabase(force_new=True)
                supabase.table('outbound_messages').update(update_data).eq(
                    'id', message_id
                ).execute()
            except Exception as retry_error:
                logger.error(f"Failed to update outbox status after retry: {retry_error}")

        except Exception as e:
            logger.error(f"Failed to update outbox status: {e}", exc_info=True)


# Singleton instance
_processor: Optional[OutboxProcessor] = None


def get_outbox_processor() -> OutboxProcessor:
    """Get or create singleton outbox processor instance"""
    global _processor
    if _processor is None:
        _processor = OutboxProcessor()
    return _processor


async def start_outbox_processor():
    """Start the outbox processor worker"""
    processor = get_outbox_processor()
    await processor.start()


async def stop_outbox_processor():
    """Stop the outbox processor worker"""
    processor = get_outbox_processor()
    await processor.stop()
