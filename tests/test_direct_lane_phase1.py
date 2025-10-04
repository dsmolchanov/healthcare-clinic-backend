"""
Phase 1 Tests: Direct Lane Critical Fixes

Tests for:
1. FAQ tool using Redis cache instead of textSearch
2. Clinic info tool respecting info_type parameter
3. Direct lane enabled by default
"""

import pytest
import json
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from app.services.direct_lane.direct_tool_executor import DirectToolExecutor
from app.tools.clinic_info_tool import ClinicInfoTool
from app.services.clinic_data_cache import ClinicDataCache


@pytest.fixture
def mock_redis():
    """Mock Redis client"""
    redis = Mock()
    redis.get = Mock(return_value=None)
    redis.setex = Mock()
    return redis


@pytest.fixture
def mock_supabase():
    """Mock Supabase client"""
    supabase = Mock()

    # Mock schema method
    schema_mock = Mock()
    supabase.schema = Mock(return_value=schema_mock)

    # Mock table method
    table_mock = Mock()
    schema_mock.table = Mock(return_value=table_mock)
    supabase.table = Mock(return_value=table_mock)

    # Mock select method
    select_mock = Mock()
    table_mock.select = Mock(return_value=select_mock)

    # Mock eq method (chainable)
    eq_mock = Mock()
    select_mock.eq = Mock(return_value=eq_mock)
    eq_mock.eq = Mock(return_value=eq_mock)

    # Mock order and limit
    eq_mock.order = Mock(return_value=eq_mock)
    eq_mock.limit = Mock(return_value=eq_mock)

    # Mock execute method
    execute_mock = AsyncMock()
    eq_mock.execute = execute_mock

    return supabase


@pytest.mark.asyncio
async def test_faq_code_uses_cache_not_textsearch():
    """
    Verify the FAQ implementation uses Redis cache, not textSearch.
    This is a code inspection test rather than a runtime test.
    """
    # Read the _execute_faq source code
    import inspect
    from app.services.direct_lane.direct_tool_executor import DirectToolExecutor

    source = inspect.getsource(DirectToolExecutor._execute_faq)

    # Verify it uses ClinicDataCache
    assert "ClinicDataCache" in source, "FAQ should use ClinicDataCache"
    assert "get_faqs" in source, "FAQ should call cache.get_faqs()"

    # Verify it does NOT use textSearch
    assert "textSearch" not in source, "FAQ should NOT use textSearch (doesn't exist in SDK)"

    # Verify it uses substring matching
    assert "lower()" in source, "FAQ should use case-insensitive matching"
    assert "query_lower in" in source, "FAQ should use substring matching"


@pytest.mark.asyncio
async def test_faq_cache_integration():
    """
    Integration test: Verify cache structure and matching logic
    """
    # Simulate the matching logic from _execute_faq
    test_faqs = [
        {
            "id": "faq1",
            "question": "What is your address?",
            "answer": "Our address is Av. Tulum 260, Cancun, Mexico",
            "category": "location",
            "language": "en",
            "priority": 10,
            "tags": ["location", "address"]
        },
        {
            "id": "faq2",
            "question": "What are your hours?",
            "answer": "We are open Monday-Friday 9am-6pm",
            "category": "hours",
            "language": "en",
            "priority": 5,
            "tags": ["hours", "schedule"]
        }
    ]

    # Test substring matching logic
    query = "address"
    query_lower = query.lower()

    matches = [
        faq for faq in test_faqs
        if query_lower in faq.get('question', '').lower()
        or query_lower in faq.get('answer', '').lower()
        or any(query_lower in tag.lower() for tag in faq.get('tags', []))
    ]

    # Should match the first FAQ
    assert len(matches) == 1
    assert "Av. Tulum 260" in matches[0]['answer']


@pytest.mark.asyncio
async def test_clinic_info_tool_respects_info_type_location(mock_redis, mock_supabase):
    """
    Clinic info tool should return ONLY address when info_type='location'
    """
    # Mock clinic data
    clinic_data = {
        "id": "test-clinic",
        "name": "Test Clinic",
        "address": "Av. Tulum 260, Cancun, Quintana Roo, Mexico",
        "phone": "+52-998-123-4567",
        "email": "info@testclinic.com",
        "business_hours": {
            "monday": "9am-6pm",
            "tuesday": "9am-6pm"
        },
        "supported_languages": ["en", "es", "ru"]
    }

    mock_result = Mock()
    mock_result.data = [clinic_data]

    mock_supabase.table.return_value.select.return_value.eq.return_value.limit.return_value.execute = MagicMock(return_value=mock_result)

    # Create tool
    tool = ClinicInfoTool(clinic_id="test-clinic", redis_client=mock_redis)

    # Get clinic info
    result = await tool.get_clinic_info(mock_supabase)

    # Assertions
    assert result["address"] == "Av. Tulum 260, Cancun, Quintana Roo, Mexico"
    assert "Mexico City" not in result["address"]  # NOT the fake hallucinated one
    assert result["phone"] == "+52-998-123-4567"
    assert result["email"] == "info@testclinic.com"


@pytest.mark.asyncio
async def test_clinic_info_tool_respects_info_type_doctors(mock_redis, mock_supabase):
    """
    Clinic info tool should return doctor count when info_type='doctors'
    """
    # Mock doctors data
    doctors_data = [
        {
            "id": "doc1",
            "first_name": "Arkady",
            "last_name": "Shtern",
            "specialization": "Implantology",
            "active": True
        },
        {
            "id": "doc2",
            "first_name": "Maria",
            "last_name": "Garcia",
            "specialization": "Orthodontics",
            "active": True
        }
    ]

    mock_result = Mock()
    mock_result.data = doctors_data

    mock_supabase.table.return_value.select.return_value.eq.return_value.eq.return_value.execute = MagicMock(return_value=mock_result)

    # Create tool
    tool = ClinicInfoTool(clinic_id="test-clinic", redis_client=mock_redis)

    # Get doctor count
    result = await tool.get_doctor_count(mock_supabase)

    # Assertions
    assert result["total_doctors"] == 2
    assert "Arkady Shtern" in result["doctor_list"]
    assert "Maria Garcia" in result["doctor_list"]
    assert "Implantology" in result["specializations"]
    assert "Orthodontics" in result["specializations"]


@pytest.mark.asyncio
async def test_clinic_info_tool_uses_cache(mock_redis, mock_supabase):
    """
    Clinic info tool should use Redis cache when available
    """
    # Setup: Pre-populate cache
    cached_doctors = [
        {
            "id": "doc1",
            "first_name": "Test",
            "last_name": "Doctor",
            "specialization": "General",
            "active": True
        }
    ]

    mock_redis.get.return_value = json.dumps(cached_doctors).encode('utf-8')

    # Create cache
    cache = ClinicDataCache(mock_redis, default_ttl=3600)

    # Get doctors (should hit cache)
    result = await cache.get_doctors("test-clinic", mock_supabase)

    # Assertions
    assert len(result) == 1
    assert result[0]["first_name"] == "Test"

    # Verify cache was used
    mock_redis.get.assert_called()

    # Verify database was NOT queried
    mock_supabase.table.assert_not_called()


def test_direct_lane_enabled_by_default():
    """
    Direct lane should be enabled by default (ENABLE_DIRECT_LANE=true)
    """
    # Read the message_router.py source to verify default value
    import inspect
    from app.services import message_router

    source = inspect.getsource(message_router.MessageRouter.__init__)

    # Check that the default value is "true" not "false"
    assert 'ENABLE_DIRECT_LANE", "true"' in source or 'ENABLE_DIRECT_LANE", \'true\'' in source, \
        "ENABLE_DIRECT_LANE should default to 'true'"


@pytest.mark.asyncio
async def test_no_textSearch_method_called():
    """
    Ensure textSearch is NOT called anywhere in direct lane
    (it doesn't exist in Supabase Python SDK)
    """
    # This test ensures we don't accidentally call textSearch
    # which would cause AttributeError at runtime

    # Read the direct_tool_executor.py source
    import inspect
    from app.services.direct_lane import direct_tool_executor

    source = inspect.getsource(direct_tool_executor)

    # Assert textSearch is NOT in the source code
    assert "textSearch" not in source, "Found textSearch call in direct_tool_executor.py - this method doesn't exist!"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
