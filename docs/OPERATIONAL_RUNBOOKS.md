# Operational Runbooks - mem0-redis Architecture

## Overview

This document provides operational runbooks for common incidents in the mem0-redis multi-tier memory architecture. Each runbook includes detection, diagnosis, and remediation steps.

**Quick Reference:**
- [Circuit Breaker Open](#incident-1-circuit-breaker-open)
- [Cache Stampede](#incident-2-cache-stampede)
- [Redis OOM (Out of Memory)](#incident-3-redis-oom-out-of-memory)
- [Canary Rollback](#canary-rollback-procedure)

---

## Incident #1: Circuit Breaker Open

### Symptoms
- Logs showing "Circuit breaker OPEN for mem0" messages
- mem0 write success rate drops below 99%
- Observability dashboard shows circuit_breaker_state = 'open'
- Fallback behavior activated (degraded memory persistence)

### Detection
```bash
# Check circuit breaker state in logs
fly logs -a healthcare-clinic-backend | grep "circuit breaker"

# Check metrics endpoint
curl https://healthcare-clinic-backend.fly.dev/metrics/circuit_breaker

# Query observability data
# SELECT * FROM observability_events
# WHERE event_type = 'circuit_breaker_state_change'
# AND created_at > NOW() - INTERVAL '1 hour'
```

### Root Causes
1. **mem0 API Unavailable**: External service outage or network issues
2. **High Latency**: mem0 API responding slowly (>5s threshold)
3. **Rate Limiting**: Hitting mem0 API rate limits
4. **Invalid Credentials**: MEM0_API_KEY expired or incorrect

### Remediation Steps

#### Step 1: Verify mem0 Service Status
```bash
# Check mem0 API health directly
curl -H "Authorization: Bearer $MEM0_API_KEY" \
  https://api.mem0.ai/v1/health

# Expected: 200 OK
```

#### Step 2: Check Configuration
```bash
# Verify environment variables
fly secrets list | grep MEM0

# Check current circuit breaker configuration
fly logs -a healthcare-clinic-backend | grep "Circuit breaker config" | tail -1
```

#### Step 3: Decision Tree

**If mem0 API is DOWN:**
1. Monitor mem0 status page: https://status.mem0.ai
2. Circuit breaker will auto-recover when service is restored
3. System continues with fallback (degraded memory)
4. NO IMMEDIATE ACTION REQUIRED unless outage exceeds 4 hours

**If mem0 API is UP:**
1. Check for authentication issues:
```bash
# Verify API key is valid
curl -H "Authorization: Bearer $MEM0_API_KEY" \
  https://api.mem0.ai/v1/users/me

# If 401/403: Rotate API key
fly secrets set MEM0_API_KEY=<new_key>
```

2. Check for rate limiting:
```bash
# Review recent request volume
fly logs | grep "mem0 write" | wc -l

# If rate-limited: Reduce request rate temporarily
fly secrets set CANARY_SAMPLE_RATE=0.05  # Reduce to 5%
```

#### Step 4: Manual Circuit Breaker Reset (Use with Caution)
```bash
# Only if you've confirmed mem0 is healthy and issue is resolved
# Circuit breaker will auto-reset after 60s of successful requests

# Force reload by restarting workers (last resort)
fly scale count 2 -a healthcare-clinic-backend  # Current scale
fly scale count 3 -a healthcare-clinic-backend  # Add one
fly scale count 2 -a healthcare-clinic-backend  # Remove old
```

### Prevention
- **Monitoring**: Set up alerts for circuit breaker state changes
- **API Key Rotation**: Rotate MEM0_API_KEY quarterly
- **Rate Limits**: Review mem0 plan limits monthly
- **Redundancy**: Consider backup memory provider

### Rollback Criteria
- Circuit breaker open for >4 hours
- mem0 service unavailable for extended period
- Business-critical memory features required

**Rollback Command:**
```bash
fly secrets set MEM0_READS_ENABLED=false MEM0_SHADOW_MODE=false
```

---

## Incident #2: Cache Stampede

### Symptoms
- Sudden spike in Supabase query load
- Multiple concurrent requests for same data
- Cache hit rate drops below 95%
- Slow response times (P50 >500ms)
- Database connection pool exhaustion

### Detection
```bash
# Check cache hit rate
fly logs | grep "cache_hit_rate" | tail -20

# Monitor database connections
# SELECT count(*) FROM pg_stat_activity WHERE state = 'active';

# Check for stampede patterns
fly logs | grep "Cache miss" | grep -o "clinic_id=[0-9]*" | sort | uniq -c | sort -rn
```

### Root Causes
1. **Cache Expiration**: Multiple cached items expire simultaneously
2. **Cache Invalidation**: Bulk updates trigger mass invalidation
3. **Cold Start**: Redis restart or scaling event clears cache
4. **Popular Resource**: High-traffic resource (clinic/provider) cache miss

### Remediation Steps

#### Step 1: Identify Stampede Source
```bash
# Find most requested resources
fly logs -a healthcare-clinic-backend | \
  grep "Cache miss" | \
  grep -o "key=[^ ]*" | \
  sort | uniq -c | sort -rn | head -10

# Check generation token mismatches
fly logs | grep "generation_token" | tail -20
```

#### Step 2: Immediate Mitigation

**Option A: Manual Cache Warming** (Preferred)
```bash
# Warm cache for high-traffic clinics
curl -X POST https://healthcare-clinic-backend.fly.dev/admin/cache/warm \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -d '{"clinic_ids": [1, 2, 3, 5, 8]}'  # Top 5 clinics
```

**Option B: Increase Cache TTL** (Temporary)
```python
# Update cache_service.py TTL temporarily
# Default: CACHE_TTL = 3600  # 1 hour
# Increase to: CACHE_TTL = 7200  # 2 hours during incident
```

**Option C: Enable Stale-While-Revalidate** (Emergency)
```bash
# Allow serving stale cache during stampede
fly secrets set CACHE_SERVE_STALE=true CACHE_STALE_TTL=300
```

#### Step 3: Reduce Load

```bash
# Temporarily reduce traffic to allow cache recovery
fly secrets set CANARY_SAMPLE_RATE=0.05  # 5% traffic

# Monitor recovery
watch -n 5 'fly logs | grep "cache_hit_rate" | tail -1'

# Gradually increase once hit rate >90%
fly secrets set CANARY_SAMPLE_RATE=0.1   # Back to 10%
```

#### Step 4: Investigate Root Cause

**Check Recent Deployments:**
```bash
fly releases -a healthcare-clinic-backend | head -5
```

**Check Bulk Operations:**
```bash
# Look for bulk updates that triggered mass invalidation
fly logs | grep "bulk_upload" | tail -20
fly logs | grep "cache_invalidate" | tail -50
```

**Check Redis Health:**
```bash
# Get Redis metrics
fly redis info | grep -A 20 "# Stats"

# Check for evictions (OOM condition)
fly redis info | grep evicted_keys
```

### Prevention
1. **Staggered Expiration**: Add jitter to cache TTL
```python
ttl = CACHE_TTL + random.randint(0, 300)  # ±5 minutes jitter
```

2. **Cache Warming on Deploy**: Pre-warm cache after deployment
3. **Request Coalescing**: Deduplicate concurrent requests for same key
4. **Circuit Breaker for Database**: Prevent database overload during stampede

### Monitoring Queries
```sql
-- Cache hit rate by clinic
SELECT
  clinic_id,
  COUNT(CASE WHEN cache_hit = true THEN 1 END)::float / COUNT(*) as hit_rate
FROM observability_events
WHERE event_type = 'cache_access'
  AND created_at > NOW() - INTERVAL '15 minutes'
GROUP BY clinic_id
HAVING COUNT(*) > 100
ORDER BY hit_rate ASC;

-- Concurrent requests for same resource
SELECT
  cache_key,
  COUNT(*) as concurrent_requests,
  MIN(created_at) as first_request,
  MAX(created_at) as last_request
FROM observability_events
WHERE event_type = 'cache_miss'
  AND created_at > NOW() - INTERVAL '5 minutes'
GROUP BY cache_key
HAVING COUNT(*) > 5
ORDER BY concurrent_requests DESC;
```

---

## Incident #3: Redis OOM (Out of Memory)

### Symptoms
- Redis evicting keys (evicted_keys > 0)
- Cache hit rate drops suddenly
- Errors: "OOM command not allowed when used memory > 'maxmemory'"
- Slow cache operations
- Application errors on cache writes

### Detection
```bash
# Check Redis memory usage
fly redis info | grep -A 5 "# Memory"

# Expected output:
# used_memory_human: 234.56M
# maxmemory_human: 512M
# maxmemory_policy: allkeys-lru

# Check eviction rate
fly redis info | grep evicted_keys

# Monitor key count
fly redis dbsize
```

### Root Causes
1. **Memory Leak**: Keys not expiring properly
2. **Large Objects**: Storing oversized values in cache
3. **Key Bloat**: Too many cached items
4. **Insufficient Memory**: Redis instance too small for workload

### Remediation Steps

#### Step 1: Assess Severity

**Calculate Memory Usage:**
```bash
# Get current memory stats
USED=$(fly redis info | grep "used_memory:" | cut -d: -f2)
MAX=$(fly redis info | grep "maxmemory:" | cut -d: -f2)
echo "Used: $USED / Max: $MAX"

# If >90% used: CRITICAL
# If >80% used: WARNING
# If >70% used: WATCH
```

#### Step 2: Immediate Actions (CRITICAL)

**Option A: Flush Non-Critical Keys** (Fastest)
```bash
# Flush only temporary/session keys
fly redis keys "session:*" | xargs -n 100 fly redis del

# Flush old generation caches (keeps current generation)
fly redis keys "cache:*:generation:*" | \
  grep -v "generation:$(date +%s)" | \
  xargs -n 100 fly redis del
```

**Option B: Increase Redis Memory** (If available)
```bash
# Scale up Redis instance (requires paid plan)
fly redis scale memory 1gb -a healthcare-clinic-backend

# This may cause brief downtime (1-2 minutes)
```

**Option C: Restart Redis** (LAST RESORT - causes full cache miss)
```bash
# WARNING: This clears ALL cache
fly redis restart -a healthcare-clinic-backend

# Then warm critical caches
curl -X POST https://healthcare-clinic-backend.fly.dev/admin/cache/warm-critical
```

#### Step 3: Investigate Memory Usage

**Identify Largest Keys:**
```bash
# Get top 10 largest keys
fly redis --scan --pattern "*" --count 1000 | \
  while read key; do
    echo "$(fly redis memory usage "$key") $key"
  done | sort -rn | head -10
```

**Check Key Distribution:**
```bash
# Count keys by prefix
fly redis keys "*" | cut -d: -f1 | sort | uniq -c | sort -rn

# Sample output:
# 15234 cache
# 2341 session
# 156 clinic_config
```

**Identify Leaking Patterns:**
```bash
# Check for keys without TTL (memory leak indicator)
fly redis keys "*" | while read key; do
  ttl=$(fly redis ttl "$key")
  if [ "$ttl" = "-1" ]; then
    echo "NO TTL: $key"
  fi
done
```

#### Step 4: Long-Term Fix

**Update Cache Configuration:**
```python
# In cache_service.py, add size limits:
MAX_CACHE_VALUE_SIZE = 100_000  # 100KB per value
MAX_CLINIC_CACHE_ITEMS = 1000   # Per clinic

# Implement cache value size check:
def _check_value_size(value: str) -> bool:
    if len(value.encode('utf-8')) > MAX_CACHE_VALUE_SIZE:
        logger.warning(f"Cache value too large: {len(value)} bytes")
        return False
    return True
```

**Add Memory Monitoring:**
```python
# Add to observability_service.py
async def log_redis_memory_usage():
    info = await redis_client.info('memory')
    used_pct = (info['used_memory'] / info['maxmemory']) * 100

    await log_event({
        'event_type': 'redis_memory',
        'details': {
            'used_memory_mb': info['used_memory'] / 1024 / 1024,
            'max_memory_mb': info['maxmemory'] / 1024 / 1024,
            'usage_percent': used_pct,
            'evicted_keys': info['evicted_keys']
        }
    })
```

### Prevention
1. **Memory Alerts**: Alert at 70%, 80%, 90% usage
2. **TTL Enforcement**: All keys MUST have TTL
3. **Value Size Limits**: Reject cache writes >100KB
4. **Regular Cleanup**: Weekly job to remove stale keys
5. **Capacity Planning**: Monitor growth trends monthly

### Monitoring Alerts
```yaml
# Example alert configuration (Grafana/Prometheus)
alerts:
  - name: RedisMemoryHigh
    condition: redis_used_memory_percent > 80
    for: 5m
    labels:
      severity: warning

  - name: RedisMemoryCritical
    condition: redis_used_memory_percent > 90
    for: 1m
    labels:
      severity: critical

  - name: RedisEvictions
    condition: rate(redis_evicted_keys[5m]) > 10
    for: 5m
    labels:
      severity: warning
```

---

## Canary Rollback Procedure

### When to Rollback
- P50 latency >1000ms for >10 minutes
- Error rate increases >2% from baseline
- Circuit breaker open for >4 hours
- Critical business impact (appointments failing, etc.)
- mem0 write success rate <95% for >30 minutes

### Rollback Steps

#### Phase 1: Reduce Traffic (Fastest)
```bash
# Reduce to 5% immediately
fly secrets set CANARY_SAMPLE_RATE=0.05

# Monitor for 5 minutes
watch -n 30 'fly logs | grep "P50" | tail -5'

# If stable, gradually reduce to 0
fly secrets set CANARY_SAMPLE_RATE=0.0
```

#### Phase 2: Disable Features
```bash
# Disable all new features
fly secrets set \
  FAST_PATH_ENABLED=false \
  MEM0_READS_ENABLED=false \
  MEM0_SHADOW_MODE=false \
  CANARY_SAMPLE_RATE=0.0
```

#### Phase 3: Verify Rollback
```bash
# Check that system is stable
fly logs | grep "Feature flags loaded" | tail -1

# Expected output:
# Feature flags loaded: fast_path=False, mem0_reads=False, ...

# Monitor key metrics
fly logs | grep -E "(P50|error_rate|mem0_write)" | tail -20
```

#### Phase 4: Document Incident
```markdown
# Create incident report at: docs/incidents/YYYY-MM-DD-rollback.md

## Incident: Canary Rollback
**Date:** YYYY-MM-DD HH:MM UTC
**Duration:** X minutes
**Trigger:** [P50 latency spike / Error rate increase / etc.]

### Timeline
- HH:MM - Anomaly detected
- HH:MM - Rollback initiated
- HH:MM - Traffic reduced to 5%
- HH:MM - Features disabled
- HH:MM - System stable

### Root Cause
[To be determined during post-mortem]

### Impact
- Affected users: ~X% of traffic
- Failed requests: X
- Revenue impact: $X (if applicable)

### Action Items
- [ ] Root cause analysis
- [ ] Fix implemented
- [ ] Testing completed
- [ ] Re-deployment plan approved
```

### Communication Template
```
Subject: [INCIDENT] Canary Rollback - mem0-redis Feature

Timeline:
- XX:XX UTC - Issue detected: [metric] exceeded threshold
- XX:XX UTC - Rollback initiated to [X]% traffic
- XX:XX UTC - Features disabled, system stable

Impact:
- [X]% of users affected
- No data loss
- Normal operation restored

Next Steps:
- Root cause investigation in progress
- Incident report: [link]
- Re-deployment timeline: TBD

Status: RESOLVED
```

---

## Monitoring Dashboard Queries

### Key Metrics to Track

```sql
-- P50/P95/P99 Latency (last 15 minutes)
SELECT
  percentile_cont(0.5) WITHIN GROUP (ORDER BY processing_time_ms) as p50,
  percentile_cont(0.95) WITHIN GROUP (ORDER BY processing_time_ms) as p95,
  percentile_cont(0.99) WITHIN GROUP (ORDER BY processing_time_ms) as p99
FROM observability_events
WHERE event_type = 'request_completed'
  AND created_at > NOW() - INTERVAL '15 minutes';

-- Error Rate (last 15 minutes)
SELECT
  COUNT(CASE WHEN success = false THEN 1 END)::float / COUNT(*) * 100 as error_rate_pct
FROM observability_events
WHERE event_type = 'request_completed'
  AND created_at > NOW() - INTERVAL '15 minutes';

-- Cache Hit Rate (last 15 minutes)
SELECT
  COUNT(CASE WHEN cache_hit = true THEN 1 END)::float / COUNT(*) * 100 as hit_rate_pct
FROM observability_events
WHERE event_type = 'cache_access'
  AND created_at > NOW() - INTERVAL '15 minutes';

-- mem0 Write Success Rate (last 15 minutes)
SELECT
  COUNT(CASE WHEN success = true THEN 1 END)::float / COUNT(*) * 100 as success_rate_pct
FROM observability_events
WHERE event_type = 'mem0_write'
  AND created_at > NOW() - INTERVAL '15 minutes';

-- Circuit Breaker Status
SELECT
  event_type,
  details->>'state' as state,
  details->>'failure_count' as failures,
  created_at
FROM observability_events
WHERE event_type = 'circuit_breaker_state_change'
ORDER BY created_at DESC
LIMIT 1;
```

---

## Escalation Path

### Level 1: On-Call Engineer (0-15 minutes)
- Follow runbook procedures
- Attempt automated remediation
- Monitor key metrics
- Escalate if no improvement in 15 minutes

### Level 2: Senior Engineer (15-30 minutes)
- Review incident details
- Perform root cause analysis
- Make rollback decision if needed
- Coordinate with stakeholders

### Level 3: Technical Lead (30+ minutes)
- Strategic decisions on feature rollout
- Customer communication
- Post-incident review planning
- Long-term architecture decisions

### Contact Information
```
On-Call Engineer: [PagerDuty rotation]
Senior Engineer: [Slack channel: #oncall-escalation]
Technical Lead: [Direct contact info]
```

---

## Post-Incident Checklist

After resolving any incident:

- [ ] Verify all metrics returned to normal
- [ ] Document incident timeline
- [ ] Identify root cause
- [ ] Create action items with owners and due dates
- [ ] Update runbooks based on learnings
- [ ] Schedule post-mortem meeting (within 48 hours)
- [ ] Communicate resolution to stakeholders
- [ ] Review and update monitoring/alerts if needed

---

## Additional Resources

- **Architecture Docs**: `docs/FEATURE_FLAGS_DEPLOYMENT.md`
- **Testing Guide**: `tests/integration/README.md`
- **Performance Baselines**: `tests/load/PERFORMANCE_REPORT.md`
- **Feature Flags**: `app/utils/feature_flags.py`
- **Observability**: `app/services/observability_service.py`

**Last Updated:** 2025-10-16
**Version:** 1.0
**Owner:** Platform Engineering Team
