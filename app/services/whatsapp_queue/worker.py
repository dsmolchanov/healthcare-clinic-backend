"""
WhatsApp Queue Worker
Background worker that processes messages from Redis Streams
"""
import asyncio
import json
import os
import time
import random
from typing import Dict, Any, Optional

from .config import (
    CONSUMER_GROUP, MAX_DELIVERIES,
    BASE_BACKOFF, MAX_BACKOFF, TOKENS_PER_SECOND, BUCKET_CAPACITY,
    logger
)
from .queue import (
    stream_key, dlq_key, ensure_group, get_redis_client, notification_channel
)
from .evolution_client import is_connected, send_text
from .rate_limiter import TokenBucket

#
# Tunables (overridable via env)
#
CLAIM_IDLE_MS: int = int(os.getenv("WA_STREAM_CLAIM_IDLE_MS", "15000"))  # XAUTOCLAIM min idle
READ_COUNT:    int = int(os.getenv("WA_READ_COUNT", "32"))                # Messages per read (increased from 10)
BLOCK_MS:      int = int(os.getenv("WA_READ_BLOCK_MS", "250"))            # Read timeout (reduced from 5000ms)

# Pub/Sub notification settings (Phase 2: Push-based wake-up)
USE_PUBSUB = os.getenv("WA_USE_PUBSUB", "1") != "0"                      # Enable Pub/Sub notifications
PUBSUB_TIMEOUT = int(os.getenv("WA_PUBSUB_TIMEOUT", "30"))               # Fallback poll interval (seconds)

# Worker performance tunables
MAX_CONCURRENCY = int(os.getenv("WA_WORKER_CONCURRENCY", "4"))           # Max in-flight messages
OPTIMISTIC_SEND = os.getenv("WA_OPTIMISTIC_SEND", "1") != "0"            # Skip connection checks
CHECK_CONN_TTL = float(os.getenv("WA_CHECK_CONN_TTL", "3.0"))            # Connection check cache TTL
IDLE_SLEEP_BASE = float(os.getenv("WA_IDLE_SLEEP_BASE", "0.05"))         # Idle sleep duration


def exponential_backoff(attempts: int, base: float = BASE_BACKOFF, cap: float = MAX_BACKOFF) -> float:
    """
    Calculate exponential backoff with jitter

    Args:
        attempts: Number of attempts so far
        base: Base delay in seconds
        cap: Maximum delay in seconds

    Returns:
        Delay in seconds with jitter applied
    """
    delay = min(cap, base * (2 ** max(0, attempts - 1)))
    # Add jitter (±25%)
    jitter = random.uniform(0.75, 1.25)
    return delay * jitter


class WhatsAppWorker:
    """Background worker that processes WhatsApp message queue"""

    def __init__(self, instance: str, consumer_name: Optional[str] = None):
        """
        Initialize WhatsApp worker

        Args:
            instance: WhatsApp instance name to process
            consumer_name: Unique consumer name (auto-generated if not provided)
        """
        self.instance = instance
        self.consumer_name = consumer_name or f"worker-{int(time.time())}-{os.getpid()}"
        self.redis = get_redis_client()
        self.rate_limiter = TokenBucket(
            instance=instance,
            tokens_per_second=TOKENS_PER_SECOND,
            capacity=BUCKET_CAPACITY
        )
        self.running = False
        self.processed_count = 0
        self.failed_count = 0
        self._autoclaim_cursor = "0-0"  # cursor for XAUTOCLAIM iteration

        # Connection state memo for optimistic send
        self._last_conn_ts: float = 0.0
        self._last_conn_ok: bool = True

        # Concurrency guard for bounded parallelism
        self._sema = asyncio.Semaphore(MAX_CONCURRENCY)

        # Pub/Sub client for push notifications (Phase 2)
        self.pubsub = None
        self.pubsub_notifications_received = 0

        # Ensure consumer group exists
        ensure_group(self.redis, instance)
        logger.info(f"Initialized worker for instance={instance}, consumer={self.consumer_name}")

    async def process_message(self, redis_msg_id: str, payload: Dict[str, Any]) -> None:
        """
        Process a single message from the queue

        Args:
            redis_msg_id: Redis stream message ID
            payload: Message payload containing to, text, etc.
        """
        message_id = payload.get("message_id", "unknown")
        to = payload.get("to", "")
        text = payload.get("text", "")
        attempts = int(payload.get("attempts", 0))

        logger.info(f"Processing message {message_id} (attempt {attempts+1}/{MAX_DELIVERIES})")

        # Rate limiting - wait for token (per-instance bucket)
        await self.rate_limiter.wait_for_token()

        # Option A (default): optimistic send — skip extra GET roundtrip
        if OPTIMISTIC_SEND:
            connected = True
        else:
            # Option B: cached connection check (cheap, refresh every CHECK_CONN_TTL)
            now = time.time()
            if now - self._last_conn_ts >= CHECK_CONN_TTL:
                try:
                    self._last_conn_ok = await is_connected(self.instance)
                except Exception as e:
                    logger.warning(f"Connection check failed: {e}. Assuming disconnected; will retry.")
                    self._last_conn_ok = False
                self._last_conn_ts = now
            connected = self._last_conn_ok
            if not connected:
                logger.warning(f"Evolution not connected for {self.instance}, will retry")
                await self._retry_message(payload, redis_msg_id, attempts + 1)
                return

        # Send message
        logger.info(f"Sending message {message_id} to {to}")
        try:
            result = await send_text(self.instance, to, text)
            # Handle both old (bool) and new (dict) return formats
            if isinstance(result, dict):
                success = result.get('success', False)
            else:
                success = bool(result)
        except Exception as e:
            logger.error(f"send_text raised {e!r}; will retry")
            success = False

        if success:
            logger.info(f"✅ Message {message_id} sent successfully")
            self.processed_count += 1

            # Acknowledge and delete from stream
            self.redis.xack(stream_key(self.instance), CONSUMER_GROUP, redis_msg_id)
            self.redis.xdel(stream_key(self.instance), redis_msg_id)
        else:
            logger.error(f"❌ Failed to send message {message_id}")
            self.failed_count += 1
            await self._retry_message(payload, redis_msg_id, attempts + 1)

    async def _retry_message(self, payload: Dict[str, Any], redis_msg_id: str, attempts: int):
        """
        Retry a failed message or move to DLQ

        Args:
            payload: Message payload
            redis_msg_id: Redis stream message ID
            attempts: Number of attempts so far
        """
        message_id = payload.get("message_id", "unknown")
        key = stream_key(self.instance)

        if attempts >= MAX_DELIVERIES:
            logger.error(f"Message {message_id} exceeded max deliveries ({MAX_DELIVERIES}), moving to DLQ")

            # Move to dead letter queue
            payload["final_error"] = "max_deliveries_exceeded"
            payload["failed_at"] = time.time()
            self.redis.xadd(dlq_key(self.instance), fields={"payload": json.dumps(payload)})

            # Remove from main stream
            self.redis.xack(key, CONSUMER_GROUP, redis_msg_id)
            self.redis.xdel(key, redis_msg_id)
        else:
            # Calculate backoff delay
            delay = exponential_backoff(attempts)
            logger.info(f"Retrying message {message_id} in {delay:.1f}s (attempt {attempts}/{MAX_DELIVERIES})")

            # ACK + DEL current message BEFORE requeueing to avoid pending buildup
            self.redis.xack(key, CONSUMER_GROUP, redis_msg_id)
            self.redis.xdel(key, redis_msg_id)

            # Wait for backoff
            await asyncio.sleep(delay)

            # Re-queue with updated attempt count
            payload["attempts"] = attempts
            new_msg_id = self.redis.xadd(key, fields={"payload": json.dumps(payload)})
            logger.debug(f"Requeued message {message_id} as {new_msg_id}")

    async def _drain_queue(self):
        """
        Drain all pending messages from the queue.
        Called when notified via Pub/Sub or on fallback poll.
        """
        key = stream_key(self.instance)
        loop = asyncio.get_event_loop()

        # 1) Claim orphaned messages
        def _autoclaim():
            try:
                reply = self.redis.xautoclaim(
                    key,
                    CONSUMER_GROUP,
                    self.consumer_name,
                    min_idle_time=CLAIM_IDLE_MS,
                    start_id=self._autoclaim_cursor,
                    count=READ_COUNT,
                )
                if isinstance(reply, (list, tuple)) and len(reply) >= 2:
                    next_id = reply[0]
                    claimed = reply[1] or []
                    self._autoclaim_cursor = next_id if isinstance(next_id, str) else str(next_id)
                    if claimed:
                        return [(key, claimed)]
                return []
            except Exception as e:
                logger.debug(f"XAUTOCLAIM failed: {e}")
                return []

        msgs = await loop.run_in_executor(None, _autoclaim)

        # 2) Read new messages (non-blocking)
        if not msgs:
            def _read_new():
                return self.redis.xreadgroup(
                    groupname=CONSUMER_GROUP,
                    consumername=self.consumer_name,
                    streams={key: ">"},
                    count=READ_COUNT,
                    block=100  # Short block (100ms) to avoid hanging
                )
            msgs = await loop.run_in_executor(None, _read_new)

        if not msgs:
            return  # No messages to process

        # 3) Process all messages
        tasks = []
        for _, entries in msgs:
            if len(entries) > 0:
                logger.info(f"📬 Processing {len(entries)} message(s) from queue")
            for msg_id, fields in entries:
                async def _one(msg_id=msg_id, fields=fields):
                    async with self._sema:
                        try:
                            payload = json.loads(fields.get("payload", "{}"))
                        except json.JSONDecodeError as e:
                            logger.error(f"Failed to parse message {msg_id}: {e}")
                            self.redis.xadd(
                                dlq_key(self.instance),
                                fields={"payload": json.dumps({"error": f"json_decode_error: {str(e)}", "raw": str(fields)})}
                            )
                            self.redis.xack(key, CONSUMER_GROUP, msg_id)
                            self.redis.xdel(key, msg_id)
                            return
                        try:
                            await self.process_message(msg_id, payload)
                        except Exception as e:
                            logger.error(f"Error processing message {msg_id}: {e}", exc_info=True)
                            self.redis.xadd(
                                dlq_key(self.instance),
                                fields={"payload": json.dumps({"error": f"processing_error: {str(e)}", "raw": str(fields)})}
                            )
                            self.redis.xack(key, CONSUMER_GROUP, msg_id)
                            self.redis.xdel(key, msg_id)
                tasks.append(asyncio.create_task(_one()))

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def run(self):
        """Main worker loop - processes messages from Redis Streams with Pub/Sub notifications"""
        key = stream_key(self.instance)
        self.running = True

        logger.info(f"Starting worker loop for {self.instance}")
        logger.info(f"Stream key: {key}")
        logger.info(f"Consumer group: {CONSUMER_GROUP}")
        logger.info(f"Consumer name: {self.consumer_name}")
        logger.info(f"Configuration: rate={TOKENS_PER_SECOND} msg/s, max_deliveries={MAX_DELIVERIES}")
        logger.info(f"Pub/Sub enabled: {USE_PUBSUB} (fallback poll every {PUBSUB_TIMEOUT}s)")

        # Inspect current group/consumers
        try:
            depth = self.redis.xlen(key)
            logger.info(f"Initial queue depth: {depth} messages")
            try:
                for g in self.redis.xinfo_groups(key):
                    if g.get("name") == CONSUMER_GROUP:
                        logger.info(
                            f"Group status: consumers={g.get('consumers')} pending={g.get('pending')} last_delivered={g.get('last-delivered-id')}"
                        )
            except Exception as e:
                logger.debug(f"XINFO GROUPS failed (non-fatal): {e}")
        except Exception as e:
            logger.error(f"Failed to query stream stats: {e}")

        # Register this worker as a consumer
        try:
            loop = asyncio.get_event_loop()
            def _register_consumer():
                return self.redis.xreadgroup(
                    groupname=CONSUMER_GROUP,
                    consumername=self.consumer_name,
                    streams={key: ">"},
                    count=0,
                    block=1
                )
            await loop.run_in_executor(None, _register_consumer)
            logger.info("Registered consumer with XREADGROUP noop (>)")
        except Exception as e:
            logger.debug(f"Consumer registration noop failed (non-fatal): {e}")

        # Subscribe to Pub/Sub notifications if enabled
        if USE_PUBSUB:
            try:
                self.pubsub = self.redis.pubsub()
                channel = notification_channel(self.instance)
                self.pubsub.subscribe(channel)
                logger.info(f"✅ Subscribed to Pub/Sub channel: {channel}")
            except Exception as e:
                logger.error(f"Failed to subscribe to Pub/Sub: {e}. Falling back to polling.")
                self.pubsub = None

        iteration = 0
        last_heartbeat = time.time()
        heartbeat_interval = 300  # 5 minutes

        while self.running:
            try:
                iteration += 1

                # Log heartbeat every 5 minutes
                current_time = time.time()
                if current_time - last_heartbeat >= heartbeat_interval:
                    logger.info(
                        f"Worker heartbeat - processed={self.processed_count}, "
                        f"failed={self.failed_count}, pubsub_notifications={self.pubsub_notifications_received}"
                    )
                    last_heartbeat = current_time

                # === Phase 2: Pub/Sub-based wake-up ===
                if USE_PUBSUB and self.pubsub:
                    # Wait for notification or timeout
                    loop = asyncio.get_event_loop()

                    def _get_message():
                        # get_message() blocks up to timeout (in seconds)
                        return self.pubsub.get_message(timeout=PUBSUB_TIMEOUT, ignore_subscribe_messages=True)

                    msg = await loop.run_in_executor(None, _get_message)

                    if msg and msg['type'] == 'message':
                        # Notification received! Drain the queue
                        self.pubsub_notifications_received += 1
                        logger.debug(f"Pub/Sub notification received, draining queue")
                        await self._drain_queue()
                    elif msg is None:
                        # Timeout reached (30s), do fallback poll
                        logger.debug(f"Pub/Sub timeout ({PUBSUB_TIMEOUT}s), fallback poll")
                        await self._drain_queue()
                    # else: ignore other message types (subscribe confirmations, etc.)

                else:
                    # === Legacy: Polling mode (if Pub/Sub disabled) ===
                    loop = asyncio.get_event_loop()

                    # 1) Claim orphaned messages
                    def _autoclaim():
                        try:
                            reply = self.redis.xautoclaim(
                                key,
                                CONSUMER_GROUP,
                                self.consumer_name,
                                min_idle_time=CLAIM_IDLE_MS,
                                start_id=self._autoclaim_cursor,
                                count=READ_COUNT,
                            )
                            if isinstance(reply, (list, tuple)) and len(reply) >= 2:
                                next_id = reply[0]
                                claimed = reply[1] or []
                                self._autoclaim_cursor = next_id if isinstance(next_id, str) else str(next_id)
                                if claimed:
                                    return [(key, claimed)]
                            return []
                        except Exception as e:
                            logger.debug(f"XAUTOCLAIM failed: {e}")
                            return []

                    msgs = await loop.run_in_executor(None, _autoclaim)

                    # 2) Read new messages
                    if not msgs:
                        def _read_new():
                            return self.redis.xreadgroup(
                                groupname=CONSUMER_GROUP,
                                consumername=self.consumer_name,
                                streams={key: ">"},
                                count=READ_COUNT,
                                block=BLOCK_MS
                            )
                        msgs = await loop.run_in_executor(None, _read_new)

                    if not msgs:
                        # No messages available - sleep briefly
                        await asyncio.sleep(IDLE_SLEEP_BASE * random.uniform(0.9, 1.3))
                        continue

                    # Process messages
                    tasks = []
                    for _, entries in msgs:
                        if len(entries) > 0:
                            logger.info(f"📬 Processing {len(entries)} message(s) from queue")
                        for msg_id, fields in entries:
                            async def _one(msg_id=msg_id, fields=fields):
                                async with self._sema:
                                    try:
                                        payload = json.loads(fields.get("payload", "{}"))
                                    except json.JSONDecodeError as e:
                                        logger.error(f"Failed to parse message {msg_id}: {e}")
                                        self.redis.xadd(
                                            dlq_key(self.instance),
                                            fields={"payload": json.dumps({"error": f"json_decode_error: {str(e)}", "raw": str(fields)})}
                                        )
                                        self.redis.xack(key, CONSUMER_GROUP, msg_id)
                                        self.redis.xdel(key, msg_id)
                                        return
                                    try:
                                        await self.process_message(msg_id, payload)
                                    except Exception as e:
                                        logger.error(f"Error processing message {msg_id}: {e}", exc_info=True)
                                        self.redis.xadd(
                                            dlq_key(self.instance),
                                            fields={"payload": json.dumps({"error": f"processing_error: {str(e)}", "raw": str(fields)})}
                                        )
                                        self.redis.xack(key, CONSUMER_GROUP, msg_id)
                                        self.redis.xdel(key, msg_id)
                            tasks.append(asyncio.create_task(_one()))

                    if tasks:
                        await asyncio.gather(*tasks, return_exceptions=True)

            except Exception as e:
                logger.error(f"Error in worker loop: {e}", exc_info=True)
                await asyncio.sleep(0.5)

        # Cleanup on shutdown
        if self.pubsub:
            try:
                self.pubsub.unsubscribe()
                self.pubsub.close()
            except Exception as e:
                logger.debug(f"Error closing Pub/Sub: {e}")

        logger.info(f"Worker stopped. Processed: {self.processed_count}, Failed: {self.failed_count}")

    async def stop(self):
        """Stop the worker gracefully"""
        logger.info(f"Stopping worker for {self.instance}")
        self.running = False

    def get_stats(self) -> Dict[str, Any]:
        """Get worker statistics"""
        return {
            "instance": self.instance,
            "consumer_name": self.consumer_name,
            "running": self.running,
            "processed_count": self.processed_count,
            "failed_count": self.failed_count,
            "pubsub_enabled": USE_PUBSUB,
            "pubsub_notifications_received": self.pubsub_notifications_received,
            "pubsub_connected": self.pubsub is not None
        }


def start_worker(instance: str, consumer_name: Optional[str] = None):
    """
    Start the worker (blocking)

    This is a synchronous entry point for running the worker standalone.

    Args:
        instance: WhatsApp instance name
        consumer_name: Optional consumer name
    """
    worker = WhatsAppWorker(instance, consumer_name)
    asyncio.run(worker.run())