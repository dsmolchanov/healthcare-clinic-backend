# Canary Deployment Monitoring Guide

## Overview

This guide provides monitoring procedures for the 24-hour canary deployment phases of the mem0-redis multi-tier memory architecture.

**Current Phase:** Phase 1 (10% Traffic)
**Started:** 2025-10-16 18:35 UTC
**Next Check:** Every 4 hours for 24h

---

## Quick Status Check

Run this every 4 hours during the 24h observation period:

```bash
# Save as: check_canary_status.sh
#!/bin/bash
set -e

echo "=== Canary Status Check - $(date -u '+%Y-%m-%d %H:%M:%S UTC') ==="
echo

# 1. Feature Flags
echo "📋 Feature Flags:"
fly logs -a healthcare-clinic-backend | grep "Feature flags loaded" | tail -1
echo

# 2. Latency (P50/P95/P99)
echo "⏱️  Latency (last 100 requests):"
fly logs -a healthcare-clinic-backend | grep -E "P50|P95|P99" | tail -5
echo

# 3. Error Rate
echo "❌ Error Rate:"
fly logs -a healthcare-clinic-backend | grep "error_rate" | tail -3
echo

# 4. Cache Hit Rate
echo "💾 Cache Hit Rate:"
fly logs -a healthcare-clinic-backend | grep "cache_hit_rate" | tail -3
echo

# 5. mem0 Write Success
echo "🧠 mem0 Write Success:"
fly logs -a healthcare-clinic-backend | grep "mem0_write_success" | tail -3
echo

# 6. Circuit Breaker Status
echo "🔌 Circuit Breaker:"
fly logs -a healthcare-clinic-backend | grep "circuit_breaker" | tail -3
echo

# 7. Application Health
echo "🏥 Application Health:"
curl -s https://healthcare-clinic-backend.fly.dev/health | jq '.'
echo

echo "=== Status Check Complete ==="
```

---

## Canary Phase Progression

### Phase 1: 10% Traffic (Current)
**Duration:** 24 hours
**Started:** 2025-10-16 18:35 UTC
**Target End:** 2025-10-17 18:35 UTC

**Configuration:**
```bash
FAST_PATH_ENABLED=true
CANARY_SAMPLE_RATE=0.1
MEM0_READS_ENABLED=true
MEM0_SHADOW_MODE=false
```

**Success Criteria:**
- ✅ P50 latency <500ms (target: <400ms)
- ✅ P95 latency <1000ms
- ✅ P99 latency <2000ms
- ✅ Error rate <1% (baseline comparison)
- ✅ Cache hit rate >95%
- ✅ mem0 write success >99%
- ✅ Circuit breaker stays closed
- ✅ No critical incidents

**Monitoring Schedule:**
- Hour 0-4: Check every 1 hour
- Hour 4-12: Check every 2 hours
- Hour 12-24: Check every 4 hours

### Phase 2: 50% Traffic (Pending)
**Duration:** 24 hours
**Start:** After Phase 1 success
**Prerequisites:** All Phase 1 success criteria met

**Configuration:**
```bash
fly secrets set CANARY_SAMPLE_RATE=0.5
```

**Success Criteria:**
- ✅ P50 latency <500ms
- ✅ Error rate stable vs Phase 1
- ✅ Cache hit rate >95%
- ✅ mem0 write success >99%
- ✅ No performance degradation vs Phase 1

### Phase 3: 100% Rollout (Pending)
**Duration:** Continuous monitoring
**Start:** After Phase 2 success
**Prerequisites:** All Phase 2 success criteria met

**Configuration:**
```bash
fly secrets set CANARY_SAMPLE_RATE=1.0
```

**Success Criteria:**
- ✅ All metrics stable for 48 hours
- ✅ mem0 write success >99%
- ✅ Cache hit rate >95%
- ✅ P50 latency <500ms
- ✅ No increase in error rate

---

## Detailed Monitoring Procedures

### 1. Latency Monitoring

#### Fly.io Logs
```bash
# Real-time latency monitoring
fly logs -a healthcare-clinic-backend | grep -E "processing_time|P50|P95"

# Get latency stats for last hour
fly logs -a healthcare-clinic-backend --region iad -i "1h" | \
  grep "processing_time_ms" | \
  awk '{print $NF}' | \
  sort -n | \
  awk '{
    arr[NR]=$1
    sum+=$1
  }
  END {
    print "Count:", NR
    print "Mean:", sum/NR, "ms"
    print "P50:", arr[int(NR*0.5)], "ms"
    print "P95:", arr[int(NR*0.95)], "ms"
    print "P99:", arr[int(NR*0.99)], "ms"
  }'
```

#### Supabase Query
```sql
-- Latency breakdown by endpoint (last 1 hour)
SELECT
  details->>'endpoint' as endpoint,
  COUNT(*) as requests,
  percentile_cont(0.5) WITHIN GROUP (ORDER BY processing_time_ms) as p50_ms,
  percentile_cont(0.95) WITHIN GROUP (ORDER BY processing_time_ms) as p95_ms,
  percentile_cont(0.99) WITHIN GROUP (ORDER BY processing_time_ms) as p99_ms
FROM observability_events
WHERE event_type = 'request_completed'
  AND created_at > NOW() - INTERVAL '1 hour'
GROUP BY details->>'endpoint'
ORDER BY requests DESC;
```

**Alert Thresholds:**
- 🟡 Warning: P50 >400ms for >5 minutes
- 🔴 Critical: P50 >500ms for >10 minutes
- 🚨 Emergency: P50 >1000ms for >5 minutes → Immediate rollback

### 2. Error Rate Monitoring

```bash
# Check error rate (last 1000 requests)
fly logs -a healthcare-clinic-backend | \
  grep "request_completed" | \
  tail -1000 | \
  grep -c "success=false"

# Expected: <10 errors per 1000 requests (1% error rate)
```

#### Detailed Error Analysis
```sql
-- Error breakdown by type (last 1 hour)
SELECT
  details->>'error_type' as error_type,
  details->>'error_message' as error_message,
  COUNT(*) as occurrences,
  MAX(created_at) as last_seen
FROM observability_events
WHERE event_type = 'request_completed'
  AND success = false
  AND created_at > NOW() - INTERVAL '1 hour'
GROUP BY details->>'error_type', details->>'error_message'
ORDER BY occurrences DESC;
```

**Alert Thresholds:**
- 🟡 Warning: Error rate >1% for >5 minutes
- 🔴 Critical: Error rate >2% for >5 minutes → Consider rollback
- 🚨 Emergency: Error rate >5% → Immediate rollback

### 3. Cache Performance

```bash
# Cache hit rate monitoring
fly logs -a healthcare-clinic-backend | \
  grep "cache_access" | \
  tail -1000 | \
  awk '{
    if ($0 ~ /cache_hit=true/) hits++
    total++
  }
  END {
    print "Hit Rate:", (hits/total)*100 "%"
    print "Hits:", hits, "/ Total:", total
  }'
```

#### Cache Analysis Queries
```sql
-- Cache hit rate by resource type
SELECT
  details->>'cache_key_prefix' as resource_type,
  COUNT(*) as accesses,
  COUNT(CASE WHEN cache_hit = true THEN 1 END)::float / COUNT(*) * 100 as hit_rate_pct,
  AVG(CASE WHEN cache_hit = false THEN processing_time_ms END) as avg_miss_time_ms
FROM observability_events
WHERE event_type = 'cache_access'
  AND created_at > NOW() - INTERVAL '1 hour'
GROUP BY details->>'cache_key_prefix'
ORDER BY accesses DESC;

-- Cache stampede detection
SELECT
  cache_key,
  COUNT(*) as concurrent_misses,
  MIN(created_at) as first_miss,
  MAX(created_at) as last_miss,
  EXTRACT(EPOCH FROM (MAX(created_at) - MIN(created_at))) as duration_sec
FROM observability_events
WHERE event_type = 'cache_miss'
  AND created_at > NOW() - INTERVAL '15 minutes'
GROUP BY cache_key
HAVING COUNT(*) > 10
ORDER BY concurrent_misses DESC;
```

**Alert Thresholds:**
- 🟡 Warning: Hit rate <95% for >10 minutes
- 🔴 Critical: Hit rate <90% for >5 minutes → Investigate stampede
- 🔴 Stampede: >20 concurrent misses for same key → Follow runbook

### 4. mem0 Integration Health

```bash
# mem0 write success rate
fly logs -a healthcare-clinic-backend | \
  grep "mem0_write" | \
  tail -100 | \
  awk '{
    if ($0 ~ /success=true/) success++
    total++
  }
  END {
    print "Success Rate:", (success/total)*100 "%"
    print "Successes:", success, "/ Total:", total
  }'
```

#### mem0 Performance Queries
```sql
-- mem0 write performance (last 1 hour)
SELECT
  COUNT(*) as total_writes,
  COUNT(CASE WHEN success = true THEN 1 END)::float / COUNT(*) * 100 as success_rate_pct,
  AVG(processing_time_ms) as avg_write_time_ms,
  MAX(processing_time_ms) as max_write_time_ms,
  COUNT(CASE WHEN processing_time_ms > 5000 THEN 1 END) as slow_writes_over_5s
FROM observability_events
WHERE event_type = 'mem0_write'
  AND created_at > NOW() - INTERVAL '1 hour';

-- mem0 failures by error type
SELECT
  details->>'error_type' as error_type,
  COUNT(*) as failures,
  AVG(processing_time_ms) as avg_time_ms,
  MAX(created_at) as last_failure
FROM observability_events
WHERE event_type = 'mem0_write'
  AND success = false
  AND created_at > NOW() - INTERVAL '1 hour'
GROUP BY details->>'error_type'
ORDER BY failures DESC;
```

**Alert Thresholds:**
- 🟡 Warning: Write success <99% for >10 minutes
- 🔴 Critical: Write success <95% for >5 minutes → Check circuit breaker
- 🚨 Emergency: Write success <90% → Follow circuit breaker runbook

### 5. Circuit Breaker Monitoring

```bash
# Circuit breaker state
fly logs -a healthcare-clinic-backend | \
  grep "circuit_breaker" | \
  tail -10
```

#### Circuit Breaker Queries
```sql
-- Circuit breaker state history
SELECT
  details->>'state' as state,
  details->>'failure_count' as failures,
  details->>'success_count' as successes,
  created_at
FROM observability_events
WHERE event_type = 'circuit_breaker_state_change'
ORDER BY created_at DESC
LIMIT 10;

-- Current circuit breaker health
SELECT
  COALESCE(
    (SELECT details->>'state' FROM observability_events
     WHERE event_type = 'circuit_breaker_state_change'
     ORDER BY created_at DESC LIMIT 1),
    'closed'
  ) as current_state,
  COUNT(*) FILTER (WHERE event_type = 'mem0_write' AND success = false) as recent_failures,
  COUNT(*) FILTER (WHERE event_type = 'mem0_write') as recent_attempts
FROM observability_events
WHERE created_at > NOW() - INTERVAL '5 minutes';
```

**Alert Thresholds:**
- 🟡 Warning: Circuit breaker state changes (closed → half-open)
- 🔴 Critical: Circuit breaker OPEN → Follow runbook immediately
- 🟢 Recovery: Circuit breaker closed for >15 minutes after opening

### 6. Redis Health

```bash
# Redis memory and performance
fly redis info | grep -A 30 "# Memory"
fly redis info | grep -A 20 "# Stats"
```

**Key Metrics to Watch:**
```bash
# Memory usage
used_memory_human: Should be <80% of maxmemory
evicted_keys: Should be 0

# Operations
instantaneous_ops_per_sec: Baseline <1000/sec
total_commands_processed: Continuously increasing

# Connections
connected_clients: Should be stable (typically 5-20)
```

**Alert Thresholds:**
- 🟡 Warning: Memory >70%
- 🔴 Critical: Memory >80% → Plan scaling
- 🚨 Emergency: Memory >90% or evictions >0 → Follow Redis OOM runbook

---

## Validation Checklist

### Pre-Deployment (Completed ✅)
- [x] All Task 1-9 implementation complete
- [x] Feature flags deployed with CANARY_SAMPLE_RATE=0.1
- [x] Operational runbooks documented
- [x] Monitoring queries prepared
- [x] Rollback procedures documented

### Phase 1: Hour 1-4 (Check Hourly)
- [ ] **Hour 1**: All metrics within normal range
- [ ] **Hour 2**: No latency spikes or errors
- [ ] **Hour 3**: Cache performance stable
- [ ] **Hour 4**: mem0 integration healthy

### Phase 1: Hour 4-12 (Check Every 2 Hours)
- [ ] **Hour 6**: Sustained performance
- [ ] **Hour 8**: No circuit breaker trips
- [ ] **Hour 10**: Cache hit rate >95%
- [ ] **Hour 12**: Error rate <1%

### Phase 1: Hour 12-24 (Check Every 4 Hours)
- [ ] **Hour 16**: All metrics stable
- [ ] **Hour 20**: Long-term performance confirmed
- [ ] **Hour 24**: Phase 1 success criteria met

### Phase 1 Completion Criteria
- [ ] P50 latency <500ms for entire 24h period
- [ ] No error rate increases vs baseline
- [ ] Cache hit rate >95% maintained
- [ ] mem0 write success >99% maintained
- [ ] Circuit breaker remained closed
- [ ] Zero critical incidents
- [ ] Post-deployment review completed

### Phase 2: 50% Traffic (Pending Phase 1 Success)
**Start Condition:** All Phase 1 criteria met + approval from Technical Lead

**Deployment:**
```bash
# Increase traffic to 50%
fly secrets set CANARY_SAMPLE_RATE=0.5

# Monitor intensively for first 4 hours
# Then follow same schedule as Phase 1
```

### Phase 3: 100% Rollout (Pending Phase 2 Success)
**Start Condition:** All Phase 2 criteria met + 48h stability

**Deployment:**
```bash
# Full rollout
fly secrets set CANARY_SAMPLE_RATE=1.0

# Monitor for 48 hours continuous
# Declare epic complete if all criteria met
```

---

## Automated Monitoring Setup

### Option 1: Cron Job (Simple)
```bash
# Add to crontab: Run status check every 4 hours
0 */4 * * * /path/to/check_canary_status.sh >> /var/log/canary_monitor.log 2>&1
```

### Option 2: Continuous Monitoring Script
```bash
#!/bin/bash
# Save as: monitor_canary.sh

PHASE1_START="2025-10-16 18:35:00"
DURATION_HOURS=24

while true; do
  ELAPSED=$(( ($(date +%s) - $(date -d "$PHASE1_START" +%s)) / 3600 ))

  if [ $ELAPSED -ge $DURATION_HOURS ]; then
    echo "=== Phase 1 Complete: 24 hours elapsed ==="
    exit 0
  fi

  echo "=== Hour $ELAPSED / $DURATION_HOURS ==="
  ./check_canary_status.sh

  # Alert if any metric fails
  if ./check_canary_status.sh | grep -q "ALERT"; then
    echo "⚠️  ALERT DETECTED - Review logs immediately"
    # Optional: Send notification (email, Slack, PagerDuty)
  fi

  # Sleep for check interval based on phase
  if [ $ELAPSED -lt 4 ]; then
    sleep 3600  # 1 hour
  elif [ $ELAPSED -lt 12 ]; then
    sleep 7200  # 2 hours
  else
    sleep 14400  # 4 hours
  fi
done
```

### Option 3: Grafana Dashboard (Advanced)
```yaml
# Example Grafana dashboard JSON (simplified)
{
  "dashboard": {
    "title": "mem0-redis Canary Monitoring",
    "panels": [
      {
        "title": "Request Latency (P50/P95/P99)",
        "targets": [
          {
            "query": "SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY processing_time_ms) FROM observability_events WHERE event_type = 'request_completed' AND created_at > NOW() - INTERVAL '15 minutes'"
          }
        ]
      },
      {
        "title": "Error Rate",
        "targets": [
          {
            "query": "SELECT COUNT(CASE WHEN success = false THEN 1 END)::float / COUNT(*) * 100 FROM observability_events WHERE event_type = 'request_completed' AND created_at > NOW() - INTERVAL '15 minutes'"
          }
        ]
      },
      {
        "title": "Cache Hit Rate",
        "targets": [
          {
            "query": "SELECT COUNT(CASE WHEN cache_hit = true THEN 1 END)::float / COUNT(*) * 100 FROM observability_events WHERE event_type = 'cache_access' AND created_at > NOW() - INTERVAL '15 minutes'"
          }
        ]
      }
    ]
  }
}
```

---

## Success Metrics Summary

### Phase 1 (10% Traffic) - 24h
| Metric | Target | Alert Threshold | Status |
|--------|--------|----------------|--------|
| P50 Latency | <400ms | >500ms for 10min | ⏳ Monitoring |
| P95 Latency | <1000ms | >1500ms for 10min | ⏳ Monitoring |
| Error Rate | <1% | >2% for 5min | ⏳ Monitoring |
| Cache Hit Rate | >95% | <90% for 5min | ⏳ Monitoring |
| mem0 Success | >99% | <95% for 5min | ⏳ Monitoring |
| Circuit Breaker | Closed | Opens | ⏳ Monitoring |

### Phase 2 (50% Traffic) - 24h
Same metrics as Phase 1, plus:
| Metric | Target | Alert Threshold |
|--------|--------|----------------|
| Performance vs Phase 1 | No degradation | >10% slower |
| Error rate vs Phase 1 | Stable | >0.5% increase |

### Phase 3 (100% Rollout) - Continuous
Same metrics, monitored continuously for 48h before declaring success.

---

## Troubleshooting Quick Reference

### If P50 Latency >500ms
1. Check cache hit rate (should be >95%)
2. Check Redis health (memory, ops/sec)
3. Check database query time
4. Consider: Reduce CANARY_SAMPLE_RATE to 0.05

### If Error Rate Increases
1. Check error types in logs
2. Check circuit breaker status
3. Check external service health (mem0, Supabase)
4. Consider: Immediate rollback if >2%

### If Cache Hit Rate <95%
1. Check for cache stampede patterns
2. Check Redis memory usage
3. Verify generation token logic
4. Consider: Manual cache warming

### If mem0 Success <99%
1. Check circuit breaker status
2. Verify MEM0_API_KEY validity
3. Check mem0 service status
4. Follow Circuit Breaker runbook if open

### If Circuit Breaker Opens
1. **IMMEDIATE ACTION REQUIRED**
2. Follow `OPERATIONAL_RUNBOOKS.md` → Circuit Breaker section
3. Do NOT attempt manual reset without investigation
4. Prepare for potential rollback

---

## Communication Plan

### Status Updates
**Frequency:** Daily during canary phase

**Template:**
```
Subject: [CANARY] Day X/3 - mem0-redis Phase 1 Status

Phase: 1 (10% traffic)
Date: YYYY-MM-DD
Status: ✅ On Track / ⚠️  Issues Detected / 🚨 Rollback Required

Metrics (last 24h):
- P50 Latency: XXXms (target: <500ms)
- Error Rate: X.X% (target: <1%)
- Cache Hit Rate: XX% (target: >95%)
- mem0 Success: XX.X% (target: >99%)
- Circuit Breaker: Closed ✅

Issues: [None / List any issues]

Next Steps: [Continue monitoring / Escalate to Phase 2 / Rollback]

Dashboard: [link]
```

### Escalation Protocol
- **Yellow Alert**: Metrics approach thresholds → Increase monitoring frequency
- **Red Alert**: Metrics exceed thresholds → Prepare rollback, notify team
- **Emergency**: Critical failure → Immediate rollback, incident response

---

## Phase Transition Approval

### Criteria for Phase 1 → Phase 2
- [ ] All 24h monitoring checks completed
- [ ] All success criteria met consistently
- [ ] No critical incidents during Phase 1
- [ ] Post-Phase 1 review completed
- [ ] Technical Lead approval obtained
- [ ] Runbooks validated (at least one test invocation)

### Approval Sign-off
```
Phase 1 Completion: YYYY-MM-DD HH:MM UTC
Approved by: [Technical Lead Name]
Next Phase Start: YYYY-MM-DD HH:MM UTC

Notes:
[Any observations or concerns before proceeding]
```

---

## Additional Resources

- **Operational Runbooks**: `docs/OPERATIONAL_RUNBOOKS.md`
- **Feature Flags Guide**: `docs/FEATURE_FLAGS_DEPLOYMENT.md`
- **Performance Baselines**: `tests/load/PERFORMANCE_REPORT.md`
- **Epic Tracking**: `.claude/epics/mem0-redis/epic.md`

**Last Updated:** 2025-10-16 18:40 UTC
**Version:** 1.0
**Phase:** 1 (10% Traffic)
**Owner:** Platform Engineering Team
