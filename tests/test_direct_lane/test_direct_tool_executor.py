"""
Comprehensive tests for DirectToolExecutor

Tests cover:
- Tool execution with mocked Supabase client
- Budget enforcement and timeouts
- Circuit breaker integration
- Error handling and fallback
- Two-phase booking saga
"""

import pytest
import asyncio
import time
from unittest.mock import Mock, AsyncMock, MagicMock, patch
from app.services.direct_lane.direct_tool_executor import DirectToolExecutor
from app.services.direct_lane.tool_intent_classifier import ToolIntentMatch, DirectToolIntent
from app.services.direct_lane.circuit_breaker import CircuitBreaker


class MockSupabaseResponse:
    """Mock Supabase response object"""
    def __init__(self, data, error=None):
        self.data = data
        self.error = error


class MockSupabaseClient:
    """Mock Supabase client for testing"""
    def __init__(self):
        self.from_ = Mock(return_value=self)
        self.select = Mock(return_value=self)
        self.eq = Mock(return_value=self)
        self.ilike = Mock(return_value=self)
        self.limit = Mock(return_value=self)
        self.textSearch = Mock(return_value=self)
        self.execute = AsyncMock()
        self.rpc = Mock(return_value=self)


@pytest.fixture
def mock_supabase():
    """Create mock Supabase client"""
    return MockSupabaseClient()


@pytest.fixture
def executor(mock_supabase):
    """Create DirectToolExecutor with mocked dependencies"""
    return DirectToolExecutor(
        clinic_id="test-clinic-123",
        supabase_client=mock_supabase
    )


class TestDirectToolExecutor:
    """Test suite for DirectToolExecutor"""

    # ==================== FAQ Execution Tests ====================

    @pytest.mark.asyncio
    async def test_execute_faq_success(self, executor, mock_supabase):
        """Test successful FAQ query execution"""
        # Setup mock response
        mock_supabase.execute.return_value = MockSupabaseResponse(data=[
            {
                "question": "What are your hours?",
                "answer": "We are open Mon-Fri 9am-5pm"
            }
        ])

        # Create intent match
        tool_match = ToolIntentMatch(
            intent=DirectToolIntent.FAQ_QUERY,
            confidence=0.9,
            extracted_args={"query": "hours", "language": "en"},
            reasoning="FAQ pattern matched",
            language="en"
        )

        # Execute
        result = await executor.execute_tool(tool_match)

        # Verify
        assert result["success"] is True
        assert "response" in result
        assert "What are your hours?" in result["response"]
        assert result["tool_used"] == "faq_query"
        assert result["latency_ms"] < 1000

    @pytest.mark.asyncio
    async def test_execute_faq_no_results(self, executor, mock_supabase):
        """Test FAQ query with no results"""
        # Setup mock response (empty)
        mock_supabase.execute.return_value = MockSupabaseResponse(data=[])

        tool_match = ToolIntentMatch(
            intent=DirectToolIntent.FAQ_QUERY,
            confidence=0.9,
            extracted_args={"query": "unknown", "language": "en"},
            reasoning="FAQ pattern matched",
            language="en"
        )

        result = await executor.execute_tool(tool_match)

        assert result["success"] is True
        assert "couldn't find" in result["response"].lower()

    # ==================== Price Query Tests ====================

    @pytest.mark.asyncio
    async def test_execute_price_query_success(self, executor, mock_supabase):
        """Test successful price query execution"""
        # Setup mock response
        mock_supabase.execute.return_value = MockSupabaseResponse(data=[
            {
                "name": "Dental Cleaning",
                "base_price": 120.00,
                "description": "Professional teeth cleaning"
            },
            {
                "name": "Deep Cleaning",
                "base_price": 250.00,
                "description": "Deep cleaning with scaling"
            }
        ])

        tool_match = ToolIntentMatch(
            intent=DirectToolIntent.PRICE_QUERY,
            confidence=0.85,
            extracted_args={"query": "cleaning", "language": "en"},
            reasoning="Price pattern matched",
            language="en"
        )

        result = await executor.execute_tool(tool_match)

        assert result["success"] is True
        assert "Dental Cleaning" in result["response"]
        assert "$120.00" in result["response"]
        assert result["metadata"]["services_found"] == 2

    @pytest.mark.asyncio
    async def test_execute_price_query_no_results(self, executor, mock_supabase):
        """Test price query with no matching services"""
        mock_supabase.execute.return_value = MockSupabaseResponse(data=[])

        tool_match = ToolIntentMatch(
            intent=DirectToolIntent.PRICE_QUERY,
            confidence=0.85,
            extracted_args={"query": "nonexistent", "language": "en"},
            reasoning="Price pattern matched",
            language="en"
        )

        result = await executor.execute_tool(tool_match)

        assert result["success"] is True
        assert "No services found" in result["response"]

    # ==================== Availability Check Tests ====================

    @pytest.mark.asyncio
    async def test_execute_availability_check_success(self, executor, mock_supabase):
        """Test successful availability check"""
        # Setup mock RPC response
        mock_supabase.execute.return_value = MockSupabaseResponse(data=[
            {"slot_start": "2025-10-15T10:00:00", "slot_end": "2025-10-15T10:30:00"},
            {"slot_start": "2025-10-15T11:00:00", "slot_end": "2025-10-15T11:30:00"},
            {"slot_start": "2025-10-15T14:00:00", "slot_end": "2025-10-15T14:30:00"},
        ])

        tool_match = ToolIntentMatch(
            intent=DirectToolIntent.CHECK_AVAILABILITY,
            confidence=0.8,
            extracted_args={"date": "2025-10-15", "query": "availability", "language": "en"},
            reasoning="Availability pattern matched",
            language="en"
        )

        result = await executor.execute_tool(tool_match)

        assert result["success"] is True
        assert "Available slots" in result["response"]
        assert result["metadata"]["slots_count"] == 3

    @pytest.mark.asyncio
    async def test_execute_availability_no_slots(self, executor, mock_supabase):
        """Test availability check with no available slots"""
        mock_supabase.execute.return_value = MockSupabaseResponse(data=[])

        tool_match = ToolIntentMatch(
            intent=DirectToolIntent.CHECK_AVAILABILITY,
            confidence=0.8,
            extracted_args={"date": "2025-10-15", "query": "availability", "language": "en"},
            reasoning="Availability pattern matched",
            language="en"
        )

        result = await executor.execute_tool(tool_match)

        assert result["success"] is True
        assert "No available slots" in result["response"]

    @pytest.mark.asyncio
    async def test_execute_availability_missing_date(self, executor, mock_supabase):
        """Test availability check without date"""
        tool_match = ToolIntentMatch(
            intent=DirectToolIntent.CHECK_AVAILABILITY,
            confidence=0.6,
            extracted_args={"query": "availability", "language": "en"},
            reasoning="Availability pattern matched",
            language="en"
        )

        result = await executor.execute_tool(tool_match)

        assert result["success"] is False
        assert "specify a date" in result["response"].lower()

    # ==================== Booking Tests ====================

    @pytest.mark.asyncio
    async def test_execute_booking_success(self, executor, mock_supabase):
        """Test successful booking with two-phase saga"""
        # Mock hold creation response
        hold_response = MockSupabaseResponse(data={
            "success": True,
            "hold_id": "hold-123"
        })

        # Mock confirm response
        confirm_response = MockSupabaseResponse(data={
            "success": True,
            "appointment_id": "appt-456"
        })

        # Setup mock to return different responses
        mock_supabase.execute.side_effect = [hold_response, confirm_response]

        tool_match = ToolIntentMatch(
            intent=DirectToolIntent.BOOK_APPOINTMENT,
            confidence=0.9,
            extracted_args={
                "selected_slot": {
                    "slot_id": "slot-789",
                    "doctor_id": "doc-123",
                    "room_id": "room-456",
                    "slot_start": "2025-10-15T10:00:00",
                    "slot_end": "2025-10-15T10:30:00"
                }
            },
            reasoning="Booking confirmation",
            language="en"
        )

        context = {
            "session_id": "session-123",
            "patient_id": "patient-789",
            "service_id": "service-456"
        }

        result = await executor.execute_tool(tool_match, context=context)

        assert result["success"] is True
        assert "booked" in result["response"].lower()
        assert result["metadata"]["appointment_id"] == "appt-456"
        assert result["metadata"]["calendar_sync"] == "queued"

    @pytest.mark.asyncio
    async def test_execute_booking_hold_failed(self, executor, mock_supabase):
        """Test booking when hold creation fails"""
        # Mock failed hold response
        hold_response = MockSupabaseResponse(data={
            "success": False,
            "message": "Slot already taken"
        })

        mock_supabase.execute.return_value = hold_response

        tool_match = ToolIntentMatch(
            intent=DirectToolIntent.BOOK_APPOINTMENT,
            confidence=0.9,
            extracted_args={
                "selected_slot": {
                    "slot_id": "slot-789",
                    "doctor_id": "doc-123",
                    "room_id": "room-456",
                    "slot_start": "2025-10-15T10:00:00",
                    "slot_end": "2025-10-15T10:30:00"
                }
            },
            reasoning="Booking confirmation",
            language="en"
        )

        result = await executor.execute_tool(tool_match)

        assert result["success"] is False
        assert "already taken" in result["response"].lower()

    @pytest.mark.asyncio
    async def test_execute_booking_missing_slot(self, executor, mock_supabase):
        """Test booking without selected slot"""
        tool_match = ToolIntentMatch(
            intent=DirectToolIntent.BOOK_APPOINTMENT,
            confidence=0.5,
            extracted_args={},
            reasoning="Booking pattern",
            language="en"
        )

        result = await executor.execute_tool(tool_match)

        assert result["success"] is False
        assert "select a time slot" in result["response"].lower()

    # ==================== Circuit Breaker Tests ====================

    @pytest.mark.asyncio
    async def test_circuit_breaker_opens_after_failures(self, executor, mock_supabase):
        """Test circuit breaker opens after consecutive failures"""
        # Make execute fail
        mock_supabase.execute.side_effect = Exception("Database error")

        tool_match = ToolIntentMatch(
            intent=DirectToolIntent.FAQ_QUERY,
            confidence=0.9,
            extracted_args={"query": "test", "language": "en"},
            reasoning="FAQ pattern",
            language="en"
        )

        # Record 5 failures
        for i in range(5):
            result = await executor.execute_tool(tool_match)
            assert result["success"] is False

        # Circuit should now be open
        result = await executor.execute_tool(tool_match)
        assert result["fallback_triggered"] is True
        assert "Circuit breaker is open" in result.get("error", "")

    @pytest.mark.asyncio
    async def test_circuit_breaker_per_tool(self, executor, mock_supabase):
        """Test circuit breaker is independent per tool"""
        # Make FAQ fail
        mock_supabase.execute.side_effect = Exception("Database error")

        faq_match = ToolIntentMatch(
            intent=DirectToolIntent.FAQ_QUERY,
            confidence=0.9,
            extracted_args={"query": "test", "language": "en"},
            reasoning="FAQ pattern",
            language="en"
        )

        # Open FAQ circuit
        for i in range(5):
            await executor.execute_tool(faq_match)

        # Make price succeed
        mock_supabase.execute.side_effect = None
        mock_supabase.execute.return_value = MockSupabaseResponse(data=[])

        price_match = ToolIntentMatch(
            intent=DirectToolIntent.PRICE_QUERY,
            confidence=0.85,
            extracted_args={"query": "test", "language": "en"},
            reasoning="Price pattern",
            language="en"
        )

        # Price should still work
        result = await executor.execute_tool(price_match)
        assert result["success"] is True

    # ==================== Budget Enforcement Tests ====================

    @pytest.mark.asyncio
    async def test_timeout_enforcement(self, executor, mock_supabase):
        """Test execution timeout enforcement"""
        # Make execute take too long
        async def slow_execute(*args, **kwargs):
            await asyncio.sleep(2)  # Exceeds 800ms budget
            return MockSupabaseResponse(data=[])

        mock_supabase.execute = slow_execute

        tool_match = ToolIntentMatch(
            intent=DirectToolIntent.FAQ_QUERY,
            confidence=0.9,
            extracted_args={"query": "test", "language": "en"},
            reasoning="FAQ pattern",
            language="en"
        )

        result = await executor.execute_tool(tool_match)

        assert result["success"] is False
        assert "Budget exceeded" in result.get("error", "")
        assert result["fallback_triggered"] is True

    @pytest.mark.asyncio
    async def test_latency_tracking(self, executor, mock_supabase):
        """Test latency is tracked correctly"""
        mock_supabase.execute.return_value = MockSupabaseResponse(data=[])

        tool_match = ToolIntentMatch(
            intent=DirectToolIntent.FAQ_QUERY,
            confidence=0.9,
            extracted_args={"query": "test", "language": "en"},
            reasoning="FAQ pattern",
            language="en"
        )

        result = await executor.execute_tool(tool_match)

        assert "latency_ms" in result
        assert result["latency_ms"] > 0
        assert result["latency_ms"] < 1000  # Should be fast

    # ==================== Error Handling Tests ====================

    @pytest.mark.asyncio
    async def test_handles_supabase_error(self, executor, mock_supabase):
        """Test graceful handling of Supabase errors"""
        mock_supabase.execute.side_effect = Exception("Connection timeout")

        tool_match = ToolIntentMatch(
            intent=DirectToolIntent.FAQ_QUERY,
            confidence=0.9,
            extracted_args={"query": "test", "language": "en"},
            reasoning="FAQ pattern",
            language="en"
        )

        result = await executor.execute_tool(tool_match)

        assert result["success"] is False
        assert result["fallback_triggered"] is True
        assert "error" in result

    @pytest.mark.asyncio
    async def test_handles_unknown_intent(self, executor, mock_supabase):
        """Test handling of unknown intent"""
        tool_match = ToolIntentMatch(
            intent=DirectToolIntent.UNKNOWN,
            confidence=0.0,
            extracted_args={},
            reasoning="No pattern matched",
            language="en"
        )

        result = await executor.execute_tool(tool_match)

        assert result["success"] is False
        assert "Unknown tool intent" in result["error"]

    # ==================== Metadata Tests ====================

    @pytest.mark.asyncio
    async def test_includes_metadata(self, executor, mock_supabase):
        """Test result includes proper metadata"""
        mock_supabase.execute.return_value = MockSupabaseResponse(data=[])

        tool_match = ToolIntentMatch(
            intent=DirectToolIntent.FAQ_QUERY,
            confidence=0.9,
            extracted_args={"query": "test", "language": "en"},
            reasoning="FAQ pattern",
            language="en"
        )

        result = await executor.execute_tool(tool_match)

        assert result["tool_used"] == "faq_query"
        assert result["routing_path"] == "direct_function_call"
        assert result["fallback_triggered"] is False
        assert "latency_ms" in result


class TestDirectToolExecutorIntegration:
    """Integration tests for DirectToolExecutor"""

    @pytest.mark.asyncio
    async def test_full_booking_flow(self, mock_supabase):
        """Test complete booking flow from availability to confirmation"""
        executor = DirectToolExecutor(
            clinic_id="test-clinic",
            supabase_client=mock_supabase
        )

        # Step 1: Check availability
        avail_response = MockSupabaseResponse(data=[
            {"slot_start": "2025-10-15T10:00:00", "slot_end": "2025-10-15T10:30:00"},
        ])
        mock_supabase.execute.return_value = avail_response

        avail_match = ToolIntentMatch(
            intent=DirectToolIntent.CHECK_AVAILABILITY,
            confidence=0.8,
            extracted_args={"date": "2025-10-15", "query": "availability", "language": "en"},
            reasoning="Availability check",
            language="en"
        )

        avail_result = await executor.execute_tool(avail_match)
        assert avail_result["success"] is True

        # Step 2: Book the slot
        hold_response = MockSupabaseResponse(data={"success": True, "hold_id": "hold-123"})
        confirm_response = MockSupabaseResponse(data={"success": True, "appointment_id": "appt-456"})
        mock_supabase.execute.side_effect = [hold_response, confirm_response]

        book_match = ToolIntentMatch(
            intent=DirectToolIntent.BOOK_APPOINTMENT,
            confidence=0.9,
            extracted_args={
                "selected_slot": {
                    "slot_id": "slot-1",
                    "doctor_id": "doc-1",
                    "room_id": "room-1",
                    "slot_start": "2025-10-15T10:00:00",
                    "slot_end": "2025-10-15T10:30:00"
                }
            },
            reasoning="Booking confirmation",
            language="en"
        )

        book_result = await executor.execute_tool(book_match, context={"session_id": "sess-1"})
        assert book_result["success"] is True
        assert "appointment_id" in book_result["metadata"]


# Run manual test if called directly
if __name__ == "__main__":
    print("DirectToolExecutor tests require pytest to run")
    print("Run: pytest tests/test_direct_lane/test_direct_tool_executor.py -v")
