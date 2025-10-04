# Calendar Sync Diagnosis

## Issue
From logs at 21:53:12-13, bulk sync was triggered but returned:
- `get_unsynced_appointments` RPC called successfully (200 OK)
- Result: No appointments to sync

## Possible Causes

### 1. No Calendar Integration Set Up
**Check**: Does the healthcare.calendar_integrations table have records?
```sql
SELECT * FROM healthcare.calendar_integrations WHERE sync_enabled = true;
```

### 2. No Appointments Exist
**Check**: Are there appointments in the database?
```sql
SELECT COUNT(*) FROM appointments;
```

### 3. All Appointments Already Synced
**Check**: Do appointments have `google_event_id` populated?
```sql
SELECT COUNT(*) as total,
       COUNT(google_event_id) as synced  
FROM appointments;
```

### 4. RPC Function Filter Issue
The `get_unsynced_appointments` function might be filtering too aggressively.

**Check**: Does the RPC function exist and what are its criteria?
```sql
\df get_unsynced_appointments
```

## Code Flow After Migration

1. User triggers sync via `/api/calendar/sync/bulk`
2. Code calls `healthcare.get_calendar_integration_by_clinic()`
3. If integration found, retrieves credentials from vault
4. Creates Google Calendar event
5. Updates appointment with `google_event_id`

## What We Changed

- ✅ Updated `create_calendar_event()` to use new RPC (line 835)
- ✅ Added vault credential retrieval (line 858)
- ✅ Proper error handling for missing integration

## Expected Behavior

If calendar is NOT configured:
- Should return `{'success': True, 'note': 'Google Calendar not configured'}`

If calendar IS configured but no credentials in vault:
- Should return `{'success': False, 'error': 'Failed to retrieve calendar credentials'}`

## Next Steps

1. Check if calendar integration exists in DB
2. Check if there are appointments to sync
3. Test sync manually with a specific appointment ID
4. Check vault has actual credentials (not just vault_ref)

