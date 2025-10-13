# Load Testing for Room Assignment Feature

## Overview

Load tests for validating room assignment performance under concurrent load, measuring:
- Room assignment latency (target: <100ms p95)
- Conflict detection accuracy and speed
- Rules engine evaluation performance
- Database deadlock prevention
- Race condition handling

## Prerequisites

### Required Tools

```bash
# Install locust for load testing
pip install locust

# Optional: k6 for additional load testing
brew install k6  # macOS
```

### Test Environment Setup

1. **Database Preparation**:
   ```bash
   # Apply migrations
   cd apps/healthcare-backend
   python3 apply_migration.py ../../infra/db/migrations/add_room_display_config.sql
   python3 apply_migration.py ../../infra/db/migrations/add_room_performance_indexes.sql
   ```

2. **Create Test Data**:
   ```bash
   # Run setup script to create test clinic, doctors, patients, rooms
   python3 tests/load/setup_test_data.py
   ```

3. **Set Environment Variables**:
   ```bash
   export TEST_CLINIC_ID="uuid-from-setup"
   export TEST_DOCTOR_ID="uuid-from-setup"
   export TEST_PATIENT_ID="uuid-from-setup"
   export TEST_ROOM_1_ID="uuid-from-setup"
   export TEST_ROOM_2_ID="uuid-from-setup"
   export TEST_ROOM_3_ID="uuid-from-setup"
   ```

## Running Load Tests

### Scenario 1: Concurrent Booking Storm

Test 50 simultaneous appointment bookings with room auto-assignment.

```bash
# Run with Web UI
locust -f tests/load/locustfile.py --host=http://localhost:8000 \
       --users=50 --spawn-rate=10 --tags=concurrent

# Headless mode with HTML report
locust -f tests/load/locustfile.py --host=http://localhost:8000 \
       --users=50 --spawn-rate=10 --headless --run-time=2m \
       --html=load_report_concurrent.html --tags=concurrent
```

**Success Criteria**:
- ✓ All 50 bookings succeed without errors
- ✓ p95 latency <100ms for room assignment
- ✓ No database deadlocks
- ✓ No duplicate room assignments (race conditions)

### Scenario 2: Room Conflict Handling

Test conflict detection with limited rooms (10 rooms, 50 booking attempts).

```bash
locust -f tests/load/locustfile.py --host=http://localhost:8000 \
       --users=50 --spawn-rate=10 --headless --run-time=2m \
       --html=load_report_conflict.html --tags=conflict
```

**Success Criteria**:
- ✓ No double-bookings (same room, overlapping times)
- ✓ Conflict detection <50ms
- ✓ Alternative rooms suggested correctly
- ✓ Graceful fallback when all rooms occupied

### Scenario 3: Rules Engine Performance

Test complex rule sets (10+ hard constraints, 20+ soft preferences).

```bash
locust -f tests/load/locustfile.py --host=http://localhost:8000 \
       --users=50 --spawn-rate=10 --headless --run-time=2m \
       --html=load_report_rules.html --tags=rules
```

**Success Criteria**:
- ✓ Rule evaluation <50ms per slot
- ✓ Hard constraints properly filter rooms
- ✓ Soft preferences influence scoring
- ✓ Policy cache reduces database queries

### All Scenarios Combined

Run all test scenarios simultaneously:

```bash
locust -f tests/load/locustfile.py --host=http://localhost:8000 \
       --users=50 --spawn-rate=10 --headless --run-time=5m \
       --html=load_report_full.html
```

## Analyzing Results

### Locust Web UI

1. Open browser to `http://localhost:8089` (when running with UI)
2. Configure users and spawn rate
3. Monitor real-time metrics:
   - Requests per second (RPS)
   - Response times (p50, p95, p99)
   - Failure rate
   - Number of users

### HTML Report

Generated reports include:
- Request statistics (count, failures, median, 95%, 99%)
- Response time charts
- Failures breakdown
- Download data for further analysis

### Custom Metrics

Load test prints custom metrics at completion:

```
================================================================================
ROOM ASSIGNMENT PERFORMANCE REPORT
================================================================================

Room Assignment Latencies:
  Average: 45.23ms
  p50: 38.12ms
  p95: 82.45ms (target: <100ms)
  p99: 95.67ms
  Target Met: ✓ YES

Conflict Detection Latencies:
  Average: 28.34ms
  p95: 42.11ms (target: <50ms)
  Target Met: ✓ YES

Rules Engine Evaluation Latencies:
  Average: 35.78ms
  p95: 48.90ms (target: <50ms)
  Target Met: ✓ YES

Total Appointments Created: 500
================================================================================
```

## Performance Targets

| Metric | Target | Measurement |
|--------|--------|-------------|
| Room Assignment | <100ms p95 | End-to-end booking with room selection |
| Conflict Detection | <50ms p95 | Overlapping appointment detection |
| Rules Evaluation | <50ms per slot | Hard + soft constraint processing |
| Concurrent Bookings | 50 simultaneous | No deadlocks or race conditions |
| Throughput | 20+ bookings/sec | Sustained load handling |

## Database Monitoring

### Query Performance

Monitor database during load test:

```sql
-- Active queries
SELECT pid, query, state, query_start
FROM pg_stat_activity
WHERE datname = 'your_database'
  AND state != 'idle'
ORDER BY query_start;

-- Lock contention
SELECT locktype, relation::regclass, mode, granted, pid
FROM pg_locks
WHERE NOT granted;

-- Index usage
SELECT schemaname, tablename, indexname, idx_scan
FROM pg_stat_user_indexes
WHERE schemaname = 'healthcare'
  AND (indexname LIKE 'idx_appointments_room%' OR indexname LIKE 'idx_rooms_clinic%')
ORDER BY idx_scan DESC;
```

### Deadlock Detection

```sql
-- Check for deadlocks in logs
SELECT * FROM pg_stat_database WHERE deadlocks > 0;
```

## Cleanup

After load testing, cleanup test appointments:

```bash
python3 tests/load/cleanup_test_data.py
```

## Troubleshooting

### High Failure Rate

1. Check backend logs for errors
2. Verify database connection pool size
3. Ensure indexes are created (see migrations)
4. Check for deadlocks in database

### Slow Response Times

1. Run EXPLAIN ANALYZE on slow queries
2. Check index usage statistics
3. Monitor database CPU/memory usage
4. Review rules engine policy cache hit rate

### Connection Errors

1. Increase uvicorn workers: `--workers=4`
2. Adjust database connection pool
3. Check network bandwidth
4. Reduce spawn rate to gradual ramp-up

## Advanced Testing

### Distributed Load Testing

Run Locust in distributed mode for higher load:

```bash
# Master node
locust -f tests/load/locustfile.py --host=http://localhost:8000 --master

# Worker nodes (run multiple terminals or machines)
locust -f tests/load/locustfile.py --host=http://localhost:8000 --worker --master-host=localhost
```

### k6 Alternative

For scripted load testing with k6:

```bash
k6 run tests/load/k6_load_test.js --vus=50 --duration=2m
```

## CI/CD Integration

Add load tests to CI/CD pipeline:

```yaml
# .github/workflows/load-test.yml
name: Load Testing

on:
  push:
    branches: [main]

jobs:
  load-test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v2
      - name: Install dependencies
        run: pip install locust
      - name: Run load tests
        run: |
          locust -f tests/load/locustfile.py --host=${{ secrets.STAGING_URL }} \
                 --users=50 --spawn-rate=10 --headless --run-time=2m \
                 --html=load_report.html
      - name: Upload report
        uses: actions/upload-artifact@v2
        with:
          name: load-test-report
          path: load_report.html
```

## Results Archive

Save results for historical comparison:

```bash
mkdir -p tests/load/results/$(date +%Y-%m-%d)
mv load_report*.html tests/load/results/$(date +%Y-%m-%d)/
```

## References

- [Locust Documentation](https://docs.locust.io/)
- [k6 Documentation](https://k6.io/docs/)
- [PostgreSQL Performance Tuning](https://wiki.postgresql.org/wiki/Performance_Optimization)
- Issue #34 - Room Assignment Testing
- Issue #35 - Smart Room Assignment Rules Engine
