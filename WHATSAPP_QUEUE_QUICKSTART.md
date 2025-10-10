# WhatsApp Queue - Quick Start Guide

## ✅ System Status: OPERATIONAL

The WhatsApp message queue system is **production-ready** and processing messages successfully.

## Quick Health Check

```bash
# Check system status
curl https://healthcare-clinic-backend.fly.dev/admin/streams/health | jq

# Expected healthy response:
{
  "status": "healthy",
  "queue_depth": 0,
  "consumers_count": 1,
  "pending": 0,
  "dlq_depth": 0
}
```

## How It Works

```
WhatsApp Message → Evolution API → Webhook → AI Processing → Redis Queue → Worker → Evolution API → WhatsApp User
     (0ms)           (instant)      (<100ms)     (5-9s)        (~50ms)      (<1s)      (<500ms)        (0ms)

Total Latency: ~6-10 seconds end-to-end
```

## Key Components

### 1. Web Process (Webhook Handler)
- **Location**: `app/apps/voice-api/evolution_webhook.py`
- **Purpose**: Receives WhatsApp messages, processes with AI, queues response
- **Scaling**: 2 machines on Fly.io
- **Command**: `uvicorn main:app --host 0.0.0.0 --port 8080`

### 2. Worker Process (Queue Consumer)
- **Location**: `run_worker.py` + `app/services/whatsapp_queue/worker.py`
- **Purpose**: Consumes messages from Redis, sends via Evolution API
- **Scaling**: 1 machine on Fly.io
- **Command**: `python run_worker.py`

### 3. Redis Streams
- **Stream Key**: `wa:{instance}:stream`
- **Consumer Group**: `wa_workers`
- **Idempotency**: 24h TTL on message IDs
- **Max Length**: 10k messages (FIFO)

### 4. Evolution API
- **URL**: `https://evolution-api-prod.fly.dev`
- **Purpose**: WhatsApp Web bridge (Baileys)
- **Features**: Send/receive messages, connection state

## Common Operations

### Scale Workers
```bash
# Increase workers for higher throughput
fly scale count 2 --process-group worker --app healthcare-clinic-backend

# Decrease workers to save resources
fly scale count 1 --process-group worker --app healthcare-clinic-backend
```

### View Logs
```bash
# All logs
fly logs --app healthcare-clinic-backend

# Worker only
fly logs --app healthcare-clinic-backend | grep -i worker

# Web only
fly logs --app healthcare-clinic-backend | grep -E "(webhook|evolution)"

# Follow live
fly logs --app healthcare-clinic-backend -f
```

### Restart Worker
```bash
# List machines
fly machines list --app healthcare-clinic-backend

# Restart specific worker machine
fly machines restart <MACHINE_ID> --app healthcare-clinic-backend
```

### Clear Queue (Emergency)
```bash
# Reset consumer group to latest (skip backlog)
curl -X POST https://healthcare-clinic-backend.fly.dev/admin/streams/reset-to-latest

# Reset to beginning (reprocess all with idempotency)
curl -X POST https://healthcare-clinic-backend.fly.dev/admin/streams/reset-to-begin

# Destroy and recreate (fresh start, loses pending refs)
curl -X DELETE https://healthcare-clinic-backend.fly.dev/admin/streams/destroy-recreate
```

### Force Claim Stuck Messages
```bash
# Transfer pending messages to manual worker
curl -X POST https://healthcare-clinic-backend.fly.dev/admin/streams/claim-pending-to-worker
```

## Configuration

### Environment Variables
```bash
# Required for worker
REDIS_URL=redis://...
EVOLUTION_SERVER_URL=https://evolution-api-prod.fly.dev
EVOLUTION_API_KEY=your-key-here

# Optional tuning
WA_STREAM_CLAIM_IDLE_MS=15000    # Claim messages idle >15s
WA_STREAM_READ_COUNT=10          # Batch size per read
WA_STREAM_BLOCK_MS=5000          # Block time waiting for messages
WA_TOKENS_PER_SECOND=1.0         # Rate limit per instance
WA_BUCKET_CAPACITY=5             # Burst capacity
WA_MAX_DELIVERIES=5              # Max retries before DLQ
```

### Check Current Settings
```bash
# View all secrets
fly secrets list --app healthcare-clinic-backend

# Set new secret
fly secrets set VARIABLE_NAME="value" --app healthcare-clinic-backend

# Remove secret
fly secrets unset VARIABLE_NAME --app healthcare-clinic-backend
```

## Monitoring

### Key Metrics to Track
```
✅ Queue Depth: Should be 0-2 normally
✅ Consumers: Should be 1+ (number of worker machines)
✅ Pending: Should be 0 (no stuck messages)
✅ DLQ Depth: Should be 0 (no failed messages)
⚠️ Alert if queue_depth >10 for >5 minutes
🚨 Alert if consumers_count=0 for >1 minute
```

### Health Check Endpoint
```bash
# Comprehensive status
curl https://healthcare-clinic-backend.fly.dev/admin/streams/health | jq

# Returns:
{
  "status": "healthy",
  "instance": "clinic-...",
  "queue_depth": 0,
  "dlq_depth": 0,
  "consumers_count": 1,
  "pending": 0,
  "last_delivered_id": "1759207939323-0",
  "consumers": [
    {
      "name": "worker-1733012345-123",
      "pending": 0,
      "idle": 1234
    }
  ],
  "issues": [],
  "recommendations": []
}
```

## Troubleshooting

### Issue: Queue Depth Increasing
**Symptoms**: `queue_depth` growing over time

**Quick Fix**:
```bash
# Check worker is running
fly machines list --app healthcare-clinic-backend | grep worker

# If stopped, restart
fly machines restart <WORKER_MACHINE_ID>

# If running, check logs
fly logs --app healthcare-clinic-backend | grep -i error

# Scale up if needed
fly scale count 2 --process-group worker
```

### Issue: Worker Not Consuming
**Symptoms**: `consumers_count: 0` but worker machine is running

**Quick Fix**:
```bash
# Force claim pending messages
curl -X POST https://healthcare-clinic-backend.fly.dev/admin/streams/claim-pending-to-worker

# Restart worker
fly machines restart <WORKER_MACHINE_ID>

# Check logs for errors
fly logs --app healthcare-clinic-backend | grep -i worker | tail -50
```

### Issue: Evolution API Errors
**Symptoms**: Worker logs show "Evolution not connected"

**Quick Fix**:
```bash
# Check Evolution status
curl https://evolution-api-prod.fly.dev/instance/connectionState/your-instance \
  -H "apikey: YOUR_KEY" | jq

# If disconnected, restart instance
curl -X POST https://evolution-api-prod.fly.dev/instance/restart/your-instance \
  -H "apikey: YOUR_KEY"

# Or reconnect
curl -X POST https://evolution-api-prod.fly.dev/instance/connect/your-instance \
  -H "apikey: YOUR_KEY"
```

### Issue: Messages in DLQ
**Symptoms**: `dlq_depth > 0`

**Investigation**:
```bash
# Connect to Redis to inspect DLQ
redis-cli
> XRANGE wa:your-instance:dlq - + COUNT 10

# Check error reasons in payload
> XREAD COUNT 1 STREAMS wa:your-instance:dlq 0-0
```

## Performance Optimization

### Current Performance
- **Webhook Response**: <100ms ✅
- **AI Processing**: 5-9s ⚠️ (primary bottleneck)
- **Queue → Send**: <1s ✅
- **Total E2E**: ~6-10s

### Optimization Ideas
1. **Immediate Ack**: Send quick "Processing..." message before AI
2. **Cache Clinic Data**: Redis cache for services/settings
3. **Parallel Queries**: Run RAG/context/services concurrently
4. **Skip Embeddings**: For simple greetings/confirmations

## Key Files

```
clinics/backend/
├── app/
│   ├── api/
│   │   ├── evolution_webhook.py        # Webhook handler (web process)
│   │   └── admin_streams.py            # Admin endpoints
│   └── services/
│       └── whatsapp_queue/
│           ├── __init__.py             # Public API
│           ├── config.py               # Configuration
│           ├── queue.py                # Queue operations
│           ├── worker.py               # Worker loop (XAUTOCLAIM)
│           ├── evolution_client.py     # Evolution API client
│           └── rate_limiter.py         # Token bucket
├── run_worker.py                       # Worker entry point
├── fly.toml                            # Fly.io config (processes)
└── WHATSAPP_QUEUE_IMPLEMENTATION.md    # Full documentation
```

## Support

- **Full Documentation**: `WHATSAPP_QUEUE_IMPLEMENTATION.md`
- **Redis Streams**: https://redis.io/docs/data-types/streams/
- **Evolution API**: https://github.com/EvolutionAPI/evolution-api

---

**Last Updated**: 2025-09-30
**Status**: ✅ Production Ready