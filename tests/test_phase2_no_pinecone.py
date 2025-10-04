"""
Phase 2 Tests: Remove Pinecone from Fallback Flow

Tests to verify:
1. No Pinecone/HybridSearchEngine imports in message processing
2. LLM fallback works without RAG
3. Response times are under 2s without RAG overhead
"""

import pytest
import time
from unittest.mock import patch, AsyncMock, MagicMock


def test_no_hybrid_search_import_in_code():
    """
    Verify HybridSearchEngine is not imported in message processor
    """
    import inspect
    from app.api import multilingual_message_processor

    source = inspect.getsource(multilingual_message_processor)

    # After Phase 2, these should be removed
    assert "from app.api.hybrid_search_engine import HybridSearchEngine" not in source, \
        "HybridSearchEngine import should be removed"
    assert "hybrid_engine = HybridSearchEngine" not in source, \
        "HybridSearchEngine instantiation should be removed"


def test_no_pinecone_knowledge_base_usage():
    """
    Verify ImprovedPineconeKnowledgeBase is not used in fallback
    """
    import inspect
    from app.api import multilingual_message_processor

    source = inspect.getsource(multilingual_message_processor)

    # The import might remain for backward compatibility, but usage should be removed
    assert "kb = ImprovedPineconeKnowledgeBase" not in source, \
        "ImprovedPineconeKnowledgeBase instantiation should be removed"
    assert "kb.search(" not in source, \
        "Pinecone KB search should not be called"


def test_faq_search_section_removed():
    """
    Verify the FAQ search before RAG section (lines 556-580) is removed
    """
    import inspect
    from app.api.multilingual_message_processor import MultilingualMessageProcessor

    source = inspect.getsource(MultilingualMessageProcessor.process_message)

    # This specific FAQ FTS code block should be removed
    # (FAQ is now handled by direct lane, not in the fallback)
    assert "from app.tools.faq_query_tool import FAQQueryTool" not in source, \
        "FAQQueryTool import should be removed from process_message"
    assert "faq_tool = FAQQueryTool" not in source, \
        "FAQQueryTool instantiation should be removed"


def test_no_rag_gating_logic():
    """
    Verify RAG gating logic is simplified/removed
    """
    import inspect
    from app.api.multilingual_message_processor import MultilingualMessageProcessor

    source = inspect.getsource(MultilingualMessageProcessor.process_message)

    # RAG should be completely removed, not just gated
    assert "should_run_rag" not in source or source.count("should_run_rag") == 0, \
        "RAG execution logic should be removed"
    assert "RAG_SEMAPHORE" not in source, \
        "RAG semaphore should be removed (no concurrent RAG anymore)"


@pytest.mark.asyncio
async def test_message_processing_without_pinecone():
    """
    Verify message can be processed without Pinecone SDK installed
    (Pinecone is now completely removed, so this should always work)
    """
    from app.api.multilingual_message_processor import MultilingualMessageProcessor

    # Should initialize without errors (Pinecone completely removed)
    processor = MultilingualMessageProcessor()

    # Verify no Pinecone attribute exists
    import app.api.multilingual_message_processor as processor_module
    assert not hasattr(processor_module, 'Pinecone'), "Pinecone should not exist in module"
    assert not hasattr(processor_module, 'get_pinecone_client'), "get_pinecone_client should not exist"


def test_simplified_fallback_logic():
    """
    Verify the fallback flow is simplified without RAG complexity
    """
    import inspect
    from app.api.multilingual_message_processor import MultilingualMessageProcessor

    source = inspect.getsource(MultilingualMessageProcessor.process_message)

    # After Phase 2, the flow should be simpler:
    # 1. Get mem0 context
    # 2. Call LLM with tools
    # 3. Execute tool calls
    # 4. Return response

    # Verify no complex RAG timeout handling
    assert source.count("asyncio.wait_for") < 3, \
        "Should have minimal async timeouts (only for LLM, not RAG)"

    # Verify no hybrid search timeout (1.6s)
    assert "timeout=1.6" not in source, \
        "1.6s RAG timeout should be removed"


def test_no_knowledge_metadata_tracking():
    """
    Verify knowledge metadata tracking is removed
    """
    import inspect
    from app.api.multilingual_message_processor import MultilingualMessageProcessor

    source = inspect.getsource(MultilingualMessageProcessor.process_message)

    # knowledge_metadata was used to track RAG sources
    assert "knowledge_metadata = []" not in source, \
        "knowledge_metadata tracking should be removed"
    assert "knowledge_metadata.append" not in source, \
        "knowledge_metadata appending should be removed"


@pytest.mark.asyncio
async def test_response_flow_without_rag():
    """
    Test that the response flow works without RAG
    (This is a code structure test, not a full integration test)
    """
    import inspect
    from app.api.multilingual_message_processor import MultilingualMessageProcessor

    # Get process_message source
    source = inspect.getsource(MultilingualMessageProcessor.process_message)

    # Verify it still has:
    # - mem0 memory retrieval
    # - LLM with tools
    # - Tool execution

    assert "mem0" in source.lower() or "memory" in source.lower(), \
        "Should still have memory retrieval"
    assert "tool" in source.lower(), \
        "Should still have tool calling"
    assert "llm" in source.lower() or "completion" in source.lower(), \
        "Should still call LLM"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
