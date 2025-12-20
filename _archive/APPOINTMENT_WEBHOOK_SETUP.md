# Appointment Calendar Auto-Sync Setup

## Overview
Automatically sync appointments to Google Calendar when they're created or updated.

## Components Created

### 1. Database Trigger ✅
- **Migration**: `create_appointment_calendar_webhook.sql`
- **Function**: `healthcare.notify_appointment_change()`
- **Trigger**: `appointment_calendar_sync_trigger`
- **Status**: Applied to database

### 2. Webhook Endpoints ✅
- **File**: `app/webhooks/appointment_sync_webhook.py`
- **Endpoints**:
  - POST `/webhooks/appointment-sync/supabase` - For Supabase Database Webhooks
  - POST `/webhooks/appointment-sync/pg-notify` - For pg_notify events

### 3. Backend Integration ✅
- Webhook router registered in `app/main.py`
- Uses `ExternalCalendarService` for calendar sync
- Background tasks for async processing

## Setup Instructions

### Option 1: Supabase Database Webhooks (Recommended)

1. **Go to Supabase Dashboard**
   - Navigate to your project: https://supabase.com/dashboard
   - Go to Database → Webhooks

2. **Create New Webhook**
   - Click "Create a new hook"
   - Name: `appointment_calendar_sync`
   - Table: `healthcare.appointments`
   - Events: Check `Insert` and `Update`
   - Type: `HTTP Request`

3. **Configure Webhook**
   - Method: `POST`
   - URL: `https://healthcare-clinic-backend.fly.dev/webhooks/appointment-sync/supabase`
   - Headers:
     ```json
     {
       "Content-Type": "application/json"
     }
     ```

4. **Test Webhook**
   - Create a test appointment in Supabase
   - Check backend logs for sync confirmation
   - Verify event appears in Google Calendar

### Option 2: Edge Function (Alternative)

If you prefer using Supabase Edge Functions:

```typescript
// Create edge function at supabase/functions/appointment-sync/index.ts
import { serve } from "https://deno.land/std@0.168.0/http/server.ts"

serve(async (req) => {
  const payload = await req.json()
  
  // Forward to backend webhook
  const response = await fetch(
    'https://healthcare-clinic-backend.fly.dev/webhooks/appointment-sync/supabase',
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    }
  )
  
  return new Response(JSON.stringify(await response.json()), {
    headers: { 'Content-Type': 'application/json' }
  })
})
```

Then configure database webhook to call the edge function.

## How It Works

1. **Appointment Created/Updated** → Database trigger fires
2. **Webhook Called** → Supabase calls backend webhook endpoint
3. **Background Task** → Backend queues calendar sync task
4. **Calendar Sync** → Appointment synced to doctor's Google Calendar
5. **Multi-Doctor Support** → Automatically uses correct doctor's calendar

## What Gets Synced

- ✅ **New Appointments**: status = 'scheduled' or 'confirmed'
- ✅ **Updated Appointments**: Changes to time, doctor, or details
- ✅ **Doctor-Specific Calendars**: Uses multi-doctor sub-calendars
- ✅ **Rich Event Details**: Patient name, type, notes, phone

## What Doesn't Get Synced (Yet)

- ❌ **Cancellations**: Calendar event deletion not implemented
- ❌ **Draft Appointments**: Only scheduled/confirmed appointments sync
- ❌ **No Doctor**: Appointments without a doctor assigned

## Monitoring

Check backend logs for sync activity:
```bash
fly logs -a healthcare-clinic-backend | grep "appointment.*sync"
```

Look for log messages:
- `✅ Successfully synced appointment {id} to calendar`
- `❌ Failed to sync appointment {id}: {error}`

## Troubleshooting

### Webhook Not Firing
1. Check Supabase Webhook logs in Dashboard
2. Verify URL is correct and accessible
3. Check if trigger is enabled:
   ```sql
   SELECT tgname, tgenabled 
   FROM pg_trigger 
   WHERE tgrelid = 'healthcare.appointments'::regclass;
   ```

### Calendar Not Syncing
1. Check backend logs for errors
2. Verify Google Calendar OAuth token is valid
3. Ensure multi-doctor mode is enabled
4. Confirm doctor has a calendar ID

### Token Expired
If you see "Token expired" errors:
1. Go to Integrations page
2. Disconnect Google Calendar
3. Reconnect to refresh token

## Testing

Test the webhook manually:
```bash
curl -X POST https://healthcare-clinic-backend.fly.dev/webhooks/appointment-sync/supabase \
  -H "Content-Type: application/json" \
  -d '{
    "type": "INSERT",
    "record": {
      "id": "test-id",
      "status": "scheduled"
    }
  }'
```

## Next Steps

- [ ] Deploy backend (fix volume issue first)
- [ ] Configure Supabase Database Webhook
- [ ] Test with real appointment creation
- [ ] Implement calendar event deletion for cancellations
- [ ] Add retry logic for failed syncs
