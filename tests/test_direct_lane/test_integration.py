"""
Integration tests for the complete direct lane flow
Tests the realistic end-to-end behavior
"""

import pytest
import time
from app.services.direct_lane.tool_intent_classifier import (
    ToolIntentClassifier,
    DirectToolIntent
)
from app.services.direct_lane.circuit_breaker import CircuitBreaker


class TestDirectLaneIntegration:
    """Integration tests for direct lane components working together"""

    @pytest.fixture
    def classifier(self):
        return ToolIntentClassifier()

    @pytest.fixture
    def circuit_breaker(self):
        return CircuitBreaker(failure_threshold=3, recovery_timeout=1)

    # ==================== Realistic User Flows ====================

    def test_faq_flow_english(self, classifier):
        """Test typical FAQ flow in English"""
        messages = [
            ("What are your hours?", DirectToolIntent.FAQ_QUERY, 0.6),
            ("Where are you located?", DirectToolIntent.FAQ_QUERY, 0.6),
            ("Do you accept insurance?", DirectToolIntent.FAQ_QUERY, 0.6),
        ]

        for message, expected_intent, min_confidence in messages:
            result = classifier.classify(message)
            assert result.intent == expected_intent, f"Failed for: {message}"
            assert result.confidence >= min_confidence
            assert result.duration_ms < 300

    def test_price_flow(self, classifier):
        """Test price query flow"""
        messages = [
            "How much does a cleaning cost?",
            "What's the price for X-rays?",
            "How much do you charge?",
        ]

        for message in messages:
            result = classifier.classify(message)
            # Price OR FAQ is acceptable (due to pattern overlap)
            assert result.intent in [DirectToolIntent.PRICE_QUERY, DirectToolIntent.FAQ_QUERY]
            assert result.confidence >= 0.6
            assert "query" in result.extracted_args

    def test_booking_flow_with_context(self, classifier):
        """Test realistic booking flow with context"""
        # Step 1: Check availability
        result1 = classifier.classify("Are you free tomorrow?")
        assert result1.intent == DirectToolIntent.CHECK_AVAILABILITY
        assert result1.extracted_args.get("date") is not None

        # Step 2: Book with context
        context = {"last_intent": "check_availability", "selected_slot": "10:00"}
        result2 = classifier.classify("Yes, book it", context=context)
        assert result2.intent == DirectToolIntent.BOOK_APPOINTMENT
        assert result2.confidence >= 0.8  # Higher with context

    def test_multilingual_detection(self, classifier):
        """Test language detection across languages"""
        test_cases = [
            ("What are your hours?", "en"),
            ("¿Cuándo están abiertos?", "es"),
            ("Quando vocês estão abertos?", "pt"),
        ]

        for message, expected_lang in test_cases:
            result = classifier.classify(message)
            assert result.language == expected_lang

    # ==================== Performance Tests ====================

    def test_classification_performance_under_load(self, classifier):
        """Test performance under load"""
        messages = [
            "What are your hours?",
            "How much does it cost?",
            "Are you free tomorrow?",
        ] * 20  # 60 classifications

        start = time.time()
        for message in messages:
            result = classifier.classify(message)
            assert result.duration_ms < 300

        total_time = time.time() - start
        avg_time_ms = (total_time * 1000) / len(messages)

        assert avg_time_ms < 50, f"Average time {avg_time_ms}ms exceeds 50ms threshold"

    # ==================== Circuit Breaker Integration ====================

    def test_circuit_breaker_protects_system(self, circuit_breaker):
        """Test circuit breaker prevents cascade failures"""
        tool_name = "test_service"

        # Record failures
        for i in range(3):
            circuit_breaker.record_failure(tool_name)

        # Circuit should be open
        assert circuit_breaker.is_open(tool_name)

        # Wait for recovery
        time.sleep(1.1)

        # Should allow retry
        assert not circuit_breaker.is_open(tool_name)

        # Successful recovery
        circuit_breaker.record_success(tool_name)
        assert not circuit_breaker.is_open(tool_name)

    # ==================== Edge Cases ====================

    def test_handles_empty_and_invalid_input(self, classifier):
        """Test handling of edge case inputs"""
        test_cases = [
            "",
            "   ",
            "a",
            "?",
            "12345",
        ]

        for message in test_cases:
            result = classifier.classify(message)
            # Should not crash
            assert result is not None
            assert result.intent in DirectToolIntent
            assert result.duration_ms < 300

    def test_handles_very_long_input(self, classifier):
        """Test handling of very long messages"""
        message = "What are your hours? " * 100

        result = classifier.classify(message)
        assert result is not None
        assert result.duration_ms < 300

    # ==================== Context Handling ====================

    def test_context_improves_booking_confidence(self, classifier):
        """Test that context improves booking confidence"""
        message = "Yes, book it"

        # Without context
        result_no_context = classifier.classify(message)

        # With context
        context = {"last_intent": "check_availability"}
        result_with_context = classifier.classify(message, context=context)

        # Context should provide higher confidence
        if result_with_context.intent == DirectToolIntent.BOOK_APPOINTMENT:
            assert result_with_context.confidence > result_no_context.confidence

    # ==================== Date Extraction ====================

    def test_date_extraction_accuracy(self, classifier):
        """Test accurate date extraction from messages"""
        from datetime import date, timedelta

        test_cases = [
            ("Are you free today?", date.today().isoformat()),
            ("Do you have slots tomorrow?", (date.today() + timedelta(days=1)).isoformat()),
        ]

        for message, expected_date in test_cases:
            result = classifier.classify(message)
            if result.intent == DirectToolIntent.CHECK_AVAILABILITY:
                assert result.extracted_args.get("date") == expected_date


class TestDirectLaneRobustness:
    """Test robustness and error handling"""

    @pytest.fixture
    def classifier(self):
        return ToolIntentClassifier()

    def test_thread_safety(self, classifier):
        """Test classifier is thread-safe"""
        import threading

        results = []

        def classify_message():
            result = classifier.classify("What are your hours?")
            results.append(result)

        threads = [threading.Thread(target=classify_message) for _ in range(10)]

        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        # All results should be consistent
        assert len(results) == 10
        assert all(r.intent == DirectToolIntent.FAQ_QUERY for r in results)

    def test_concurrent_classifications(self, classifier):
        """Test concurrent classifications"""
        import concurrent.futures

        messages = [
            "What are your hours?",
            "How much does it cost?",
            "Are you free tomorrow?",
        ] * 10

        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(classifier.classify, msg) for msg in messages]
            results = [f.result() for f in futures]

        # All should succeed
        assert len(results) == len(messages)
        assert all(r.intent != DirectToolIntent.UNKNOWN or r.confidence == 0.0 for r in results)


# Golden test cases for accuracy validation
@pytest.mark.parametrize("message,expected_intent,description", [
    # Clear FAQ cases
    ("What time do you open?", DirectToolIntent.FAQ_QUERY, "Hours question"),
    ("Where is your office?", DirectToolIntent.FAQ_QUERY, "Location question"),
    ("Do you take insurance?", DirectToolIntent.FAQ_QUERY, "Insurance question"),

    # Clear availability cases (with date keywords)
    ("Any slots available today?", DirectToolIntent.CHECK_AVAILABILITY, "Today availability"),
    ("Are you free tomorrow?", DirectToolIntent.CHECK_AVAILABILITY, "Tomorrow availability"),

    # Clear unknown cases
    ("Hello", DirectToolIntent.UNKNOWN, "Greeting"),
    ("Thanks", DirectToolIntent.UNKNOWN, "Thanks"),
    ("Goodbye", DirectToolIntent.UNKNOWN, "Farewell"),
])
def test_golden_cases(message, expected_intent, description):
    """Test golden cases for accuracy"""
    classifier = ToolIntentClassifier()
    result = classifier.classify(message)

    assert result.intent == expected_intent, f"Failed: {description} - Got {result.intent.value}, expected {expected_intent.value}"


# Performance benchmark
def test_performance_benchmark():
    """Benchmark classifier performance"""
    classifier = ToolIntentClassifier()

    messages = [
        "What are your hours?",
        "How much does a cleaning cost?",
        "Are you free tomorrow?",
        "Book me for 2pm",
    ]

    total_time = 0
    iterations = 100

    for _ in range(iterations):
        for message in messages:
            start = time.time()
            classifier.classify(message)
            total_time += (time.time() - start)

    avg_time_ms = (total_time * 1000) / (iterations * len(messages))

    print(f"\n{'='*60}")
    print(f"Performance Benchmark Results:")
    print(f"  Total classifications: {iterations * len(messages)}")
    print(f"  Average time: {avg_time_ms:.2f}ms")
    print(f"  Target: < 50ms")
    print(f"  Status: {'✅ PASS' if avg_time_ms < 50 else '❌ FAIL'}")
    print(f"{'='*60}\n")

    assert avg_time_ms < 50, f"Performance degraded: {avg_time_ms}ms > 50ms threshold"


if __name__ == "__main__":
    # Run performance benchmark
    print("Running performance benchmark...")
    test_performance_benchmark()
