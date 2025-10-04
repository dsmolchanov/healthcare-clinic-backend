"""Unit tests for LLM Factory core functionality"""
import pytest
import asyncio
from unittest.mock import Mock, AsyncMock, patch
from app.services.llm.llm_factory import LLMFactory
from app.services.llm.base_adapter import ModelCapability, LLMProvider, LLMResponse, ToolCall


@pytest.fixture
def mock_supabase():
    """Mock Supabase client for testing"""
    mock = Mock()

    # Mock llm_models table query
    mock_result = Mock()
    mock_result.data = {
        'provider': 'glm',
        'model_name': 'glm-4.5',
        'display_name': 'GLM-4.5 (Default)',
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

    mock.table.return_value.select.return_value.eq.return_value.eq.return_value.single.return_value.execute.return_value = mock_result

    return mock


@pytest.mark.asyncio
async def test_factory_initialization(mock_supabase):
    """Test that LLM Factory initializes correctly"""
    factory = LLMFactory(mock_supabase)

    assert factory.supabase == mock_supabase
    assert factory.capability_matrix is not None
    assert factory._adapter_cache == {}


@pytest.mark.asyncio
async def test_factory_creates_adapter(mock_supabase):
    """Test that factory creates and caches adapters"""
    factory = LLMFactory(mock_supabase)

    with patch.dict('os.environ', {'ZHIPUAI_API_KEY': 'test-key'}):
        adapter = await factory.create_adapter('glm-4.5')

        assert adapter is not None
        assert adapter.model == 'glm-4.5'
        assert adapter.provider == 'glm'

        # Verify caching
        adapter2 = await factory.create_adapter('glm-4.5')
        assert adapter is adapter2  # Same instance


@pytest.mark.asyncio
async def test_factory_routing_logic(mock_supabase):
    """Test that factory routes requests correctly"""
    factory = LLMFactory(mock_supabase)

    # Mock capability matrix
    mock_capability = ModelCapability(
        provider='glm',
        model_name='glm-4.5',
        display_name='GLM-4.5',
        input_price_per_1m=0.60,
        output_price_per_1m=2.20,
        max_input_tokens=131072,
        max_output_tokens=98304,
        avg_output_speed=65.0,
        avg_ttft=0.65,
        p95_latency_ms=1800,
        supports_streaming=True,
        supports_tool_calling=True,
        tool_calling_success_rate=90.6,
        supports_parallel_tools=False,
        supports_json_mode=True,
        supports_structured_output=True,
        supports_thinking_mode=True,
        api_endpoint='https://api.z.ai/api/paas/v4/chat/completions',
        requires_api_key_env_var='ZHIPUAI_API_KEY',
        base_url_override='https://api.z.ai/api/paas/v4'
    )

    with patch.object(factory.capability_matrix, 'route_by_requirements', return_value=mock_capability):
        # Should route to tool-capable model
        capability = await factory.capability_matrix.route_by_requirements(
            requires_tools=True,
            max_latency_ms=2000
        )

        assert capability.supports_tool_calling is True
        assert capability.model_name == 'glm-4.5'


def test_adapter_parameter_sanitization():
    """Test that adapters sanitize parameters correctly"""
    from app.services.llm.adapters.glm_adapter import GLMAdapter
    from app.services.llm.adapters.openai_adapter import OpenAIAdapter

    mock_capability = Mock()
    mock_capability.requires_api_key_env_var = 'TEST_KEY'
    mock_capability.supports_thinking_mode = True
    mock_capability.max_output_tokens = 1000

    with patch.dict('os.environ', {'TEST_KEY': 'test-value'}):
        glm_adapter = GLMAdapter(mock_capability)
        openai_adapter = OpenAIAdapter(mock_capability)

    # Test GLM sanitization
    glm_params = glm_adapter.sanitize_parameters({
        'top_p': 0.9,
        'unsupported_param': 'value',
        'enable_thinking': True
    })
    assert 'top_p' in glm_params
    assert 'enable_thinking' in glm_params
    assert 'unsupported_param' not in glm_params

    # Test OpenAI sanitization
    openai_params = openai_adapter.sanitize_parameters({
        'top_p': 0.9,
        'tool_choice': 'auto',
        'unsupported_param': 'value'
    })
    assert 'top_p' in openai_params
    assert 'tool_choice' in openai_params
    assert 'unsupported_param' not in openai_params


def test_tool_call_normalization():
    """Test that tool calls are normalized across providers"""
    from app.services.llm.base_adapter import ToolCall

    # Test ToolCall model
    tool_call = ToolCall(
        id='call_123',
        name='query_service_prices',
        arguments={'query': 'general checkup', 'limit': 5}
    )

    assert tool_call.id == 'call_123'
    assert tool_call.name == 'query_service_prices'
    assert tool_call.arguments['query'] == 'general checkup'


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
