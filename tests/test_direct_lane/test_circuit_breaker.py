"""
Comprehensive tests for CircuitBreaker

Tests cover:
- State transitions (CLOSED → OPEN → HALF_OPEN → CLOSED)
- Failure threshold enforcement
- Recovery timeout
- Thread safety
- Multiple tool tracking
"""

import pytest
import time
import threading
from datetime import datetime, timedelta
from app.services.direct_lane.circuit_breaker import CircuitBreaker, CircuitState


class TestCircuitBreaker:
    """Test suite for CircuitBreaker"""

    @pytest.fixture
    def circuit_breaker(self):
        """Create circuit breaker instance with short timeouts for testing"""
        return CircuitBreaker(failure_threshold=5, recovery_timeout=1)

    # ==================== Basic State Tests ====================

    def test_initial_state_closed(self, circuit_breaker):
        """Test circuit breaker starts in CLOSED state"""
        assert not circuit_breaker.is_open("test_tool")

    def test_state_closed_after_success(self, circuit_breaker):
        """Test state remains CLOSED after successful execution"""
        circuit_breaker.record_success("test_tool")
        assert not circuit_breaker.is_open("test_tool")

    # ==================== Failure Threshold Tests ====================

    def test_opens_after_threshold_failures(self, circuit_breaker):
        """Test circuit opens after reaching failure threshold"""
        tool_name = "test_tool"

        # Record failures up to threshold (5)
        for i in range(5):
            circuit_breaker.record_failure(tool_name)

        # Circuit should now be OPEN
        assert circuit_breaker.is_open(tool_name)

    def test_does_not_open_below_threshold(self, circuit_breaker):
        """Test circuit stays CLOSED below failure threshold"""
        tool_name = "test_tool"

        # Record failures below threshold (4 < 5)
        for i in range(4):
            circuit_breaker.record_failure(tool_name)

        # Circuit should still be CLOSED
        assert not circuit_breaker.is_open(tool_name)

    def test_success_resets_failure_count(self, circuit_breaker):
        """Test successful execution resets failure count"""
        tool_name = "test_tool"

        # Record some failures
        for i in range(3):
            circuit_breaker.record_failure(tool_name)

        # Record success - should reset counter
        circuit_breaker.record_success(tool_name)

        # Record more failures (should need 5 more to open)
        for i in range(4):
            circuit_breaker.record_failure(tool_name)

        # Circuit should still be CLOSED (only 4 failures since last success)
        assert not circuit_breaker.is_open(tool_name)

    # ==================== Recovery Tests ====================

    def test_transitions_to_half_open_after_timeout(self, circuit_breaker):
        """Test circuit transitions to HALF_OPEN after recovery timeout"""
        tool_name = "test_tool"

        # Open the circuit
        for i in range(5):
            circuit_breaker.record_failure(tool_name)

        assert circuit_breaker.is_open(tool_name)

        # Wait for recovery timeout (1 second)
        time.sleep(1.1)

        # Circuit should now be HALF_OPEN (returns False to allow test)
        assert not circuit_breaker.is_open(tool_name)

    def test_closes_after_successful_recovery(self, circuit_breaker):
        """Test circuit closes after successful execution in HALF_OPEN state"""
        tool_name = "test_tool"

        # Open the circuit
        for i in range(5):
            circuit_breaker.record_failure(tool_name)

        # Wait for recovery
        time.sleep(1.1)

        # Successful execution should close the circuit
        circuit_breaker.record_success(tool_name)

        # Verify circuit is CLOSED
        assert not circuit_breaker.is_open(tool_name)

        # Verify failure count is reset
        circuit_breaker.record_failure(tool_name)
        assert not circuit_breaker.is_open(tool_name)  # Still closed after 1 failure

    def test_reopens_after_failure_in_half_open(self, circuit_breaker):
        """Test circuit reopens if failure occurs in HALF_OPEN state"""
        tool_name = "test_tool"

        # Open the circuit
        for i in range(5):
            circuit_breaker.record_failure(tool_name)

        # Wait for recovery
        time.sleep(1.1)

        # Failure in HALF_OPEN should increment count
        circuit_breaker.record_failure(tool_name)

        # Circuit should be OPEN again
        assert circuit_breaker.is_open(tool_name)

    # ==================== Multiple Tool Tests ====================

    def test_independent_tool_tracking(self, circuit_breaker):
        """Test each tool has independent circuit breaker state"""
        tool1 = "faq_query"
        tool2 = "price_query"

        # Open circuit for tool1
        for i in range(5):
            circuit_breaker.record_failure(tool1)

        # tool1 should be OPEN, tool2 should be CLOSED
        assert circuit_breaker.is_open(tool1)
        assert not circuit_breaker.is_open(tool2)

        # Record failure for tool2
        circuit_breaker.record_failure(tool2)

        # tool1 still OPEN, tool2 still CLOSED
        assert circuit_breaker.is_open(tool1)
        assert not circuit_breaker.is_open(tool2)

    def test_multiple_tools_simultaneous(self, circuit_breaker):
        """Test multiple tools can be tracked simultaneously"""
        tools = ["faq", "price", "availability", "booking"]

        # Open circuits for first two tools
        for tool in tools[:2]:
            for i in range(5):
                circuit_breaker.record_failure(tool)

        # Verify states
        assert circuit_breaker.is_open(tools[0])
        assert circuit_breaker.is_open(tools[1])
        assert not circuit_breaker.is_open(tools[2])
        assert not circuit_breaker.is_open(tools[3])

    # ==================== Thread Safety Tests ====================

    def test_thread_safe_concurrent_failures(self, circuit_breaker):
        """Test thread-safe recording of concurrent failures"""
        tool_name = "test_tool"
        num_threads = 10
        failures_per_thread = 2

        def record_failures():
            for _ in range(failures_per_thread):
                circuit_breaker.record_failure(tool_name)

        threads = [threading.Thread(target=record_failures) for _ in range(num_threads)]

        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        # Total failures should trigger circuit breaker (20 > 5)
        assert circuit_breaker.is_open(tool_name)

    def test_thread_safe_concurrent_success(self, circuit_breaker):
        """Test thread-safe recording of concurrent successes"""
        tool_name = "test_tool"
        num_threads = 5

        # First, record some failures
        for i in range(3):
            circuit_breaker.record_failure(tool_name)

        def record_success():
            circuit_breaker.record_success(tool_name)

        threads = [threading.Thread(target=record_success) for _ in range(num_threads)]

        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        # Circuit should be CLOSED and failure count reset
        assert not circuit_breaker.is_open(tool_name)

    def test_thread_safe_state_check(self, circuit_breaker):
        """Test thread-safe state checking"""
        tool_name = "test_tool"
        num_threads = 20

        # Open the circuit
        for i in range(5):
            circuit_breaker.record_failure(tool_name)

        results = []

        def check_state():
            results.append(circuit_breaker.is_open(tool_name))

        threads = [threading.Thread(target=check_state) for _ in range(num_threads)]

        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        # All threads should see the same state
        assert all(results)  # All should be True (OPEN)
        assert len(results) == num_threads

    # ==================== Edge Cases ====================

    def test_handles_new_tool_name(self, circuit_breaker):
        """Test handling of previously unseen tool names"""
        new_tool = "never_seen_before"

        # Should not be open initially
        assert not circuit_breaker.is_open(new_tool)

        # Should be able to record failures
        circuit_breaker.record_failure(new_tool)
        assert not circuit_breaker.is_open(new_tool)

    def test_handles_empty_tool_name(self, circuit_breaker):
        """Test handling of empty tool name"""
        # Should not crash
        circuit_breaker.record_failure("")
        assert not circuit_breaker.is_open("")

    def test_rapid_state_transitions(self, circuit_breaker):
        """Test rapid state transitions"""
        tool_name = "test_tool"

        # Open
        for i in range(5):
            circuit_breaker.record_failure(tool_name)
        assert circuit_breaker.is_open(tool_name)

        # Wait for recovery
        time.sleep(1.1)

        # Close
        circuit_breaker.record_success(tool_name)
        assert not circuit_breaker.is_open(tool_name)

        # Open again
        for i in range(5):
            circuit_breaker.record_failure(tool_name)
        assert circuit_breaker.is_open(tool_name)

    # ==================== Configuration Tests ====================

    def test_custom_failure_threshold(self):
        """Test custom failure threshold"""
        cb = CircuitBreaker(failure_threshold=3, recovery_timeout=1)
        tool_name = "test_tool"

        # Should open after 3 failures
        for i in range(3):
            cb.record_failure(tool_name)

        assert cb.is_open(tool_name)

    def test_custom_recovery_timeout(self):
        """Test custom recovery timeout"""
        cb = CircuitBreaker(failure_threshold=5, recovery_timeout=2)
        tool_name = "test_tool"

        # Open the circuit
        for i in range(5):
            cb.record_failure(tool_name)

        # Should still be open before timeout
        time.sleep(1.5)
        assert cb.is_open(tool_name)

        # Should be half-open after timeout
        time.sleep(0.6)  # Total: 2.1 seconds
        assert not cb.is_open(tool_name)

    # ==================== Stress Tests ====================

    def test_high_volume_failures(self, circuit_breaker):
        """Test handling of high volume of failures"""
        tool_name = "test_tool"

        # Record many failures
        for i in range(100):
            circuit_breaker.record_failure(tool_name)

        # Should be open
        assert circuit_breaker.is_open(tool_name)

        # Should recover after timeout
        time.sleep(1.1)
        circuit_breaker.record_success(tool_name)
        assert not circuit_breaker.is_open(tool_name)

    def test_many_tools_simultaneously(self):
        """Test handling many different tools simultaneously"""
        cb = CircuitBreaker(failure_threshold=5, recovery_timeout=1)

        # Create 50 different tools
        tools = [f"tool_{i}" for i in range(50)]

        # Open half of them
        for tool in tools[:25]:
            for i in range(5):
                cb.record_failure(tool)

        # Verify states
        for i, tool in enumerate(tools):
            if i < 25:
                assert cb.is_open(tool), f"{tool} should be open"
            else:
                assert not cb.is_open(tool), f"{tool} should be closed"

    # ==================== Logging/State Inspection Tests ====================

    def test_failure_count_increments(self, circuit_breaker):
        """Test failure count increments correctly"""
        tool_name = "test_tool"

        for i in range(1, 5):
            circuit_breaker.record_failure(tool_name)
            # Internal state check (if we had access to it)
            # For now, we verify via behavior

        # 4 failures - should still be closed
        assert not circuit_breaker.is_open(tool_name)

        # 5th failure - should open
        circuit_breaker.record_failure(tool_name)
        assert circuit_breaker.is_open(tool_name)


class TestCircuitBreakerIntegration:
    """Integration tests for CircuitBreaker in realistic scenarios"""

    def test_realistic_failure_recovery_cycle(self):
        """Test realistic failure and recovery cycle"""
        cb = CircuitBreaker(failure_threshold=5, recovery_timeout=1)
        tool_name = "database_query"

        # Simulate 5 database failures
        for i in range(5):
            cb.record_failure(tool_name)

        # Circuit should be open (preventing further attempts)
        assert cb.is_open(tool_name)

        # Wait for recovery period
        time.sleep(1.1)

        # System is back - should allow retry
        assert not cb.is_open(tool_name)

        # Successful retry
        cb.record_success(tool_name)

        # Circuit should be closed and healthy
        assert not cb.is_open(tool_name)

    def test_intermittent_failures(self):
        """Test handling of intermittent failures"""
        cb = CircuitBreaker(failure_threshold=5, recovery_timeout=1)
        tool_name = "api_call"

        # Simulate intermittent failures (not consecutive)
        cb.record_failure(tool_name)
        cb.record_success(tool_name)  # Resets
        cb.record_failure(tool_name)
        cb.record_failure(tool_name)
        cb.record_success(tool_name)  # Resets
        cb.record_failure(tool_name)

        # Should still be closed (failures reset by successes)
        assert not cb.is_open(tool_name)


# Run manual test if called directly
if __name__ == "__main__":
    print("Running CircuitBreaker manual tests...")
    print("=" * 60)

    cb = CircuitBreaker(failure_threshold=3, recovery_timeout=2)
    tool = "test_tool"

    print("\n1. Initial state (CLOSED):")
    print(f"   Is open? {cb.is_open(tool)}")

    print("\n2. Recording 3 failures (should OPEN):")
    for i in range(3):
        cb.record_failure(tool)
        print(f"   Failure {i+1} recorded")
    print(f"   Is open? {cb.is_open(tool)}")

    print("\n3. Waiting 2 seconds for recovery...")
    time.sleep(2.1)
    print(f"   Is open? {cb.is_open(tool)} (should be False - HALF_OPEN)")

    print("\n4. Recording success (should CLOSE):")
    cb.record_success(tool)
    print(f"   Is open? {cb.is_open(tool)}")

    print("\n" + "=" * 60)
    print("Manual test complete!")
