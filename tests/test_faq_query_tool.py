"""
Unit tests for FAQ Query Tool
"""

import pytest
from app.tools.faq_query_tool import FAQQueryTool, query_faqs


@pytest.fixture
def faq_tool():
    """Create FAQ tool instance for testing"""
    # Use test clinic UUID (replace with actual test clinic)
    return FAQQueryTool(clinic_id="00000000-0000-0000-0000-000000000000")


@pytest.mark.asyncio
async def test_search_faqs_english(faq_tool):
    """Test English FAQ search"""
    results = await faq_tool.search_faqs(
        query="What are your hours?",
        language="en",
        limit=3
    )

    # Should return results (assuming sample data exists)
    assert isinstance(results, list)
    if results:
        assert 'question' in results[0]
        assert 'answer' in results[0]
        assert 'relevance_score' in results[0]
        assert results[0]['language'] == 'english'


@pytest.mark.asyncio
async def test_search_faqs_spanish(faq_tool):
    """Test Spanish FAQ search"""
    results = await faq_tool.search_faqs(
        query="¿Cuál es su horario?",
        language="es",
        limit=3
    )

    assert isinstance(results, list)
    if results:
        assert results[0]['language'] == 'spanish'


@pytest.mark.asyncio
async def test_category_filter(faq_tool):
    """Test category filtering"""
    results = await faq_tool.search_faqs(
        query="insurance",
        category="insurance",
        language="en",
        limit=5
    )

    assert isinstance(results, list)
    for result in results:
        assert result['category'] == 'insurance'


@pytest.mark.asyncio
async def test_min_score_threshold(faq_tool):
    """Test minimum score filtering"""
    results = await faq_tool.search_faqs(
        query="random gibberish xyz123",
        min_score=0.5,  # High threshold
        language="en"
    )

    # Should return empty list for nonsense query
    assert results == []


@pytest.mark.asyncio
async def test_featured_faqs(faq_tool):
    """Test featured FAQs retrieval"""
    results = await faq_tool.get_featured_faqs(language="english")

    assert isinstance(results, list)
    for result in results:
        assert result.get('is_featured') == True


@pytest.mark.asyncio
async def test_query_faqs_wrapper():
    """Test LLM wrapper function"""
    result_str = await query_faqs(
        clinic_id="00000000-0000-0000-0000-000000000000",
        query="What are your hours?",
        limit=3
    )

    assert isinstance(result_str, str)
    assert len(result_str) > 0
    # Should contain either results or "No FAQ found"
    assert ("Found" in result_str or "No FAQ" in result_str)


@pytest.mark.asyncio
async def test_error_handling(faq_tool):
    """Test graceful error handling"""
    # Test with invalid parameters
    results = await faq_tool.search_faqs(
        query="",  # Empty query
        language="en"
    )

    # Should return empty list, not raise exception
    assert results == []
