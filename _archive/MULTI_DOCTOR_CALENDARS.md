# Multi-Doctor Sub-Calendars Feature

## Overview

This feature creates separate, color-coded Google Calendar sub-calendars for each doctor in your practice, providing better visual organization and management.

## Benefits

### Before (Single Calendar)
- All appointments in one calendar
- No visual differentiation between doctors
- Hard to see which doctor has appointments
- Can't toggle doctors on/off

### After (Multi-Doctor Calendars)
- ✅ Each doctor has their own calendar
- ✅ Unique color per doctor (11 colors available)
- ✅ Toggle doctors on/off in Google Calendar UI
- ✅ Better visual organization
- ✅ Each doctor can share their calendar with specific people

## Architecture

### Database Schema

**New Columns in `doctors` table**:
- `google_calendar_id` - The sub-calendar ID for this doctor
- `google_calendar_color_id` - Color ID (1-11) for visual differentiation
- `google_calendar_created_at` - When the calendar was created

**New Column in `calendar_integrations` table**:
- `multi_doctor_mode` - Boolean flag to enable/disable multi-doctor mode

### RPC Function

`get_doctor_calendar_id(p_doctor_id, p_organization_id)`:
- Returns doctor's sub-calendar ID if multi-doctor mode is enabled
- Returns 'primary' if multi-doctor mode is disabled
- Handles fallback gracefully

## Setup Process

### 1. Enable Multi-Doctor Mode

```bash
POST /api/calendar-management/setup-multi-doctor
{
  "organization_id": "your-org-id"
}
```

This will:
1. Fetch all doctors in the organization
2. Create a Google Calendar sub-calendar for each doctor
3. Assign unique colors (cycles through 11 colors)
4. Enable multi-doctor mode for the organization

### 2. Check Status

```bash
GET /api/calendar-management/status/{organization_id}
```

Returns:
```json
{
  "success": true,
  "organization_id": "...",
  "multi_doctor_mode": true,
  "total_doctors": 5,
  "doctors_with_calendars": 5,
  "doctors": [
    {
      "id": "...",
      "name": "Dr. Smith",
      "has_calendar": true,
      "calendar_id": "abc123@group.calendar.google.com",
      "color_id": "1"
    }
  ]
}
```

### 3. Disable Multi-Doctor Mode (Optional)

```bash
POST /api/calendar-management/disable-multi-doctor/{organization_id}
```

This will:
- Revert to using primary calendar for all doctors
- Keep sub-calendars intact (doesn't delete them)
- Future appointments will use primary calendar

## Color Mapping

Google Calendar provides 11 standard colors:

1. **Lavender** (#7986CB) - Light purple/blue
2. **Sage** (#33B679) - Green
3. **Grape** (#8E24AA) - Purple
4. **Flamingo** (#E67C73) - Pink/coral
5. **Banana** (#F6BF26) - Yellow
6. **Tangerine** (#F4511E) - Orange
7. **Peacock** (#039BE5) - Blue
8. **Graphite** (#616161) - Gray
9. **Blueberry** (#3F51B5) - Dark blue
10. **Basil** (#0B8043) - Dark green
11. **Tomato** (#D50000) - Red

Colors are assigned automatically in rotation.

## How It Works

### Appointment Sync Flow

1. **Create Appointment**:
   - System calls `_get_doctor_calendar()` with doctor_id and org_id
   - RPC function checks if multi-doctor mode is enabled
   - Returns doctor's sub-calendar ID or 'primary'
   - Event is created in the target calendar

2. **Visual Result in Google Calendar**:
   - Dr. Smith's appointments appear in Lavender calendar
   - Dr. Jones's appointments appear in Sage calendar
   - Dr. Garcia's appointments appear in Grape calendar
   - Each doctor's schedule is visually distinct

3. **Toggle Doctors**:
   - In Google Calendar web/mobile app
   - Click calendar name in left sidebar
   - Checkmark controls visibility
   - Hide/show doctors without affecting data

## Migration Path

### Enabling for Existing Practice

If you already have appointments synced to primary calendar:

1. **Enable multi-doctor mode**:
   ```bash
   POST /api/calendar-management/setup-multi-doctor
   ```

2. **Future appointments** will automatically use doctor-specific calendars

3. **Existing appointments** remain in primary calendar (optional: migrate them)

### Migrating Existing Appointments

To move existing appointments to doctor-specific calendars:

1. The appointments already have `doctor_id`
2. Re-sync them using the sync API:
   ```bash
   POST /api/calendar-sync/bulk
   ```
3. System will create events in doctor-specific calendars

## API Endpoints

### Setup Multi-Doctor Calendars
```
POST /api/calendar-management/setup-multi-doctor
Body: { "organization_id": "uuid" }
```

### Get Multi-Doctor Status
```
GET /api/calendar-management/status/{organization_id}
```

### Disable Multi-Doctor Mode
```
POST /api/calendar-management/disable-multi-doctor/{organization_id}
```

## Files Modified

1. **infra/db/migrations/multi_doctor_calendars.sql**
   - Database schema changes

2. **app/services/doctor_calendar_manager.py**
   - NEW: Service to manage doctor calendars

3. **app/services/external_calendar_service.py**
   - Added `_get_doctor_calendar()` method
   - Updated `create_calendar_event()` to use doctor's calendar

4. **app/api/calendar_management.py**
   - NEW: API endpoints for calendar management

5. **app/main.py**
   - Registered calendar_management router

## Testing

### Test Setup

1. **Setup calendars**:
   ```bash
   curl -X POST http://localhost:8000/api/calendar-management/setup-multi-doctor \
     -H "Content-Type: application/json" \
     -d '{"organization_id": "your-org-id"}'
   ```

2. **Check status**:
   ```bash
   curl http://localhost:8000/api/calendar-management/status/your-org-id
   ```

3. **Create test appointment**:
   ```bash
   POST /api/appointments/book
   # Should create event in doctor's specific calendar
   ```

4. **Verify in Google Calendar**:
   - Open Google Calendar
   - See multiple calendars in left sidebar (one per doctor)
   - Each with unique color
   - Appointments appear in respective doctor's calendar

## Troubleshooting

### Issue: Calendars not appearing in Google Calendar

**Solution**:
- Check that sub-calendars were created successfully
- Verify multi_doctor_mode is enabled in database
- Check RPC function returns correct calendar_id

### Issue: All appointments still going to primary calendar

**Solution**:
- Check multi_doctor_mode flag: `SELECT multi_doctor_mode FROM calendar_integrations`
- Verify doctors have google_calendar_id set
- Check logs for "_get_doctor_calendar" calls

### Issue: Can't see doctor calendars in Google Calendar mobile

**Solution**:
- Tap hamburger menu → Settings
- Scroll to "Show more" under calendars
- Toggle on doctor calendars

## Future Enhancements

1. **Custom Colors**: Allow admins to choose specific colors per doctor
2. **Calendar Sharing**: Auto-share doctor calendars with front desk staff
3. **Bulk Migration**: Migrate all existing appointments to doctor calendars
4. **Calendar Permissions**: Fine-grained access control per doctor

## Summary

Multi-doctor calendars provide:
- 📅 Better visual organization
- 🎨 Color-coded per doctor
- 👁️ Toggle visibility per doctor
- 🔒 Individual calendar permissions
- 📱 Works in web and mobile Google Calendar

**Cost**: Free (uses Google Calendar's built-in secondary calendars feature)
