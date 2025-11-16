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

from app.db.supabase_client import get_supabase_client

logger = logging.getLogger(__name__)


class OutboxProcessor:
    """
    Processes outbound_messages table as authoritative queue
    Delivers messages through existing queue infrastructure
    """

    def __init__(self):
        self.supabase = get_supabase_client(schema='healthcare')
        self.running = False
        self.poll_interval = float(os.getenv('OUTBOX_POLL_INTERVAL', '0.5'))  # 500ms
        self.batch_size = int(os.getenv('OUTBOX_BATCH_SIZE', '10'))
        self.max_retries = int(os.getenv('OUTBOX_MAX_RETRIES', '5'))

        logger.info(
            f"OutboxProcessor initialized: poll_interval={self.poll_interval}s, "
            f"batch_size={self.batch_size}, max_retries={self.max_retries}"
        )

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
        """
        try:
            result = self.supabase.table('outbound_messages').select('*').in_(
                'delivery_status', ['pending', 'failed']
            ).lt(
                'retry_count', self.max_retries
            ).order('created_at').limit(self.batch_size).execute()

            return result.data if result.data else []

        except Exception as e:
            logger.error(f"Failed to fetch pending messages: {e}", exc_info=True)
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
            success = await self._send_via_evolution(
                instance_name=msg['instance_name'],
                to_number=msg['to_number'],
                text=msg['message_text'],
                message_id=msg['message_id']
            )

            if success:
                # Mark delivered
                await self._update_status(
                    message_id,
                    'delivered',
                    delivered_at=datetime.now(timezone.utc)
                )
                logger.info(f"✅ Outbox message {message_id} delivered successfully")
            else:
                # Mark failed, increment retry
                await self._update_status(
                    message_id,
                    'failed',
                    failed_at=datetime.now(timezone.utc),
                    error_message='Send failed - Evolution API error',
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
        message_id: str
    ) -> bool:
        """
        Send message via Evolution API

        Uses existing queue infrastructure for actual delivery.

        Args:
            instance_name: WhatsApp instance name
            to_number: Recipient phone number
            text: Message text
            message_id: Unique message ID

        Returns:
            True if send successful, False otherwise
        """
        try:
            # Import here to avoid circular dependency
            from app.services.whatsapp_queue import enqueue_message

            # Queue the message through existing infrastructure
            # The existing worker will handle rate limiting, circuit breaker, etc.
            success = await enqueue_message(
                instance=instance_name,
                to_number=to_number,
                text=text,
                message_id=message_id
            )

            return success

        except Exception as e:
            logger.error(f"Failed to send via Evolution: {e}", exc_info=True)
            return False

    async def _update_status(
        self,
        message_id: str,
        status: str,
        **kwargs
    ):
        """
        Update message status in outbound_messages table

        Args:
            message_id: Message UUID
            status: New status (pending|queued|delivered|failed)
            **kwargs: Additional fields to update (queued_at, delivered_at, etc.)
        """
        try:
            update_data = {'delivery_status': status}

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

            self.supabase.table('outbound_messages').update(update_data).eq(
                'id', message_id
            ).execute()

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
