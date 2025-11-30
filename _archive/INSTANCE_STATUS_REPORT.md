# WhatsApp Instance Status Report
## Generated: 2025-10-20

## Summary

✅ **Found: 1 existing WhatsApp integration**

- Location: `healthcare.integrations` table
- Type: whatsapp
- Migration status: Already in new table structure

## Current Status

### What We Know:
1. ✅ You have 1 WhatsApp integration in the system
2. ✅ It's already in the correct table (`healthcare.integrations`)
3. ⚠️ Need to verify if it has a `webhook_token`

### What Needs Verification:

Check if your existing instance has a webhook token by running:

```bash
cd apps/healthcare-backend

# Method 1: Via production
fly ssh console -C "python3 -c '
from app.db.supabase_client import get_supabase_client
supabase = get_supabase_client()
result = supabase.schema(\"healthcare\").table(\"integrations\").select(\"id,webhook_token,webhook_url,config\").eq(\"type\",\"whatsapp\").execute()
print(\"Has token:\", bool(result.data[0].get(\"webhook_token\") if result.data else False))
print(\"Instance:\", result.data[0].get(\"config\",{}).get(\"instance\") if result.data else \"N/A\")
'"

# Method 2: Via get_webhook_tokens.py (after next deployment)
python3 get_webhook_tokens.py
```

## Possible Scenarios

### Scenario A: Instance HAS webhook_token ✅

**Status:** Ready to use!

**Next Steps:**
1. Get the webhook URL: `python3 get_webhook_tokens.py`
2. Update Evolution webhook to new URL
3. Test: `python3 test_webhook_endpoint.py <token> --prod`
4. Done!

**Old webhook URL (deprecated):**
```
https://healthcare-clinic-backend.fly.dev/webhooks/evolution/{instance_name}
```

**New webhook URL (use this):**
```
https://healthcare-clinic-backend.fly.dev/webhooks/evolution/whatsapp/{token}
```

---

### Scenario B: Instance MISSING webhook_token ⚠️

**Status:** Needs token generation

**Why:** Instance was created before token system was implemented

**Fix:** Re-run schema migration to generate token

```bash
cd apps/healthcare-backend
python3 apply_migration.py ../../infra/db/migrations/20251020_add_webhook_tokens_to_integrations.sql
```

This will:
- Add `webhook_token` to your existing instance
- Generate `webhook_url` automatically
- Safe to run (idempotent - won't duplicate data)

**Then:**
1. Get your new webhook URL: `python3 get_webhook_tokens.py`
2. Update Evolution webhook configuration
3. Test the new endpoint

---

## Migration Status

### Legacy Tables Check:
- `public.evolution_instances`: No data found (or table doesn't exist)
- `healthcare.integrations`: **1 instance found** ✅

**Conclusion:** No legacy migration needed! Your instance is already in the new system structure.

---

## Immediate Next Steps

### Step 1: Verify Token Status (Choose one method)

**Method A: Quick check via production**
```bash
fly ssh console -C "python3 -c 'from app.db.supabase_client import get_supabase_client; s=get_supabase_client(); r=s.schema(\"healthcare\").table(\"integrations\").select(\"webhook_token\").eq(\"type\",\"whatsapp\").execute(); print(\"Has token:\", bool(r.data[0].get(\"webhook_token\")) if r.data else False)'"
```

**Method B: Wait for deployment, then:**
```bash
cd apps/healthcare-backend
python3 get_webhook_tokens.py
```

### Step 2: Based on Result

**If HAS token:**
→ Update Evolution webhook to new URL (see `EVOLUTION_WEBHOOK_UPDATE_GUIDE.md`)

**If MISSING token:**
→ Run schema migration to generate it:
```bash
python3 apply_migration.py ../../infra/db/migrations/20251020_add_webhook_tokens_to_integrations.sql
```

### Step 3: Update Evolution Webhook

1. Get webhook URL from `get_webhook_tokens.py`
2. Go to Evolution dashboard: `https://evolution-api-prod.fly.dev/manager`
3. Select your instance
4. Settings → Webhooks
5. Update URL to new format
6. Save

### Step 4: Test

```bash
# Test the new endpoint
python3 test_webhook_endpoint.py <your-token> --prod

# Or send a real WhatsApp message and check logs
fly logs -a healthcare-clinic-backend -f | grep "Token Async"
```

---

## Quick Command Reference

```bash
# Check current status
python3 get_webhook_tokens.py

# Generate tokens if missing
python3 apply_migration.py ../../infra/db/migrations/20251020_add_webhook_tokens_to_integrations.sql

# Test endpoint
python3 test_webhook_endpoint.py <token> --prod

# Monitor logs
fly logs -a healthcare-clinic-backend -f | grep "WhatsApp"
```

---

## Documentation

- **UI Setup**: `UI_INTEGRATION_SETUP_GUIDE.md`
- **Webhook Migration**: `EVOLUTION_WEBHOOK_UPDATE_GUIDE.md`
- **Legacy Migration**: `LEGACY_INSTANCE_MIGRATION.md` (not needed - you're already on new system!)
- **Cleanup**: `WHATSAPP_CONSOLIDATION_CLEANUP.md`

---

## Support

If you encounter issues:
1. Check backend logs: `fly logs -a healthcare-clinic-backend`
2. Verify Evolution is running: `fly status -a evolution-api-prod`
3. Test webhook manually: `python3 test_webhook_endpoint.py <token> --prod`
4. Review guides in `apps/healthcare-backend/`

---

**Bottom Line:** You have 1 existing instance that's already in the new system. Just need to verify it has a token, then update the Evolution webhook URL. No complex migration needed! 🎉
