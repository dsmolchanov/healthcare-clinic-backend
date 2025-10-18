# FSM Tests README

## Test Dependencies

Ensure all test dependencies are installed:

```bash
pip install -r tests/requirements-test.txt
```

### Key Dependencies for SlotManager Tests
- `pytest` - Test framework
- `pytest-asyncio` - Async test support
- `pytest-cov` - Coverage reporting
- `freezegun` - Time mocking for boundary tests
- `pytz` - Timezone handling (install if not auto-installed: `pip install pytz`)

## Running SlotManager Tests

### Run all FSM tests
```bash
cd apps/healthcare-backend
pytest tests/fsm/ -v
```

### Run SlotManager unit tests only
```bash
pytest tests/fsm/test_slot_manager.py -v
```

### Run timezone validation integration tests only
```bash
pytest tests/fsm/test_timezone_validation.py -v
```

### Run with coverage
```bash
pytest tests/fsm/test_slot_manager.py tests/fsm/test_timezone_validation.py \
  --cov=app.fsm.slot_manager --cov-report=term-missing
```

**Target Coverage**: >90% for `app/fsm/slot_manager.py`

## Test Structure

### Unit Tests: `test_slot_manager.py` (40+ tests)
Tests slot management, doctor name extraction, and date parsing with mocked database.

Key test categories:
- **Slot Management**: add_slot, confirm_slot, check_slots_stale, has_required_slots
- **Doctor Name Extraction**: 20+ regex examples ensuring zero false captures of "доктор"
- **Date Parsing**: Relative dates, DD.MM formats, leap years, month boundaries
- **Validation**: Timezone-aware date validation, doctor name validation

### Integration Tests: `test_timezone_validation.py` (30+ tests)
Tests end-to-end flows across multiple timezones with database integration.

Key test categories:
- **Multi-Timezone**: Validation across Moscow, NYC, Tokyo, London, UTC
- **Midnight Boundaries**: Local vs. UTC time edge cases
- **DST Transitions**: Spring-forward handling
- **Database Integration**: Timezone and doctor lookup from Supabase
- **Future Limits**: 90-day ahead booking restrictions

## Critical Test Scenarios

### 1. Doctor Name Extraction (Zero False Positives)
The regex must NEVER capture the word "доктор" itself, only the actual doctor name.

**Test**: `test_extract_doctor_name_no_false_captures`
- "нужен доктор" → `None` (not "доктор")
- "к доктору Иванову" → "Иванову" (not "доктору")

### 2. Slot Staleness Boundary (Exactly 300 Seconds)
Slots are stale only if age > 300 seconds, not >= 300.

**Test**: `test_check_slots_stale_boundary_exactly_300_seconds`
- 300.0s → NOT stale
- 300.1s → stale

### 3. Timezone-Aware Validation
Date validation must use clinic timezone, not server timezone.

**Test**: `test_validate_date_across_timezones`
- Past in UTC might be future in Tokyo (and vice versa)

### 4. 90-Day Future Limit Boundary
Appointments can be booked up to and including 90 days ahead.

**Test**: `test_accept_dates_exactly_90_days`
- Day 90 → valid
- Day 91 → invalid

## Notes for Stream A

When `slot_manager.py` is implemented:

1. **Import Path**: Tests expect `from app.fsm.slot_manager import SlotManager`
2. **Async Methods**: `get_clinic_timezone`, `validate_date_slot`, `validate_doctor_name` must be async
3. **Supabase Client**: `SlotManager.__init__()` should set `self.supabase`
4. **Method Signatures**: Must match those in tests (see test docstrings)

## Troubleshooting

### Import Error: `ModuleNotFoundError: No module named 'app.fsm.slot_manager'`
**Cause**: Stream A hasn't created `slot_manager.py` yet.
**Solution**: Wait for Stream A to complete or create stub implementation.

### Import Error: `No module named 'pytz'`
**Cause**: pytz not installed.
**Solution**: `pip install pytz`

### Test Failures: Timezone-related
**Cause**: Tests use `datetime.now()` which is timezone-dependent.
**Solution**: Verify `freezegun` is installed and working. Some tests freeze time for determinism.

### Coverage < 90%
**Cause**: Some branches or methods not tested.
**Solution**: Review coverage report (`--cov-report=html`) and add tests for uncovered lines.

## Coordination with Stream A

Stream B (this test suite) was developed in parallel with Stream A (implementation).
Tests are written from specification and define expected behavior.

Once Stream A completes:
1. Run all tests: `pytest tests/fsm/test_slot_manager.py tests/fsm/test_timezone_validation.py -v`
2. Check coverage: `pytest --cov=app.fsm.slot_manager --cov-report=term-missing`
3. Fix any failures (implementation vs. spec discrepancies)
4. Verify >90% coverage achieved
