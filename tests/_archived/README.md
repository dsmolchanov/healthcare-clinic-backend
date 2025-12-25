# Archived Tests

This directory contains test files that have been archived as part of Phase 5 technical debt remediation.

## Archive Reason

These tests are skeleton implementations for features that do not yet exist. They were created as placeholders during an earlier planning phase but the underlying features were never implemented.

Per the Phase 5 cap rule: If a skipped test requires >2 hours of debugging (or in this case, implementing the entire feature), it should be archived and a tech debt ticket created.

## Archived Files

| File | Original Skip Count | Reason |
|------|---------------------|--------|
| `test_constraint_engine.py` | 13 | ConstraintEngine class not implemented |
| `test_preference_scorer.py` | 12 | PreferenceScorer class not implemented |
| `test_scheduling_e2e.py` | 19 | Full scheduling system not complete |
| `test_scheduling_api.py` | 20 | Scheduling API endpoints not complete |
| `test_escalation_manager.py` | 8 | EscalationManager class not implemented |

## Re-enabling Tests

When implementing these features, move the corresponding test file back to `tests/` and implement the test cases:

```bash
# Example: When implementing ConstraintEngine
mv tests/_archived/test_constraint_engine.py tests/
# Then implement the tests
```

## Related Tech Debt Tickets

- TECH-DEBT: Implement ConstraintEngine for scheduling validation
- TECH-DEBT: Implement PreferenceScorer for slot ranking
- TECH-DEBT: Complete scheduling E2E flow
- TECH-DEBT: Implement EscalationManager for no-slot scenarios

## Archive Date

2025-12-24 (Phase 5 - API & Test Improvements)
