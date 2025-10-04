"""
Comprehensive tests for ToolIntentClassifier

Tests cover:
- Intent detection accuracy
- Multilingual support
- Context-aware classification
- Edge cases and error handling
- Performance budgets
"""

import pytest
import time
from app.services.direct_lane.tool_intent_classifier import (
    ToolIntentClassifier,
    DirectToolIntent,
    ToolIntentMatch
)


class TestToolIntentClassifier:
    """Test suite for ToolIntentClassifier"""

    @pytest.fixture
    def classifier(self):
        """Create classifier instance"""
        return ToolIntentClassifier()

    # ==================== FAQ Query Tests ====================

    def test_faq_query_detection_english(self, classifier):
        """Test FAQ query detection in English"""
        test_cases = [
            "What are your hours?",
            "When are you open?",
            "Where is your location?",
            "Do you accept insurance?",
            "What payment methods do you accept?",
            "What is your cancellation policy?",
        ]

        for message in test_cases:
            result = classifier.classify(message)
            assert result.intent == DirectToolIntent.FAQ_QUERY
            assert result.confidence >= 0.6
            assert result.language == "en"
            assert "query" in result.extracted_args

    def test_faq_query_detection_spanish(self, classifier):
        """Test FAQ query detection in Spanish"""
        test_cases = [
            "¿Cuándo están abiertos?",
            "¿Dónde está su ubicación?",
            "¿Aceptan seguro?",
            "¿Qué horarios tienen?",
        ]

        for message in test_cases:
            result = classifier.classify(message)
            assert result.intent == DirectToolIntent.FAQ_QUERY
            assert result.confidence >= 0.6
            assert result.language == "es"

    def test_faq_query_detection_portuguese(self, classifier):
        """Test FAQ query detection in Portuguese"""
        test_cases = [
            "Quando vocês estão abertos?",
            "Onde fica a localização?",
            "Qual é o horário?",
        ]

        for message in test_cases:
            result = classifier.classify(message)
            assert result.intent == DirectToolIntent.FAQ_QUERY
            assert result.confidence >= 0.6
            assert result.language == "pt"

    # ==================== Price Query Tests ====================

    def test_price_query_detection_english(self, classifier):
        """Test price query detection in English"""
        test_cases = [
            "How much does a cleaning cost?",
            "What's the price for a checkup?",
            "How much do you charge for teeth whitening?",
            "What are your fees?",
            "Is it expensive?",
        ]

        for message in test_cases:
            result = classifier.classify(message)
            assert result.intent == DirectToolIntent.PRICE_QUERY
            assert result.confidence >= 0.85
            assert result.language == "en"
            assert "query" in result.extracted_args

    def test_price_query_extraction(self, classifier):
        """Test service name extraction from price queries"""
        message = "How much does a root canal cost?"
        result = classifier.classify(message)

        assert result.intent == DirectToolIntent.PRICE_QUERY
        assert "root canal" in result.extracted_args["query"].lower()

    def test_price_query_detection_spanish(self, classifier):
        """Test price query detection in Spanish"""
        test_cases = [
            "¿Cuánto cuesta una limpieza?",
            "¿Cuál es el precio de un chequeo?",
            "¿Cuánto cobran?",
        ]

        for message in test_cases:
            result = classifier.classify(message)
            assert result.intent == DirectToolIntent.PRICE_QUERY
            assert result.confidence >= 0.85
            assert result.language == "es"

    # ==================== Availability Check Tests ====================

    def test_availability_check_detection(self, classifier):
        """Test availability check detection"""
        test_cases = [
            "What times are available today?",
            "Are you free tomorrow?",
            "Can I book an appointment for next week?",
            "Do you have any slots available?",
            "I need to schedule a visit",
        ]

        for message in test_cases:
            result = classifier.classify(message)
            assert result.intent == DirectToolIntent.CHECK_AVAILABILITY
            assert result.confidence >= 0.6
            assert result.language == "en"

    def test_availability_date_extraction_today(self, classifier):
        """Test date extraction for 'today'"""
        from datetime import date

        message = "What times are available today?"
        result = classifier.classify(message)

        assert result.intent == DirectToolIntent.CHECK_AVAILABILITY
        assert result.extracted_args["date"] == date.today().isoformat()

    def test_availability_date_extraction_tomorrow(self, classifier):
        """Test date extraction for 'tomorrow'"""
        from datetime import date, timedelta

        message = "Are you free tomorrow?"
        result = classifier.classify(message)

        assert result.intent == DirectToolIntent.CHECK_AVAILABILITY
        expected_date = (date.today() + timedelta(days=1)).isoformat()
        assert result.extracted_args["date"] == expected_date

    def test_availability_date_extraction_explicit(self, classifier):
        """Test explicit date extraction"""
        message = "Do you have slots on 2025-10-15?"
        result = classifier.classify(message)

        assert result.intent == DirectToolIntent.CHECK_AVAILABILITY
        assert "2025-10-15" in result.extracted_args["date"]

    def test_availability_spanish(self, classifier):
        """Test availability in Spanish"""
        test_cases = [
            "¿Qué horarios tienen disponibles hoy?",
            "¿Están libres mañana?",
            "Necesito agendar una cita",
        ]

        for message in test_cases:
            result = classifier.classify(message)
            assert result.intent == DirectToolIntent.CHECK_AVAILABILITY
            assert result.language == "es"

    # ==================== Booking Confirmation Tests ====================

    def test_booking_without_context(self, classifier):
        """Test booking detection without context (lower confidence)"""
        message = "I want to book for 2pm"
        result = classifier.classify(message)

        assert result.intent == DirectToolIntent.BOOK_APPOINTMENT
        assert result.confidence < 0.8  # Lower confidence without context

    def test_booking_with_context(self, classifier):
        """Test booking detection with availability context (higher confidence)"""
        message = "Yes, book the 2pm slot"
        context = {"last_intent": "check_availability"}

        result = classifier.classify(message, context=context)

        assert result.intent == DirectToolIntent.BOOK_APPOINTMENT
        assert result.confidence >= 0.8  # High confidence with context

    def test_booking_time_extraction(self, classifier):
        """Test time extraction from booking message"""
        message = "Book me for 2:30pm please"
        result = classifier.classify(message)

        assert result.intent == DirectToolIntent.BOOK_APPOINTMENT
        assert "time" in result.extracted_args
        assert "2:30" in result.extracted_args["time"]

    def test_booking_confirmation_keywords(self, classifier):
        """Test booking confirmation with various keywords"""
        test_cases = [
            "Yes, confirm the appointment",
            "Ok, book it",
            "Proceed with the reservation",
            "I'll take that slot",
        ]

        context = {"last_intent": "check_availability"}

        for message in test_cases:
            result = classifier.classify(message, context=context)
            assert result.intent == DirectToolIntent.BOOK_APPOINTMENT
            assert result.confidence >= 0.8

    # ==================== Language Detection Tests ====================

    def test_language_detection_english(self, classifier):
        """Test English language detection"""
        messages = [
            "What are your hours?",
            "How much does it cost?",
            "I need an appointment",
        ]

        for message in messages:
            result = classifier.classify(message)
            assert result.language == "en"

    def test_language_detection_spanish(self, classifier):
        """Test Spanish language detection"""
        messages = [
            "¿Cuándo están abiertos?",
            "¿Cuánto cuesta?",
            "Necesito una cita",
        ]

        for message in messages:
            result = classifier.classify(message)
            assert result.language == "es"

    def test_language_detection_portuguese(self, classifier):
        """Test Portuguese language detection"""
        messages = [
            "Quando vocês estão abertos?",
            "Quanto custa?",
            "Preciso de uma consulta",
        ]

        for message in messages:
            result = classifier.classify(message)
            assert result.language == "pt"

    # ==================== Edge Cases ====================

    def test_unknown_intent(self, classifier):
        """Test messages with no matching intent"""
        test_cases = [
            "Hello",
            "Thanks",
            "Goodbye",
            "Random text without keywords",
        ]

        for message in test_cases:
            result = classifier.classify(message)
            assert result.intent == DirectToolIntent.UNKNOWN
            assert result.confidence == 0.0

    def test_empty_message(self, classifier):
        """Test empty message handling"""
        result = classifier.classify("")
        assert result.intent == DirectToolIntent.UNKNOWN
        assert result.confidence == 0.0

    def test_very_long_message(self, classifier):
        """Test very long message handling"""
        message = "What are your hours? " * 100
        result = classifier.classify(message)
        # Should still detect FAQ intent
        assert result.intent == DirectToolIntent.FAQ_QUERY

    # ==================== Performance Tests ====================

    def test_classification_performance(self, classifier):
        """Test classification completes within budget"""
        message = "What are your hours?"

        start = time.time()
        result = classifier.classify(message, max_duration_ms=300)
        duration_ms = (time.time() - start) * 1000

        assert duration_ms < 300  # Must complete within 300ms budget
        assert result.duration_ms < 300

    def test_classification_performance_multiple(self, classifier):
        """Test average performance across multiple classifications"""
        messages = [
            "What are your hours?",
            "How much does a cleaning cost?",
            "Are you free tomorrow?",
            "Book me for 2pm",
        ] * 10  # 40 total classifications

        start = time.time()
        for message in messages:
            classifier.classify(message)
        duration_ms = (time.time() - start) * 1000

        avg_ms = duration_ms / len(messages)
        assert avg_ms < 50  # Average should be much faster than budget

    # ==================== Confidence Thresholds ====================

    def test_high_confidence_faq(self, classifier):
        """Test high confidence FAQ detection (multiple patterns)"""
        # Message matching multiple patterns should have higher confidence
        message = "What are your hours and where is your location?"
        result = classifier.classify(message)

        assert result.intent == DirectToolIntent.FAQ_QUERY
        assert result.confidence >= 0.9  # High confidence with multiple matches

    def test_medium_confidence_faq(self, classifier):
        """Test medium confidence FAQ detection (single pattern)"""
        message = "What are your hours?"
        result = classifier.classify(message)

        assert result.intent == DirectToolIntent.FAQ_QUERY
        # Confidence may vary based on single vs multiple pattern matches

    # ==================== Context Handling ====================

    def test_context_preservation(self, classifier):
        """Test context is preserved in extracted args"""
        context = {
            "session_id": "test-123",
            "doctor_id": "doc-456",
            "service_id": "svc-789",
        }

        message = "Yes, book it"
        result = classifier.classify(message, context=context)

        assert result.intent == DirectToolIntent.BOOK_APPOINTMENT
        # Context values should be in extracted args
        assert result.extracted_args.get("doctor_id") == "doc-456"
        assert result.extracted_args.get("service_id") == "svc-789"

    # ==================== Multilingual Edge Cases ====================

    def test_mixed_language_detection(self, classifier):
        """Test handling of mixed language messages"""
        # Should detect primary language
        message = "Hola, what are your hours?"
        result = classifier.classify(message)

        # Should detect Spanish keywords first
        assert result.language in ["es", "en"]

    def test_language_fallback_to_english(self, classifier):
        """Test fallback to English for unknown language"""
        # Message without specific language markers
        message = "12345 abcdef xyz"
        result = classifier.classify(message)

        assert result.language == "en"  # Default fallback

    # ==================== Golden Test Cases ====================

    @pytest.mark.parametrize("message,expected_intent,min_confidence", [
        # FAQ queries
        ("What time do you open?", DirectToolIntent.FAQ_QUERY, 0.6),
        ("Do you accept credit cards?", DirectToolIntent.FAQ_QUERY, 0.6),
        ("What's your address?", DirectToolIntent.FAQ_QUERY, 0.6),

        # Price queries
        ("How much for a cleaning?", DirectToolIntent.PRICE_QUERY, 0.85),
        ("What's the cost?", DirectToolIntent.PRICE_QUERY, 0.85),

        # Availability
        ("Any slots available today?", DirectToolIntent.CHECK_AVAILABILITY, 0.6),
        ("Can I schedule for tomorrow?", DirectToolIntent.CHECK_AVAILABILITY, 0.6),

        # Unknown
        ("Hello", DirectToolIntent.UNKNOWN, 0.0),
        ("Thanks", DirectToolIntent.UNKNOWN, 0.0),
    ])
    def test_golden_cases(self, classifier, message, expected_intent, min_confidence):
        """Test golden test cases for accuracy"""
        result = classifier.classify(message)

        assert result.intent == expected_intent
        assert result.confidence >= min_confidence


class TestToolIntentMatch:
    """Test ToolIntentMatch dataclass"""

    def test_intent_match_creation(self):
        """Test creating ToolIntentMatch instance"""
        match = ToolIntentMatch(
            intent=DirectToolIntent.FAQ_QUERY,
            confidence=0.95,
            extracted_args={"query": "test"},
            reasoning="Test reasoning",
            language="en",
            duration_ms=50
        )

        assert match.intent == DirectToolIntent.FAQ_QUERY
        assert match.confidence == 0.95
        assert match.extracted_args == {"query": "test"}
        assert match.reasoning == "Test reasoning"
        assert match.language == "en"
        assert match.duration_ms == 50


# Run performance benchmark if called directly
if __name__ == "__main__":
    classifier = ToolIntentClassifier()

    print("Running performance benchmark...")
    print("=" * 60)

    test_messages = [
        "What are your hours?",
        "How much does a cleaning cost?",
        "Are you free tomorrow?",
        "Book me for 2pm",
        "¿Cuándo están abiertos?",
        "¿Cuánto cuesta?",
    ]

    for message in test_messages:
        start = time.time()
        result = classifier.classify(message)
        duration_ms = (time.time() - start) * 1000

        print(f"\nMessage: {message}")
        print(f"Intent: {result.intent.value}")
        print(f"Confidence: {result.confidence:.2f}")
        print(f"Language: {result.language}")
        print(f"Duration: {duration_ms:.1f}ms")

    print("\n" + "=" * 60)
    print("Benchmark complete!")
