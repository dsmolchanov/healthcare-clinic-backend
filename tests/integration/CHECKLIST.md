# Integration Tests Validation Checklist

Use this checklist to validate integration tests before marking Issue #34 complete.

## Pre-Test Setup

- [ ] Database migrations applied
  ```bash
  python3 apply_migration.py ../../infra/db/migrations/add_room_display_config.sql
  python3 apply_migration.py ../../infra/db/migrations/add_room_performance_indexes.sql
  ```

- [ ] Environment variables configured
  ```bash
  echo $SUPABASE_URL
  echo $SUPABASE_SERVICE_ROLE_KEY
  ```

- [ ] Test dependencies installed
  ```bash
  pip install -r tests/requirements-test.txt
  ```

- [ ] Backend server running (for API tests)
  ```bash
  uvicorn app.main:app --reload --port 8000
  ```

## Test Execution

### All Integration Tests

- [ ] Run all integration tests
  ```bash
  pytest tests/integration/ -v -s
  ```
  **Expected**: All tests pass (10/10)

### Test Class by Test Class

- [ ] TestCompleteBookingFlow (2 tests)
- [ ] TestRoomOverrideFlow (1 test)
- [ ] TestCalendarSyncWithRoom (1 test)
- [ ] TestColorCustomization (1 test)
- [ ] TestConflictDetection (1 test)
- [ ] TestRulesEngineIntegration (2 tests)
- [ ] TestMultiClinicIsolation (1 test)
- [ ] TestErrorRecovery (1 test)

### Coverage Report

- [ ] Generate coverage report
  ```bash
  pytest tests/integration/ --cov --cov-report=html
  open htmlcov/index.html
  ```
  **Target**: >85% coverage for room assignment code

## Test Validations

### Database Integrity

- [ ] No orphaned test data after tests
  ```sql
  SELECT COUNT(*) FROM clinics WHERE name LIKE '%Integration Test%';
  -- Expected: 0 (all cleaned up)
  ```

- [ ] Audit logs created for room overrides
  ```sql
  SELECT COUNT(*) FROM hipaa_audit_logs WHERE action = 'room_override';
  ```

- [ ] Room display configs working
  ```sql
  SELECT * FROM room_display_config LIMIT 5;
  ```

### Functionality

- [ ] Room auto-assignment works
- [ ] Multiple available rooms → best room selected
- [ ] No available rooms → graceful fallback
- [ ] Room override creates audit log
- [ ] Conflict detection prevents double-booking
- [ ] Rules engine filters rooms correctly
- [ ] Cross-clinic isolation enforced
- [ ] Database errors handled gracefully

## Performance

- [ ] Individual tests complete in <10 seconds
- [ ] Full suite completes in <60 seconds
- [ ] No memory leaks (check with monitoring)
- [ ] Database connections properly closed

## Issues Found

Document any issues found during validation:

```
Issue 1:
- Description:
- Severity: [Critical / High / Medium / Low]
- Steps to reproduce:
- Expected:
- Actual:
- Fix required:

Issue 2:
...
```

## Sign-Off

- [ ] All tests pass
- [ ] Coverage target met (>85%)
- [ ] No critical issues found
- [ ] Performance acceptable
- [ ] Test data cleaned up
- [ ] Documentation reviewed

**Validated By**: ___________________
**Date**: ___________________
**Status**: [ ] PASS / [ ] FAIL
**Notes**:

