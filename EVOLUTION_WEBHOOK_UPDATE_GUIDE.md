# Evolution API Webhook Update Guide

## Overview

This guide walks through updating Evolution API webhooks to use the new token-based URL format. This must be done **before** dropping legacy tables.

## Prerequisites

✅ New token-based webhook endpoint deployed to production
✅ Database migration applied (`20251020_add_webhook_tokens_to_integrations.sql`)
✅ All WhatsApp integrations have `webhook_token` populated

## Step 1: Retrieve Webhook Tokens

Run the helper script to get all webhook URLs:

```bash
cd apps/healthcare-backend

# Show all webhook tokens and URLs
python3 get_webhook_tokens.py

# Show with detailed instructions
python3 get_webhook_tokens.py --verbose

# Export to JSON for backup
python3 get_webhook_tokens.py --export
```

This will display output like:

```
[1] Shtern Dental Clinic
    Clinic ID: abc123...
    Instance: clinic-abc123
    Status: active | Enabled: True
    Phone: +1234567890

    🔑 Webhook Token:
       dGhpc2lzYXRva2VuZXhhbXBsZTE...

    🔗 Webhook URL:
       https://healthcare-clinic-backend.fly.dev/webhooks/evolution/whatsapp/dGhpc2lzYXRva2VuZXhhbXBsZTE...
```

## Step 2: Update Evolution API Webhooks

For each WhatsApp integration:

### Option A: Via Evolution API Dashboard

1. **Login** to Evolution API dashboard (e.g., `https://evolution-api-prod.fly.dev`)

2. **Select Instance** - Find the instance matching the `instance_name` from Step 1

3. **Update Webhook Settings**:
   - Navigate to Settings → Webhooks
   - Find the "Message" webhook configuration
   - Update URL from:
     ```
     https://healthcare-clinic-backend.fly.dev/webhooks/evolution/{instance_name}
     ```
   - To new format:
     ```
     https://healthcare-clinic-backend.fly.dev/webhooks/evolution/whatsapp/{webhook_token}
     ```

4. **Verify Signature** - Ensure `X-Webhook-Signature` header is enabled

5. **Save** changes

### Option B: Via Evolution API (cURL)

```bash
# Set variables
EVOLUTION_API_URL="https://evolution-api-prod.fly.dev"
INSTANCE_NAME="clinic-abc123"
WEBHOOK_TOKEN="dGhpc2lzYXRva2VuZXhhbXBsZTE..."
API_KEY="your-evolution-api-key"

# Update webhook
curl -X PUT "${EVOLUTION_API_URL}/webhook/set/${INSTANCE_NAME}" \
  -H "apikey: ${API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{
    "webhook": {
      "url": "https://healthcare-clinic-backend.fly.dev/webhooks/evolution/whatsapp/'"${WEBHOOK_TOKEN}"'",
      "webhook_by_events": false,
      "events": ["messages.upsert"]
    },
    "webhook_wa_business": {
      "url": ""
    }
  }'
```

## Step 3: Test New Webhook

After updating each webhook:

1. **Send Test Message** - Send a WhatsApp message to the instance

2. **Check Logs** - Monitor backend logs for:
   ```
   [WhatsApp Webhook V2] Token: dGhpc2l...
   [Token Async] ✅ Resolved: token → clinic Shtern Dental Clinic
   ```

3. **Verify Response** - Confirm the bot responds correctly

4. **Check for Errors** - Look for any error logs related to:
   - "No clinic found for token"
   - "Invalid webhook signature"
   - "Token cache MISS"

## Step 4: Monitor Transition Period

During the 2-4 week transition:

### What to Monitor

- **Webhook Success Rate** - Both old and new endpoints
- **Cache Hit Rate** - Token-based cache performance
- **Response Times** - Verify improved performance (zero DB queries)
- **Error Logs** - Any issues with token routing

### Monitoring Commands

```bash
# Check logs on Fly.io
fly logs -a healthcare-clinic-backend | grep "WhatsApp Webhook V2"

# Check token cache hits
fly logs -a healthcare-clinic-backend | grep "Token cache HIT"

# Check for errors
fly logs -a healthcare-clinic-backend | grep "Token Async.*❌"
```

## Step 5: Rollback Plan (If Needed)

If issues arise with the new endpoint:

### Quick Rollback

1. **Revert Evolution Webhooks** back to old URL format:
   ```
   https://healthcare-clinic-backend.fly.dev/webhooks/evolution/{instance_name}
   ```

2. **Old Endpoint Still Works** - The legacy endpoint remains in code for backwards compatibility

3. **Investigate** - Review logs to understand the issue

4. **Fix and Re-attempt** - Fix the issue and try updating webhooks again

## Step 6: Verify All Instances Updated

After updating all instances, verify none are using the old URL:

```bash
# This script checks Evolution API for old URLs (if accessible)
# You may need to manually verify in Evolution API dashboard

# Count instances still using old format (check Evolution API logs)
fly logs -a evolution-api-prod | grep "webhooks/evolution/{instance_name}"
```

## Common Issues

### Issue: "No clinic found for token"

**Cause**: Webhook token doesn't exist in database or cache

**Fix**:
```bash
# Verify token exists
cd apps/healthcare-backend
python3 -c "
from app.db.supabase_client import get_supabase_client
supabase = get_supabase_client()
result = supabase.schema('healthcare').table('integrations').select('webhook_token, clinic_id').eq('webhook_token', 'YOUR_TOKEN').execute()
print(result.data)
"

# Warm cache if needed
python3 -c "
import asyncio
from app.services.whatsapp_clinic_cache import get_whatsapp_clinic_cache
asyncio.run(get_whatsapp_clinic_cache().warmup_all_instances())
"
```

### Issue: "Invalid webhook signature"

**Cause**: Evolution API signature doesn't match

**Fix**: Verify `EVOLUTION_WEBHOOK_SECRET` environment variable matches Evolution API configuration

### Issue: Token cache misses

**Cause**: Redis cache not warmed or expired

**Fix**: Run cache warmup:
```bash
cd apps/healthcare-backend
python3 -c "
import asyncio
from app.services.whatsapp_clinic_cache import get_whatsapp_clinic_cache
result = asyncio.run(get_whatsapp_clinic_cache().warmup_all_instances())
print(f'Cached {result[\"tokens_cached\"]} tokens')
"
```

## Success Criteria

✅ All Evolution instances updated to new webhook URL
✅ Test messages working on all instances
✅ Logs show token-based routing (`Token cache HIT`)
✅ Zero DB query performance confirmed
✅ No increase in error rate
✅ Old endpoint receiving zero traffic

## Next Steps

After 2-4 weeks of successful operation:

1. ✅ Drop legacy tables (see `WHATSAPP_CONSOLIDATION_CLEANUP.md`)
2. ✅ Remove legacy code from `evolution_webhook.py`
3. ✅ Update monitoring dashboards to remove old endpoint metrics

## Support

If you encounter issues:

1. Check logs: `fly logs -a healthcare-clinic-backend`
2. Review this guide's Common Issues section
3. Test with a single instance first before updating all
4. Keep the old endpoint as fallback during transition
