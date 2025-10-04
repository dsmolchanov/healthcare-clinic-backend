# Disaster Recovery Manual

## Quick Reference - Emergency Contacts

- **On-Call Engineer**: Check PagerDuty or Slack #oncall
- **Supabase Support**: https://supabase.com/dashboard/support
- **Fly.io Status**: https://status.fly.io/
- **Evolution API (WhatsApp)**: Check internal docs for support

## Disaster Types & Severity Levels

### Severity Levels
- **SEV-1 (Critical)**: Complete outage, all services down
- **SEV-2 (High)**: Database failure or data loss risk
- **SEV-3 (Medium)**: Single service degradation
- **SEV-4 (Low)**: Performance degradation, no data risk

### Common Disaster Scenarios

| Disaster Type | Severity | Impact | Recovery Time |
|--------------|----------|--------|---------------|
| Database Failure | SEV-1 | No appointments, no access | 10-30 min |
| Redis Cache Failure | SEV-3 | Slow performance, session loss | 5-10 min |
| WhatsApp Service Down | SEV-3 | No WhatsApp messaging | 15-30 min |
| Network Outage | SEV-1 | Complete service unavailable | Varies |
| Data Corruption | SEV-2 | Incorrect data displayed | 30-60 min |
| Google Calendar Sync Failure | SEV-4 | Calendar not updating | 5-15 min |

## Immediate Response Checklist

### 🚨 First 5 Minutes

1. **Verify the Issue**
   ```bash
   # Check service health
   curl https://healthcare-clinic-backend.fly.dev/health

   # Check Fly.io status
   fly status --app healthcare-clinic-backend

   # Check logs
   fly logs --app healthcare-clinic-backend --since 10m
   ```

2. **Assess Impact**
   - How many users affected?
   - Is data at risk?
   - Which services are down?
   - Can users still access critical features?

3. **Communicate**
   - Post in #incidents Slack channel
   - Update status page if SEV-1 or SEV-2
   - Notify on-call manager for SEV-1

4. **Enable Maintenance Mode (if needed)**
   ```bash
   # Enable maintenance mode
   fly secrets set MAINTENANCE_MODE=true --app healthcare-clinic-backend
   ```

## Recovery Procedures by Disaster Type

### 1. Database Failure (Supabase Down)

**Symptoms**:
- 500 errors on all API calls
- "Database connection failed" in logs
- No data loading in frontend

**Recovery Steps**:

1. **Switch to Offline Mode**
   ```bash
   cd clinics/backend
   python3 -c "
   from app.services.graceful_degradation_handler import GracefulDegradationHandler
   # System will auto-detect and switch to cache-only mode
   "
   ```

2. **Check Supabase Status**
   - Visit: https://status.supabase.com/
   - Check dashboard: https://supabase.com/dashboard

3. **If Supabase is Down - Use Cache**
   - System automatically serves from Redis cache
   - Read-only mode activated
   - Wait for Supabase recovery

4. **If Data Corruption - Restore from PITR**
   ```bash
   # Supabase has automatic 2-minute recovery point
   # Contact Supabase support for PITR restoration
   # Or use Supabase Dashboard > Settings > Backups > Point in Time Recovery

   # Target recovery time (example: 30 minutes ago)
   # Recovery Point: YYYY-MM-DD HH:MM:SS
   ```

5. **Verify Recovery**
   ```bash
   cd clinics/backend
   python3 -c "
   from app.services.disaster_recovery_orchestrator import DisasterRecoveryOrchestrator
   # Run integrity checks
   "
   ```

### 2. Redis Cache Failure

**Symptoms**:
- Slow performance
- Session logout issues
- "Redis connection refused" in logs

**Recovery Steps**:

1. **Check Redis Status**
   ```bash
   # If using Fly.io Redis
   fly redis status

   # Test connection
   redis-cli ping
   ```

2. **Restore Redis from Backup**
   ```bash
   cd clinics/backend
   python3 -c "
   import asyncio
   from app.services.external_backup_service import ExternalBackupService
   import redis.asyncio as redis

   async def restore():
       redis_client = redis.from_url('redis://localhost:6379')
       backup_service = ExternalBackupService(redis_client)

       # List available backups
       backups = await backup_service.list_backups()
       print(f'Available backups: {backups}')

       # Restore latest
       if backups:
           await backup_service.restore_from_backup(backups[0]['path'])
           print('Redis restored successfully')

   asyncio.run(restore())
   "
   ```

3. **Rebuild Cache from Database**
   ```bash
   cd clinics/backend
   python3 -c "
   import asyncio
   from app.services.offline_cache_manager import OfflineCacheManager

   async def rebuild():
       # This will rebuild cache from Supabase
       cache_manager = OfflineCacheManager(redis_client, supabase)
       await cache_manager.refresh_cache(force=True)
       print('Cache rebuilt')

   asyncio.run(rebuild())
   "
   ```

### 3. WhatsApp Service Failure

**Symptoms**:
- WhatsApp messages not sending/receiving
- Evolution API errors in logs
- Webhook failures

**Recovery Steps**:

1. **Check Evolution API**
   ```bash
   # Check Evolution API health
   curl https://evolution-api-prod.fly.dev/health

   # Check instance status
   fly logs --app evolution-api-prod --since 10m
   ```

2. **Restore WhatsApp Session**
   ```bash
   cd clinics/backend
   python3 -c "
   import asyncio
   from app.services.external_backup_service import ExternalBackupService

   async def restore_whatsapp():
       backup_service = ExternalBackupService(redis_client)
       backups = await backup_service.list_backups()

       if backups:
           # Extract WhatsApp auth from backup
           import json
           with open(backups[0]['path'], 'r') as f:
               data = json.load(f)

           await backup_service.restore_whatsapp_auth(data['whatsapp'])
           print('WhatsApp auth restored')

   asyncio.run(restore_whatsapp())
   "
   ```

3. **Restart Evolution API**
   ```bash
   fly apps restart evolution-api-prod
   ```

### 4. Complete System Outage

**Symptoms**:
- All services unreachable
- Multiple component failures
- Network connectivity issues

**Recovery Steps**:

1. **Run Automated Recovery**
   ```bash
   cd clinics/backend
   python3 -c "
   import asyncio
   from app.services.disaster_recovery_orchestrator import DisasterRecoveryOrchestrator

   async def auto_recover():
       orchestrator = DisasterRecoveryOrchestrator(...)

       # Detect disaster
       disaster = await orchestrator.detect_disaster()
       if disaster:
           print(f'Disaster detected: {disaster.type.value}')

           # Create recovery plan
           plan = await orchestrator.create_recovery_plan(disaster)
           print(f'Recovery plan: {plan.plan_id}')

           # Execute recovery
           results = await orchestrator.execute_recovery(plan)
           print(f'Recovery results: {results}')

   asyncio.run(auto_recover())
   "
   ```

2. **Manual Recovery Steps** (if automation fails):

   a. **Check Infrastructure**
   ```bash
   # Check Fly.io apps
   fly apps list
   fly status --app healthcare-clinic-backend
   fly status --app evolution-api-prod
   ```

   b. **Restore Services in Order**
   1. Database (Supabase) - Wait or contact support
   2. Redis Cache - Restore from backup
   3. Backend API - Restart if needed
   4. WhatsApp Service - Restore auth
   5. Frontend - Should auto-recover

   c. **Verify Each Component**
   ```bash
   # Test each service
   curl https://healthcare-clinic-backend.fly.dev/health
   curl https://evolution-api-prod.fly.dev/health
   ```

### 5. Google Calendar Sync Issues

**Symptoms**:
- Calendar events not syncing
- OAuth token expired
- 401/403 errors in logs

**Recovery Steps**:

1. **Check OAuth Status**
   ```bash
   cd clinics/backend
   python3 -c "
   from app.calendar.oauth_manager import CalendarOAuthManager
   manager = CalendarOAuthManager()
   # This will attempt token refresh
   "
   ```

2. **Re-authenticate** (if tokens invalid)
   - Direct user to: https://healthcare-clinic-backend.fly.dev/api/onboarding/calendar/setup
   - Complete OAuth flow
   - Verify webhook registration

## Backup Management

### Creating Manual Backup

```bash
cd clinics/backend
python3 -c "
import asyncio
from app.services.external_backup_service import ExternalBackupService

async def backup():
    backup_service = ExternalBackupService(redis_client)
    path = await backup_service.create_complete_backup()
    print(f'Backup created: {path}')

asyncio.run(backup())
"
```

### Scheduled Backups

Backups run automatically every 6 hours. Check status:

```bash
# View recent backups
ls -la /data/backups/

# Check backup job logs
fly logs --app healthcare-clinic-backend | grep -i backup
```

### Backup Retention

- **Local backups**: 7 days (automatic cleanup)
- **Supabase PITR**: 7 days (configurable)
- **Long-term**: Manual monthly exports recommended

## Monitoring & Alerts

### Health Check Endpoints

```bash
# Main health check
curl https://healthcare-clinic-backend.fly.dev/health

# Detailed component health
curl https://healthcare-clinic-backend.fly.dev/api/health/detailed

# Service degradation status
curl https://healthcare-clinic-backend.fly.dev/api/health/degradation
```

### Key Metrics to Monitor

1. **Database**
   - Connection pool usage
   - Query response time
   - Replication lag

2. **Redis**
   - Memory usage
   - Hit/miss ratio
   - Connection count

3. **API**
   - Response time p95
   - Error rate
   - Request volume

4. **WhatsApp**
   - Message delivery rate
   - Webhook success rate
   - Session status

## Post-Incident Procedures

### 1. Verify System Health

```bash
cd clinics/backend
python3 test_disaster_recovery.py
```

### 2. Clear Stale Cache

```bash
# Clear potentially corrupted cache
redis-cli FLUSHDB

# Rebuild from database
python3 -c "
from app.services.offline_cache_manager import OfflineCacheManager
cache_manager.refresh_cache(force=True)
"
```

### 3. Notify Users

- Update status page
- Send recovery notification
- Document known issues

### 4. Post-Mortem

Within 48 hours:
1. Create incident report
2. Document timeline
3. Identify root cause
4. Create action items
5. Update runbooks

## Preventive Measures

### Daily Checks
- [ ] Verify backup job ran
- [ ] Check service health dashboard
- [ ] Review error logs

### Weekly Tasks
- [ ] Test backup restoration (staging)
- [ ] Review monitoring alerts
- [ ] Update on-call schedule

### Monthly Tasks
- [ ] Full disaster recovery drill
- [ ] Review and update runbooks
- [ ] Audit backup retention
- [ ] Performance baseline review

## Command Quick Reference

```bash
# Service Management
fly apps list                                    # List all apps
fly status --app [app-name]                      # Check app status
fly logs --app [app-name] --since 10m           # View recent logs
fly apps restart [app-name]                      # Restart app

# Database
fly postgres connect -a [db-app]                 # Connect to database
fly secrets list --app [app-name]                # View secrets

# Redis
redis-cli ping                                   # Test Redis connection
redis-cli INFO                                   # Redis statistics
redis-cli FLUSHDB                               # Clear database (CAUTION!)

# Backups
python3 create_backup.py                         # Manual backup
python3 restore_backup.py [backup-file]          # Restore from backup
ls -la /data/backups/                           # List backups

# Testing
python3 test_disaster_recovery.py                # Run DR tests
curl https://[app-url]/health                   # Health check
```

## Important Notes

1. **Never skip verification steps** - Always verify recovery before declaring incident resolved
2. **Communicate frequently** - Over-communication is better than silence
3. **Document everything** - Keep notes during incident for post-mortem
4. **Test in staging first** - When possible, test recovery procedures in staging
5. **Escalate when needed** - Don't hesitate to escalate for SEV-1/SEV-2

## Support Contacts

- **Supabase Dashboard**: https://supabase.com/dashboard
- **Fly.io Dashboard**: https://fly.io/dashboard
- **Google Cloud Console**: https://console.cloud.google.com
- **Status Page**: Update at https://status.yourcompany.com

---

*Last Updated: 2025-09-28*
*Version: 1.0*