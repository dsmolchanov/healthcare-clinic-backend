# Feature Flags Deployment Guide

## Overview

This guide describes how to deploy the mem0-redis epic features using feature flags for safe, gradual rollout with the ability to rollback quickly in case of issues.

## Feature Flags

### Available Flags

| Flag | Purpose | Default | Notes |
|------|---------|---------|-------|
| `FAST_PATH_ENABLED` | Enable fast-path routing for FAQ/price queries | `false` | Bypasses full LLM for simple queries |
| `MEM0_READS_ENABLED` | Enable mem0 memory reads | `false` | Requires mem0 service to be healthy |
| `MEM0_SHADOW_MODE` | Run mem0 writes without reads | `false` | Test writes without impacting users |
| `CANARY_SAMPLE_RATE` | Percentage of traffic for canary (0.0-1.0) | `0.0` | Controls gradual rollout |

### Flag Behavior

**mem0 Read/Write Logic:**
- Reads enabled: `MEM0_READS_ENABLED=true` AND `MEM0_SHADOW_MODE=false`
- Writes enabled: `MEM0_READS_ENABLED=true` OR `MEM0_SHADOW_MODE=true`

**Fast-Path Logic:**
- When disabled: All queries go through full LLM processing
- When enabled: FAQ/price queries use template responses (<500ms)

## Deployment Stages

### Stage 1: Shadow Mode (10% Canary)

**Purpose:** Test mem0 writes without affecting user experience

```bash
./scripts/canary-deploy.sh stage1
```

**Configuration:**
```
FAST_PATH_ENABLED=false
MEM0_READS_ENABLED=false
MEM0_SHADOW_MODE=true
CANARY_SAMPLE_RATE=0.1
```

**Expected Behavior:**
- mem0 writes to 10% of conversations
- No mem0 reads (users get standard responses)
- Fast-path disabled
- Monitor mem0 write success rate

**Monitoring Checklist:**
- [ ] mem0 write success rate >99%
- [ ] No increase in error rates
- [ ] mem0 service health check passing
- [ ] Monitor for 30 minutes minimum

---

### Stage 2: Fast-Path Enabled (50% Canary)

**Purpose:** Enable fast-path routing while continuing mem0 shadow writes

```bash
./scripts/canary-deploy.sh stage2
```

**Configuration:**
```
FAST_PATH_ENABLED=true
MEM0_READS_ENABLED=false
MEM0_SHADOW_MODE=true
CANARY_SAMPLE_RATE=0.5
```

**Expected Behavior:**
- Fast-path routing for FAQ/price queries
- P50 latency <500ms for fast-path queries
- mem0 shadow writes continue
- 50% of traffic uses new features

**Monitoring Checklist:**
- [ ] P50 latency <500ms for FAQ/price queries
- [ ] P95 latency <1s
- [ ] Fast-path hit rate >70% for FAQ queries
- [ ] No increase in error rates
- [ ] Monitor for 30 minutes minimum

---

### Stage 3: mem0 Reads Enabled (50% Canary)

**Purpose:** Enable full mem0 read/write functionality

```bash
./scripts/canary-deploy.sh stage3
```

**Configuration:**
```
FAST_PATH_ENABLED=true
MEM0_READS_ENABLED=true
MEM0_SHADOW_MODE=false
CANARY_SAMPLE_RATE=0.5
```

**Expected Behavior:**
- Full mem0 read/write enabled
- Multi-turn context retention
- Fast-path continues working
- 50% of traffic uses all features

**Monitoring Checklist:**
- [ ] mem0 read success rate >95%
- [ ] Multi-turn conversation context preserved
- [ ] User satisfaction metrics stable or improved
- [ ] Latency within acceptable ranges
- [ ] Monitor for 1 hour minimum

---

### Stage 4: Full Rollout (100%)

**Purpose:** Enable features for all users

```bash
./scripts/canary-deploy.sh full
```

**Configuration:**
```
FAST_PATH_ENABLED=true
MEM0_READS_ENABLED=true
MEM0_SHADOW_MODE=false
CANARY_SAMPLE_RATE=1.0
```

**Expected Behavior:**
- All features enabled for 100% of traffic
- Full performance improvements
- Complete mem0 integration

**Monitoring Checklist:**
- [ ] All metrics stable after 2-4 hours
- [ ] User feedback positive
- [ ] Error rates within acceptable ranges
- [ ] Performance targets met

---

## Rollback Procedures

### Full Rollback

If critical issues occur, disable all features immediately:

```bash
./scripts/rollback-feature-flags.sh
```

**Time to Rollback:** <2 minutes

**Effect:** All features disabled, system returns to baseline behavior

---

### Partial Rollback

#### Disable Fast-Path Only

If fast-path routing causes issues but mem0 is working:

```bash
./scripts/rollback-feature-flags.sh --partial
```

**Effect:**
- Disables fast-path routing
- Keeps mem0 shadow mode writes
- Canary disabled

---

#### Disable mem0 Only

If mem0 service is experiencing issues:

```bash
./scripts/rollback-feature-flags.sh --mem0-only
```

**Effect:**
- Disables mem0 reads
- Keeps shadow mode writes (for later analysis)
- Fast-path continues working

---

## Manual Flag Changes

### Via Fly.io CLI

Set individual flags manually:

```bash
fly secrets set FAST_PATH_ENABLED=true --app healthcare-clinic-backend
fly secrets set MEM0_READS_ENABLED=false --app healthcare-clinic-backend
fly secrets set MEM0_SHADOW_MODE=true --app healthcare-clinic-backend
fly secrets set CANARY_SAMPLE_RATE=0.25 --app healthcare-clinic-backend
```

### Via Fly.io Dashboard

1. Go to https://fly.io/dashboard
2. Select `healthcare-clinic-backend` app
3. Navigate to Secrets section
4. Update the following secrets:
   - `FAST_PATH_ENABLED`
   - `MEM0_READS_ENABLED`
   - `MEM0_SHADOW_MODE`
   - `CANARY_SAMPLE_RATE`

**Note:** Changes take ~30 seconds to deploy

---

## Monitoring & Observability

### Key Metrics to Monitor

**Latency:**
- P50 latency (target: <500ms for fast-path)
- P95 latency (target: <2s)
- P99 latency (target: <5s)

**Success Rates:**
- mem0 write success rate (target: >99%)
- mem0 read success rate (target: >95%)
- Fast-path hit rate (target: >70% for FAQ queries)

**Error Rates:**
- API error rate (target: <1%)
- mem0 timeout rate (target: <2%)
- Circuit breaker trips (should be 0)

### Where to Monitor

1. **Prometheus Dashboards:**
   - Latency percentiles
   - Success/error rates
   - Feature flag status

2. **Grafana Dashboards:**
   - System health overview
   - mem0 integration metrics
   - Fast-path performance

3. **Fly.io Logs:**
   ```bash
   fly logs --app healthcare-clinic-backend
   ```

4. **Application Logs:**
   - Feature flag status on startup
   - Fast-path detections
   - mem0 read/write operations

---

## Testing Feature Flags

### Unit Tests

Run feature flag tests:

```bash
cd apps/healthcare-backend
python3 -m pytest tests/unit/test_feature_flags.py -v
```

**Expected:** All 22 tests pass

### Integration Tests

Test feature flag behavior in staging:

```bash
# Set staging flags
fly secrets set FAST_PATH_ENABLED=true --app healthcare-clinic-backend-staging

# Run integration tests
python3 -m pytest tests/integration/ -v -k feature_flags
```

---

## Troubleshooting

### Issue: Fast-path not working

**Symptoms:** Fast-path queries still going through full LLM

**Check:**
1. Verify flag is set: `fly secrets list --app healthcare-clinic-backend`
2. Check logs for "Fast-path disabled via feature flag"
3. Verify intent patterns match query types

**Fix:**
```bash
fly secrets set FAST_PATH_ENABLED=true --app healthcare-clinic-backend
```

---

### Issue: mem0 writes failing

**Symptoms:** mem0 write success rate <99%

**Check:**
1. mem0 service health
2. Pinecone connection status
3. API rate limits

**Fix:**
```bash
# Temporary: Disable mem0 reads, keep shadow mode
./scripts/rollback-feature-flags.sh --mem0-only

# Debug mem0 connection
fly ssh console --app healthcare-clinic-backend
# Inside container:
python3 -c "from app.api.message_processor import memory; print(memory)"
```

---

### Issue: High latency after enabling features

**Symptoms:** P95 latency >2s

**Check:**
1. Database query times
2. Redis connection pool
3. mem0 API latency

**Fix:**
```bash
# Quick rollback
./scripts/rollback-feature-flags.sh

# Investigate with metrics
fly logs --app healthcare-clinic-backend | grep "latency"
```

---

## Best Practices

1. **Always monitor for at least 30 minutes** between stages
2. **Use canary sampling** (0.1 → 0.5 → 1.0) instead of direct 100% rollout
3. **Test in staging first** with same feature flags
4. **Have rollback script ready** before starting deployment
5. **Monitor error rates and latency** continuously during rollout
6. **Document any issues** encountered for future deployments
7. **Communicate with team** before major stage transitions

---

## Support

For issues or questions:
- Check application logs: `fly logs --app healthcare-clinic-backend`
- Review Prometheus/Grafana dashboards
- Contact DevOps team for infrastructure issues
- Review this document and rollback procedures
