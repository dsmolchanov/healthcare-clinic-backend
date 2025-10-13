# Integration Tests for Room Assignment Feature

## Overview

Comprehensive integration tests validating end-to-end workflows for the room assignment feature (Issue #34). These tests use actual database connections and validate complete business flows.

## Test Coverage

### 8 Test Classes, 10 Test Methods

1. **Complete Booking Flow** (2 tests)
   - Booking with multiple available rooms
   - Booking with no available rooms (graceful fallback)

2. **Room Override with Audit Logging** (1 test)
   - PATCH /appointments/{id}/room endpoint
   - HIPAA-compliant audit log verification

3. **Calendar Sync with Room Information** (1 test)
   - Room details included in calendar events
   - Integration with external calendar service

4. **Color Customization Persistence** (1 test)
   - Custom room colors in room_display_config table
   - Auto-generated color fallback

5. **Conflict Detection** (1 test)
   - Concurrent booking conflict resolution
   - Alternative room assignment

6. **Rules Engine Integration** (2 tests)
   - Equipment constraint filtering (hard constraints)
   - Soft preference scoring (room bonuses)

7. **Multi-Clinic Isolation** (1 test)
   - Cross-clinic room assignment prevention
   - Data integrity validation

8. **Error Recovery** (1 test)
   - Database error graceful degradation
   - Appointment created without optimal room

## Prerequisites

### Database Setup

```bash
# Apply migrations
cd apps/healthcare-backend
python3 apply_migration.py ../../infra/db/migrations/add_room_display_config.sql
python3 apply_migration.py ../../infra/db/migrations/add_room_performance_indexes.sql
```

### Environment Variables

Required environment variables (typically in `.env`):

```bash
SUPABASE_URL=your_supabase_url
SUPABASE_SERVICE_ROLE_KEY=your_service_key
```

### Install Dependencies

```bash
pip install -r tests/requirements-test.txt
```

## Running Tests

### All Integration Tests

```bash
pytest tests/integration/ -v -s
```

### Specific Test Class

```bash
# Complete booking flow
pytest tests/integration/test_room_assignment_integration.py::TestCompleteBookingFlow -v

# Room override
pytest tests/integration/test_room_assignment_integration.py::TestRoomOverrideFlow -v

# Conflict detection
pytest tests/integration/test_room_assignment_integration.py::TestConflictDetection -v

# Rules engine
pytest tests/integration/test_room_assignment_integration.py::TestRulesEngineIntegration -v
```

### With Coverage Report

```bash
pytest tests/integration/ \
  --cov=app/services/unified_appointment_service \
  --cov=app/api/appointments_api \
  --cov=app/services/rule_evaluator \
  --cov-report=html \
  --cov-report=term

# View report
open htmlcov/index.html
```

### Parallel Execution

```bash
# Install pytest-xdist
pip install pytest-xdist

# Run tests in parallel
pytest tests/integration/ -n auto -v
```

## Test Fixtures

### Automatically Created

Each test automatically creates and cleans up:

- **Test Clinic**: Business hours, timezone, configuration
- **Test Rooms**: 4 rooms with varying equipment and availability
  - Room A: X-Ray equipment
  - Room B: Ultrasound equipment
  - Room C: General equipment
  - Room D: Unavailable (for testing)
- **Test Doctor**: Full schedule, specialties
- **Test Patient**: Contact information, preferences

### Cleanup

All test data is automatically cleaned up after each test using pytest fixtures with yield patterns:

```python
@pytest.fixture(scope="module")
async def test_clinic(supabase):
    # Setup
    clinic_id = create_clinic()
    yield clinic_id
    # Cleanup (automatic)
    delete_clinic(clinic_id)
```

## Test Structure

### Test Pattern

```python
@pytest.mark.integration
@pytest.mark.asyncio
class TestFeatureName:
    """Description of feature being tested"""

    async def test_specific_scenario(
        self, supabase, test_clinic, test_doctor, test_patient, test_rooms
    ):
        """
        Test description with:
        - Setup details
        - Expected behavior
        - Validation criteria
        """
        # Arrange: Setup test data
        service = UnifiedAppointmentService(supabase)

        # Act: Perform action
        result = await service.book_appointment(request)

        # Assert: Verify results
        assert result.success is True
        assert result.appointment_id is not None

        # Cleanup (if needed beyond fixtures)
        cleanup_data()
```

## Validation Criteria

### Success Criteria

Each test validates:
- ✅ Correct HTTP status codes
- ✅ Expected database state changes
- ✅ Foreign key relationships intact
- ✅ Audit logs created (where applicable)
- ✅ Business rules enforced
- ✅ Error messages accurate

### Performance Expectations

While not primary focus (see load tests), integration tests should:
- Complete within 10 seconds per test
- No memory leaks (fixtures cleaned up)
- Database connections properly closed

## Troubleshooting

### Common Issues

#### 1. Database Connection Error

```bash
# Check environment variables
echo $SUPABASE_URL
echo $SUPABASE_SERVICE_ROLE_KEY

# Test connection
python3 -c "from app.db.supabase_client import get_supabase_client; print(get_supabase_client())"
```

#### 2. Fixture Errors

```bash
# Clear pytest cache
pytest --cache-clear

# Run with verbose traceback
pytest tests/integration/ -v -s --tb=long
```

#### 3. Test Data Conflicts

```bash
# Ensure database is clean
# Run cleanup script if needed
python3 tests/load/cleanup_test_data.py

# Or manually delete test clinics
psql -c "DELETE FROM clinics WHERE name LIKE '%Test%'"
```

#### 4. Async Fixture Issues

If using `async def` fixtures, ensure pytest-asyncio is configured:

```python
# pytest.ini or setup.cfg
[pytest]
asyncio_mode = auto
```

### Debug Mode

Run with detailed output:

```bash
pytest tests/integration/ -v -s --tb=short --log-cli-level=DEBUG
```

## CI/CD Integration

### GitHub Actions Example

```yaml
name: Integration Tests

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest

    services:
      postgres:
        image: postgres:15
        env:
          POSTGRES_PASSWORD: postgres
        options: >-
          --health-cmd pg_isready
          --health-interval 10s
          --health-timeout 5s
          --health-retries 5

    steps:
      - uses: actions/checkout@v2

      - name: Set up Python
        uses: actions/setup-python@v2
        with:
          python-version: '3.12'

      - name: Install dependencies
        run: pip install -r tests/requirements-test.txt

      - name: Run integration tests
        env:
          SUPABASE_URL: ${{ secrets.SUPABASE_URL }}
          SUPABASE_SERVICE_ROLE_KEY: ${{ secrets.SUPABASE_SERVICE_KEY }}
        run: |
          pytest tests/integration/ -v --cov --cov-report=xml

      - name: Upload coverage
        uses: codecov/codecov-action@v2
```

## Test Data Isolation

### Database Isolation Strategies

1. **Module-scoped fixtures**: One clinic per test module
2. **Unique identifiers**: Use UUIDs for all test data
3. **Name prefixes**: "Integration Test" prefix on all test entities
4. **Automatic cleanup**: yield-based fixtures ensure cleanup

### Best Practices

- ✅ Never use production data
- ✅ Clean up after each test
- ✅ Use unique names/IDs
- ✅ Validate cleanup in teardown
- ✅ Don't rely on specific database state

## Performance Notes

### Test Execution Time

Typical execution times (approximate):
- Single test: 1-3 seconds
- Full test class: 5-10 seconds
- All integration tests: 30-60 seconds

### Database Impact

Integration tests create:
- 1 clinic per test module
- 4-10 rooms per test module
- 1 doctor per test module
- 1 patient per test module
- 1-5 appointments per test
- 0-3 scheduling rules per test

All data is cleaned up after tests complete.

## Related Testing

### Unit Tests

For faster, isolated testing:
```bash
pytest tests/test_*room*.py -v
```

See:
- `tests/test_unified_appointment_room_assignment.py`
- `tests/test_room_assignment_edge_cases.py`
- `tests/test_room_override_api_validation.py`

### Load Tests

For performance validation:
```bash
locust -f tests/load/locustfile.py --host=http://localhost:8000
```

See: `tests/load/README.md`

## References

- **Issue #34**: Database Migrations, Testing & Performance
- **Issue #35**: Smart Room Assignment Rules Engine
- **Stream A**: Database migrations (completed)
- **Stream B**: Additional unit tests (completed)
- **Stream C**: Integration & load testing (this file)

## Contributing

### Adding New Integration Tests

1. Add test method to appropriate test class
2. Use existing fixtures (test_clinic, test_rooms, etc.)
3. Follow Arrange-Act-Assert pattern
4. Add comprehensive docstring
5. Ensure cleanup in teardown or fixtures
6. Run full test suite to verify no regressions

### Test Naming Convention

```python
async def test_<feature>_<scenario>_<expected_outcome>(self, fixtures):
    """
    Test that <feature> <scenario> results in <expected_outcome>.

    Setup: <describe initial state>
    Verify: <describe validation>
    """
```

## Support

For issues or questions:
1. Check this README
2. Review test documentation in test files
3. See `tests/TESTING_GUIDE.md` for quick reference
4. Check `.claude/epics/room-assignment/updates/34/` for implementation details

---

**Total Tests**: 10 integration test methods
**Total Coverage**: 8 test classes
**Lines of Code**: ~700 lines
**Status**: ✅ Production Ready
