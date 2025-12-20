# Evolution WhatsApp Instance Restoration Guide

## Problem Summary

The Evolution API server has a failed WhatsApp instance with a **401 Unauthorized** error:

```
Instance: clinic-4e8ddba1-ad52-4613-9a03-ec64636b3f6c-1760994893945
Error: Connection Failure (401 Unauthorized)
Cause: Expired or invalid WhatsApp Web session credentials
```

Additionally, there are **10+ invalid test instances** that need cleanup.

## Quick Diagnosis

From the logs:
- ✅ Evolution API is running (`evolution-api-prod.fly.dev`)
- ❌ Main clinic instance has expired credentials (401 error)
- ⚠️ Multiple test instances with invalid credentials
- 🔄 Auto-reconnection is failing

## Solution Options

### Option 1: Automated Restoration (Recommended)

Use the Python script for full automation:

```bash
# Step 1: Authenticate to Fly.io
fly auth login

# Step 2: Run restoration script
cd apps/healthcare-backend
python3 restore_evolution_instance.py
```

**What it does:**
1. ✅ Checks database configuration
2. 🗑️ Deletes failed instance
3. 🆕 Creates new instance with proper webhook
4. 💾 Updates database with new instance name
5. 📱 Provides QR code scanning instructions

### Option 2: Manual Restoration

#### Step 2.1: Cleanup Invalid Instances

```bash
cd apps/healthcare-backend
./cleanup_evolution_instances.sh
```

Or manually via Evolution Manager:
1. Open: https://evolution-api-prod.fly.dev/manager
2. Delete each invalid instance

**Instances to delete:**
- `clinic-4e8ddba1-ad52-4613-9a03-ec64636b3f6c-1760994893945` (failed)
- `complete-test-1757901945`
- `final-rpc-test-1757903110`
- `frontend-format-test-1757903854`
- `frontend-test-camel`
- `test-debug-422`
- `test-final-1757902500`
- `test-from-curl`
- `test-instance`
- `test-snakecase`

#### Step 2.2: Check Database Configuration

```bash
fly ssh console -a healthcare-clinic-backend -C "python3 -c '
from app.db.supabase_client import get_supabase_client
import json
s = get_supabase_client()
r = s.schema(\"healthcare\").table(\"integrations\").select(\"id,clinic_id,webhook_token,webhook_url,config\").eq(\"type\",\"whatsapp\").execute()
for item in r.data:
    print(json.dumps(item, indent=2))
'"
```

**Note down:**
- `clinic_id`
- `webhook_token`
- `webhook_url`

#### Step 2.3: Create New Instance

**Method A: Via Frontend UI** (Easiest)

1. Go to: https://plaintalk-frontend.vercel.app
2. Navigate to **Integrations**
3. If there's an existing WhatsApp integration:
   - Click **Edit** or **Reconnect**
   - This will generate a new QR code
4. If not, click **Add Integration**:
   - Type: WhatsApp
   - Provider: Evolution API
   - Select your clinic
   - Instance name: `clinic-{clinic_id}` (simplified)
5. Scan QR code with WhatsApp
6. Wait for connection confirmation

**Method B: Via API** (Advanced)

```bash
# Set your webhook token from database
WEBHOOK_TOKEN="your-token-here"
CLINIC_ID="your-clinic-id"
INSTANCE_NAME="clinic-${CLINIC_ID}"

# Create instance
curl -X POST "https://evolution-api-prod.fly.dev/instance/create" \
  -H "Content-Type: application/json" \
  -d "{
    \"instanceName\": \"${INSTANCE_NAME}\",
    \"qrcode\": true,
    \"webhook\": \"https://healthcare-clinic-backend.fly.dev/webhooks/evolution/whatsapp/${WEBHOOK_TOKEN}\",
    \"webhook_by_events\": false,
    \"events\": [
      \"QRCODE_UPDATED\",
      \"MESSAGES_UPSERT\",
      \"MESSAGES_UPDATE\",
      \"CONNECTION_UPDATE\"
    ]
  }"
```

#### Step 2.4: Update Database (If using API method)

```bash
fly ssh console -a healthcare-clinic-backend -C "python3 -c '
from app.db.supabase_client import get_supabase_client
s = get_supabase_client()

# Get integration
r = s.schema(\"healthcare\").table(\"integrations\").select(\"*\").eq(\"type\",\"whatsapp\").execute()
integration = r.data[0]

# Update config with new instance name
new_config = integration.get(\"config\", {})
new_config[\"instance\"] = \"clinic-{CLINIC_ID}\"  # Replace with actual ID

s.schema(\"healthcare\").table(\"integrations\").update({
    \"config\": new_config
}).eq(\"id\", integration[\"id\"]).execute()

print(\"✅ Database updated\")
'"
```

#### Step 2.5: Scan QR Code

1. Open Evolution Manager: https://evolution-api-prod.fly.dev/manager
2. Find your new instance (`clinic-{clinic_id}`)
3. Click on it to view QR code
4. Open WhatsApp on your phone
5. Go to **Settings** → **Linked Devices** → **Link a Device**
6. Scan the QR code
7. Wait for connection (status should change to "open")

### Option 3: Fresh Start via Frontend

If you don't need to preserve anything:

1. **Delete old integration** (if exists):
   - Go to frontend → Integrations
   - Delete WhatsApp integration

2. **Create new integration**:
   - Click "Add Integration"
   - Fill in details
   - Scan QR code
   - Done!

## Verification Steps

After restoration, verify everything works:

### 1. Check Instance Status

```bash
fly logs -a evolution-api-prod -f | grep "clinic-"
```

Should see: `Connection update: {"connection":"open"}`

### 2. Check Backend Logs

```bash
fly logs -a healthcare-clinic-backend -f | grep "WhatsApp"
```

### 3. Test Message Flow

Send a test message to your WhatsApp number:

```bash
cd apps/healthcare-backend
python3 test_webhook_endpoint.py <webhook-token> --prod
```

Or send a real WhatsApp message and check logs:

```bash
fly logs -a healthcare-clinic-backend -f | grep "Token Async"
```

Should see: `[Token Async Handler] Received webhook for clinic_id=...`

### 4. Check Database State

```bash
fly ssh console -a healthcare-clinic-backend -C "python3 get_webhook_tokens.py"
```

Should show active integration with new instance name.

## Common Issues & Solutions

### Issue 1: "No access token available"

**Solution:**
```bash
fly auth login
```

### Issue 2: QR Code Expires

**Symptom:** QR code times out before scanning

**Solution:**
- QR codes expire after 60 seconds
- Refresh the page in Evolution Manager
- Or reconnect/recreate the instance

### Issue 3: Connection Closes Immediately After Scanning

**Symptom:** Connection goes to "open" then immediately to "close"

**Possible causes:**
- WhatsApp Web already linked to 4 devices (max limit)
- Phone is offline
- WhatsApp needs update

**Solution:**
- Unlink one device from WhatsApp
- Ensure phone has internet
- Update WhatsApp to latest version

### Issue 4: Webhook Not Receiving Messages

**Check:**
1. Webhook URL is correct in Evolution instance
2. Backend is running: `fly status -a healthcare-clinic-backend`
3. No errors in backend logs: `fly logs -a healthcare-clinic-backend`

**Test webhook:**
```bash
python3 test_webhook_endpoint.py <token> --prod
```

### Issue 5: Database Out of Sync

**Symptom:** Database shows old instance name

**Fix:**
```bash
# Update database manually (replace values)
fly ssh console -a healthcare-clinic-backend -C "python3 -c '
from app.db.supabase_client import get_supabase_client
s = get_supabase_client()
s.schema(\"healthcare\").table(\"integrations\").update({
    \"config\": {\"instance\": \"NEW_INSTANCE_NAME\"}
}).eq(\"type\", \"whatsapp\").execute()
print(\"Updated\")
'"
```

## Prevention

To avoid this issue in the future:

1. **Monitor connection status:**
   ```bash
   # Add to cron or monitoring
   fly logs -a evolution-api-prod | grep "connection.*close"
   ```

2. **Set up alerts** for 401 errors

3. **Keep phone online** with good internet connection

4. **Don't unlink manually** from WhatsApp app

5. **Regular health checks:**
   ```bash
   # Check daily
   curl https://evolution-api-prod.fly.dev/instance/fetchInstances
   ```

## Quick Command Reference

```bash
# Authenticate
fly auth login

# Automated restoration
cd apps/healthcare-backend
python3 restore_evolution_instance.py

# Manual cleanup
./cleanup_evolution_instances.sh

# Check status
fly logs -a evolution-api-prod -f

# Check backend
fly logs -a healthcare-clinic-backend -f

# Test webhook
python3 test_webhook_endpoint.py <token> --prod

# Evolution Manager
open https://evolution-api-prod.fly.dev/manager
```

## Support

If issues persist:

1. Check Evolution API status: `fly status -a evolution-api-prod`
2. Restart Evolution API: `fly restart -a evolution-api-prod`
3. Check backend status: `fly status -a healthcare-clinic-backend`
4. Review full logs for errors
5. Verify Supabase connection

## Files Reference

- `restore_evolution_instance.py` - Automated restoration script
- `cleanup_evolution_instances.sh` - Cleanup invalid instances
- `get_webhook_tokens.py` - Check database state
- `test_webhook_endpoint.py` - Test webhook connectivity
- `UI_INTEGRATION_SETUP_GUIDE.md` - Frontend setup guide
- `EVOLUTION_WEBHOOK_UPDATE_GUIDE.md` - Webhook configuration

---

**Last Updated:** 2025-11-14

**Status:** Ready to use

**Estimated Time:** 10-15 minutes for automated restoration
