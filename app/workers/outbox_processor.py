"""
Authoritative Outbox Processor Worker
P1 Enhancement #3: Single source of truth for outbound message delivery

Processes messages from healthcare.outbound_messages table and delivers them
through the existing WhatsApp queue infrastructure with circuit breaker protection.

HYBRID ARCHITECTURE (v2):
- Supabase Realtime subscription for instant delivery (primary)
- Exponential backoff polling as safety net (fallback)
- Self-healing reconnection on subscription failure

This reduces Supabase load from ~120 queries/min to ~2 queries/min when idle,
while maintaining instant delivery under normal conditions.
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
    Processes outbound_messages table as authoritative queue.
    Delivers messages through existing queue infrastructure.

    Uses hybrid Realtime + polling for optimal efficiency:
    - Realtime: Instant notification on INSERT (primary path)
    - Polling: Exponential backoff safety net (catches missed events)
    """

    # Backoff configuration
    MIN_POLL_INTERVAL = 1.0      # Minimum poll interval (when active)
    MAX_POLL_INTERVAL = 30.0     # Maximum poll interval (when idle)
    BACKOFF_MULTIPLIER = 2.0     # Exponential backoff multiplier
    REALTIME_RESET_INTERVAL = 5.0  # Reset to fast polling after Realtime event

    def __init__(self):
        self._supabase = None
        self._realtime_client = None
        self._realtime_channel = None
        self.running = False

        # Polling configuration (now with backoff)
        self.current_poll_interval = self.MIN_POLL_INTERVAL
        self.batch_size = int(os.getenv('OUTBOX_BATCH_SIZE', '10'))
        self.max_retries = int(os.getenv('OUTBOX_MAX_RETRIES', '5'))

        # Realtime state
        self._realtime_connected = False
        self._wake_event = asyncio.Event()  # Signaled by Realtime on INSERT
        self._last_realtime_event = 0.0     # Timestamp of last Realtime event

        # Stats for monitoring
        self._stats = {
            'realtime_events': 0,
            'poll_cycles': 0,
            'messages_processed': 0,
            'realtime_reconnects': 0,
        }

        logger.info(
            f"OutboxProcessor initialized (hybrid mode): "
            f"batch_size={self.batch_size}, max_retries={self.max_retries}, "
            f"poll_range={self.MIN_POLL_INTERVAL}s-{self.MAX_POLL_INTERVAL}s"
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
        """Start processing loop with hybrid Realtime + polling."""
        self.running = True
        logger.info("OutboxProcessor started (hybrid mode)")

        # Start Realtime subscription in background
        realtime_task = asyncio.create_task(self._realtime_loop())

        try:
            await self._polling_loop()
        finally:
            # Clean up Realtime on shutdown
            realtime_task.cancel()
            try:
                await realtime_task
            except asyncio.CancelledError:
                pass
            await self._disconnect_realtime()

    async def _polling_loop(self):
        """
        Polling loop with exponential backoff.

        This is the safety net - catches messages if Realtime misses them.
        Backs off when idle, resets to fast polling on Realtime events.
        """
        import time

        while self.running:
            try:
                self._stats['poll_cycles'] += 1

                # Fetch pending/failed messages with retry budget remaining
                messages = await self._fetch_pending_messages()

                if messages:
                    logger.debug(f"Processing {len(messages)} outbox messages")
                    # Reset to fast polling when we find work
                    self.current_poll_interval = self.MIN_POLL_INTERVAL

                    for msg in messages:
                        await self._process_message(msg)
                        self._stats['messages_processed'] += 1
                else:
                    # No messages - apply exponential backoff
                    # But reset if we got a recent Realtime event
                    if time.time() - self._last_realtime_event < self.REALTIME_RESET_INTERVAL:
                        self.current_poll_interval = self.MIN_POLL_INTERVAL
                    else:
                        self.current_poll_interval = min(
                            self.current_poll_interval * self.BACKOFF_MULTIPLIER,
                            self.MAX_POLL_INTERVAL
                        )

                # Wait for either:
                # 1. Poll interval to elapse
                # 2. Realtime wake event (instant wake)
                try:
                    await asyncio.wait_for(
                        self._wake_event.wait(),
                        timeout=self.current_poll_interval
                    )
                    # Woken by Realtime - clear event and process immediately
                    self._wake_event.clear()
                    self.current_poll_interval = self.MIN_POLL_INTERVAL
                except asyncio.TimeoutError:
                    # Normal timeout - continue polling
                    pass

            except Exception as e:
                logger.error(f"Outbox processor error: {e}", exc_info=True)
                await asyncio.sleep(1)  # Brief pause on error

    async def _realtime_loop(self):
        """
        Realtime subscription loop with automatic reconnection.

        Subscribes to INSERT events on outbound_messages table.
        On new message, wakes up the polling loop immediately.
        """
        while self.running:
            try:
                await self._connect_realtime()

                # Keep alive while connected
                while self.running and self._realtime_connected:
                    await asyncio.sleep(5)  # Health check interval

            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"Realtime subscription error: {e}")
                self._realtime_connected = False
                self._stats['realtime_reconnects'] += 1

                # Wait before reconnecting (with jitter)
                import random
                await asyncio.sleep(5 + random.random() * 5)

    async def _connect_realtime(self):
        """
        Connect to Supabase Realtime and subscribe to outbound_messages INSERTs.
        """
        try:
            supabase_url = os.getenv("SUPABASE_URL")
            supabase_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_ANON_KEY")

            if not supabase_url or not supabase_key:
                logger.warning("Supabase credentials not found, Realtime disabled")
                return

            # Import Realtime client
            try:
                from realtime import AsyncRealtimeClient, RealtimeSubscribeStates
            except ImportError:
                logger.warning("realtime-py not installed, falling back to polling only")
                return

            # Build Realtime URL (extract project ID from URL)
            # URL format: https://<project-id>.supabase.co
            project_id = supabase_url.replace("https://", "").replace("http://", "").split(".")[0]
            realtime_url = f"wss://{project_id}.supabase.co/realtime/v1/websocket"

            # Create Realtime client
            self._realtime_client = AsyncRealtimeClient(realtime_url, supabase_key)
            await self._realtime_client.connect()

            # Subscribe to INSERT events on outbound_messages
            def on_insert(_payload):
                """Handle INSERT event from Realtime."""
                import time as t
                self._stats['realtime_events'] += 1
                self._last_realtime_event = t.time()
                self._wake_event.set()  # Wake up polling loop
                logger.debug("Realtime: new outbound message detected")

            def on_subscribe(status, err):
                """Handle subscription status changes."""
                if status == RealtimeSubscribeStates.SUBSCRIBED:
                    self._realtime_connected = True
                    logger.info("✅ Realtime subscription active for outbound_messages")
                elif err:
                    logger.error(f"Realtime subscription error: {err}")
                    self._realtime_connected = False

            # Create channel and subscribe to postgres changes
            self._realtime_channel = self._realtime_client.channel('outbox-inserts')
            self._realtime_channel.on_postgres_changes(
                "INSERT",
                schema='public',
                table='outbound_messages',
                callback=on_insert
            )
            self._realtime_channel.subscribe(on_subscribe)

            # Start listening (this keeps the connection alive)
            # Run in background task since listen() blocks
            asyncio.create_task(self._realtime_listen())

        except Exception as e:
            logger.error(f"Failed to connect Realtime: {e}")
            self._realtime_connected = False
            raise

    async def _realtime_listen(self):
        """Keep Realtime connection alive by listening for messages."""
        try:
            if self._realtime_client:
                await self._realtime_client.listen()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.warning(f"Realtime listen error: {e}")
            self._realtime_connected = False

    async def _disconnect_realtime(self):
        """Disconnect from Supabase Realtime."""
        try:
            if self._realtime_channel:
                try:
                    self._realtime_channel.unsubscribe()
                except Exception:
                    pass  # May already be unsubscribed
                self._realtime_channel = None

            if self._realtime_client:
                try:
                    await self._realtime_client.close()
                except Exception:
                    pass  # May already be closed
                self._realtime_client = None

            self._realtime_connected = False
            logger.info("Realtime subscription closed")

        except Exception as e:
            logger.warning(f"Error disconnecting Realtime: {e}")

    async def stop(self):
        """Stop processing loop."""
        self.running = False
        self._wake_event.set()  # Wake up polling loop to exit
        logger.info(
            f"OutboxProcessor stopped. Stats: "
            f"realtime_events={self._stats['realtime_events']}, "
            f"poll_cycles={self._stats['poll_cycles']}, "
            f"messages_processed={self._stats['messages_processed']}, "
            f"realtime_reconnects={self._stats['realtime_reconnects']}"
        )

    def get_stats(self) -> Dict[str, Any]:
        """Get processor statistics for monitoring."""
        return {
            **self._stats,
            'realtime_connected': self._realtime_connected,
            'current_poll_interval': self.current_poll_interval,
        }

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
        provider_message_id: Optional[str] = None,
        text_hash: Optional[str] = None,
        remote_jid: Optional[str] = None,
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
        # Build update data outside try block to ensure it's defined for retry
        update_data: Dict[str, Any] = {'delivery_status': status}

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

        try:
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
