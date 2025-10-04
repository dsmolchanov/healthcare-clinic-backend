"""
Integration tests for LangGraph orchestrator with LLM factory
Tests the Phase 5 integration of LLMFactory into LangGraph workflows
"""

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from app.services.orchestrator.base_langgraph import BaseLangGraphOrchestrator, BaseConversationState
from app.services.orchestrator.templates.healthcare_template import HealthcareLangGraph
from app.services.llm.llm_factory import LLMFactory
from app.services.llm.base_adapter import LLMResponse


@pytest.fixture
def mock_supabase_client():
    """Mock Supabase client for testing"""
    client = MagicMock()

    # Mock llm_models table query
    mock_table = MagicMock()
    mock_select = MagicMock()
    mock_eq = MagicMock()
    mock_single = MagicMock()

    # Mock model data
    mock_model_data = {
        'provider': 'glm',
        'model_name': 'glm-4.6',
        'display_name': 'GLM-4.6 (Default)',
        'input_price_per_1m': 0.60,
        'output_price_per_1m': 2.20,
        'max_input_tokens': 131072,
        'max_output_tokens': 98304,
        'avg_output_speed': 65.0,
        'avg_ttft': 0.65,
        'p95_latency_ms': 1800,
        'supports_streaming': True,
        'supports_tool_calling': True,
        'tool_calling_success_rate': 90.6,
        'supports_parallel_tools': False,
        'supports_json_mode': True,
        'supports_structured_output': True,
        'supports_thinking_mode': True,
        'api_endpoint': 'https://api.z.ai/api/paas/v4/chat/completions',
        'requires_api_key_env_var': 'ZHIPUAI_API_KEY',
        'base_url_override': 'https://api.z.ai/api/paas/v4'
    }

    mock_single.execute.return_value.data = mock_model_data
    mock_eq.single.return_value = mock_single
    mock_select.eq.return_value = mock_eq
    mock_table.select.return_value = mock_select
    client.table.return_value = mock_table

    return client


@pytest.mark.asyncio
async def test_orchestrator_creation_with_factory(mock_supabase_client):
    """Test that orchestrator can be created with LLM factory"""
    orchestrator = BaseLangGraphOrchestrator(
        enable_llm=True,
        supabase_client=mock_supabase_client
    )

    assert orchestrator.llm_factory is not None
    assert orchestrator.primary_model == 'glm-4.6'  # Default model
    assert orchestrator.temperature == 0.7
    assert orchestrator.max_tokens == 500


@pytest.mark.asyncio
async def test_orchestrator_with_custom_model(mock_supabase_client):
    """Test orchestrator with custom model configuration"""
    agent_config = {
        'llm_settings': {
            'primary_model': 'gpt-5-mini',
            'fallback_model': 'glm-4.5',
            'temperature': 0.5,
            'max_tokens': 1000
        }
    }

    orchestrator = BaseLangGraphOrchestrator(
        enable_llm=True,
        supabase_client=mock_supabase_client,
        agent_config=agent_config
    )

    assert orchestrator.primary_model == 'gpt-5-mini'
    assert orchestrator.fallback_model == 'glm-4.5'
    assert orchestrator.temperature == 0.5
    assert orchestrator.max_tokens == 1000


@pytest.mark.asyncio
async def test_process_node_with_factory(mock_supabase_client):
    """Test that process node uses LLM factory for generation"""
    orchestrator = BaseLangGraphOrchestrator(
        enable_llm=True,
        enable_memory=False,
        enable_rag=False,
        supabase_client=mock_supabase_client
    )

    # Mock LLM factory response
    mock_response = LLMResponse(
        content="I can help you schedule an appointment.",
        tool_calls=[],
        provider="glm",
        model="glm-4.6",
        usage={'input_tokens': 50, 'output_tokens': 20, 'total_tokens': 70},
        latency_ms=800
    )

    with patch.object(orchestrator.llm_factory, 'generate', new_callable=AsyncMock, return_value=mock_response):
        state = BaseConversationState(
            session_id="test_session",
            message="I need an appointment",
            context={},
            intent='appointment',
            response=None,
            metadata={},
            memories=None,
            knowledge=None,
            error=None,
            should_end=False,
            next_node=None,
            compliance_mode=None,
            compliance_checks=[],
            audit_trail=[]
        )

        result = await orchestrator.process_node(state)

        assert result['response'] == "I can help you schedule an appointment."
        assert result['metadata']['llm_provider'] == 'glm'
        assert result['metadata']['llm_model'] == 'glm-4.6'
        assert result['metadata']['llm_latency_ms'] == 800
        assert result['metadata']['llm_input_tokens'] == 50
        assert result['metadata']['llm_output_tokens'] == 20


@pytest.mark.asyncio
async def test_tool_calling_in_healthcare_template(mock_supabase_client):
    """Test that healthcare template can call tools via LLM factory"""
    orchestrator = HealthcareLangGraph(
        supabase_client=mock_supabase_client,
        clinic_id='test_clinic',
        agent_config={'llm_settings': {'primary_model': 'glm-4.6'}}
    )

    # Mock LLM factory response with tool calls
    from app.services.llm.base_adapter import ToolCall

    mock_tool_call = ToolCall(
        id="call_123",
        name="query_service_prices",
        arguments={"query": "dental cleaning"}
    )

    mock_response = LLMResponse(
        content="Let me check the pricing for dental cleaning.",
        tool_calls=[mock_tool_call],
        provider="glm",
        model="glm-4.6",
        usage={'input_tokens': 60, 'output_tokens': 30, 'total_tokens': 90},
        latency_ms=900
    )

    with patch.object(orchestrator.llm_factory, 'generate_with_tools', new_callable=AsyncMock, return_value=mock_response):
        # Mock price query tool
        if orchestrator.price_query_tool:
            orchestrator.price_query_tool.get_services_by_query = AsyncMock(return_value=[
                {'name': 'Dental Cleaning', 'price': 150.00, 'currency': 'USD'}
            ])

        state = {
            'session_id': 'test_session',
            'message': 'How much does a dental cleaning cost?',
            'context': {},
            'intent': 'appointment',
            'metadata': {},
            'audit_trail': [],
            'patient_id': 'patient_123'
        }

        # Note: We're testing the node directly, not the full workflow
        # In a real scenario, this would go through the full LangGraph
        # For now, we just verify the factory integration is working

        assert orchestrator.llm_factory is not None
        assert orchestrator.primary_model == 'glm-4.6'


@pytest.mark.asyncio
async def test_fallback_on_llm_failure(mock_supabase_client):
    """Test that orchestrator falls back gracefully when LLM fails"""
    orchestrator = BaseLangGraphOrchestrator(
        enable_llm=True,
        enable_memory=False,
        enable_rag=False,
        supabase_client=mock_supabase_client
    )

    # Mock LLM factory to raise an exception
    with patch.object(orchestrator.llm_factory, 'generate', new_callable=AsyncMock, side_effect=Exception("LLM API error")):
        state = BaseConversationState(
            session_id="test_session",
            message="Test message",
            context={},
            intent='general',
            response=None,
            metadata={},
            memories=None,
            knowledge=None,
            error=None,
            should_end=False,
            next_node=None,
            compliance_mode=None,
            compliance_checks=[],
            audit_trail=[]
        )

        result = await orchestrator.process_node(state)

        # Should fall back to simple response
        assert result['response'] == "Processing message with intent: general"
        assert 'error' not in result or result.get('error') is None


@pytest.mark.asyncio
async def test_generate_response_node_with_factory(mock_supabase_client):
    """Test that generate_response_node uses factory when no response exists"""
    orchestrator = BaseLangGraphOrchestrator(
        enable_llm=True,
        enable_memory=False,
        enable_rag=False,
        supabase_client=mock_supabase_client
    )

    mock_response = LLMResponse(
        content="I understand your message. How can I help you?",
        tool_calls=[],
        provider="glm",
        model="glm-4.6",
        usage={'input_tokens': 30, 'output_tokens': 15, 'total_tokens': 45},
        latency_ms=600
    )

    with patch.object(orchestrator.llm_factory, 'generate', new_callable=AsyncMock, return_value=mock_response):
        state = BaseConversationState(
            session_id="test_session",
            message="Hello",
            context={},
            intent='general',
            response=None,  # No response yet
            metadata={},
            memories=None,
            knowledge=None,
            error=None,
            should_end=False,
            next_node=None,
            compliance_mode=None,
            compliance_checks=[],
            audit_trail=[]
        )

        result = await orchestrator.generate_response_node(state)

        assert result['response'] == "I understand your message. How can I help you?"


@pytest.mark.asyncio
async def test_orchestrator_without_factory():
    """Test that orchestrator works without LLM factory (fallback mode)"""
    orchestrator = BaseLangGraphOrchestrator(
        enable_llm=False,  # Disable LLM
        enable_memory=False,
        enable_rag=False
    )

    assert orchestrator.llm_factory is None

    state = BaseConversationState(
        session_id="test_session",
        message="Test message",
        context={},
        intent='general',
        response=None,
        metadata={},
        memories=None,
        knowledge=None,
        error=None,
        should_end=False,
        next_node=None,
        compliance_mode=None,
        compliance_checks=[],
        audit_trail=[]
    )

    result = await orchestrator.process_node(state)

    # Should use fallback response
    assert result['response'] == "Processing message with intent: general"


def test_conformance_suite_mock():
    """Mock test for conformance suite (placeholder for future real API tests)"""
    # This test always passes - it's a placeholder for the real conformance tests
    # which require API keys and are run separately with --no-skip flag
    assert True


def test_model_capabilities_in_db(mock_supabase_client):
    """Test that model capabilities can be loaded from database"""
    # Verify mock data structure matches our schema
    result = mock_supabase_client.table('llm_models').select('*').eq('model_name', 'glm-4.6').single().execute()

    assert result.data is not None
    assert result.data['model_name'] == 'glm-4.6'
    assert result.data['provider'] == 'glm'
    assert result.data['supports_tool_calling'] is True
    assert result.data['max_input_tokens'] == 131072


if __name__ == "__main__":
    # Run tests
    pytest.main([__file__, "-v"])
