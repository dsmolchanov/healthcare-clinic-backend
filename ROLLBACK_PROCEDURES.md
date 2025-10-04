# Rollback Procedures for LangGraph Migration

## Overview

This document outlines the step-by-step procedures for rolling back the LangGraph migration at any stage of deployment. These procedures ensure minimal downtime and data integrity during rollback operations.

## Quick Rollback Commands

### Immediate Full Rollback
```bash
# Stop all canary traffic immediately
./scripts/rollback.sh --immediate --preserve-data

# Or manually:
fly scale count 0 -a healthcare-clinic-backend-canary
fly scale count 3 -a healthcare-clinic-backend-stable
```

### Gradual Rollback
```bash
# Reduce canary traffic gradually
./scripts/rollback.sh --gradual --percentage 0
```

## Rollback Triggers

### Automatic Triggers
- **Error Rate > 10%**: Immediate rollback
- **P95 Latency > 3000ms**: Immediate rollback
- **Success Rate < 90%**: Immediate rollback
- **Grok Circuit Breaker Open > 5 minutes**: Disable Grok only
- **Memory Usage > 95%**: Scale and alert, then rollback if not resolved

### Manual Triggers
- Engineering team decision
- Customer complaints
- Security incident
- Data corruption detected

## Phase-Specific Rollback Procedures

### Phase 1: Security Foundation Rollback

```bash
# Disable HMAC verification temporarily
export DISABLE_HMAC_VERIFICATION=true

# Restore previous webhook handler
git checkout stable -- app/api/evolution_webhook.py
fly deploy -a healthcare-clinic-backend --strategy immediate

# Re-enable after fix
unset DISABLE_HMAC_VERIFICATION
```

### Phase 2: LangGraph Extraction Rollback

```bash
# Switch back to direct chat implementation
export USE_LANGGRAPH=false

# Deploy stable version
cd clinics/backend
git checkout stable
fly deploy --strategy immediate

# Verify direct chat is working
curl -X POST https://healthcare-clinic-backend.fly.dev/test/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "test"}'
```

### Phase 3: Dual-Lane Routing Rollback

```bash
# Disable dual-lane routing
export DUAL_LANE_ROUTING_ENABLED=false

# Route all traffic through single lane
fly secrets set ROUTING_MODE=single -a healthcare-clinic-backend

# Restart services
fly apps restart healthcare-clinic-backend
```

### Phase 4: RAG/Memory Rollback

```bash
# Disable RAG and memory features
export ENABLE_RAG=false
export ENABLE_MEMORY=false

# Clear corrupted cache if needed
python scripts/clear_rag_cache.py

# Restart without RAG/memory
fly apps restart healthcare-clinic-backend
```

### Phase 5: Grok Integration Rollback

```bash
# Force OpenAI-only mode
export GROK_ENABLED=false
export LLM_PROVIDER=openai

# Update secrets
fly secrets set GROK_ENABLED=false -a healthcare-clinic-backend
fly secrets set LLM_PROVIDER=openai -a healthcare-clinic-backend

# Restart
fly apps restart healthcare-clinic-backend
```

## Step-by-Step Rollback Process

### 1. Incident Detection
```bash
# Check current status
fly status -a healthcare-clinic-backend
fly logs -a healthcare-clinic-backend --since 5m

# Check metrics
curl https://healthcare-clinic-backend.fly.dev/metrics
```

### 2. Preserve Current State
```bash
# Backup current configuration
fly secrets list -a healthcare-clinic-backend > secrets_backup.txt
fly config save -a healthcare-clinic-backend > config_backup.toml

# Export logs for analysis
fly logs -a healthcare-clinic-backend --since 1h > incident_logs.txt

# Backup database state (if needed)
pg_dump $DATABASE_URL > db_backup_$(date +%Y%m%d_%H%M%S).sql
```

### 3. Initiate Rollback
```bash
# Stop new deployments
fly scale count 0 -a healthcare-clinic-backend-canary

# Route traffic to stable
fly secrets set CANARY_PERCENTAGE=0 -a healthcare-clinic-backend

# Scale stable deployment
fly scale count 3 -a healthcare-clinic-backend-stable
```

### 4. Verify Rollback
```bash
# Health checks
curl https://healthcare-clinic-backend.fly.dev/health

# Test critical endpoints
python scripts/test_critical_endpoints.py

# Monitor error rates
watch -n 5 'curl -s https://healthcare-clinic-backend.fly.dev/metrics | grep error_rate'
```

### 5. Clean Up
```bash
# Remove canary deployment (after verification)
fly apps destroy healthcare-clinic-backend-canary

# Reset feature flags
fly secrets unset CANARY_ENABLED -a healthcare-clinic-backend

# Clear temporary data
redis-cli FLUSHDB
```

## Database Rollback

### Schema Rollback
```sql
-- Rollback migrations in reverse order
BEGIN;

-- Phase 6 rollback
DROP TABLE IF EXISTS langgraph_checkpoints;
DROP TABLE IF EXISTS langgraph_states;

-- Phase 5 rollback
DROP TABLE IF EXISTS llm_metrics;
DROP TABLE IF EXISTS grok_usage;

-- Phase 4 rollback
DROP TABLE IF EXISTS rag_embeddings;
DROP TABLE IF EXISTS memory_stores;
DROP TABLE IF EXISTS appointment_locks;

-- Phase 3 rollback
DROP TABLE IF EXISTS routing_decisions;

-- Phase 2 rollback
DROP TABLE IF EXISTS compliance_logs;

-- Phase 1 rollback
DROP TABLE IF EXISTS hmac_validations;
DROP TABLE IF EXISTS rate_limit_buckets;

COMMIT;
```

### Data Recovery
```bash
# Restore from backup
psql $DATABASE_URL < db_backup_20250129_120000.sql

# Verify data integrity
python scripts/verify_data_integrity.py

# Reconcile any missing data
python scripts/reconcile_data.py --since "2025-01-29 12:00:00"
```

## Feature Flag Rollback

### Disable All Features
```javascript
// feature_flags.json
{
  "langgraph_enabled": false,
  "dual_lane_routing": false,
  "grok_integration": false,
  "rag_memory_enabled": false,
  "appointment_tools_enabled": false
}
```

### Apply Feature Flags
```bash
# Update feature flags
fly secrets set FEATURE_FLAGS='{"langgraph_enabled":false}' -a healthcare-clinic-backend

# Restart to apply
fly apps restart healthcare-clinic-backend
```

## Monitoring During Rollback

### Key Metrics to Watch
```bash
# Error rates
curl -s https://healthcare-clinic-backend.fly.dev/metrics | grep -E "error_rate|success_rate"

# Response times
curl -s https://healthcare-clinic-backend.fly.dev/metrics | grep -E "p50|p95|p99"

# Active sessions
curl -s https://healthcare-clinic-backend.fly.dev/metrics | grep active_sessions

# Database connections
curl -s https://healthcare-clinic-backend.fly.dev/metrics | grep pg_connections
```

### Alert Thresholds During Rollback
- Error rate should decrease to < 1% within 5 minutes
- P95 latency should return to baseline within 10 minutes
- Success rate should exceed 99% within 15 minutes

## Communication During Rollback

### Internal Communication
```bash
# Notify engineering team
./scripts/notify.sh --channel engineering --message "Rollback initiated for LangGraph migration"

# Update incident channel
./scripts/notify.sh --channel incidents --message "Rollback in progress, ETA 15 minutes"
```

### Customer Communication
```bash
# Update status page
curl -X POST https://status.example.com/api/incidents \
  -H "Authorization: Bearer $STATUS_API_KEY" \
  -d '{"status": "investigating", "message": "We are experiencing issues and investigating"}'

# Send customer notifications (if needed)
python scripts/notify_affected_customers.py
```

## Post-Rollback Actions

### 1. Incident Report
```markdown
# Incident Report Template
- **Date/Time**: [UTC timestamp]
- **Duration**: [minutes]
- **Impact**: [% of users affected]
- **Root Cause**: [technical description]
- **Resolution**: [rollback procedure used]
- **Lessons Learned**: [improvements needed]
- **Action Items**: [prevention measures]
```

### 2. Root Cause Analysis
```bash
# Collect logs
fly logs -a healthcare-clinic-backend --since 24h > rca_logs.txt

# Analyze metrics
python scripts/analyze_metrics.py --incident-time "2025-01-29 12:00:00"

# Generate report
python scripts/generate_rca_report.py
```

### 3. Fix and Re-deploy
```bash
# Create fix branch
git checkout -b fix/langgraph-migration-issue

# Apply fixes
# ... make necessary code changes ...

# Test thoroughly
python test_phase6_integration.py

# Deploy to staging first
fly deploy -a healthcare-clinic-backend-staging

# Monitor staging
./scripts/monitor_staging.sh --duration 1h

# If stable, proceed with production
fly deploy -a healthcare-clinic-backend --strategy canary
```

## Rollback Verification Checklist

- [ ] All traffic routed to stable version
- [ ] Error rates returned to baseline
- [ ] Response times within SLA
- [ ] Database connections stable
- [ ] No data loss or corruption
- [ ] All critical features functional
- [ ] Monitoring alerts cleared
- [ ] Customer impact assessed
- [ ] Incident report drafted
- [ ] Team debriefed

## Emergency Contacts

- **On-Call Engineer**: Use PagerDuty
- **Platform Team Lead**: [Contact via Slack]
- **Database Admin**: [Contact via Slack]
- **Security Team**: security@example.com
- **Customer Success**: cs-escalation@example.com

## Recovery Time Objectives

- **Detection**: < 2 minutes
- **Decision**: < 5 minutes
- **Rollback Execution**: < 10 minutes
- **Verification**: < 5 minutes
- **Total RTO**: < 22 minutes

## Appendix: Useful Commands

```bash
# View all running services
fly apps list

# Check deployment history
fly releases -a healthcare-clinic-backend

# Rollback to specific version
fly deploy -a healthcare-clinic-backend --image registry.fly.io/healthcare-clinic-backend@sha256:HASH

# Emergency database connection
fly postgres connect -a healthcare-db

# Force restart all instances
fly apps restart healthcare-clinic-backend --force

# Scale to zero (emergency stop)
fly scale count 0 -a healthcare-clinic-backend

# View real-time metrics
fly metrics -a healthcare-clinic-backend
```

## Testing Rollback Procedures

Run rollback drills regularly:

```bash
# Staging environment drill
./scripts/rollback_drill.sh --env staging --scenario high-error-rate

# Production drill (read-only)
./scripts/rollback_drill.sh --env production --dry-run

# Document results
./scripts/document_drill.sh --date $(date +%Y-%m-%d)
```

Remember: **When in doubt, roll back!** It's better to rollback quickly and investigate than to leave users with a degraded experience.