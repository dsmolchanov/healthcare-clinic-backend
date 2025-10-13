"""
Load Testing for Healthcare Backend

This package contains load testing scripts and utilities for performance validation.

Test Scenarios:
1. Concurrent Booking Storm: 50 simultaneous appointment bookings
2. Room Conflict Handling: Limited rooms with contention
3. Rules Engine Performance: Complex rule sets at scale

Performance Targets:
- Room assignment: <100ms p95
- Conflict detection: <50ms
- Rules evaluation: <50ms per slot
- Throughput: 20+ bookings/sec

Tools:
- Locust: HTTP load testing
- Custom metrics tracking
- Performance report generation

Usage:
    # Setup test data
    python3 setup_test_data.py

    # Run load tests
    locust -f locustfile.py --host=http://localhost:8000 --users=50 --spawn-rate=10

    # Cleanup
    python3 cleanup_test_data.py

See README.md for detailed instructions.
"""

__version__ = "1.0.0"
__author__ = "Healthcare Backend Team"
