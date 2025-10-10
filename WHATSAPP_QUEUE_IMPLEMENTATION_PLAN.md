# WhatsApp Queue-Worker Implementation Plan
**Evolution API Resilience & Message Delivery Reliability**

## Overview

Implement a production-ready queue-worker architecture to decouple HTTP request handling from WhatsApp message sending via Evolution API. This eliminates timeout failures caused by Evolution/Baileys' intermittent connection issues and provides guaranteed message delivery with automatic retries.

**Current Problem:** Evolution API (Baileys) loses WhatsApp Web connection between messages, requiring 60+ second reconnection sequences that exceed timeouts, resulting in failed message deliveries.

**Solution:** Treat Evolution API as an unreliable "black box" and build resilience in our backend using Redis Streams-based queue-worker pattern.

## Current State Analysis

### Existing Architecture
- **Backend**: FastAPI application at `clinics/backend/`
- **Evolution Webhook**: `app/apps/voice-api/evolution_webhook.py` handles incoming WhatsApp messages
- **Message Router**: `app/services/message_router.py` routes to LangGraph for AI processing
- **Current Send Pattern**: Direct HTTP call to Evolution API with fire-and-forget attempt
- **Current Problem**: `send_whatsapp_via_evolution()` function (line 361-477 in evolution_webhook.py) attempts immediate send with no retry mechanism

### Key Discoveries:
- **evolution_webhook.py:268**: Current send function called from background task processor
- **evolution_webhook.py:361-477**: `send_whatsapp_via_evolution()` sends directly to Evolution API
- **No queue infrastructure**: Messages fail permanently if Evolution is disconnected
- **No retry logic**: Single attempt with no fallback mechanism
- **No rate limiting**: Could trigger WhatsApp bans with burst sends
- **Logs show**: Backend processes in 50ms but Evolution times out after 60+ seconds

### Current Flow:
```
WhatsApp Message → Evolution → Backend Webhook (50ms)
                                      ↓
                              AI Processing (50-70ms)
                                      ↓
                        Direct Evolution API Call (FAILS if disconnected)
```

## Desired End State

### Target Architecture:
```
WhatsApp Message → Evolution → Backend Webhook (50ms)
                                      ↓
                              AI Processing (50-70ms)
                                      ↓
                         Redis Queue (5ms) ← Returns immediately
                                      ↓
                              Background Worker
                                      ↓
                         Check Evolution Connection
                                      ↓
                  If open: Send    If closed: Retry with backoff
```

### Success Metrics:
- **Zero failed messages** due to Evolution disconnection
- **< 100ms** webhook response time (independent of Evolution state)
- **Automatic retry** with exponential backoff up to 5 attempts
- **Message delivery** even when Evolution reconnects after 60+ seconds
- **Rate limiting** to prevent WhatsApp bans (1 msg/sec, burst 5)
- **Observability**: Health endpoint showing queue depth and Evolution state

### Verification:
```bash
# Send message during Evolution disconnection
curl -X POST /webhooks/evolution/{instance} -d '{"message": "test"}'
→ Returns 200 OK in < 100ms

# Check health
curl /health/whatsapp
→ {"queue_depth": 1, "evolution_connected": false, "status": "degraded"}

# Wait for Evolution reconnect
# Check health again
curl /health/whatsapp
→ {"queue_depth": 0, "evolution_connected": true, "status": "healthy"}

# Message delivered to WhatsApp user
```

## What We're NOT Doing

- **NOT forking Evolution API** - treating it as immutable black box
- **NOT modifying Baileys** - avoiding upstream maintenance burden
- **NOT implementing custom WebSocket** - relying on Evolution's connection management
- **NOT adding message encryption** - Evolution handles WhatsApp E2E encryption
- **NOT building admin UI** - focusing on core reliability (can add later)
- **NOT implementing message threading** - maintaining simple FIFO per instance
- **NOT adding message deduplication beyond 24h** - relying on Evolution's built-in dedup

## Implementation Approach

Use Redis Streams (not Redis Lists) for:
- **Consumer Groups**: Multiple workers can share load
- **At-least-once delivery**: Automatic message recovery if worker crashes
- **Per-instance streams**: Natural partitioning by WhatsApp instance
- **Pending message tracking**: Built-in monitoring and recovery

Key patterns:
- **Token bucket rate limiting**: Prevent WhatsApp bans
- **Idempotency keys**: Prevent duplicate sends on retries
- **Dead letter queue (DLQ)**: Capture permanently failed messages for manual review
- **Connection pre-check**: Don't attempt send if Evolution is disconnected

---

## Phase 1: Redis Infrastructure Setup

### Overview
Set up Redis instance and integrate connection into backend application. This provides the foundation for the queue-worker system without disrupting existing functionality.

### Changes Required:

#### 1. Deploy Redis on Fly.io
**Commands**:
```bash
fly redis create \
  --name healthcare-clinic-redis \
  --region sjc \
  --plan free

# Save connection URL
fly secrets set REDIS_URL="redis://..." --app healthcare-clinic-backend
```

#### 2. Update Backend Dependencies
**File**: `clinics/backend/requirements.txt`
**Changes**: Add Redis dependencies
```
redis==5.0.1
hiredis==2.2.3  # C parser for better performance
```

#### 3. Add Redis Configuration
**File**: `clinics/backend/app/config.py` (create if doesn't exist)
**Changes**: Add Redis configuration
```python
import os
from redis import Redis

# Redis Configuration
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")

def get_redis_client() -> Redis:
    """Get configured Redis client"""
    return Redis.from_url(
        REDIS_URL,
        decode_responses=True,
        socket_connect_timeout=5,
        socket_timeout=5,
        retry_on_timeout=True
    )
```

#### 4. Add Redis Health Check
**File**: `clinics/backend/app/main.py`
**Changes**: Add Redis health check to existing /health endpoint
```python
from app.config import get_redis_client

@app.get("/health")
async def health():
    """Health check including Redis connectivity"""
    health_status = {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "services": {}
    }

    # Check Redis
    try:
        redis_client = get_redis_client()
        redis_client.ping()
        health_status["services"]["redis"] = "connected"
    except Exception as e:
        health_status["services"]["redis"] = f"error: {e}"
        health_status["status"] = "degraded"

    return health_status
```

### Success Criteria:

#### Automated Verification:
- [ ] Redis deployed on Fly.io: `fly redis list | grep healthcare-clinic-redis`
- [ ] Redis secret configured: `fly secrets list --app healthcare-clinic-backend | grep REDIS_URL`
- [ ] Requirements installed: `pip install -r clinics/backend/requirements.txt`
- [ ] Redis connection works: `python -c "from app.config import get_redis_client; get_redis_client().ping()"`
- [ ] Health endpoint returns Redis status: `curl http://localhost:8080/health | jq .services.redis`

#### Manual Verification:
- [ ] Fly.io Redis dashboard shows active connection
- [ ] Health endpoint shows "redis": "connected"
- [ ] No errors in application logs related to Redis
- [ ] Redis connection survives backend restart

---

## Phase 2: Queue Infrastructure Integration

### Overview
Integrate the provided queue-worker code into the backend codebase, adapting it to our existing architecture and naming conventions.

### Changes Required:

#### 1. Copy Queue Implementation Files
**Directory**: `clinics/backend/app/services/whatsapp_queue/`
**Files to create**:
- `__init__.py` - Package initialization
- `config.py` - Queue configuration (adapted from provided code)
- `queue.py` - Queue operations (Redis Streams helpers)
- `evolution_client.py` - Evolution API client (adapted)
- `e164.py` - Phone number normalization
- `worker.py` - Background worker process
- `health.py` - Health check endpoints

**Adaptations needed**:
```python
# clinics/backend/app/services/whatsapp_queue/config.py
import os

# Inherit from main app config
from app.config import REDIS_URL

# Evolution API settings (from existing code)
EVOLUTION_API_URL = os.getenv("EVOLUTION_API_URL", "https://evolution-api-prod.fly.dev")
EVOLUTION_API_KEY = os.getenv("EVOLUTION_API_KEY", "evolution_api_key_2024")

# Queue settings
CONSUMER_GROUP = "wa_workers"
MAX_DELIVERIES = 5
BASE_BACKOFF = 2.0  # seconds
MAX_BACKOFF = 60.0  # seconds

# Rate limiting (conservative to avoid WhatsApp bans)
TOKENS_PER_SECOND = 1.0  # 1 message per second per instance
BUCKET_CAPACITY = 5  # allow burst of 5 messages

# HTTP timeouts
EVOLUTION_HTTP_TIMEOUT = 15.0  # seconds (Evolution may take time to reconnect)
```

#### 2. Integrate with Existing Evolution Webhook
**File**: `clinics/backend/app/apps/voice-api/evolution_webhook.py`
**Changes**: Replace direct send with queue enqueue

**Before** (line 361-477):
```python
async def send_whatsapp_via_evolution(instance_name: str, to_number: str, text: str) -> bool:
    """Send WhatsApp message via Evolution API and return success flag."""
    # ... current implementation with direct HTTP call
```

**After**:
```python
async def send_whatsapp_via_evolution(instance_name: str, to_number: str, text: str) -> bool:
    """Queue WhatsApp message for async sending (returns immediately)"""
    from app.services.whatsapp_queue import enqueue_message
    import uuid

    message_id = str(uuid.uuid4())

    print(f"[SendMessage] Queueing message {message_id} for {to_number}")
    print(f"[SendMessage] Instance: {instance_name}")
    print(f"[SendMessage] Text length: {len(text)} chars")

    try:
        # Queue the message (non-blocking)
        success = await enqueue_message(
            instance=instance_name,
            to_number=to_number,
            text=text,
            message_id=message_id
        )

        if success:
            print(f"[SendMessage] ✅ Message queued successfully (id: {message_id})")
            return True
        else:
            print(f"[SendMessage] ❌ Failed to queue message")
            return False

    except Exception as e:
        print(f"[SendMessage] ❌ Queue error: {e}")
        return False
```

#### 3. Create Queue Helper Module
**File**: `clinics/backend/app/services/whatsapp_queue/__init__.py`
**Changes**: Export main queue functions
```python
"""WhatsApp Queue-Worker System"""

from .queue import enqueue_message, get_queue_depth
from .worker import start_worker
from .health import get_whatsapp_health

__all__ = [
    'enqueue_message',
    'get_queue_depth',
    'start_worker',
    'get_whatsapp_health'
]
```

#### 4. Create Async Enqueue Function
**File**: `clinics/backend/app/services/whatsapp_queue/queue.py`
**Changes**: Add async wrapper for Redis operations
```python
import asyncio
import json
import time
import uuid
from typing import Dict, Any, Optional
from redis import Redis
from .config import REDIS_URL, CONSUMER_GROUP

def get_redis_client() -> Redis:
    """Get Redis client (synchronous)"""
    return Redis.from_url(REDIS_URL, decode_responses=True)

# Stream key patterns
STREAM_KEY_TEMPLATE = "wa:{instance}:stream"
DLQ_KEY_TEMPLATE = "wa:{instance}:dlq"
IDEMP_KEY_TEMPLATE = "wa:msg:{message_id}"

def stream_key(instance: str) -> str:
    return STREAM_KEY_TEMPLATE.format(instance=instance)

def dlq_key(instance: str) -> str:
    return DLQ_KEY_TEMPLATE.format(instance=instance)

def idempotency_key(message_id: str) -> str:
    return IDEMP_KEY_TEMPLATE.format(message_id=message_id)

def ensure_group(r: Redis, instance: str):
    """Create consumer group if it doesn't exist"""
    key = stream_key(instance)
    try:
        r.xgroup_create(name=key, groupname=CONSUMER_GROUP, id="0-0", mkstream=True)
    except Exception:
        # Group already exists
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
    """
    if not message_id:
        message_id = str(uuid.uuid4())

    # Run Redis operations in thread pool (Redis is synchronous)
    loop = asyncio.get_event_loop()

    def _enqueue():
        r = get_redis_client()

        # Check idempotency
        idemp_key = idempotency_key(message_id)
        if not r.set(idemp_key, "1", nx=True, ex=86400):  # 24h TTL
            print(f"[Queue] Message {message_id} already queued (idempotent)")
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

        print(f"[Queue] Message {message_id} added to stream: {redis_msg_id}")
        return True

    try:
        success = await loop.run_in_executor(None, _enqueue)
        return success
    except Exception as e:
        print(f"[Queue] Error enqueueing message: {e}")
        return False

async def get_queue_depth(instance: str) -> int:
    """Get current queue depth for an instance"""
    loop = asyncio.get_event_loop()

    def _get_depth():
        r = get_redis_client()
        key = stream_key(instance)
        try:
            return r.xlen(key)
        except Exception:
            return 0

    return await loop.run_in_executor(None, _get_depth)
```

### Success Criteria:

#### Automated Verification:
- [ ] Queue module imports successfully: `python -c "from app.services.whatsapp_queue import enqueue_message"`
- [ ] Redis streams created: `redis-cli XINFO GROUPS wa:clinic-...:stream`
- [ ] Message enqueued successfully: Test with curl to webhook endpoint
- [ ] Idempotency works: Send same message twice, verify only one queued
- [ ] Queue depth reported correctly: `curl /health/whatsapp | jq .queue_depth`

#### Manual Verification:
- [ ] Webhook returns 200 OK in < 100ms regardless of Evolution state
- [ ] Messages appear in Redis stream: `redis-cli XLEN wa:clinic-...:stream`
- [ ] No errors in backend logs
- [ ] Existing webhook functionality unchanged (messages still routed to AI)

---

## Phase 3: Background Worker Implementation

### Overview
Implement the background worker process that consumes messages from the queue and sends them via Evolution API with retry logic and rate limiting.

### Changes Required:

#### 1. Copy Worker Implementation
**File**: `clinics/backend/app/services/whatsapp_queue/worker.py`
**Changes**: Adapt provided worker code to our config
```python
import asyncio
import json
import os
import time
from typing import Dict, Any
from redis import Redis

from .config import (
    REDIS_URL, CONSUMER_GROUP, MAX_DELIVERIES,
    BASE_BACKOFF, MAX_BACKOFF, TOKENS_PER_SECOND, BUCKET_CAPACITY
)
from .queue import (
    stream_key, dlq_key, ensure_group, get_redis_client,
    idempotency_key
)
from .evolution_client import is_connected, send_text
from .rate_limiter import TokenBucket

def exponential_backoff(attempts: int, base: float = BASE_BACKOFF, cap: float = MAX_BACKOFF) -> float:
    """Calculate exponential backoff with jitter"""
    delay = min(cap, base * (2 ** max(0, attempts - 1)))
    # Add jitter (50%)
    import random
    return delay * random.uniform(0.5, 1.5)

class WhatsAppWorker:
    """Background worker that processes WhatsApp message queue"""

    def __init__(self, instance: str, consumer_name: Optional[str] = None):
        self.instance = instance
        self.consumer_name = consumer_name or f"worker-{int(time.time())}"
        self.redis = get_redis_client()
        self.rate_limiter = TokenBucket(
            instance=instance,
            tokens_per_second=TOKENS_PER_SECOND,
            capacity=BUCKET_CAPACITY
        )
        ensure_group(self.redis, instance)
        print(f"[Worker] Initialized for instance={instance}, consumer={self.consumer_name}")

    async def process_message(self, redis_msg_id: str, payload: Dict[str, Any]) -> None:
        """Process a single message from the queue"""
        message_id = payload["message_id"]
        to = payload["to"]
        text = payload["text"]
        attempts = int(payload.get("attempts", 0))

        print(f"[Worker] Processing message {message_id} (attempt {attempts+1}/{MAX_DELIVERIES})")

        # Rate limiting
        await self.rate_limiter.wait_for_token()

        # Check Evolution connection
        if not await is_connected(self.instance):
            print(f"[Worker] Evolution not connected, will retry")
            await self._retry_message(payload, redis_msg_id, attempts + 1)
            return

        # Send message
        print(f"[Worker] Sending message {message_id} to {to}")
        success = await send_text(self.instance, to, text)

        if success:
            print(f"[Worker] ✅ Message {message_id} sent successfully")
            # Acknowledge and delete from stream
            self.redis.xack(stream_key(self.instance), CONSUMER_GROUP, redis_msg_id)
            self.redis.xdel(stream_key(self.instance), redis_msg_id)
        else:
            print(f"[Worker] ❌ Failed to send message {message_id}")
            await self._retry_message(payload, redis_msg_id, attempts + 1)

    async def _retry_message(self, payload: Dict[str, Any], redis_msg_id: str, attempts: int):
        """Retry a failed message or move to DLQ"""
        if attempts >= MAX_DELIVERIES:
            print(f"[Worker] Message {payload['message_id']} exceeded max deliveries, moving to DLQ")
            # Move to dead letter queue
            payload["final_error"] = "max_deliveries_exceeded"
            self.redis.xadd(dlq_key(self.instance), fields={"payload": json.dumps(payload)})
            # Remove from main stream
            self.redis.xack(stream_key(self.instance), CONSUMER_GROUP, redis_msg_id)
            self.redis.xdel(stream_key(self.instance), redis_msg_id)
        else:
            # Calculate backoff and re-queue
            delay = exponential_backoff(attempts)
            print(f"[Worker] Retrying message {payload['message_id']} in {delay:.1f}s")
            await asyncio.sleep(delay)

            # Update attempts and re-add to stream
            payload["attempts"] = attempts
            self.redis.xadd(stream_key(self.instance), fields={"payload": json.dumps(payload)})

            # Remove old message
            self.redis.xack(stream_key(self.instance), CONSUMER_GROUP, redis_msg_id)
            self.redis.xdel(stream_key(self.instance), redis_msg_id)

    async def run(self):
        """Main worker loop"""
        key = stream_key(self.instance)
        print(f"[Worker] Starting worker loop for {self.instance}")

        while True:
            try:
                # First, recover any pending messages for this consumer
                msgs = self.redis.xreadgroup(
                    groupname=CONSUMER_GROUP,
                    consumername=self.consumer_name,
                    streams={key: "0"},  # "0" = pending messages
                    count=10,
                    block=1000  # 1 second timeout
                )

                # If no pending, read new messages
                if not msgs:
                    msgs = self.redis.xreadgroup(
                        groupname=CONSUMER_GROUP,
                        consumername=self.consumer_name,
                        streams={key: ">"},  # ">" = new messages only
                        count=10,
                        block=5000  # 5 second timeout
                    )

                if not msgs:
                    await asyncio.sleep(0.1)
                    continue

                # Process messages
                for _key, entries in msgs:
                    for msg_id, fields in entries:
                        try:
                            payload = json.loads(fields.get("payload", "{}"))
                            await self.process_message(msg_id, payload)
                        except Exception as e:
                            print(f"[Worker] Error processing message {msg_id}: {e}")
                            # Move to DLQ with error
                            self.redis.xadd(
                                dlq_key(self.instance),
                                fields={"payload": json.dumps({"error": str(e), "raw": fields})}
                            )
                            self.redis.xack(key, CONSUMER_GROUP, msg_id)
                            self.redis.xdel(key, msg_id)

            except Exception as e:
                print(f"[Worker] Error in worker loop: {e}")
                await asyncio.sleep(0.5)

def start_worker(instance: str, consumer_name: Optional[str] = None):
    """Start the worker (blocking)"""
    worker = WhatsAppWorker(instance, consumer_name)
    asyncio.run(worker.run())
```

#### 2. Implement Rate Limiter
**File**: `clinics/backend/app/services/whatsapp_queue/rate_limiter.py`
**Changes**: Token bucket implementation
```python
import asyncio
import time
from redis import Redis
from .queue import get_redis_client

class TokenBucket:
    """Token bucket rate limiter using Redis"""

    def __init__(self, instance: str, tokens_per_second: float, capacity: int):
        self.instance = instance
        self.tokens_per_second = tokens_per_second
        self.capacity = capacity
        self.redis = get_redis_client()

        # Redis keys
        self.bucket_key = f"wa:{instance}:bucket"
        self.timestamp_key = f"wa:{instance}:bucket:ts"

    def _refill(self):
        """Refill tokens based on elapsed time"""
        now = time.time()
        last_ts = self.redis.get(self.timestamp_key)

        if last_ts is None:
            # Initialize
            self.redis.set(self.timestamp_key, str(now))
            self.redis.set(self.bucket_key, self.capacity)
            return

        last = float(last_ts)
        elapsed = max(0.0, now - last)
        tokens_to_add = int(elapsed * self.tokens_per_second)

        if tokens_to_add > 0:
            self.redis.set(self.timestamp_key, str(now))
            current = int(self.redis.get(self.bucket_key) or 0)
            new_count = min(self.capacity, current + tokens_to_add)
            self.redis.set(self.bucket_key, new_count)

    def _take_token(self) -> bool:
        """Try to take one token from bucket"""
        self._refill()

        # Atomic decrement if available
        with self.redis.pipeline() as pipe:
            try:
                pipe.watch(self.bucket_key)
                current = int(pipe.get(self.bucket_key) or 0)

                if current <= 0:
                    pipe.unwatch()
                    return False

                pipe.multi()
                pipe.decr(self.bucket_key)
                pipe.execute()
                return True
            except Exception:
                return False

    async def wait_for_token(self):
        """Wait until a token is available (with exponential backoff)"""
        attempt = 0
        while not self._take_token():
            # Exponential backoff (up to 1 second)
            delay = min(1.0, 0.1 * (2 ** attempt))
            await asyncio.sleep(delay)
            attempt += 1

            if attempt >= 10:  # Max ~10 seconds wait
                print(f"[RateLimit] Warning: Long wait for token on {self.instance}")
                attempt = 5  # Reset to moderate backoff
```

#### 3. Adapt Evolution Client
**File**: `clinics/backend/app/services/whatsapp_queue/evolution_client.py`
**Changes**: Use existing Evolution API patterns
```python
import httpx
from .config import EVOLUTION_API_URL, EVOLUTION_API_KEY, EVOLUTION_HTTP_TIMEOUT
from .e164 import to_jid

async def is_connected(instance: str) -> bool:
    """Check if Evolution instance is connected to WhatsApp"""
    url = f"{EVOLUTION_API_URL}/instance/connectionState/{instance}"
    headers = {"apikey": EVOLUTION_API_KEY}

    try:
        async with httpx.AsyncClient(timeout=EVOLUTION_HTTP_TIMEOUT) as client:
            response = await client.get(url, headers=headers)

            if response.status_code != 200:
                return False

            data = response.json()
            # Handle different response formats
            state = data.get("instance", {}).get("state") or data.get("state")
            return state == "open"

    except Exception as e:
        print(f"[EvolutionClient] Connection check failed: {e}")
        return False

async def send_text(instance: str, to_number: str, text: str) -> bool:
    """Send text message via Evolution API"""
    url = f"{EVOLUTION_API_URL}/message/sendText/{instance}"
    headers = {
        "apikey": EVOLUTION_API_KEY,
        "Content-Type": "application/json"
    }

    # Convert to WhatsApp JID format
    jid_number = to_jid(to_number)

    payload = {
        "number": jid_number,
        "text": text,
        "delay": 1000  # 1 second natural delay
    }

    try:
        print(f"[EvolutionClient] Sending to {jid_number} via {instance}")
        async with httpx.AsyncClient(timeout=EVOLUTION_HTTP_TIMEOUT) as client:
            response = await client.post(url, headers=headers, json=payload)

            if response.status_code < 400:
                print(f"[EvolutionClient] ✅ Message sent (status {response.status_code})")
                return True
            else:
                print(f"[EvolutionClient] ❌ Failed with status {response.status_code}")
                return False

    except Exception as e:
        print(f"[EvolutionClient] ❌ Send error: {e}")
        return False
```

#### 4. Copy Phone Number Utility
**File**: `clinics/backend/app/services/whatsapp_queue/e164.py`
**Changes**: Copy as-is from provided code
```python
def to_jid(number: str) -> str:
    """
    Convert phone number to WhatsApp JID format
    Examples:
      +79857608984 → 79857608984@s.whatsapp.net
      79857608984 → 79857608984@s.whatsapp.net
    """
    # Remove existing JID suffix if present
    clean = number.replace("@s.whatsapp.net", "")
    # Remove + and any formatting
    clean = clean.replace("+", "").replace(" ", "").replace("-", "")
    # Add JID suffix
    return f"{clean}@s.whatsapp.net"
```

#### 5. Start Worker on Application Startup
**File**: `clinics/backend/app/main.py`
**Changes**: Add worker startup in background
```python
import asyncio
from app.services.whatsapp_queue.worker import WhatsAppWorker

# Store worker reference
worker_task = None

@app.on_event("startup")
async def startup_worker():
    """Start WhatsApp queue worker on application startup"""
    global worker_task

    # Get instance name from environment
    instance_name = os.getenv("INSTANCE_NAME", "clinic-3e411ecb-3411-4add-91e2-8fa897310cb0-1757905315621")

    print(f"[Startup] Starting WhatsApp worker for instance: {instance_name}")

    # Create and start worker in background
    worker = WhatsAppWorker(instance=instance_name)
    worker_task = asyncio.create_task(worker.run())

    print("[Startup] ✅ WhatsApp queue worker started")

@app.on_event("shutdown")
async def shutdown_worker():
    """Gracefully shutdown worker"""
    global worker_task

    if worker_task:
        print("[Shutdown] Stopping WhatsApp worker...")
        worker_task.cancel()
        try:
            await worker_task
        except asyncio.CancelledError:
            pass
        print("[Shutdown] ✅ WhatsApp worker stopped")
```

### Success Criteria:

#### Automated Verification:
- [ ] Worker starts without errors: Check application logs for "[Startup] ✅ WhatsApp queue worker started"
- [ ] Worker consumes messages: Queue message and verify it disappears from stream
- [ ] Rate limiting works: Queue 10 messages, verify they send at ~1/second
- [ ] Retry logic works: Disconnect Evolution, queue message, verify retries with backoff
- [ ] DLQ captures failed messages: After 5 failed attempts, message appears in DLQ
- [ ] Worker recovers pending messages: Kill worker mid-processing, restart, verify message reprocessed

#### Manual Verification:
- [ ] Send test message while Evolution is connected → delivered within 2 seconds
- [ ] Send test message while Evolution is disconnected → delivered after reconnection
- [ ] Send 10 messages rapidly → all delivered at controlled rate (no bans)
- [ ] Restart backend → worker resumes processing pending messages
- [ ] Check DLQ after failures → messages contain useful debugging info

---

## Phase 4: Health & Observability

### Overview
Add comprehensive health checks and observability endpoints to monitor queue health, Evolution connection status, and message processing metrics.

### Changes Required:

#### 1. Create Health Check Module
**File**: `clinics/backend/app/services/whatsapp_queue/health.py`
**Changes**: Implement health check logic
```python
from typing import Dict, Any
from .queue import get_redis_client, stream_key, dlq_key
from .evolution_client import is_connected

async def get_whatsapp_health(instance: str) -> Dict[str, Any]:
    """
    Get comprehensive health status for WhatsApp queue system

    Returns:
        {
            "queue_depth": int,
            "dlq_depth": int,
            "evolution_connected": bool,
            "status": "healthy" | "degraded" | "unhealthy",
            "details": {...}
        }
    """
    redis = get_redis_client()

    # Get queue depths
    try:
        queue_depth = redis.xlen(stream_key(instance))
        dlq_depth = redis.xlen(dlq_key(instance))
    except Exception as e:
        return {
            "status": "unhealthy",
            "error": f"Redis error: {e}"
        }

    # Check Evolution connection
    evolution_connected = await is_connected(instance)

    # Determine overall status
    if queue_depth > 1000:
        status = "unhealthy"
    elif queue_depth > 100 or not evolution_connected:
        status = "degraded"
    else:
        status = "healthy"

    return {
        "queue_depth": queue_depth,
        "dlq_depth": dlq_depth,
        "evolution_connected": evolution_connected,
        "status": status,
        "details": {
            "instance": instance,
            "queue_high_water_mark": 100,
            "queue_critical_mark": 1000
        }
    }

async def get_detailed_metrics(instance: str) -> Dict[str, Any]:
    """Get detailed metrics for monitoring/alerting"""
    redis = get_redis_client()
    health = await get_whatsapp_health(instance)

    # Get consumer group info
    try:
        stream_info = redis.xinfo_stream(stream_key(instance))
        group_info = redis.xinfo_groups(stream_key(instance))
    except Exception:
        stream_info = {}
        group_info = []

    return {
        **health,
        "metrics": {
            "stream_length": stream_info.get("length", 0),
            "stream_first_entry_id": stream_info.get("first-entry"),
            "stream_last_entry_id": stream_info.get("last-entry"),
            "consumer_groups": len(group_info),
            "group_details": group_info
        }
    }
```

#### 2. Add Health Endpoints to API
**File**: `clinics/backend/app/main.py`
**Changes**: Add new health check endpoints
```python
from app.services.whatsapp_queue.health import get_whatsapp_health, get_detailed_metrics

@app.get("/health/whatsapp")
async def whatsapp_health():
    """
    WhatsApp queue health check
    Returns queue depth, Evolution connection status, and overall health
    """
    instance_name = os.getenv("INSTANCE_NAME", "clinic-3e411ecb-3411-4add-91e2-8fa897310cb0-1757905315621")
    return await get_whatsapp_health(instance_name)

@app.get("/health/whatsapp/detailed")
async def whatsapp_health_detailed():
    """
    Detailed WhatsApp queue metrics for monitoring/alerting
    Includes consumer group info, stream metadata, etc.
    """
    instance_name = os.getenv("INSTANCE_NAME", "clinic-3e411ecb-3411-4add-91e2-8fa897310cb0-1757905315621")
    return await get_detailed_metrics(instance_name)

@app.get("/health/whatsapp/dlq")
async def whatsapp_dlq():
    """
    Get dead letter queue messages
    Returns failed messages that need manual intervention
    """
    instance_name = os.getenv("INSTANCE_NAME", "clinic-3e411ecb-3411-4add-91e2-8fa897310cb0-1757905315621")
    redis = get_redis_client()

    try:
        # Get last 100 DLQ messages
        messages = redis.xrevrange(dlq_key(instance_name), count=100)

        dlq_messages = []
        for msg_id, fields in messages:
            import json
            payload = json.loads(fields.get("payload", "{}"))
            dlq_messages.append({
                "redis_id": msg_id,
                "message_id": payload.get("message_id"),
                "to": payload.get("to"),
                "text": payload.get("text", "")[:50] + "...",  # Preview
                "error": payload.get("final_error"),
                "attempts": payload.get("attempts")
            })

        return {
            "dlq_depth": len(dlq_messages),
            "messages": dlq_messages
        }
    except Exception as e:
        return {"error": str(e)}
```

#### 3. Add Logging Configuration
**File**: `clinics/backend/app/services/whatsapp_queue/config.py`
**Changes**: Add structured logging
```python
import logging

# Configure logger for queue system
logger = logging.getLogger("whatsapp_queue")
logger.setLevel(logging.INFO)

# Add handler if not already configured
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter(
        "[%(asctime)s] [%(name)s] %(levelname)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)
```

Replace `print()` statements in worker and queue modules with `logger` calls:
```python
# Before
print(f"[Worker] Processing message {message_id}")

# After
from .config import logger
logger.info(f"Processing message {message_id}", extra={"message_id": message_id})
```

### Success Criteria:

#### Automated Verification:
- [ ] Health endpoint accessible: `curl http://localhost:8080/health/whatsapp`
- [ ] Health returns correct queue depth: Queue 5 messages, verify depth=5
- [ ] Health reflects Evolution state: Disconnect Evolution, verify evolution_connected=false
- [ ] Detailed metrics endpoint works: `curl http://localhost:8080/health/whatsapp/detailed`
- [ ] DLQ endpoint accessible: `curl http://localhost:8080/health/whatsapp/dlq`
- [ ] Status changes with queue depth: Queue 150 messages, verify status="degraded"

#### Manual Verification:
- [ ] Health endpoint shows "healthy" when system operating normally
- [ ] Health endpoint shows "degraded" when Evolution disconnected
- [ ] Health endpoint shows "unhealthy" when queue depth > 1000
- [ ] DLQ endpoint shows failed messages with useful error information
- [ ] Logs are structured and include relevant context (message_id, instance, etc.)
- [ ] Metrics can be parsed for external monitoring (Prometheus, DataDog, etc.)

---

## Testing Strategy

### Unit Tests

**File**: `clinics/backend/tests/test_whatsapp_queue.py`
```python
import pytest
import asyncio
from app.services.whatsapp_queue import enqueue_message, get_queue_depth
from app.services.whatsapp_queue.queue import idempotency_key, get_redis_client

@pytest.mark.asyncio
async def test_enqueue_message():
    """Test message enqueuing"""
    instance = "test-instance"
    result = await enqueue_message(
        instance=instance,
        to_number="+79857608984",
        text="Test message",
        message_id="test-123"
    )
    assert result is True

    # Verify in Redis
    depth = await get_queue_depth(instance)
    assert depth >= 1

@pytest.mark.asyncio
async def test_idempotency():
    """Test idempotency prevents duplicates"""
    instance = "test-instance"
    message_id = "test-idemp-123"

    # First enqueue
    result1 = await enqueue_message(
        instance=instance,
        to_number="+79857608984",
        text="Test",
        message_id=message_id
    )
    assert result1 is True

    # Second enqueue with same ID
    result2 = await enqueue_message(
        instance=instance,
        to_number="+79857608984",
        text="Test",
        message_id=message_id
    )
    assert result2 is True  # Still returns True (idempotent)

    # Verify only one message in queue
    redis = get_redis_client()
    key = idempotency_key(message_id)
    assert redis.exists(key) == 1

@pytest.mark.asyncio
async def test_exponential_backoff():
    """Test backoff calculation"""
    from app.services.whatsapp_queue.worker import exponential_backoff

    # First attempt
    delay1 = exponential_backoff(1, base=2.0, cap=60.0)
    assert 1.0 <= delay1 <= 3.0  # 2 * [0.5, 1.5]

    # Third attempt
    delay3 = exponential_backoff(3, base=2.0, cap=60.0)
    assert 4.0 <= delay3 <= 12.0  # 8 * [0.5, 1.5]

    # High attempt (capped)
    delay10 = exponential_backoff(10, base=2.0, cap=60.0)
    assert delay10 <= 90.0  # 60 * 1.5
```

### Integration Tests

**File**: `clinics/backend/tests/integration/test_whatsapp_flow.py`
```python
import pytest
import asyncio
from app.services.whatsapp_queue import enqueue_message, get_queue_depth
from app.services.whatsapp_queue.health import get_whatsapp_health

@pytest.mark.integration
@pytest.mark.asyncio
async def test_complete_flow():
    """Test complete message flow: enqueue → worker → Evolution"""
    instance = "clinic-3e411ecb-3411-4add-91e2-8fa897310cb0-1757905315621"

    # Enqueue message
    success = await enqueue_message(
        instance=instance,
        to_number="+79857608984",
        text="Integration test message"
    )
    assert success is True

    # Check health
    health = await get_whatsapp_health(instance)
    assert health["queue_depth"] >= 1

    # Wait for worker to process (with timeout)
    max_wait = 30  # 30 seconds
    start = asyncio.get_event_loop().time()

    while True:
        depth = await get_queue_depth(instance)
        if depth == 0:
            break

        elapsed = asyncio.get_event_loop().time() - start
        if elapsed > max_wait:
            pytest.fail("Message not processed within 30 seconds")

        await asyncio.sleep(1)

    # Verify message was processed
    final_depth = await get_queue_depth(instance)
    assert final_depth == 0
```

### Manual Testing Steps

#### 1. Basic Flow Test
```bash
# 1. Start backend
cd clinics/backend
uvicorn app.main:app --reload

# 2. Send test message
curl -X POST http://localhost:8080/webhooks/evolution/clinic-3e411ecb-3411-4add-91e2-8fa897310cb0-1757905315621 \
  -H "Content-Type: application/json" \
  -d '{
    "instanceName": "clinic-3e411ecb-3411-4add-91e2-8fa897310cb0-1757905315621",
    "message": {
      "key": {"remoteJid": "79857608984@s.whatsapp.net", "fromMe": false},
      "message": {"conversation": "Test from queue"},
      "pushName": "Test User"
    }
  }'

# 3. Check health
curl http://localhost:8080/health/whatsapp | jq

# Expected: queue_depth=1, evolution_connected=true

# 4. Wait 2-3 seconds

# 5. Check health again
curl http://localhost:8080/health/whatsapp | jq

# Expected: queue_depth=0 (message processed)

# 6. Verify WhatsApp delivery
# Check phone +79857608984 for AI response
```

#### 2. Resilience Test (Evolution Disconnected)
```bash
# 1. Stop Evolution API temporarily
fly scale count 0 --app evolution-api-prod

# 2. Send test message
curl -X POST http://localhost:8080/webhooks/evolution/...
# Should return 200 OK immediately

# 3. Check health
curl http://localhost:8080/health/whatsapp | jq
# Expected: queue_depth=1, evolution_connected=false, status="degraded"

# 4. Check worker logs
# Should see: "[Worker] Evolution not connected, will retry"

# 5. Restart Evolution
fly scale count 1 --app evolution-api-prod

# 6. Wait for reconnection (~30-60 seconds)

# 7. Check health again
curl http://localhost:8080/health/whatsapp | jq
# Expected: queue_depth=0, evolution_connected=true, status="healthy"

# 8. Verify message delivered to WhatsApp
```

#### 3. Load Test (Rate Limiting)
```bash
# Send 10 messages rapidly
for i in {1..10}; do
  curl -X POST http://localhost:8080/webhooks/evolution/... \
    -H "Content-Type: application/json" \
    -d "{\"message\": {\"conversation\": \"Test $i\"}}"
done

# Check health
curl http://localhost:8080/health/whatsapp | jq

# Monitor worker logs - should see rate limiting in action
# Messages should be sent at ~1 per second, not all at once

# Verify all 10 messages delivered without WhatsApp ban
```

#### 4. DLQ Test (Permanent Failure)
```bash
# 1. Queue message with invalid number
curl -X POST http://localhost:8080/webhooks/evolution/... \
  -d '{"message": {"conversation": "Test", "key": {"remoteJid": "invalid"}}}'

# 2. Wait for 5 retry attempts (~2-5 minutes with exponential backoff)

# 3. Check DLQ
curl http://localhost:8080/health/whatsapp/dlq | jq

# Expected: DLQ contains message with error details
# {
#   "dlq_depth": 1,
#   "messages": [{
#     "message_id": "...",
#     "to": "invalid",
#     "error": "max_deliveries_exceeded",
#     "attempts": 5
#   }]
# }
```

---

## Performance Considerations

### Expected Performance Metrics

| Metric | Target | Measurement |
|--------|--------|-------------|
| Webhook response time | < 100ms | Independent of Evolution state |
| Message enqueue latency | < 10ms | Redis XADD operation |
| Message processing rate | 1 msg/sec | Per instance (rate limited) |
| Queue depth (normal) | < 10 | Average during operation |
| Queue depth (degraded) | < 100 | During Evolution reconnection |
| DLQ rate | < 0.1% | Failed messages requiring manual intervention |
| Worker memory | < 100MB | Per worker process |
| Redis memory | ~1KB/msg | Typical message payload size |

### Scalability

**Horizontal Scaling**:
- Multiple worker processes can consume from same stream (Redis Consumer Groups)
- Each worker gets unique `consumer_name` to avoid duplicate processing
- Workers automatically coordinate via Redis

**To scale workers**:
```bash
# Run multiple workers on different machines
# Worker 1
CONSUMER_NAME=worker-1 python -m app.services.whatsapp_queue.worker

# Worker 2 (on different machine or container)
CONSUMER_NAME=worker-2 python -m app.services.whatsapp_queue.worker
```

**Vertical Scaling**:
- Current implementation targets ~1000 messages/instance queue depth before "unhealthy"
- Can be increased by tuning `TOKENS_PER_SECOND` and `BUCKET_CAPACITY`
- Redis Streams can handle millions of messages per instance

### Resource Optimization

**Redis Memory Management**:
```python
# Streams auto-trim to 10,000 messages per instance
r.xadd(stream_key(instance), fields={...}, maxlen=10000, approximate=True)

# DLQ should be manually reviewed and pruned
# Add cron job to clean old DLQ messages:
# redis-cli XTRIM wa:clinic-...:dlq MAXLEN ~ 1000
```

**Worker Optimization**:
- Use `block=5000` in XREADGROUP to reduce Redis polling
- Process up to 10 messages per read (`count=10`) to batch operations
- Sleep between iterations to prevent CPU spinning

---

## Migration Notes

### Deployment Strategy

**Zero-Downtime Deployment**:

1. **Phase 1 & 2 (Infrastructure)**:
   - Deploy Redis infrastructure
   - Deploy queue code (inactive until Phase 3)
   - No impact on existing functionality
   - Rollback: Simply don't proceed to Phase 3

2. **Phase 3 (Worker)**:
   - Deploy worker startup code
   - Messages start being queued instead of sent directly
   - **Critical**: Monitor for first 10 minutes:
     ```bash
     # Watch queue depth
     watch -n 5 'curl -s http://localhost:8080/health/whatsapp | jq'

     # Watch worker logs
     fly logs --app healthcare-clinic-backend | grep Worker
     ```
   - **Rollback**: Revert `evolution_webhook.py` to direct send (git revert)

3. **Phase 4 (Health)**:
   - Add monitoring endpoints
   - No functional impact
   - Rollback: Not needed (additive only)

### Rollback Procedure

If issues occur after Phase 3 deployment:

```bash
# 1. Immediately revert to previous deployment
fly deploy --app healthcare-clinic-backend --image registry.fly.io/healthcare-clinic-backend:deployment-PREVIOUS

# 2. Verify health
curl https://healthcare-clinic-backend.fly.dev/health

# 3. Check if queue has pending messages
redis-cli -u $REDIS_URL XLEN wa:clinic-...:stream

# 4. If messages in queue, manually process:
#    Option A: Start standalone worker
#    python -m app.services.whatsapp_queue.worker
#
#    Option B: Drain queue manually
#    redis-cli -u $REDIS_URL XRANGE wa:clinic-...:stream - + COUNT 10
#    # Process each message by calling Evolution API directly
```

### Data Migration

**No data migration required** - this is a new system. Existing functionality remains unchanged until Phase 3.

**Cleaning up after rollback**:
```bash
# Clear queue if needed
redis-cli -u $REDIS_URL DEL wa:clinic-...:stream
redis-cli -u $REDIS_URL DEL wa:clinic-...:dlq

# Clear idempotency keys (expire after 24h anyway)
redis-cli -u $REDIS_URL --scan --pattern "wa:msg:*" | xargs redis-cli DEL
```

---

## Monitoring & Alerting

### Recommended Alerts

**Critical** (PagerDuty / immediate response):
- Queue depth > 1000 for > 5 minutes
- Evolution disconnected for > 10 minutes
- DLQ depth > 50
- Worker process crashed (no heartbeat)

**Warning** (Slack / review within 1 hour):
- Queue depth > 100 for > 5 minutes
- Evolution disconnected for > 2 minutes
- DLQ depth > 10
- Message processing rate < 0.5 msg/sec

**Info** (Dashboard / daily review):
- Average queue depth trend
- Message processing latency distribution
- Evolution connection uptime %
- DLQ rate over time

### Monitoring Queries

**Prometheus-style metrics** (if using Prometheus exporter):
```promql
# Queue depth gauge
whatsapp_queue_depth{instance="clinic-..."}

# Messages processed counter
whatsapp_messages_processed_total{instance="clinic-...", status="success|failed"}

# Processing latency histogram
whatsapp_message_latency_seconds{instance="clinic-..."}

# Evolution connection state
whatsapp_evolution_connected{instance="clinic-..."} == 0|1
```

**Health check polling** (if using simple HTTP monitoring):
```bash
# Poll every 60 seconds
* * * * * curl -s https://healthcare-clinic-backend.fly.dev/health/whatsapp | \
          jq -r '.status' | \
          grep -q "healthy" || \
          send-alert "WhatsApp queue unhealthy"
```

---

## References

- Original issue diagnosis: `clinics/backend/BACKEND_ISSUE_FOUND.md`
- Evolution API documentation: https://doc.evolution-api.com/
- Redis Streams documentation: https://redis.io/docs/data-types/streams/
- WhatsApp Business API rate limits: https://developers.facebook.com/docs/whatsapp/messaging-limits
- Fly.io Redis: https://fly.io/docs/reference/redis/

## Appendix: Configuration Reference

### Environment Variables

```bash
# Required
REDIS_URL=redis://default:password@host:port
EVOLUTION_API_URL=https://evolution-api-prod.fly.dev
EVOLUTION_API_KEY=evolution_api_key_2024
INSTANCE_NAME=clinic-3e411ecb-3411-4add-91e2-8fa897310cb0-1757905315621

# Optional (with defaults)
WA_CONSUMER_GROUP=wa_workers
WA_MAX_DELIVERIES=5
WA_BASE_BACKOFF=2.0
WA_MAX_BACKOFF=60.0
WA_TOKENS_PER_SECOND=1.0
WA_BUCKET_CAPACITY=5
WA_EVOLUTION_HTTP_TIMEOUT=15.0
CONSUMER_NAME=worker-1  # Auto-generated if not set
```

### Redis Key Patterns

```
wa:{instance}:stream         - Main message queue (Redis Stream)
wa:{instance}:dlq            - Dead letter queue (failed messages)
wa:msg:{message_id}          - Idempotency keys (24h TTL)
wa:{instance}:bucket         - Token bucket counter
wa:{instance}:bucket:ts      - Token bucket timestamp
```

### Queue Message Format

```json
{
  "message_id": "uuid-v4",
  "to": "+79857608984",
  "text": "Message content",
  "queued_at": 1696118400.123,
  "attempts": 0,
  "metadata": {
    "from_number": "79857608984",
    "clinic_id": "uuid",
    "user_name": "Patient Name"
  }
}
```

---

## Implementation Checklist

### Pre-Implementation
- [ ] Review plan with team
- [ ] Confirm Redis budget/tier sufficient (Fly.io free tier OK for <1000 msg/day)
- [ ] Identify instance names to support
- [ ] Schedule deployment window
- [ ] Prepare rollback procedure

### Phase 1: Redis Infrastructure
- [ ] Create Fly.io Redis instance
- [ ] Configure REDIS_URL secret
- [ ] Update requirements.txt
- [ ] Add Redis health check
- [ ] Deploy and verify

### Phase 2: Queue Integration
- [ ] Copy queue implementation files
- [ ] Adapt evolution_webhook.py
- [ ] Add async enqueue function
- [ ] Test queue operations locally
- [ ] Deploy and verify (messages queued but not processed yet)

### Phase 3: Worker Implementation
- [ ] Copy worker implementation
- [ ] Implement rate limiter
- [ ] Adapt Evolution client
- [ ] Add worker startup
- [ ] Test locally with Evolution API
- [ ] Deploy and monitor closely

### Phase 4: Observability
- [ ] Add health endpoints
- [ ] Configure structured logging
- [ ] Set up monitoring/alerting
- [ ] Document runbooks
- [ ] Deploy and verify

### Post-Implementation
- [ ] Monitor for 24 hours
- [ ] Review DLQ for issues
- [ ] Tune rate limits if needed
- [ ] Document lessons learned
- [ ] Schedule periodic DLQ review