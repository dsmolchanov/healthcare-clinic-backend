# Legacy WhatsApp Instance Migration Guide

## Overview

This guide helps you migrate existing WhatsApp instances from the old instance-based webhook system to the new token-based system.

## Understanding the Migration

### Old System (Legacy)
- **Tables**: Data scattered across 8 tables (`evolution_instances`, `whatsapp_business_configs`, etc.)
- **Webhook URL**: `https://healthcare-clinic-backend.fly.dev/webhooks/evolution/{instance_name}`
- **Routing**: Instance name in URL → DB lookup → clinic resolution
- **Performance**: 2-3 DB queries per webhook

### New System (Token-Based)
- **Table**: Single source of truth (`healthcare.integrations`)
- **Webhook URL**: `https://healthcare-clinic-backend.fly.dev/webhooks/evolution/whatsapp/{webhook_token}`
- **Routing**: Token → Redis cache → clinic resolution
- **Performance**: Zero DB queries on cache hit

## Migration Scenarios

### Scenario 1: Fresh Start (No Legacy Instances)

**You have:** No existing WhatsApp integrations

**Action:**
✅ Use the UI to create new integrations (see `UI_INTEGRATION_SETUP_GUIDE.md`)
✅ New instances automatically get webhook tokens
✅ No migration needed!

---

### Scenario 2: Existing Instances in `evolution_instances`

**You have:** Active WhatsApp instances in the old `evolution_instances` table

**Action:** Run the data migration

```bash
cd apps/healthcare-backend

# Step 1: Check what needs migration
python3 check_legacy_instances.py

# Step 2: Preview migration (dry-run)
python3 migrate_whatsapp_integrations.py --dry-run

# Step 3: Execute migration
python3 migrate_whatsapp_integrations.py
```

**What this does:**
1. ✅ Copies data from `evolution_instances` → `healthcare.integrations`
2. ✅ Auto-generates `webhook_token` for each instance
3. ✅ Preserves `instance_name` in `config` JSONB (backwards compat)
4. ✅ Maps Evolution status to integration status
5. ✅ Links to correct clinic via organization_id

**After migration:**
- Old endpoint still works (backwards compatible)
- New token-based endpoint ready
- Both tables have data (old table not dropped yet)

---

### Scenario 3: Existing Instances Without Tokens

**You have:** Records in `healthcare.integrations` but no `webhook_token`

**Cause:** Migration ran before schema update

**Action:** Re-run schema migration to generate tokens

```bash
cd apps/healthcare-backend

# This will add webhook_token to existing records
python3 apply_migration.py ../../infra/db/migrations/20251020_add_webhook_tokens_to_integrations.sql
```

The migration is idempotent - safe to run multiple times.

---

## Step-by-Step Legacy Instance Migration

### Step 1: Check Current State

```bash
cd apps/healthcare-backend
python3 check_legacy_instances.py
```

This shows:
- How many instances in old tables
- How many in new table
- Which ones need migration
- Which ones missing tokens

### Step 2: Run Data Migration

```bash
# Preview first
python3 migrate_whatsapp_integrations.py --dry-run

# If looks good, execute
python3 migrate_whatsapp_integrations.py
```

**Expected output:**
```
================================================================================
WhatsApp Integration Migration
================================================================================
Mode: LIVE MIGRATION

📥 Fetching Evolution instances...
Found 2 Evolution instances

✅ Migrated clinic-abc123 → Shtern Dental Clinic
✅ Migrated clinic-xyz789 → Another Clinic

================================================================================
Migration Complete:
  Migrated: 2
  Skipped:  0
  Errors:   0
================================================================================

📊 Migration Verification:
   Evolution instances (non-error): 2
   WhatsApp integrations: 2
✅ Counts match - migration successful!
```

### Step 3: Verify Migration

```bash
# Get all webhook tokens (should show your migrated instances)
python3 get_webhook_tokens.py
```

You should see output like:
```
[1] Shtern Dental Clinic
    Clinic ID: abc123...
    Instance: clinic-abc123
    Status: active | Enabled: True
    Phone: +1234567890

    🔑 Webhook Token:
       dGhpc2lzYXRva2VuZXhhbXBsZTE...

    🔗 Webhook URL:
       https://healthcare-clinic-backend.fly.dev/webhooks/evolution/whatsapp/dGhpc...
```

### Step 4: Update Evolution Webhooks

For each migrated instance, update the webhook URL in Evolution API:

**Old URL (still works):**
```
https://healthcare-clinic-backend.fly.dev/webhooks/evolution/clinic-abc123
```

**New URL (use this):**
```
https://healthcare-clinic-backend.fly.dev/webhooks/evolution/whatsapp/dGhpc2lzYXRva2VuZXhhbXBsZTE...
```

**How to update:**

1. **Via Evolution Dashboard:**
   - Go to `https://evolution-api-prod.fly.dev/manager`
   - Select your instance
   - Settings → Webhooks
   - Update URL to new format
   - Save

2. **Via API:**
   ```bash
   INSTANCE="clinic-abc123"
   TOKEN="dGhpc2lzYXRva2VuZXhhbXBsZTE..."

   curl -X PUT "https://evolution-api-prod.fly.dev/webhook/set/${INSTANCE}" \
     -H "apikey: YOUR_API_KEY" \
     -H "Content-Type: application/json" \
     -d '{
       "webhook": {
         "url": "https://healthcare-clinic-backend.fly.dev/webhooks/evolution/whatsapp/'"${TOKEN}"'",
         "webhook_by_events": false,
         "events": ["messages.upsert"]
       }
     }'
   ```

### Step 5: Test Migration

```bash
# Test each migrated instance
python3 test_webhook_endpoint.py <webhook_token> --prod

# Or send a real WhatsApp message and check logs
fly logs -a healthcare-clinic-backend -f | grep "Token Async"
```

### Step 6: Monitor

Monitor for 1-2 weeks to ensure stability:

```bash
# Watch token-based routing
fly logs -a healthcare-clinic-backend | grep "Token cache HIT"

# Watch for any issues
fly logs -a healthcare-clinic-backend | grep "❌"
```

### Step 7: Cleanup (After 2-4 Weeks)

Once verified, drop legacy tables:

```bash
cd apps/healthcare-backend
python3 apply_migration.py ../../infra/db/migrations/20251020_drop_legacy_whatsapp_tables.sql
```

⚠️ **WARNING**: This is irreversible! Only do after:
- All webhooks updated to new URLs
- 2-4 weeks of stable operation
- Confirmed no traffic to old endpoint

---

## Backwards Compatibility

During migration, BOTH endpoints work:

✅ **Old endpoint** (deprecated, still functional):
```
POST /webhooks/evolution/{instance_name}
```
- Uses instance name from URL
- Looks up clinic via cache or DB
- Works with existing Evolution configs

✅ **New endpoint** (recommended):
```
POST /webhooks/evolution/whatsapp/{webhook_token}
```
- Uses secure token from URL
- Zero DB queries on cache hit
- Better performance and security

**You can migrate gradually** - update webhooks one at a time, test each one.

---

## Common Migration Issues

### Issue: "Clinic not found" after migration

**Cause:** Organization → Clinic mapping issue

**Solution:**
```sql
-- Check the mapping
SELECT id, name, organization_id
FROM clinics
WHERE organization_id = 'YOUR_ORG_ID';

-- If no clinic found, ensure organization has a clinic
```

### Issue: Tokens not generated

**Cause:** Migration ran in wrong order

**Solution:**
```bash
# Re-run schema migration (safe - idempotent)
python3 apply_migration.py ../../infra/db/migrations/20251020_add_webhook_tokens_to_integrations.sql
```

### Issue: Duplicate instances after migration

**Cause:** Migration ran multiple times

**Solution:**
The migration has UPSERT logic (check + update/insert), but if you see duplicates:

```sql
-- Check for duplicates
SELECT clinic_id, type, provider, COUNT(*)
FROM healthcare.integrations
WHERE type = 'whatsapp'
GROUP BY clinic_id, type, provider
HAVING COUNT(*) > 1;

-- Manually remove duplicates (keep the one with webhook_token)
```

### Issue: Old webhook still receiving traffic

**Cause:** Evolution webhook not updated

**Solution:**
- Verify webhook URL in Evolution settings
- Check Evolution API logs
- Ensure URL includes `/whatsapp/{token}` not `/{instance_name}`

---

## Migration Checklist

Before starting:
- [ ] Database backup (Supabase automatic backups enabled)
- [ ] List all existing instances
- [ ] Note current webhook URLs

Migration steps:
- [ ] Run `check_legacy_instances.py`
- [ ] Run migration dry-run
- [ ] Execute migration
- [ ] Verify webhook tokens generated
- [ ] Get new webhook URLs
- [ ] Update Evolution webhooks (one at a time)
- [ ] Test each instance
- [ ] Monitor logs for 1-2 weeks

After verification (2-4 weeks):
- [ ] Confirm zero traffic to old endpoint
- [ ] Run cleanup migration (drop legacy tables)
- [ ] Remove old webhook code (optional)

---

## Need Help?

1. Check current state: `python3 check_legacy_instances.py`
2. Review migration logs: Migration script outputs detailed logs
3. Test specific instance: `python3 test_webhook_endpoint.py <token> --prod`
4. Check backend logs: `fly logs -a healthcare-clinic-backend`
5. Refer to: `EVOLUTION_WEBHOOK_UPDATE_GUIDE.md` for webhook config

---

## Quick Commands Reference

```bash
# Check legacy instances
python3 check_legacy_instances.py

# Migrate data (dry-run first)
python3 migrate_whatsapp_integrations.py --dry-run
python3 migrate_whatsapp_integrations.py

# Get webhook tokens
python3 get_webhook_tokens.py

# Test endpoint
python3 test_webhook_endpoint.py <token> --prod

# Monitor logs
fly logs -a healthcare-clinic-backend -f | grep "Token"

# Cleanup (after 2-4 weeks)
python3 apply_migration.py ../../infra/db/migrations/20251020_drop_legacy_whatsapp_tables.sql
```
