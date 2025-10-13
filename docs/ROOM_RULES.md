# Room-Specific Rule Types

This document describes the room-specific rule types available in the RuleEvaluator service for intelligent room assignment during appointment scheduling.

## Overview

The RuleEvaluator now supports 4 room-specific rule types that enable automated room assignment based on various constraints and preferences:

- **2 Hard Constraints**: Must be satisfied for a room to be valid
- **2 Soft Preferences**: Used for scoring and ranking available rooms

## Hard Constraints

### 1. room_type_match

Validates that the room type matches service requirements.

**Use Case**: Ensure surgeries only happen in procedure/operating rooms, not consultation rooms.

**Configuration**:
```json
{
  "id": "surgery-requires-procedure-room",
  "name": "Surgeries require procedure rooms",
  "priority": 1,
  "rule_type": "hard_constraint",
  "conditions": {
    "type": "room_type_match",
    "required_room_types": ["procedure", "operating"]
  },
  "actions": {
    "block_assignment": true,
    "message": "This service requires a procedure room"
  }
}
```

**Parameters**:
- `required_room_types` (array): List of acceptable room types
- Room types should match values in `healthcare.rooms.room_type` column

**Behavior**:
- ✅ **Pass**: Room type is in the `required_room_types` list
- ❌ **Fail**: Room type is not in the list → Room cannot be used

**Example**: Service "Knee Surgery" requires room types ["procedure", "operating"]. Room 201 (type: "consultation") fails. Room 202 (type: "procedure") passes.

---

### 2. cleaning_buffer

Enforces cleaning time buffer between appointments in the same room.

**Use Case**: Ensure rooms have adequate time for cleaning/sanitization between patients.

**Configuration**:
```json
{
  "id": "room-cleaning-buffer",
  "name": "15 minute room cleaning buffer",
  "priority": 1,
  "rule_type": "hard_constraint",
  "conditions": {
    "type": "cleaning_buffer",
    "buffer_minutes": 15
  },
  "actions": {
    "block_assignment": true,
    "message": "Room needs {buffer_minutes} minutes for cleaning"
  }
}
```

**Parameters**:
- `buffer_minutes` (integer, optional): Required buffer time in minutes. If not specified, uses room's `cleaning_duration_minutes` from database.

**Behavior**:
- ✅ **Pass**: No appointments within `buffer_minutes` before/after this slot
- ❌ **Fail**: Another appointment exists too close → Room cannot be used

**Example**: Room 201 needs 15 min cleaning. Appointment A ends at 2:00pm. Next slot at 2:10pm fails (only 10 min gap). Slot at 2:15pm passes.

**Database Field**: Reads `healthcare.rooms.cleaning_duration_minutes` (default: 15)

---

## Soft Preferences

### 3. doctor_room_preference

Scores rooms higher if the doctor prefers them.

**Use Case**: Doctors may prefer specific rooms due to equipment familiarity, location, or workflow efficiency.

**Configuration**:
```json
{
  "id": "doctor-preferred-rooms",
  "name": "Dr. Smith's preferred rooms",
  "priority": 2,
  "rule_type": "soft_preference",
  "conditions": {
    "type": "doctor_room_preference",
    "preferred_rooms": ["room-201-id", "room-203-id"],
    "score_modifier": 20,
    "penalty": 5
  }
}
```

**Parameters**:
- `preferred_rooms` (array): List of room IDs the doctor prefers
- `score_modifier` (integer, default: 10): Bonus points if room is preferred
- `penalty` (integer, default: 5): Penalty points if room is not preferred

**Behavior**:
- ✅ **Preferred room**: Returns True, adds `score_modifier` points
- ❌ **Not preferred**: Returns False, subtracts `penalty` points

**Example**: Dr. Smith prefers Rooms 201 and 203. When booking:
- Room 201: +20 points (preferred)
- Room 202: -5 points (not preferred)
- Room 203: +20 points (preferred)

**Note**: This is optional. If `preferred_rooms` is empty, no penalty is applied.

---

### 4. utilization_balancing

Prefers less-utilized rooms for load balancing across the clinic.

**Use Case**: Distribute appointments evenly across rooms to avoid overloading specific rooms and balance wear/tear.

**Configuration**:
```json
{
  "id": "balance-room-utilization",
  "name": "Balance room utilization",
  "priority": 3,
  "rule_type": "soft_preference",
  "conditions": {
    "type": "utilization_balancing",
    "max_daily_appointments": 20,
    "underutilized_bonus": 10,
    "overutilized_penalty": 10
  }
}
```

**Parameters**:
- `max_daily_appointments` (integer, required): Expected max appointments per room per day
- `underutilized_bonus` (integer, default: 10): Bonus for rooms with <50% utilization
- `overutilized_penalty` (integer, default: 10): Penalty for rooms with >80% utilization

**Behavior**:
- **< 50% utilization**: Returns True, adds `underutilized_bonus` points
- **50-80% utilization**: Returns True, neutral score (normal utilization)
- **> 80% utilization**: Returns False, subtracts `overutilized_penalty` points

**Example**: Max = 20 appointments/day
- Room 201 (5 appointments, 25%): +10 points (underutilized)
- Room 202 (12 appointments, 60%): 0 points (normal)
- Room 203 (18 appointments, 90%): -10 points (overutilized)

**Calculation**: `utilization_ratio = current_appointments / max_daily_appointments`

---

## Implementation Details

### Helper Methods

The RuleEvaluator includes 4 new helper methods for room data:

1. **`_get_room_type(room_id: str) -> str`**
   - Queries `healthcare.rooms.room_type`
   - Returns empty string on error

2. **`_get_room_cleaning_duration(room_id: str) -> int`**
   - Queries `healthcare.rooms.cleaning_duration_minutes`
   - Default: 15 minutes

3. **`_check_room_buffer_time(room_id, start_time, end_time, buffer_minutes) -> bool`**
   - Checks room's appointment schedule for conflicts
   - Similar to `_check_buffer_time` but for rooms instead of doctors

4. **`_get_room_appointment_count(room_id: str, date: datetime.date) -> int`**
   - Counts confirmed appointments for room on given date
   - Returns 0 on error

### Database Schema

Required columns in `healthcare.rooms` table:
```sql
CREATE TABLE healthcare.rooms (
  id UUID PRIMARY KEY,
  clinic_id UUID NOT NULL,
  room_number VARCHAR(20),
  room_name VARCHAR(100),
  room_type VARCHAR(50),  -- Used by room_type_match
  equipment JSONB,
  capacity INT,
  is_available BOOLEAN,
  cleaning_duration_minutes INT DEFAULT 15,  -- Used by cleaning_buffer
  created_at TIMESTAMPTZ,
  updated_at TIMESTAMPTZ
);
```

### Error Handling

All helper methods include try/except blocks with sensible defaults:
- Database errors log via `logger.error()` but don't crash
- Missing data returns safe defaults (empty string, 0, True)
- Network timeouts fail gracefully

---

## Usage Examples

### Example 1: Configure Rules for Surgery Center

```json
[
  {
    "id": "rule-1",
    "name": "Surgeries need operating rooms",
    "priority": 1,
    "rule_type": "hard_constraint",
    "conditions": {
      "type": "room_type_match",
      "required_room_types": ["operating", "procedure"]
    }
  },
  {
    "id": "rule-2",
    "name": "30 minute deep cleaning for procedures",
    "priority": 1,
    "rule_type": "hard_constraint",
    "conditions": {
      "type": "cleaning_buffer",
      "buffer_minutes": 30
    }
  },
  {
    "id": "rule-3",
    "name": "Prefer underutilized rooms",
    "priority": 2,
    "rule_type": "soft_preference",
    "conditions": {
      "type": "utilization_balancing",
      "max_daily_appointments": 15,
      "underutilized_bonus": 15
    }
  }
]
```

### Example 2: General Practice Clinic

```json
[
  {
    "id": "rule-1",
    "name": "Consultations in consultation rooms",
    "priority": 1,
    "rule_type": "hard_constraint",
    "conditions": {
      "type": "room_type_match",
      "required_room_types": ["consultation", "examination"]
    }
  },
  {
    "id": "rule-2",
    "name": "Standard 15 min cleaning",
    "priority": 1,
    "rule_type": "hard_constraint",
    "conditions": {
      "type": "cleaning_buffer"
      // Uses room's default cleaning_duration_minutes
    }
  },
  {
    "id": "rule-3",
    "name": "Dr. Johnson's preferred rooms",
    "priority": 2,
    "rule_type": "soft_preference",
    "conditions": {
      "type": "doctor_room_preference",
      "preferred_rooms": ["room-uuid-1", "room-uuid-2"],
      "score_modifier": 25
    }
  }
]
```

---

## Testing

Unit tests are available in `tests/services/test_rule_evaluator_rooms.py` with 26 comprehensive tests covering:
- Success and failure scenarios for each rule type
- Edge cases (empty lists, zero values)
- Error handling
- Helper method functionality

Run tests:
```bash
pytest tests/services/test_rule_evaluator_rooms.py -v
```

---

## Migration Notes

For existing clinics migrating to room-based scheduling:

1. **Populate room data**: Ensure `healthcare.rooms` table has complete data
2. **Set cleaning times**: Configure `cleaning_duration_minutes` per room
3. **Define room types**: Standardize room type values across clinic
4. **Create rules**: Start with hard constraints, add preferences gradually
5. **Test thoroughly**: Use staging environment to validate rule behavior

---

## Related

- **PRD**: `.claude/prds/room-assignment.md`
- **Epic**: `.claude/epics/room-assignment/epic.md`
- **Issue**: [#29 - Extend Rules Engine for Room Types](https://github.com/dsmolchanov/livekit-voice-agent/issues/29)
- **Implementation**: `apps/healthcare-backend/app/services/rule_evaluator.py` (lines 401-555)
- **Tests**: `tests/services/test_rule_evaluator_rooms.py`

---

**Last Updated**: 2025-10-13
**Version**: 1.0
**Author**: Issue #29 Implementation
