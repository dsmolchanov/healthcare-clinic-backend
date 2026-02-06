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

from redis.exceptions import ConnectionError as RedisConnectionError

from .config import (
    CONSUMER_GROUP, MAX_DELIVERIES,
    BASE_BACKOFF, MAX_BACKOFF, TOKENS_PER_SECOND, BUCKET_CAPACITY,
    logger
)
from .queue import (
    stream_key, dlq_key, ensure_group, get_redis_client
)
from .evolution_client import is_connected, send_text
from .rate_limiter import TokenBucket

#
# Tunables (overridable via env)
#
CLAIM_IDLE_MS: int = int(os.getenv("WA_STREAM_CLAIM_IDLE_MS", "15000"))  # XAUTOCLAIM min idle
READ_COUNT:    int = int(os.getenv("WA_READ_COUNT", "32"))                # Messages per read (increased from 10)
BLOCK_MS:      int = int(os.getenv("WA_READ_BLOCK_MS", "250"))            # Read timeout (reduced from 5000ms)

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

    async def run(self):
        """Main worker loop - processes messages from Redis Streams"""
        key = stream_key(self.instance)
        self.running = True

        logger.info(f"Starting worker loop for {self.instance}")
        logger.info(f"Stream key: {key}")
        logger.info(f"Consumer group: {CONSUMER_GROUP}")
        logger.info(f"Consumer name: {self.consumer_name}")
        logger.info(f"Configuration: rate={TOKENS_PER_SECOND} msg/s, max_deliveries={MAX_DELIVERIES}")

        # Inspect current group/consumers, but do NOT force reset (avoids accidental duplicates)
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

        # Register this worker as a consumer with a no-op read (ensures it shows up in XINFO CONSUMERS)
        try:
            loop = asyncio.get_event_loop()
            def _register_consumer():
                # count=0 gives immediate return; block small just in case
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

        iteration = 0
        last_heartbeat = time.time()
        heartbeat_interval = 300  # 5 minutes in seconds

        while self.running:
            try:
                iteration += 1

                # Log heartbeat every 5 minutes
                current_time = time.time()
                if current_time - last_heartbeat >= heartbeat_interval:
                    logger.info(f"Worker heartbeat - processed={self.processed_count}, failed={self.failed_count}")
                    last_heartbeat = current_time

                # Run Redis operations in thread pool (redis client is synchronous)
                loop = asyncio.get_event_loop()

                # 1) Adopt orphaned/pending entries (e.g., stuck under debug consumers) using XAUTOCLAIM
                def _autoclaim():
                    """
                    Robust XAUTOCLAIM wrapper.
                    Redis replies:
                      - (next_id, [(id, {fields})...])                         # Redis 6.2 / redis-py classic
                      - (next_id, [(id, {fields})...], entries_deleted)        # Redis 7.x (some clients)
                    When no claimable entries remain, next_id is '0-0' and list is empty.
                    """
                    try:
                        reply = self.redis.xautoclaim(
                            key,
                            CONSUMER_GROUP,
                            self.consumer_name,
                            min_idle_time=CLAIM_IDLE_MS,
                            start_id=self._autoclaim_cursor,
                            count=READ_COUNT,
                        )

                        # Normalize reply to (next_id, claimed_list[, *_])
                        if isinstance(reply, (list, tuple)) and len(reply) >= 2:
                            next_id = reply[0]
                            claimed = reply[1] or []
                            # Advance cursor
                            self._autoclaim_cursor = next_id if isinstance(next_id, str) else str(next_id)

                            if not claimed:
                                # Expected when nothing is claimable; keep noise low.
                                logger.debug("XAUTOCLAIM: no entries to claim (cursor=%s)", self._autoclaim_cursor)
                                return []

                            # Shape to match xreadgroup: [(stream, [(id, fields), ...])]
                            return [(key, claimed)]

                        # Unexpected-but-nonfatal shapes: log at debug, not warning.
                        logger.debug("XAUTOCLAIM: non-standard reply shape: %r", reply)
                        return []

                    except Exception as e:
                        logger.debug("XAUTOCLAIM unavailable/failed: %s", e)
                        return []

                msgs = await loop.run_in_executor(None, _autoclaim)

                # 2) If nothing claimed, read NEW messages only
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
                    # No messages available - sleep briefly with jitter
                    await asyncio.sleep(IDLE_SLEEP_BASE * random.uniform(0.9, 1.3))
                    continue

                # Process messages with bounded concurrency
                tasks = []
                for stream_name, entries in msgs:
                    n = len(entries)
                    if n > 0:
                        logger.info(f"📬 Processing {n} message(s) from queue")
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
                    # Let tasks finish; avoid unhandled exceptions
                    await asyncio.gather(*tasks, return_exceptions=True)

            except (ConnectionError, RedisConnectionError, OSError, TimeoutError) as e:
                logger.warning(f"Redis connection error: {e}. Reconnecting...")
                try:
                    self.redis = get_redis_client()
                    ensure_group(self.redis, self.instance)
                    logger.info("Redis reconnected successfully")
                except Exception as reconnect_err:
                    logger.error(f"Redis reconnect failed: {reconnect_err}")
                await asyncio.sleep(2)
            except Exception as e:
                logger.error(f"Error in worker loop: {e}", exc_info=True)
                # Check if it's a redis connection error wrapped in another exception
                if "Connection" in str(e) or "closed" in str(e).lower():
                    try:
                        self.redis = get_redis_client()
                        ensure_group(self.redis, self.instance)
                        logger.info("Redis reconnected after wrapped connection error")
                    except Exception:
                        pass
                await asyncio.sleep(1)

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
            "failed_count": self.failed_count
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