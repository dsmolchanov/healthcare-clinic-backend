# WhatsApp Integration Consolidation - Cleanup Guide

## Overview

This document outlines the cleanup process after the WhatsApp integration consolidation. The new token-based webhook system is now in place, but legacy code and tables remain for backwards compatibility.

## Current State

### ✅ Completed
1. **Database Schema Extension** - `healthcare.integrations` table has webhook_token columns
2. **Token-Based Webhook Endpoint** - New endpoint at `/webhooks/evolution/whatsapp/{webhook_token}`
3. **Cache Service Extension** - Token-based lookups implemented
4. **Migration Scripts** - Data migration script ready (`migrate_whatsapp_integrations.py`)

### ⏳ Pending Cleanup
1. **Legacy Webhook Endpoint** - Old `/webhooks/evolution/{instance_name}` endpoint still exists
2. **Legacy Tables** - 8 tables still in database (not yet dropped)
3. **Legacy Helper Functions** - Old cache and lookup functions still in code

## Cleanup Steps

### Step 1: Update Evolution API Webhooks

Before removing anything, update all Evolution API instances to use the new webhook URL format:

**Old URL:**
```
https://healthcare-clinic-backend.fly.dev/webhooks/evolution/{instance_name}
```

**New URL:**
```
https://healthcare-clinic-backend.fly.dev/webhooks/evolution/whatsapp/{webhook_token}
```

Get the webhook_token from `healthcare.integrations` table or from the frontend UI.

### Step 2: Run Data Migration

If not already done, migrate data from legacy tables:

```bash
cd apps/healthcare-backend

# Dry run first
python3 migrate_whatsapp_integrations.py --dry-run

# Execute migration
python3 migrate_whatsapp_integrations.py
```

### Step 3: Verify Migration

Check that all data was migrated successfully:

```sql
-- Count legacy records
SELECT COUNT(*) FROM public.evolution_instances WHERE status != 'error';

-- Count new records
SELECT COUNT(*) FROM healthcare.integrations WHERE type = 'whatsapp';

-- Verify tokens exist
SELECT id, clinic_id, webhook_token, webhook_url
FROM healthcare.integrations
WHERE type = 'whatsapp'
LIMIT 5;
```

### Step 4: Drop Legacy Tables (⚠️ CAUTION)

**Only after verifying** that:
- All Evolution webhooks are using new URLs
- Data migration is complete
- New endpoint is working in production

Run the cleanup migration:

```bash
cd apps/healthcare-backend
python3 apply_migration.py ../../infra/db/migrations/20251020_drop_legacy_whatsapp_tables.sql
```

This drops:
- `public.evolution_instances`
- `public.evolution_integrations`
- `public.integrations`
- `core.whatsapp_business_accounts`
- `core.whatsapp_business_config`
- `core.whatsapp_business_configs`
- `core.whatsapp_conversations`
- `healthcare.whatsapp_config`

### Step 5: Remove Legacy Code (Future PR)

After tables are dropped, remove legacy code from `evolution_webhook.py`:

**Functions to Remove:**
- `evolution_webhook()` - Old instance-based endpoint
- `process_webhook_async()` - Old background processor
- `process_evolution_message()` - Old message handler
- `get_clinic_for_org_cached()` - Old cache lookup
- `extract_org_id_from_instance()` - Old instance parser
- `_org_to_clinic_cache` - Old module-level cache

**Keep:**
- `whatsapp_webhook_v2()` - New token-based endpoint
- `process_webhook_by_token()` - New background processor
- `get_ai_response_with_rag()` - Utility function
- `send_whatsapp_via_evolution()` - Utility function
- `detect_language_simple()` - Utility function

## Rollback Plan

If issues arise:

1. **Keep Legacy Tables** - Don't drop them until new system is proven
2. **Keep Old Endpoint** - It's still functional for backwards compat
3. **Revert Evolution Webhooks** - Change back to old URL format if needed

## Benefits After Cleanup

- **Reduced Complexity** - Single source of truth for integrations
- **Better Performance** - Zero DB queries on cache hit
- **Improved Security** - Token-based routing, no instance name exposure
- **Easier Maintenance** - No more fragmented data across 8 tables

## Timeline

- **Week 1**: Deploy new endpoint, start migration
- **Week 2**: Update Evolution webhooks to new URLs
- **Week 3**: Monitor new endpoint, verify stability
- **Week 4**: Drop legacy tables and remove old code
