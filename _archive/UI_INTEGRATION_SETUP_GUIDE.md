# WhatsApp Integration Setup Guide - UI

## Overview

This guide walks you through setting up a new WhatsApp integration using the frontend UI with the new token-based webhook system.

## Prerequisites

✅ Backend deployed with token-based webhook endpoint
✅ Evolution API server running (`https://evolution-api-prod.fly.dev`)
✅ Access to the frontend UI (`https://plaintalk-frontend.vercel.app`)

## Step 1: Access the Integrations Page

1. **Login** to the frontend at `https://plaintalk-frontend.vercel.app`
2. **Navigate** to Integrations (in the sidebar or main menu)
3. **Click** "Add Integration" or "New WhatsApp Integration"

## Step 2: Create New WhatsApp Integration

### Integration Details

Fill in the integration form:

**Basic Information:**
- **Type**: WhatsApp
- **Provider**: Evolution API
- **Clinic**: Select your clinic from dropdown
- **Display Name**: (e.g., "Main Clinic WhatsApp")

**Evolution API Settings:**
- **API URL**: `https://evolution-api-prod.fly.dev`
- **Instance Name**: Choose a unique name (e.g., `clinic-{clinic_id}` or custom name)
  - **Format**: lowercase, alphanumeric with hyphens only
  - **Example**: `shtern-clinic-whatsapp`

### What Happens Automatically

When you save the integration, the backend will:

1. ✅ Generate a unique `webhook_token` automatically
2. ✅ Create a `webhook_url` in format:
   ```
   https://healthcare-clinic-backend.fly.dev/webhooks/evolution/whatsapp/{token}
   ```
3. ✅ Save integration to `healthcare.integrations` table
4. ✅ Populate cache with token mapping

## Step 3: Initialize Evolution Instance

### Option A: Via UI (If Implemented)

If the UI has Evolution initialization:

1. Click "Initialize Instance" or "Connect WhatsApp"
2. The UI should call Evolution API to create the instance
3. Wait for QR code to appear
4. Scan QR code with WhatsApp mobile app
5. Wait for connection status to change to "connected"

### Option B: Via Evolution API Dashboard

If manual setup needed:

1. **Go to** `https://evolution-api-prod.fly.dev/manager`
2. **Create Instance**:
   - Instance Name: (same as you entered in UI)
   - Integration: WhatsApp Business/Personal
   - Number: (if applicable)
3. **Get QR Code**: Click "Get QR Code"
4. **Scan** with WhatsApp mobile app
5. **Wait** for connection

## Step 4: Configure Evolution Webhook

This is the **CRITICAL STEP** for the new token-based system.

### Get Your Webhook URL

From the UI integrations page, find your integration and **copy the webhook URL**:

```
https://healthcare-clinic-backend.fly.dev/webhooks/evolution/whatsapp/AbCd...
```

### Configure in Evolution API

**Via Evolution Dashboard:**

1. Go to Evolution instance settings
2. Find "Webhooks" section
3. Enable webhooks
4. Set URL to the webhook URL copied above
5. Enable these events:
   - `messages.upsert` ✅
   - `messages.update`
   - `connection.update`
6. **Enable** `X-Webhook-Signature` header
7. **Save** configuration

**Via API (cURL):**

```bash
# Set variables
EVOLUTION_URL="https://evolution-api-prod.fly.dev"
INSTANCE_NAME="your-instance-name"
WEBHOOK_TOKEN="your-webhook-token"  # From UI
API_KEY="your-api-key"

# Configure webhook
curl -X PUT "${EVOLUTION_URL}/webhook/set/${INSTANCE_NAME}" \
  -H "apikey: ${API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{
    "webhook": {
      "url": "https://healthcare-clinic-backend.fly.dev/webhooks/evolution/whatsapp/'"${WEBHOOK_TOKEN}"'",
      "webhook_by_events": false,
      "events": ["messages.upsert", "messages.update", "connection.update"]
    }
  }'
```

## Step 5: Test the Integration

### Send Test Message

1. **Send a message** to your WhatsApp number from another phone
2. **Check** that the bot responds
3. **Verify** in backend logs:

```bash
fly logs -a healthcare-clinic-backend | grep "WhatsApp Webhook V2"
```

You should see:
```
[WhatsApp Webhook V2] Token: AbCd...
[Token Async] ✅ Resolved: token → clinic Your Clinic Name
[Token Async] ✅ AI response: ...
```

### Test via Script

Use the test script from the repo:

```bash
cd apps/healthcare-backend

# Get your webhook token from UI
python3 test_webhook_endpoint.py <your-webhook-token> --prod
```

## Step 6: Monitor Integration

### Check Integration Status in UI

The UI should display:

- ✅ **Status**: Connected/Active
- ✅ **Phone Number**: WhatsApp number (if available)
- ✅ **Webhook URL**: Full token-based URL
- ✅ **Last Seen**: Recent timestamp
- ✅ **Instance Name**: Your Evolution instance

### Monitor Logs

```bash
# Watch for incoming messages
fly logs -a healthcare-clinic-backend -f | grep "Token Async"

# Check for errors
fly logs -a healthcare-clinic-backend | grep "❌"

# Check cache performance
fly logs -a healthcare-clinic-backend | grep "Token cache HIT"
```

## Troubleshooting

### Issue: QR Code Not Generating

**Solution:**
1. Check Evolution API is running: `fly status -a evolution-api-prod`
2. Check Evolution API logs: `fly logs -a evolution-api-prod`
3. Try creating instance manually via Evolution dashboard

### Issue: Messages Not Being Received

**Solution:**
1. Verify webhook URL is correct in Evolution settings
2. Check webhook signature is enabled
3. Verify `EVOLUTION_WEBHOOK_SECRET` env var matches
4. Check backend logs for webhook errors
5. Test webhook manually: `python3 test_webhook_endpoint.py <token> --prod`

### Issue: "No clinic found for token"

**Solution:**
1. Verify integration exists in database:
   ```bash
   python3 get_webhook_tokens.py
   ```
2. Check token matches the one in Evolution webhook config
3. Warm up cache:
   ```bash
   python3 -c "
   import asyncio
   from app.services.whatsapp_clinic_cache import get_whatsapp_clinic_cache
   asyncio.run(get_whatsapp_clinic_cache().warmup_all_instances())
   "
   ```

### Issue: Integration Not Showing in UI

**Possible causes:**
1. Integration not saved to database
2. Frontend not fetching from `healthcare.integrations`
3. Organization/clinic filtering issue

**Solution:**
1. Check database directly:
   ```sql
   SELECT id, clinic_id, webhook_token, status, enabled
   FROM healthcare.integrations
   WHERE type = 'whatsapp'
   ORDER BY created_at DESC;
   ```
2. Verify frontend API is querying `healthcare.integrations` table
3. Check browser console for API errors

## Data Structure Reference

### healthcare.integrations Table

```sql
{
  id: UUID,
  organization_id: UUID,
  clinic_id: UUID,
  type: 'whatsapp',
  provider: 'evolution',
  status: 'active' | 'pending' | 'disconnected',
  enabled: true/false,
  webhook_token: 'AbCd...',  -- Auto-generated
  webhook_url: 'https://...',  -- Auto-computed
  phone_number: '+1234567890',
  display_name: 'Clinic Name',
  config: {
    instance: 'clinic-instance-name',
    instance_key: '...',
    connection_type: 'baileys'
  },
  connected_at: timestamp,
  last_seen_at: timestamp,
  created_at: timestamp,
  updated_at: timestamp
}
```

## Best Practices

1. **Unique Instance Names**: Use a pattern like `{clinic_slug}-{env}` (e.g., `shtern-clinic-prod`)
2. **Test Before Production**: Always test in a staging environment first
3. **Monitor Initial Setup**: Watch logs closely during first 24 hours
4. **Document Tokens**: Keep a backup of webhook tokens securely
5. **Regular Health Checks**: Set up monitoring/alerts for webhook failures

## Next Steps

After successful setup:

1. ✅ Test with various message types (text, media, etc.)
2. ✅ Configure response templates in clinic settings
3. ✅ Set up monitoring/alerts
4. ✅ Train staff on WhatsApp integration features
5. ✅ Consider setting up backup/redundancy

## Support

For issues or questions:

1. Check backend logs: `fly logs -a healthcare-clinic-backend`
2. Check Evolution logs: `fly logs -a evolution-api-prod`
3. Review this guide's Troubleshooting section
4. Test webhook manually with test script
5. Verify database records with `get_webhook_tokens.py`
