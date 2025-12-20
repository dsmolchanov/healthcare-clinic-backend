# Evolution WhatsApp Duplicate Instances Issue - Fixed

## Problem Summary

**Issue:** WhatsApp integration stuck in "pending" status after successfully scanning QR code and linking device to Evolution.

**Root Cause:** Multiple duplicate Evolution instances created for the same clinic ID, causing WhatsApp to reject authentication with `401 device_removed` error due to multi-session conflict.

## Investigation Timeline

### Discovery
- User scanned QR code successfully at 17:26:30
- WhatsApp device authenticated to instance `clinic-4e8ddba1-ad52-4613-9a03-ec64636b3f6c-1763139992470`
- Immediately received stream error: `401 device_removed` with conflict type `device_removed`
- Connection closed, Evolution marked reconnect as `false`
- New QR code generated instead of maintaining authenticated session

### Root Cause Analysis
Found **6 duplicate instances** for clinic `4e8ddba1-ad52-4613-9a03-ec64636b3f6c`:
1. `clinic-4e8ddba1-ad52-4613-9a03-ec64636b3f6c-1763133674944`
2. `clinic-4e8ddba1-ad52-4613-9a03-ec64636b3f6c-1763136505298`
3. `clinic-4e8ddba1-ad52-4613-9a03-ec64636b3f6c-1763139992470` ← Authenticated session
4. `clinic-4e8ddba1-ad52-4613-9a03-ec64636b3f6c-1763140317189` ← Active QR session
5. `clinic-4e8ddba1-ad52-4613-9a03-ec64636b3f6c-1763141478931`
6. `clinic-4e8ddba1-ad52-4613-9a03-ec64636b3f6c-1763223105337`

**WhatsApp Security**: WhatsApp detected multiple active sessions for the same phone number and rejected the connection as a security conflict.

## The Fix

### 1. Cleaned Up Duplicate Instances
```bash
./cleanup_duplicate_instances.sh "4e8ddba1-ad52-4613-9a03-ec64636b3f6c"
```

**Results:**
- ✅ Deleted all 6 duplicate instances from Evolution API
- ✅ Deleted stale database record from `healthcare.integrations`
- ✅ Verified cleanup: 0 instances remaining

### 2. Database Cleanup
```sql
DELETE FROM healthcare.integrations
WHERE id = 'a2ff6120-7fc7-4f26-b246-03ef057b4f45';
```

## Next Steps for User

1. **Create New Integration:**
   - Go to the UI
   - Create a NEW WhatsApp integration
   - This will create a single, fresh Evolution instance

2. **Scan QR Code:**
   - Scan the new QR code with your WhatsApp
   - Should connect successfully within seconds

3. **Verify Status:**
   - Integration should show as "connected"
   - Phone number should appear
   - `connected_at` timestamp should be set

## Prevention

To prevent this issue from recurring, we need to investigate why multiple instances are being created. Possible causes:

1. **UI Double-Click:** User clicking "Create Integration" multiple times
2. **Backend Race Condition:** Multiple concurrent requests creating instances
3. **Retry Logic:** Failed requests being retried without cleanup

**Recommended Fix:** Add unique constraint or check-and-create logic in `save_evolution_integration` RPC function to prevent duplicate instance creation for the same organization_id.

## Files Created

- `cleanup_duplicate_instances.sh` - Script to cleanup duplicate instances
- `fix_duplicate_instances.py` - Python version (requires env vars)
- `EVOLUTION_DUPLICATE_INSTANCES_FIX.md` - This document

## Status
✅ **FIXED** - All duplicate instances removed, database cleaned up, ready for fresh integration creation.
