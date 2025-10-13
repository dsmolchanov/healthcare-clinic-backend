"""
Integration Tests for Healthcare Backend

This package contains end-to-end integration tests that use actual database connections
and validate complete workflows across multiple services.

Test Categories:
- Room Assignment Integration: End-to-end booking, override, calendar sync
- Calendar Integration: External calendar service integration
- Rules Engine Integration: Complex rule evaluation under real conditions
- Multi-Clinic Scenarios: Cross-clinic isolation and data integrity

Usage:
    # Run all integration tests
    pytest tests/integration/ -v -s

    # Run specific test file
    pytest tests/integration/test_room_assignment_integration.py -v

    # Run with coverage
    pytest tests/integration/ --cov=app --cov-report=html

Note: Integration tests require:
- Database connection (SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
- Migrations applied
- Clean test data isolation
"""

__version__ = "1.0.0"
__author__ = "Healthcare Backend Team"
