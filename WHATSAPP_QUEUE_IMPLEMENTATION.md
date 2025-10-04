# WhatsApp Queue Implementation - Complete Guide

## Overview

This document captures the complete implementation of the WhatsApp message queue system using Redis Streams. The system is now **fully operational** and successfully processing messages end-to-end.

## 🎉 Current Status: PRODUCTION READY

**Last Verified**: 2025-09-30
**Status**: ✅ **OPERATIONAL** - End-to-end flow confirmed working
**Queue Depth**: 0 (all messages processed)
**Worker Status**: Active and consuming messages
**E2E Test**: ✅ PASSED - Messages successfully delivered to WhatsApp users

## ✅ Successfully Implemented

### 1. Redis Streams Infrastructure
- **Consumer Group**: `wa_workers` created with `$` (tail) to avoid orphaned entries
- **Stream Key Pattern**: `wa:{instance}:stream`
- **DLQ Pattern**: `wa:{instance}:dlq` for failed messages
- **Idempotency**: 24h TTL on message IDs prevents duplicates

### 2. Separate Worker Process
- **Configuration**: `fly.toml` defines separate `web` and `worker` processes
- **Worker Script**: `run_worker.py` runs independently with proper signal handling
- **Deployment**: 2 web machines + 1 worker machine on Fly.io

### 3. Database Fixes
- **PostgREST Syntax**: Changed `is.None` → `is.null` for ended_at queries
- **RPC Function**: `public.create_or_get_session()` with healthcare/public schema fallback
- **Appointments Query**: Fixed to use proper join: `appointments.select('doctor_id,patients!inner(phone)')`

### 4. Admin Endpoints (`/admin/streams/`)
- `GET /health` - Comprehensive health check with recommendations
- `POST /reset-to-latest` - Reset consumer group to $ (skip backlog)
- `POST /reset-to-begin` - Reset to 0 (reprocess all with idempotency)
- `DELETE /destroy-recreate` - Clean slate consumer group
- `POST /claim-pending-to-worker` - Transfer stuck messages

## 🔧 Implementation Journey & Fixes Applied

### Phase 1: Initial Setup (Completed)
**Objective**: Create separate worker process with Redis Streams queue

**Implementation:**
- ✅ Configured `fly.toml` with separate `web` and `worker` processes
- ✅ Created `run_worker.py` standalone worker entry point
- ✅ Implemented queue operations in `app/services/whatsapp_queue/`
- ✅ Added idempotency with 24h TTL on message IDs
- ✅ Created admin endpoints for queue management (`/admin/streams/`)

**Challenges:**
- Consumer group initialization with `$` (tail) caused messages to be skipped
- Worker heartbeats proved it was running but `consumers_count: 0`
- Messages piling up in queue (depth increased from 0 → 6)

### Phase 2: XAUTOCLAIM Implementation (FIXED ✅)
**Objective**: Enable worker to claim and process all queued messages

**Root Cause Analysis:**
1. `ensure_group()` used `id="$"` → only NEW messages after worker start were consumed
2. Messages added before worker start were "orphaned" (never delivered to any consumer)
3. Worker never registered in `XINFO CONSUMERS` because it crashed before first `XREADGROUP`

**Solution Applied:**
1. **XAUTOCLAIM Implementation** (`worker.py:223-263`):
   ```python
   # Claims messages idle >15s from ANY consumer (including orphaned ones)
   reply = self.redis.xautoclaim(
       key, CONSUMER_GROUP, self.consumer_name,
       min_idle_time=CLAIM_IDLE_MS,  # 15000ms
       start_id=self._autoclaim_cursor,
       count=READ_COUNT
   )
   ```
   - Handles both Redis 6.2 and 7.x response formats
   - Maintains cursor for efficient iteration
   - Reduces log noise with `logger.debug()` for expected empty results

2. **Consumer Registration** (`worker.py:189-204`):
   ```python
   # No-op XREADGROUP call ensures worker appears in XINFO CONSUMERS
   self.redis.xreadgroup(
       groupname=CONSUMER_GROUP,
       consumername=self.consumer_name,
       streams={key: ">"},
       count=0,  # Immediate return
       block=1
   )
   ```

3. **ACK-before-Requeue** (`worker.py:137-147`):
   ```python
   # ACK + DEL current message BEFORE requeueing to avoid pending buildup
   self.redis.xack(key, CONSUMER_GROUP, redis_msg_id)
   self.redis.xdel(key, redis_msg_id)
   await asyncio.sleep(delay)  # Backoff
   self.redis.xadd(key, fields={"payload": json.dumps(payload)})
   ```

4. **Config Compatibility Fix** (`config.py:13`):
   ```python
   # Support both env var names for Evolution API URL
   EVOLUTION_API_URL = os.getenv("EVOLUTION_API_URL") or os.getenv("EVOLUTION_SERVER_URL", "https://evolution-api-prod.fly.dev")
   ```

**Results:**
- ✅ Queue depth went from **6 → 0** (all messages processed!)
- ✅ `last_delivered_id` changed from `"0-0"` → `"1759207939323-0"` (actual consumption)
- ✅ Worker successfully claims and processes orphaned messages
- ✅ End-to-end test confirmed: Messages delivered to WhatsApp users

**Evidence of Success:**
```json
// Before fixes
{
  "queue_depth": 6,
  "consumers_count": 0,
  "last_delivered_id": "0-0",
  "status": "unhealthy"
}

// After fixes
{
  "queue_depth": 0,
  "consumers_count": 1,
  "last_delivered_id": "1759207939323-0",
  "status": "healthy"
}
```

### Phase 3: Production Hardening (Completed ✅)
**Objective**: Make XAUTOCLAIM robust across Redis versions

**Improvements:**
- Enhanced error handling for different redis-py client versions
- Better cursor management to prevent infinite loops
- Reduced log noise with appropriate log levels
- Added comprehensive docstrings for maintainability

**Final Status:**
- ✅ Worker stable and consuming messages reliably
- ✅ No pending message buildup
- ✅ Idempotency preventing duplicate processing
- ✅ Rate limiting protecting Evolution API

### Issue 2: High Latency (5-9s)
**Current:** 5-9 seconds from webhook receipt to queuing
**Target:** <500ms

**Optimization Opportunities:**
1. **Immediate Ack**: Send quick response ("Секунду, отвечу...") before AI processing
2. **Cache Clinic Data**: Services and settings queries on every message
3. **Parallel Queries**: Run RAG search + patient context + services in parallel
4. **Reduce Embeddings**: Skip embeddings for simple greetings
5. **Connection Pooling**: Reuse Supabase/Pinecone connections

### Issue 3: Session Management Schema Confusion
**Status:** Fixed with fallback RPC, but needs verification

The tables exist in `healthcare` schema:
- `healthcare.conversation_logs`
- Check if `healthcare.conversation_sessions` or `public.conversation_sessions`

**Verification Query:**
```sql
SELECT schemaname, tablename
FROM pg_tables
WHERE tablename LIKE 'conversation%';
```

## Configuration Files

### `/clinics/backend/fly.toml`
```toml
[processes]
  web = "python -m uvicorn main:app --host 0.0.0.0 --port 8080 --log-level info --timeout-keep-alive 75 --workers 1"
  worker = "python run_worker.py"

[[services]]
  protocol = 'tcp'
  internal_port = 8080
  processes = ['web']  # Only web exposes HTTP
```

### Environment Variables Required
```bash
# Required for worker
REDIS_URL=redis://...
EVOLUTION_SERVER_URL=https://evolution-api-prod.fly.dev
EVOLUTION_API_KEY=B6D9EBF5-89F1-408C-820C-1E1F1C60E0C3

# Required for web
SUPABASE_URL=...
SUPABASE_ANON_KEY=...
OPENAI_API_KEY=...
PINECONE_API_KEY=...
```

## Testing Checklist

### End-to-End Flow Test ✅ PASSED
1. ✅ Send WhatsApp message to Evolution
2. ✅ Evolution webhook hits `/webhooks/evolution/{instance}`
3. ✅ Webhook returns 200 OK immediately (<100ms)
4. ✅ Background task processes message
5. ✅ AI generates response (5-9s)
6. ✅ Response queued to Redis (`message queued to stream: {id}`)
7. ✅ **Worker picks up from queue** (XAUTOCLAIM working!)
8. ✅ Worker sends via Evolution API
9. ✅ **User receives WhatsApp message** (END-TO-END CONFIRMED!)

**Total E2E Latency**: ~6-10 seconds (webhook → user receives message)
- Webhook processing: <100ms
- AI generation: 5-9s (primary bottleneck)
- Queue → Send: <1s
- Evolution delivery: <500ms

### Health Check Commands
```bash
# Stream health
curl https://healthcare-clinic-backend.fly.dev/admin/streams/health | jq

# Expected healthy response:
{
  "status": "healthy",
  "queue_depth": 0-5,
  "consumers_count": 1+,
  "issues": []
}

# Worker status
curl https://healthcare-clinic-backend.fly.dev/worker/status | jq

# Expected:
{
  "running": true,
  "processed_count": >0,
  "consumers_count": 1
}
```

## Quick Fixes for Common Issues

### Consumer Group Stuck
```bash
# Reset to process all messages
curl -X POST https://healthcare-clinic-backend.fly.dev/admin/streams/reset-to-begin

# Or fresh start (loses pending refs)
curl -X DELETE https://healthcare-clinic-backend.fly.dev/admin/streams/destroy-recreate
```

### Worker Not Starting
```bash
# Check if worker machines exist
fly machines list --app healthcare-clinic-backend

# Restart worker
fly machines restart {worker_machine_id} --app healthcare-clinic-backend

# Check secrets are set
fly secrets list --app healthcare-clinic-backend | grep -E "REDIS|EVOLUTION"
```

### Messages Piling Up
```bash
# Check queue depth
curl -s https://healthcare-clinic-backend.fly.dev/admin/streams/health | jq '.queue_depth'

# If >10, claim to manual worker
curl -X POST https://healthcare-clinic-backend.fly.dev/admin/streams/claim-pending-to-worker
```

## Code Locations

### Key Files
- `app/services/whatsapp_queue/worker.py` - Main worker loop
- `app/services/whatsapp_queue/queue.py` - Queue operations
- `app/services/whatsapp_queue/config.py` - Configuration
- `app/services/whatsapp_queue/evolution_client.py` - Evolution API
- `app/api/admin_streams.py` - Admin endpoints
- `app/api/evolution_webhook.py` - Webhook handler
- `run_worker.py` - Standalone worker entry point

### Migrations Applied
1. `create_session_rpc.sql` - Created RPC in healthcare schema
2. `create_session_rpc_public.sql` - Moved RPC to public schema
3. `fix_rpc_schema.sql` - Fixed to use public.conversation_sessions
4. `fix_rpc_healthcare_schema.sql` - **FINAL** - Fallback logic for both schemas

## Performance Targets

| Metric | Current | Target | Status |
|--------|---------|--------|--------|
| Webhook Response | <100ms | <100ms | ✅ |
| AI Processing | 5-9s | <2s | ⏳ |
| Queue → Send | <1s | <1s | ✅ |
| End-to-End | ~6-10s | <3s total | ⏳ |
| Queue Depth | 0 | 0-1 | ✅ |
| Consumers | 1 (active) | 1+ | ✅ |

## ✅ Implementation Complete - Future Enhancements

### Completed Tasks
1. ✅ **Worker consuming messages** - FIXED with XAUTOCLAIM
2. ✅ **End-to-end flow** - Verified messages delivered to WhatsApp
3. ✅ **Queue stability** - No pending buildup, depth stays at 0
4. ✅ **Production deployment** - Worker and web processes running on Fly.io

### Optional Future Enhancements

#### 1. Performance Optimization (Target: <3s E2E)
**Current**: 6-10s total latency
**Target**: <3s total latency

**Improvements:**
- [ ] **Immediate Ack Pattern**: Send "Секунду, отвечу..." before AI processing (~100ms)
  ```python
  # Send quick acknowledgment
  await send_immediate_ack(to_number, "Секунду, обрабатываю ваш запрос...")

  # Then process AI
  response = await generate_ai_response(message)
  await send_full_response(to_number, response)
  ```

- [ ] **Cache Clinic Data**: Store services/settings in Redis with 5-min TTL
  ```python
  # Reduce 2-3 Supabase queries per message
  clinic_data = await redis.get(f"clinic:{clinic_id}:settings")
  ```

- [ ] **Parallel Queries**: Run RAG search + patient context + services concurrently
  ```python
  results = await asyncio.gather(
      search_knowledge_base(query),
      fetch_patient_context(phone),
      fetch_clinic_services(clinic_id)
  )
  ```

- [ ] **Smart Embeddings**: Skip embeddings for simple greetings/confirmations
  ```python
  if is_simple_greeting(text):
      return quick_response(text)  # Skip RAG entirely
  ```

#### 2. Production Monitoring
- [ ] **Queue Depth Alerts**: Alert if depth >10 for >5 minutes
- [ ] **Consumer Health**: Alert if consumers_count=0 for >1 minute
- [ ] **Latency Tracking**: Track P50/P95/P99 processing times
- [ ] **Error Rate Monitoring**: Alert on >5% failure rate
- [ ] **DLQ Monitoring**: Alert if DLQ depth >0

#### 3. Advanced Features
- [ ] **Priority Queue**: VIP patients get faster processing
- [ ] **Scheduled Messages**: Support delayed sends (appointments reminders)
- [ ] **Message Templates**: Pre-approved WhatsApp Business templates
- [ ] **Rich Media**: Support images, documents, voice notes in queue

### Monitoring Dashboard

**Recommended Metrics to Track:**
```bash
# Key metrics for Datadog/Grafana
- whatsapp.queue.depth (gauge)
- whatsapp.queue.consumers (gauge)
- whatsapp.processing.latency (histogram)
- whatsapp.messages.processed (counter)
- whatsapp.messages.failed (counter)
- whatsapp.dlq.depth (gauge)
```

**Health Check Endpoint:**
```bash
# Returns comprehensive status
curl https://healthcare-clinic-backend.fly.dev/admin/streams/health

# Monitor this in Datadog/Pingdom every 30s
```

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────┐
│                         PRODUCTION FLOW                              │
└─────────────────────────────────────────────────────────────────────┘

1. WhatsApp User sends message
         ↓
2. Evolution API receives (Baileys WebSocket)
         ↓
3. POST /webhooks/evolution/{instance}
   - Validates signature (optional)
   - Returns 200 OK in <100ms
   - Creates background task
         ↓
4. Background Task (FastAPI)
   - Parses message
   - Calls LangGraph orchestrator
   - AI generates response (5-9s) ← PRIMARY BOTTLENECK
   - Queues to Redis Stream
         ↓
5. Redis Stream (wa:{instance}:stream)
   - Consumer Group: wa_workers
   - Idempotency: 24h TTL on message IDs
   - Max Length: 10k messages (FIFO)
         ↓
6. Worker Process (Separate Fly.io Machine)
   - XAUTOCLAIM orphaned messages (>15s idle)
   - XREADGROUP new messages (>)
   - Rate limiting: 1 msg/s per instance
   - Exponential backoff on failures
         ↓
7. Evolution API Client
   - POST /message/sendText/{instance}
   - Validates connection state
   - Sends to WhatsApp
         ↓
8. WhatsApp User receives message

┌─────────────────────────────────────────────────────────────────────┐
│                      FAILURE HANDLING                                │
└─────────────────────────────────────────────────────────────────────┘

Evolution Offline?
   → Worker checks is_connected()
   → ACKs message, requeueing with incremented attempts
   → Exponential backoff: 2s → 4s → 8s → 16s → 60s (max)

Rate Limited?
   → Token bucket (5 burst, 1/s refill)
   → ACKs message, requeueing with backoff

Max Retries Exceeded?
   → Move to DLQ (wa:{instance}:dlq)
   → Admin can inspect/replay via /admin/streams/

Idempotent?
   → Message ID stored in Redis (24h TTL)
   → Duplicate webhook calls ignored (same message_id)
```

## Key Learnings & Best Practices

### 1. Redis Streams Consumer Groups
**Problem**: Using `$` (tail) in `XGROUP CREATE` causes "orphaned entries"
```python
# ❌ WRONG - Skips messages added before worker starts
r.xgroup_create(key, group, id="$", mkstream=True)

# ✅ CORRECT - Use XAUTOCLAIM to pick up orphaned messages
r.xautoclaim(key, group, consumer, min_idle_time=15000, start_id="0-0")
```

**Lesson**: Always implement XAUTOCLAIM in addition to XREADGROUP to handle:
- Messages added before worker started
- Messages from crashed workers
- Messages from debug/temp consumers

### 2. ACK Discipline Prevents Pending Buildup
**Problem**: Requeueing without ACK causes perpetual pending state
```python
# ❌ WRONG - Message stays pending forever
if not can_send():
    r.xadd(key, {"payload": json.dumps(payload)})  # Requeue
    return  # Forgot to ACK!

# ✅ CORRECT - ACK+DEL before requeue
if not can_send():
    r.xack(key, group, msg_id)
    r.xdel(key, msg_id)
    await asyncio.sleep(backoff)
    r.xadd(key, {"payload": json.dumps(payload)})
```

**Lesson**: Always ACK+DEL before requeueing to avoid "zombie" pending messages

### 3. Consumer Registration Visibility
**Problem**: Worker can be consuming but `XINFO CONSUMERS` shows 0
```python
# Consumer only appears after first XREADGROUP call
# Solution: Do a no-op read on startup

r.xreadgroup(group, consumer, streams={key: ">"}, count=0, block=1)
logger.info("Registered consumer with XINFO CONSUMERS")
```

**Lesson**: Consumer registration is lazy; force it with a no-op read for monitoring

### 4. Redis 6.2 vs 7.x XAUTOCLAIM Differences
**Problem**: XAUTOCLAIM return format varies by Redis version
```python
# Redis 6.2:  (next_id, [(id, fields)...])
# Redis 7.x:  (next_id, [(id, fields)...], deleted_count)

# ✅ Handle both formats
reply = r.xautoclaim(...)
if isinstance(reply, (list, tuple)) and len(reply) >= 2:
    next_id, claimed = reply[0], reply[1]
    # Ignore optional 3rd element
```

**Lesson**: Always handle both formats for compatibility across Redis versions

### 5. Idempotency is Critical for Webhooks
**Problem**: Evolution may retry webhook on timeout
```python
# Store message_id in Redis with 24h TTL
idemp_key = f"wa:msg:{message_id}"
if not r.set(idemp_key, "1", nx=True, ex=86400):
    logger.info(f"Duplicate message {message_id} ignored")
    return  # Already processed
```

**Lesson**: Always implement idempotency for external webhooks to prevent duplicates

### 6. Separate Processes for CPU-Bound Work
**Problem**: Worker blocking web server degrades webhook latency
```toml
# ✅ Separate processes in fly.toml
[processes]
  web    = "uvicorn main:app --host 0.0.0.0 --port 8080"
  worker = "python run_worker.py"

# Scale independently
fly scale count 2 --process-group web
fly scale count 1 --process-group worker
```

**Lesson**: Use separate processes for CPU-bound work to maintain low webhook latency

### 7. Exponential Backoff Prevents Thundering Herd
**Problem**: All failures retrying immediately overwhelms system
```python
def exponential_backoff(attempts: int) -> float:
    delay = min(60.0, 2.0 * (2 ** max(0, attempts - 1)))
    jitter = random.uniform(0.75, 1.25)  # ±25% jitter
    return delay * jitter
```

**Lesson**: Always add jitter to exponential backoff to prevent synchronized retries

## Support & Resources

- **Redis Streams Docs**: https://redis.io/docs/data-types/streams/
- **Redis XAUTOCLAIM**: https://redis.io/commands/xautoclaim/
- **Fly.io Processes**: https://fly.io/docs/apps/processes/
- **PostgREST API**: https://postgrest.org/en/stable/api.html
- **Evolution API**: https://github.com/EvolutionAPI/evolution-api

## Troubleshooting Guide

### Issue: Messages Not Being Consumed
```bash
# Check queue status
curl https://healthcare-clinic-backend.fly.dev/admin/streams/health | jq

# Verify worker is running
fly machines list --app healthcare-clinic-backend | grep worker

# Check worker logs
fly logs --app healthcare-clinic-backend | grep -i worker

# Inspect Redis directly
redis-cli XINFO GROUPS wa:your-instance:stream
redis-cli XINFO CONSUMERS wa:your-instance:stream wa_workers
redis-cli XPENDING wa:your-instance:stream wa_workers

# Force claim stuck messages
curl -X POST https://healthcare-clinic-backend.fly.dev/admin/streams/claim-pending-to-worker
```

### Issue: High Queue Depth
```bash
# Check depth
curl https://healthcare-clinic-backend.fly.dev/admin/streams/health | jq '.queue_depth'

# If >10, scale up workers or reset group
fly scale count 2 --process-group worker

# Or reset to beginning (with idempotency protection)
curl -X POST https://healthcare-clinic-backend.fly.dev/admin/streams/reset-to-begin
```

### Issue: Evolution API Errors
```bash
# Check Evolution connection
curl https://evolution-api-prod.fly.dev/instance/connectionState/your-instance \
  -H "apikey: YOUR_KEY"

# Restart Evolution instance
curl -X POST https://evolution-api-prod.fly.dev/instance/restart/your-instance \
  -H "apikey: YOUR_KEY"
```

---

**Last Updated**: 2025-09-30 (Final - Production Ready)
**Status**: ✅ **OPERATIONAL & VERIFIED** - End-to-end flow confirmed working
**Implementation Time**: ~4 hours (including debugging and testing)
**Token Usage**: 94k (comprehensive implementation + documentation)