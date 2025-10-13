# Room Assignment API Documentation

## Overview

This document describes the REST API endpoints for room assignment functionality in the healthcare appointment system. Room assignment is integrated into the appointment booking flow and provides automatic assignment with manual override capabilities.

**Base URL**: `https://healthcare-clinic-backend.fly.dev`
**API Version**: v1
**Authentication**: Required for all endpoints (Bearer token)

## Table of Contents

1. [Authentication](#authentication)
2. [Appointment Booking with Room Assignment](#1-appointment-booking-with-room-assignment)
3. [Manual Room Override](#2-manual-room-override)
4. [Batch Room Availability](#3-batch-room-availability)
5. [Room Display Configuration](#4-room-display-configuration)
6. [Update Room Display Configuration](#5-update-room-display-configuration)
7. [Error Codes](#error-codes)
8. [Rate Limiting](#rate-limiting)
9. [Examples](#examples)

---

## Authentication

All API requests require a valid JWT token in the Authorization header:

```http
Authorization: Bearer <your_jwt_token>
```

### Obtaining a Token

```bash
curl -X POST https://healthcare-clinic-backend.fly.dev/auth/login \
  -H "Content-Type: application/json" \
  -d '{
    "email": "user@example.com",
    "password": "your_password"
  }'
```

**Response**:
```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type": "bearer",
  "expires_in": 3600
}
```

---

## 1. Appointment Booking with Room Assignment

**Endpoint**: `POST /api/v1/appointments`

**Description**: Creates a new appointment with automatic room assignment based on availability and scheduling rules.

### Request

**Headers**:
```http
Authorization: Bearer <token>
Content-Type: application/json
```

**Body**:
```json
{
  "clinic_id": "uuid",
  "patient_id": "uuid",
  "doctor_id": "uuid",
  "service_id": "uuid",
  "start_time": "2025-10-15T10:00:00Z",
  "end_time": "2025-10-15T10:30:00Z",
  "status": "scheduled",
  "notes": "Patient requested morning appointment"
}
```

**Required Fields**:
- `clinic_id` (string, uuid): ID of the clinic
- `patient_id` (string, uuid): ID of the patient
- `doctor_id` (string, uuid): ID of the doctor
- `service_id` (string, uuid): ID of the service type
- `start_time` (string, ISO 8601): Appointment start time (UTC)
- `end_time` (string, ISO 8601): Appointment end time (UTC)

**Optional Fields**:
- `status` (string): Initial status (default: "scheduled")
- `notes` (string): Additional notes

### Response

**Success (201 Created)**:
```json
{
  "id": "appointment-uuid",
  "clinic_id": "clinic-uuid",
  "patient_id": "patient-uuid",
  "doctor_id": "doctor-uuid",
  "service_id": "service-uuid",
  "room_id": "room-uuid",
  "start_time": "2025-10-15T10:00:00Z",
  "end_time": "2025-10-15T10:30:00Z",
  "status": "scheduled",
  "notes": "Patient requested morning appointment",
  "room": {
    "id": "room-uuid",
    "room_number": "101",
    "room_name": "Consultation Room A",
    "room_type": "consultation",
    "equipment": ["basic", "ecg"]
  },
  "assignment_metadata": {
    "method": "auto",
    "score": 85.5,
    "rules_applied": [
      "room_type_match",
      "doctor_room_preference"
    ]
  },
  "created_at": "2025-10-15T09:55:00Z",
  "updated_at": "2025-10-15T09:55:00Z"
}
```

**Error (400 Bad Request)** - No rooms available:
```json
{
  "detail": {
    "error": "no_rooms_available",
    "message": "No suitable rooms available for the selected time slot",
    "suggested_times": [
      "2025-10-15T10:30:00Z",
      "2025-10-15T11:00:00Z",
      "2025-10-15T11:30:00Z"
    ]
  }
}
```

**Error (409 Conflict)** - Time slot conflict:
```json
{
  "detail": {
    "error": "time_slot_conflict",
    "message": "Doctor already has an appointment at this time",
    "conflicting_appointment_id": "existing-appointment-uuid"
  }
}
```

### Room Assignment Logic

The system automatically assigns rooms using the following process:

1. **Filter Available Rooms**: Query rooms that have no conflicting appointments
2. **Apply Hard Constraints**: Remove rooms that violate hard constraints (equipment, room type)
3. **Score Remaining Rooms**: Apply soft preferences (doctor preference, utilization balancing)
4. **Select Highest Scoring Room**: Assign room with best score
5. **Fallback**: If no rooms pass constraints, return error with suggestions

### cURL Example

```bash
curl -X POST https://healthcare-clinic-backend.fly.dev/api/v1/appointments \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "clinic_id": "550e8400-e29b-41d4-a716-446655440000",
    "patient_id": "660e8400-e29b-41d4-a716-446655440001",
    "doctor_id": "770e8400-e29b-41d4-a716-446655440002",
    "service_id": "880e8400-e29b-41d4-a716-446655440003",
    "start_time": "2025-10-15T10:00:00Z",
    "end_time": "2025-10-15T10:30:00Z"
  }'
```

---

## 2. Manual Room Override

**Endpoint**: `PATCH /api/v1/appointments/{appointment_id}/room`

**Description**: Manually override the assigned room for an existing appointment. Requires a reason for audit compliance.

### Request

**Headers**:
```http
Authorization: Bearer <token>
Content-Type: application/json
```

**URL Parameters**:
- `appointment_id` (string, uuid, required): ID of the appointment to update

**Body**:
```json
{
  "room_id": "new-room-uuid",
  "reason": "Patient requested room closer to entrance for mobility assistance"
}
```

**Required Fields**:
- `room_id` (string, uuid): ID of the new room
- `reason` (string, min 10 chars): Reason for manual override

### Response

**Success (200 OK)**:
```json
{
  "id": "appointment-uuid",
  "room_id": "new-room-uuid",
  "previous_room_id": "old-room-uuid",
  "override_reason": "Patient requested room closer to entrance for mobility assistance",
  "override_by": "user-uuid",
  "override_at": "2025-10-15T10:05:00Z",
  "room": {
    "id": "new-room-uuid",
    "room_number": "102",
    "room_name": "Consultation Room B",
    "room_type": "consultation"
  },
  "updated_at": "2025-10-15T10:05:00Z"
}
```

**Error (400 Bad Request)** - Validation errors:
```json
{
  "detail": [
    {
      "loc": ["body", "reason"],
      "msg": "ensure this value has at least 10 characters",
      "type": "value_error.any_str.min_length"
    }
  ]
}
```

**Error (400 Bad Request)** - Room from different clinic:
```json
{
  "detail": {
    "error": "invalid_room",
    "message": "Selected room belongs to a different clinic"
  }
}
```

**Error (400 Bad Request)** - Cancelled appointment:
```json
{
  "detail": {
    "error": "invalid_status",
    "message": "Cannot modify room for cancelled appointments",
    "current_status": "cancelled"
  }
}
```

**Error (404 Not Found)** - Appointment not found:
```json
{
  "detail": {
    "error": "not_found",
    "message": "Appointment not found"
  }
}
```

**Error (404 Not Found)** - Room not found:
```json
{
  "detail": {
    "error": "room_not_found",
    "message": "Room not found or not available"
  }
}
```

**Error (409 Conflict)** - Room already occupied:
```json
{
  "detail": {
    "error": "room_conflict",
    "message": "Selected room is already occupied at this time",
    "conflicting_appointment_id": "other-appointment-uuid"
  }
}
```

### Validation Rules

- Reason must be at least 10 characters
- Room must belong to same clinic as appointment
- Room must exist and be available
- Appointment status must be "scheduled" or "confirmed" (not "cancelled", "completed", "no_show")
- Room must not have conflicting appointments at the same time

### cURL Example

```bash
curl -X PATCH https://healthcare-clinic-backend.fly.dev/api/v1/appointments/550e8400-e29b-41d4-a716-446655440000/room \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "room_id": "770e8400-e29b-41d4-a716-446655440004",
    "reason": "Patient has mobility issues and needs ground floor room"
  }'
```

---

## 3. Batch Room Availability

**Endpoint**: `POST /api/v1/rooms/availability/batch`

**Description**: Get available rooms for multiple time slots in a single request. Optimized for calendar view and slot selection UI.

### Request

**Headers**:
```http
Authorization: Bearer <token>
Content-Type: application/json
```

**Body**:
```json
{
  "clinic_id": "clinic-uuid",
  "doctor_id": "doctor-uuid",
  "service_id": "service-uuid",
  "date_start": "2025-10-15T08:00:00Z",
  "date_end": "2025-10-15T18:00:00Z",
  "slot_duration_minutes": 30
}
```

**Required Fields**:
- `clinic_id` (string, uuid): ID of the clinic
- `date_start` (string, ISO 8601): Start of time range
- `date_end` (string, ISO 8601): End of time range

**Optional Fields**:
- `doctor_id` (string, uuid): Filter by doctor
- `service_id` (string, uuid): Filter by service type (applies equipment rules)
- `slot_duration_minutes` (integer, default: 30): Duration of each slot

### Response

**Success (200 OK)**:
```json
{
  "clinic_id": "clinic-uuid",
  "date_start": "2025-10-15T08:00:00Z",
  "date_end": "2025-10-15T18:00:00Z",
  "slots": [
    {
      "start_time": "2025-10-15T10:00:00Z",
      "end_time": "2025-10-15T10:30:00Z",
      "available_rooms": [
        {
          "room_id": "room-uuid-1",
          "room_number": "101",
          "room_name": "Consultation Room A",
          "room_type": "consultation",
          "score": 85.5,
          "is_preferred": true,
          "equipment": ["basic", "ecg"]
        },
        {
          "room_id": "room-uuid-2",
          "room_number": "102",
          "room_name": "Consultation Room B",
          "room_type": "consultation",
          "score": 72.0,
          "is_preferred": false,
          "equipment": ["basic"]
        }
      ],
      "recommended_room": {
        "room_id": "room-uuid-1",
        "room_number": "101",
        "score": 85.5
      }
    },
    {
      "start_time": "2025-10-15T10:30:00Z",
      "end_time": "2025-10-15T11:00:00Z",
      "available_rooms": [],
      "recommended_room": null
    }
  ],
  "total_slots": 20,
  "slots_with_availability": 18,
  "query_time_ms": 45
}
```

**Performance Metrics**:
- Target response time: < 100ms for 20 slots
- Uses database query optimization with CTEs
- Results cached for 5 minutes per clinic/doctor/service combination

### cURL Example

```bash
curl -X POST https://healthcare-clinic-backend.fly.dev/api/v1/rooms/availability/batch \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "clinic_id": "550e8400-e29b-41d4-a716-446655440000",
    "doctor_id": "770e8400-e29b-41d4-a716-446655440002",
    "service_id": "880e8400-e29b-41d4-a716-446655440003",
    "date_start": "2025-10-15T08:00:00Z",
    "date_end": "2025-10-15T18:00:00Z"
  }'
```

---

## 4. Room Display Configuration

**Endpoint**: `GET /api/v1/rooms/{room_id}/display-config`

**Description**: Get the display configuration for a room, including custom color settings.

### Request

**Headers**:
```http
Authorization: Bearer <token>
```

**URL Parameters**:
- `room_id` (string, uuid, required): ID of the room

### Response

**Success (200 OK)**:
```json
{
  "room_id": "room-uuid",
  "color_hex": "#add8e6",
  "color_source": "custom",
  "display_order": 1,
  "auto_generated_color": "#7eb5d6",
  "contrast_text_color": "#000000",
  "updated_at": "2025-10-15T09:00:00Z"
}
```

**Response Fields**:
- `color_hex` (string): Current color in hex format (custom or auto-generated)
- `color_source` (string): "custom" or "auto" - indicates if color is customized
- `display_order` (integer): Display order in lists
- `auto_generated_color` (string): Hash-based deterministic color for this room
- `contrast_text_color` (string): Recommended text color for WCAG AA compliance

**Success (200 OK)** - No custom config:
```json
{
  "room_id": "room-uuid",
  "color_hex": "#7eb5d6",
  "color_source": "auto",
  "display_order": null,
  "auto_generated_color": "#7eb5d6",
  "contrast_text_color": "#000000",
  "updated_at": null
}
```

**Error (404 Not Found)**:
```json
{
  "detail": {
    "error": "room_not_found",
    "message": "Room not found"
  }
}
```

### cURL Example

```bash
curl -X GET https://healthcare-clinic-backend.fly.dev/api/v1/rooms/550e8400-e29b-41d4-a716-446655440000/display-config \
  -H "Authorization: Bearer $TOKEN"
```

---

## 5. Update Room Display Configuration

**Endpoint**: `PUT /api/v1/rooms/{room_id}/display-config`

**Description**: Update the display configuration for a room (admin only).

### Request

**Headers**:
```http
Authorization: Bearer <token>
Content-Type: application/json
```

**URL Parameters**:
- `room_id` (string, uuid, required): ID of the room

**Body**:
```json
{
  "color_hex": "#add8e6",
  "display_order": 1
}
```

**Optional Fields**:
- `color_hex` (string, hex format): Custom color (null to reset to auto-generated)
- `display_order` (integer): Display order in lists

**Color Validation**:
- Must be valid hex color: `#RRGGBB`
- Case insensitive
- Must meet WCAG AA contrast ratio (4.5:1) - API will return recommended text color

### Response

**Success (200 OK)**:
```json
{
  "room_id": "room-uuid",
  "color_hex": "#add8e6",
  "color_source": "custom",
  "display_order": 1,
  "auto_generated_color": "#7eb5d6",
  "contrast_text_color": "#000000",
  "updated_at": "2025-10-15T10:00:00Z"
}
```

**Error (400 Bad Request)** - Invalid color:
```json
{
  "detail": {
    "error": "invalid_color",
    "message": "Color must be in hex format: #RRGGBB"
  }
}
```

**Error (403 Forbidden)** - Insufficient permissions:
```json
{
  "detail": {
    "error": "forbidden",
    "message": "Only admins can modify room display configuration"
  }
}
```

### Reset to Auto-Generated Color

To reset to auto-generated color, send `null` for `color_hex`:

```json
{
  "color_hex": null
}
```

### cURL Example

```bash
curl -X PUT https://healthcare-clinic-backend.fly.dev/api/v1/rooms/550e8400-e29b-41d4-a716-446655440000/display-config \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "color_hex": "#add8e6",
    "display_order": 1
  }'
```

---

## Error Codes

### HTTP Status Codes

| Code | Meaning | Description |
|------|---------|-------------|
| 200 | OK | Request successful |
| 201 | Created | Resource created successfully |
| 400 | Bad Request | Invalid request parameters or validation error |
| 401 | Unauthorized | Missing or invalid authentication token |
| 403 | Forbidden | Insufficient permissions |
| 404 | Not Found | Resource not found |
| 409 | Conflict | Resource conflict (e.g., room already booked) |
| 422 | Unprocessable Entity | Pydantic validation error |
| 429 | Too Many Requests | Rate limit exceeded |
| 500 | Internal Server Error | Server error (should not occur in normal operation) |

### Application Error Codes

| Code | Description | HTTP Status |
|------|-------------|-------------|
| `no_rooms_available` | No suitable rooms for time slot | 400 |
| `time_slot_conflict` | Doctor/room already booked | 409 |
| `invalid_room` | Room belongs to different clinic | 400 |
| `invalid_status` | Cannot modify cancelled/completed appointment | 400 |
| `room_not_found` | Room not found or unavailable | 404 |
| `room_conflict` | Room already occupied | 409 |
| `invalid_color` | Invalid hex color format | 400 |
| `forbidden` | Insufficient permissions | 403 |
| `not_found` | Generic resource not found | 404 |

---

## Rate Limiting

### Limits

- **Anonymous**: Not allowed
- **Authenticated Users**: 100 requests per minute
- **Admin Users**: 500 requests per minute

### Rate Limit Headers

Response includes rate limit information:

```http
X-RateLimit-Limit: 100
X-RateLimit-Remaining: 95
X-RateLimit-Reset: 1634308800
```

### Rate Limit Exceeded Response

```json
{
  "detail": {
    "error": "rate_limit_exceeded",
    "message": "Too many requests. Please try again in 60 seconds.",
    "retry_after": 60
  }
}
```

---

## Examples

### Complete Booking Flow with Room Assignment

```bash
# 1. Get available time slots with room availability
curl -X POST https://healthcare-clinic-backend.fly.dev/api/v1/rooms/availability/batch \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "clinic_id": "clinic-uuid",
    "doctor_id": "doctor-uuid",
    "service_id": "service-uuid",
    "date_start": "2025-10-15T08:00:00Z",
    "date_end": "2025-10-15T18:00:00Z"
  }'

# 2. Create appointment (room auto-assigned)
curl -X POST https://healthcare-clinic-backend.fly.dev/api/v1/appointments \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "clinic_id": "clinic-uuid",
    "patient_id": "patient-uuid",
    "doctor_id": "doctor-uuid",
    "service_id": "service-uuid",
    "start_time": "2025-10-15T10:00:00Z",
    "end_time": "2025-10-15T10:30:00Z"
  }'

# Response includes auto-assigned room
# {
#   "id": "appointment-uuid",
#   "room_id": "room-uuid",
#   "room": { "room_number": "101", ... },
#   "assignment_metadata": { "method": "auto", "score": 85.5 }
# }

# 3. Optionally override room if needed
curl -X PATCH https://healthcare-clinic-backend.fly.dev/api/v1/appointments/appointment-uuid/room \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "room_id": "different-room-uuid",
    "reason": "Patient requested ground floor room for accessibility"
  }'
```

### Customize Room Colors (Admin)

```bash
# 1. Get current color configuration
curl -X GET https://healthcare-clinic-backend.fly.dev/api/v1/rooms/room-uuid/display-config \
  -H "Authorization: Bearer $ADMIN_TOKEN"

# Response:
# {
#   "color_hex": "#7eb5d6",
#   "color_source": "auto",
#   "auto_generated_color": "#7eb5d6"
# }

# 2. Set custom color
curl -X PUT https://healthcare-clinic-backend.fly.dev/api/v1/rooms/room-uuid/display-config \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "color_hex": "#add8e6",
    "display_order": 1
  }'

# 3. Reset to auto-generated color
curl -X PUT https://healthcare-clinic-backend.fly.dev/api/v1/rooms/room-uuid/display-config \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "color_hex": null
  }'
```

---

## TypeScript SDK Example

For frontend integration, use the TypeScript SDK:

```typescript
import { AppointmentsAPI, RoomsAPI } from '@healthcare/api-client';

// Initialize clients
const appointmentsAPI = new AppointmentsAPI(config);
const roomsAPI = new RoomsAPI(config);

// Create appointment with auto room assignment
const appointment = await appointmentsAPI.createAppointment({
  clinic_id: 'clinic-uuid',
  patient_id: 'patient-uuid',
  doctor_id: 'doctor-uuid',
  service_id: 'service-uuid',
  start_time: '2025-10-15T10:00:00Z',
  end_time: '2025-10-15T10:30:00Z',
});

console.log(`Room assigned: ${appointment.room.room_number}`);
console.log(`Assignment score: ${appointment.assignment_metadata.score}`);

// Override room if needed
const updated = await appointmentsAPI.overrideRoom(appointment.id, {
  room_id: 'new-room-uuid',
  reason: 'Patient requested different room',
});

// Get room availability for calendar view
const availability = await roomsAPI.getBatchAvailability({
  clinic_id: 'clinic-uuid',
  doctor_id: 'doctor-uuid',
  date_start: '2025-10-15T08:00:00Z',
  date_end: '2025-10-15T18:00:00Z',
});

availability.slots.forEach(slot => {
  console.log(`${slot.start_time}: ${slot.available_rooms.length} rooms available`);
});
```

---

## Webhook Events

Room assignment triggers the following webhook events (if webhooks configured):

### appointment.room_assigned

```json
{
  "event": "appointment.room_assigned",
  "timestamp": "2025-10-15T10:00:00Z",
  "data": {
    "appointment_id": "appointment-uuid",
    "room_id": "room-uuid",
    "room_number": "101",
    "assignment_method": "auto",
    "score": 85.5
  }
}
```

### appointment.room_overridden

```json
{
  "event": "appointment.room_overridden",
  "timestamp": "2025-10-15T10:05:00Z",
  "data": {
    "appointment_id": "appointment-uuid",
    "previous_room_id": "old-room-uuid",
    "new_room_id": "new-room-uuid",
    "reason": "Patient requested ground floor room",
    "overridden_by": "user-uuid"
  }
}
```

---

## Related Documentation

- **User Guide**: `.claude/epics/room-assignment/USER_GUIDE.md`
- **Admin Guide**: `.claude/epics/room-assignment/ADMIN_GUIDE.md`
- **Rules Engine**: `apps/healthcare-backend/docs/ROOM_RULES.md`
- **UAT Test Plan**: `.claude/epics/room-assignment/UAT_TEST_PLAN.md`

---

**Version**: 1.0
**Last Updated**: 2025-10-13
**Maintainer**: Backend Team
**Support**: backend-support@example.com
