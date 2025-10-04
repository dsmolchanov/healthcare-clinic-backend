"""
WhatsApp Message Queue Operations
Redis Streams-based queue with idempotency and async support
"""
import asyncio
import json
import time
import uuid
from typing import Dict, Any, Optional
from redis import Redis

from .config import REDIS_URL, CONSUMER_GROUP, logger

def get_redis_client() -> Redis:
    """Get Redis client (synchronous)"""
    return Redis.from_url(REDIS_URL, decode_responses=True)

# Stream key patterns
STREAM_KEY_TEMPLATE = "wa:{instance}:stream"
DLQ_KEY_TEMPLATE = "wa:{instance}:dlq"
IDEMP_KEY_TEMPLATE = "wa:msg:{message_id}"

def stream_key(instance: str) -> str:
    """Get Redis stream key for an instance"""
    return STREAM_KEY_TEMPLATE.format(instance=instance)

def dlq_key(instance: str) -> str:
    """Get Dead Letter Queue key for an instance"""
    return DLQ_KEY_TEMPLATE.format(instance=instance)

def idempotency_key(message_id: str) -> str:
    """Get idempotency key for a message"""
    return IDEMP_KEY_TEMPLATE.format(message_id=message_id)

def ensure_group(r: Redis, instance: str):
    """
    Create consumer group if it doesn't exist.
    Uses '$' (tail) to prevent "orphaned entry" problem where messages added
    before group creation are considered already-delivered.

    Args:
        r: Redis client
        instance: WhatsApp instance name
    """
    key = stream_key(instance)
    try:
        # Use '$' (tail) instead of '0-0' to only process NEW messages
        # This prevents stuck messages that exist before group creation
        r.xgroup_create(name=key, groupname=CONSUMER_GROUP, id="$", mkstream=True)
        logger.info(f"Created consumer group {CONSUMER_GROUP} for {instance} (reading from tail)")
    except Exception as e:
        # Group already exists or error creating - this is fine
        if "BUSYGROUP" not in str(e):
            logger.debug(f"Consumer group creation note: {e}")
        pass

async def enqueue_message(
    instance: str,
    to_number: str,
    text: str,
    message_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None
) -> bool:
    """
    Queue a WhatsApp message for async sending
    Returns immediately after queueing (non-blocking)

    Args:
        instance: WhatsApp instance name
        to_number: Recipient phone number
        text: Message text content
        message_id: Optional unique message ID (generates UUID if not provided)
        metadata: Optional metadata to attach to message

    Returns:
        True if successfully queued, False otherwise
    """
    if not message_id:
        message_id = str(uuid.uuid4())

    # Run Redis operations in thread pool (Redis client is synchronous)
    loop = asyncio.get_event_loop()

    def _enqueue():
        r = get_redis_client()

        # Check idempotency
        idemp_key = idempotency_key(message_id)
        if not r.set(idemp_key, "1", nx=True, ex=86400):  # 24h TTL
            logger.info(f"Message {message_id} already queued (idempotent)")
            return True

        # Create payload
        payload = {
            "message_id": message_id,
            "to": to_number,
            "text": text,
            "queued_at": time.time(),
            "attempts": 0,
            "metadata": metadata or {}
        }

        # Ensure consumer group exists
        ensure_group(r, instance)

        # Add to stream
        redis_msg_id = r.xadd(
            stream_key(instance),
            fields={"payload": json.dumps(payload)},
            maxlen=10000,  # Keep last 10k messages per instance
            approximate=True
        )

        logger.info(f"Message {message_id} queued to stream: {redis_msg_id}")
        return True

    try:
        success = await loop.run_in_executor(None, _enqueue)
        return success
    except Exception as e:
        logger.error(f"Error enqueueing message: {e}", exc_info=True)
        return False

async def get_queue_depth(instance: str) -> int:
    """
    Get current queue depth for an instance

    Args:
        instance: WhatsApp instance name

    Returns:
        Number of messages in queue
    """
    loop = asyncio.get_event_loop()

    def _get_depth():
        r = get_redis_client()
        key = stream_key(instance)
        try:
            return r.xlen(key)
        except Exception:
            return 0

    return await loop.run_in_executor(None, _get_depth)