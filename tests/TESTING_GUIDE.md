# Testing Guide for Room Assignment Feature

Quick reference for running integration tests and load tests for Issue #34.

## Prerequisites

```bash
# Install test dependencies
cd apps/healthcare-backend
pip install -r tests/requirements-test.txt

# Ensure migrations are applied
python3 apply_migration.py ../../infra/db/migrations/add_room_display_config.sql
python3 apply_migration.py ../../infra/db/migrations/add_room_performance_indexes.sql
```

## Integration Tests

### Run All Integration Tests

```bash
pytest tests/integration/ -v -s
```

### Run Specific Test Class

```bash
# Complete booking flow
pytest tests/integration/test_room_assignment_integration.py::TestCompleteBookingFlow -v

# Room override
pytest tests/integration/test_room_assignment_integration.py::TestRoomOverrideFlow -v

# Conflict detection
pytest tests/integration/test_room_assignment_integration.py::TestConflictDetection -v
```

### Run with Coverage

```bash
pytest tests/integration/ \
  --cov=app/services/unified_appointment_service \
  --cov=app/api/appointments_api \
  --cov-report=html \
  --cov-report=term
```

### View Coverage Report

```bash
open htmlcov/index.html  # macOS
```

## Load Tests

### 1. Setup Test Data

```bash
python3 tests/load/setup_test_data.py
source tests/load/.env.loadtest
```

### 2. Run Load Tests

#### Interactive Mode (with Web UI)

```bash
locust -f tests/load/locustfile.py --host=http://localhost:8000
```

Then open browser to `http://localhost:8089`

#### Headless Mode (Automated)

```bash
# All scenarios combined
locust -f tests/load/locustfile.py --host=http://localhost:8000 \
       --users=50 --spawn-rate=10 --headless --run-time=2m \
       --html=load_report_all.html

# Scenario 1: Concurrent Booking Storm
locust -f tests/load/locustfile.py --host=http://localhost:8000 \
       --users=50 --spawn-rate=10 --headless --run-time=2m \
       --html=load_report_concurrent.html --tags=concurrent

# Scenario 2: Conflict Handling
locust -f tests/load/locustfile.py --host=http://localhost:8000 \
       --users=50 --spawn-rate=10 --headless --run-time=2m \
       --html=load_report_conflict.html --tags=conflict

# Scenario 3: Rules Engine
locust -f tests/load/locustfile.py --host=http://localhost:8000 \
       --users=50 --spawn-rate=10 --headless --run-time=2m \
       --html=load_report_rules.html --tags=rules
```

### 3. Cleanup Test Data

```bash
python3 tests/load/cleanup_test_data.py
```

## Unit Tests (from Stream B)

### Run Room Assignment Unit Tests

```bash
# All room assignment tests
pytest tests/test_*room*.py -v

# Specific files
pytest tests/test_unified_appointment_room_assignment.py -v
pytest tests/test_room_assignment_edge_cases.py -v
pytest tests/test_room_override_api_validation.py -v
pytest tests/test_rules_engine_room_assignment.py -v
```

## Performance Validation

### Check Database Indexes

```sql
-- Verify indexes exist
SELECT indexname, indexdef
FROM pg_indexes
WHERE schemaname = 'healthcare'
  AND (indexname LIKE 'idx_appointments_room%' OR indexname LIKE 'idx_rooms_clinic%');

-- Monitor index usage during tests
SELECT schemaname, tablename, indexname, idx_scan
FROM pg_stat_user_indexes
WHERE schemaname = 'healthcare'
  AND (indexname LIKE 'idx_appointments_room%' OR indexname LIKE 'idx_rooms_clinic%')
ORDER BY idx_scan DESC;
```

### Query Performance Analysis

```sql
-- Room availability query
EXPLAIN ANALYZE
SELECT * FROM appointments
WHERE room_id = 'test-room-id'
  AND start_time <= '2025-10-20 10:30:00'
  AND end_time >= '2025-10-20 10:00:00'
  AND status NOT IN ('cancelled', 'no_show');

-- Available rooms query
EXPLAIN ANALYZE
SELECT * FROM rooms
WHERE clinic_id = 'test-clinic-id'
  AND is_available = true;
```

## Continuous Integration

### GitHub Actions Example

```yaml
name: Room Assignment Tests

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest

    steps:
      - uses: actions/checkout@v2

      - name: Set up Python
        uses: actions/setup-python@v2
        with:
          python-version: '3.12'

      - name: Install dependencies
        run: |
          pip install -r apps/healthcare-backend/tests/requirements-test.txt

      - name: Run unit tests
        run: |
          cd apps/healthcare-backend
          pytest tests/test_*room*.py -v --cov --cov-report=xml

      - name: Run integration tests
        env:
          SUPABASE_URL: ${{ secrets.SUPABASE_URL }}
          SUPABASE_SERVICE_ROLE_KEY: ${{ secrets.SUPABASE_SERVICE_KEY }}
        run: |
          cd apps/healthcare-backend
          pytest tests/integration/ -v

      - name: Upload coverage
        uses: codecov/codecov-action@v2
        with:
          file: ./coverage.xml
```

## Troubleshooting

### Integration Tests Fail

1. **Database Connection Error**
   ```bash
   # Check environment variables
   echo $SUPABASE_URL
   echo $SUPABASE_SERVICE_ROLE_KEY

   # Test database connection
   python3 -c "from app.db.supabase_client import get_supabase_client; print(get_supabase_client())"
   ```

2. **Fixture Errors**
   ```bash
   # Clear pytest cache
   pytest --cache-clear

   # Run with verbose output
   pytest tests/integration/ -v -s --tb=short
   ```

### Load Tests Fail

1. **Test Data Not Found**
   ```bash
   # Verify environment variables
   env | grep TEST_

   # Re-run setup
   python3 tests/load/setup_test_data.py
   source tests/load/.env.loadtest
   ```

2. **Connection Refused**
   ```bash
   # Check backend is running
   curl http://localhost:8000/health

   # Start backend
   cd apps/healthcare-backend
   uvicorn app.main:app --reload --port 8000
   ```

3. **High Failure Rate**
   ```bash
   # Reduce load
   locust -f tests/load/locustfile.py --host=http://localhost:8000 \
          --users=10 --spawn-rate=2

   # Check backend logs
   tail -f logs/app.log
   ```

## Test Reports

### Generate HTML Reports

```bash
# Integration test coverage
pytest tests/integration/ --cov --cov-report=html

# Load test report (generated automatically)
open load_report_all.html
```

### Performance Report

Fill in actual metrics in:
```
tests/load/PERFORMANCE_REPORT.md
```

## Quick Commands

```bash
# Full test suite
pytest tests/ -v --cov

# Integration only
pytest tests/integration/ -v

# Load test (quick)
locust -f tests/load/locustfile.py --host=http://localhost:8000 \
       --users=10 --spawn-rate=5 --headless --run-time=30s

# Cleanup everything
python3 tests/load/cleanup_test_data.py
```

## Resources

- Integration Tests: `tests/integration/test_room_assignment_integration.py`
- Load Tests: `tests/load/locustfile.py`
- Load Test Documentation: `tests/load/README.md`
- Performance Report Template: `tests/load/PERFORMANCE_REPORT.md`
- Issue #34: Database Migrations, Testing & Performance
- Issue #35: Smart Room Assignment Rules Engine

---

**Need Help?**
- Review test documentation in each test file
- Check `tests/load/README.md` for detailed load testing guide
- See `.claude/epics/room-assignment/updates/34/` for implementation details
