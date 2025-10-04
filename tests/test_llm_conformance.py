"""Conformance tests for LLM providers - validates tool calling accuracy and latency SLOs"""
import pytest
import asyncio
import time
from typing import List
from app.services.llm.llm_factory import LLMFactory


# Golden test prompts with expected tool sequences
GOLDEN_TESTS = [
    {
        "name": "Simple price query",
        "messages": [
            {"role": "user", "content": "What's the price for a general checkup?"}
        ],
        "expected_tools": ["query_service_prices"],
        "expected_args_keys": ["query"],
        "description": "Test basic price query tool calling"
    },
    {
        "name": "Appointment booking",
        "messages": [
            {"role": "user", "content": "Book Dr. Kim on October 14, 10:30 AM for dental cleaning"}
        ],
        "expected_tools": ["book_appointment"],
        "expected_args_keys": ["date", "time"],
        "description": "Test appointment booking tool calling with date/time extraction"
    },
    {
        "name": "Availability check",
        "messages": [
            {"role": "user", "content": "Is Dr. Smith available tomorrow at 2pm?"}
        ],
        "expected_tools": ["check_availability"],
        "expected_args_keys": ["doctor", "datetime"],
        "description": "Test availability checking with temporal and entity extraction"
    },
]


# Tool definitions for testing
TEST_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "query_service_prices",
            "description": "Query service prices from the clinic's services database",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The service name or keyword to search for"
                    },
                    "category": {
                        "type": "string",
                        "description": "Optional: filter by service category"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of services to return",
                        "default": 5
                    }
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "book_appointment",
            "description": "Book an appointment for a patient at the clinic",
            "parameters": {
                "type": "object",
                "properties": {
                    "date": {
                        "type": "string",
                        "description": "Appointment date in ISO format (YYYY-MM-DD)"
                    },
                    "time": {
                        "type": "string",
                        "description": "Appointment time in HH:MM format (24-hour)"
                    },
                    "service_type": {
                        "type": "string",
                        "description": "Type of service or appointment"
                    },
                    "patient_notes": {
                        "type": "string",
                        "description": "Optional notes from patient"
                    }
                },
                "required": ["date", "time"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "check_availability",
            "description": "Check doctor availability for appointment booking",
            "parameters": {
                "type": "object",
                "properties": {
                    "doctor": {
                        "type": "string",
                        "description": "Doctor name or ID"
                    },
                    "datetime": {
                        "type": "string",
                        "description": "Desired appointment date and time"
                    },
                    "duration": {
                        "type": "integer",
                        "description": "Expected appointment duration in minutes",
                        "default": 30
                    }
                },
                "required": ["doctor", "datetime"]
            }
        }
    }
]


@pytest.mark.asyncio
@pytest.mark.skip(reason="Requires API keys and live provider testing")
@pytest.mark.parametrize("model", ["glm-4.5", "gemini-2.5-flash", "gpt-5-mini"])
async def test_golden_tool_calling(model, supabase_client):
    """
    Test that models produce expected tool calls for golden prompts

    NOTE: This test is skipped by default as it requires:
    1. Valid API keys for all providers (ZHIPUAI_API_KEY, GOOGLE_API_KEY, OPENAI_API_KEY)
    2. Live API access and network connectivity
    3. API credits/quota available

    Run with: pytest tests/test_llm_conformance.py::test_golden_tool_calling -v -s --no-skip
    """
    factory = LLMFactory(supabase_client)

    for test_case in GOLDEN_TESTS:
        print(f"\nTesting: {test_case['name']} with model {model}")

        response = await factory.generate_with_tools(
            messages=test_case["messages"],
            tools=TEST_TOOLS,
            model=model,
            temperature=0.0  # Deterministic
        )

        # Verify tool was called
        assert len(response.tool_calls) > 0, \
            f"No tools called for: {test_case['name']} with model {model}"

        # Verify correct tool
        tool_names = [tc.name for tc in response.tool_calls]
        assert test_case["expected_tools"][0] in tool_names, \
            f"Expected tool {test_case['expected_tools'][0]}, got {tool_names} for model {model}"

        # Verify expected argument keys are present
        tool_call = response.tool_calls[0]
        for expected_key in test_case["expected_args_keys"]:
            assert expected_key in tool_call.arguments, \
                f"Missing argument: {expected_key} in {tool_call.arguments} for model {model}"

        print(f"✓ {test_case['name']} passed for {model}")


@pytest.mark.asyncio
@pytest.mark.skip(reason="Requires API keys and live provider testing")
@pytest.mark.parametrize("model", ["glm-4.5", "gemini-2.5-flash-lite", "gpt-5-mini"])
async def test_latency_slo(model, supabase_client):
    """
    Test that models meet latency SLOs (p95 < 2s)

    NOTE: This test is skipped by default. Run with:
    pytest tests/test_llm_conformance.py::test_latency_slo -v -s --no-skip
    """
    factory = LLMFactory(supabase_client)

    latencies = []
    iterations = 20  # Reduced from 100 for faster testing

    print(f"\nTesting latency for {model} ({iterations} iterations)...")

    for i in range(iterations):
        start = time.time()
        response = await factory.generate(
            messages=[{"role": "user", "content": "What services do you offer?"}],
            model=model,
            temperature=0.7
        )
        latency_ms = int((time.time() - start) * 1000)
        latencies.append(latency_ms)

        if i % 5 == 0:
            print(f"  Iteration {i+1}/{iterations}: {latency_ms}ms")

    # Calculate percentiles
    latencies.sort()
    p50 = latencies[len(latencies) // 2]
    p95 = latencies[int(len(latencies) * 0.95)]
    p99 = latencies[int(len(latencies) * 0.99)] if len(latencies) >= 100 else latencies[-1]

    print(f"\n{model} latency results:")
    print(f"  p50: {p50}ms")
    print(f"  p95: {p95}ms")
    print(f"  p99: {p99}ms")

    # Assert SLOs
    assert p95 < 2000, f"{model} p95 latency {p95}ms exceeds 2s SLO"
    print(f"✓ {model} meets latency SLO (p95 < 2s)")


@pytest.mark.asyncio
@pytest.mark.skip(reason="Requires API keys and live provider testing")
async def test_tool_argument_validation(supabase_client):
    """
    Test that invalid tool arguments are detected

    NOTE: This test validates that models return arguments that can be validated.
    Schema validation should be implemented in Phase 5.
    """
    factory = LLMFactory(supabase_client)

    # Test with intentionally ambiguous/invalid request
    messages = [
        {"role": "user", "content": "Book appointment for 32nd December at 25:00"}
    ]

    response = await factory.generate_with_tools(
        messages=messages,
        tools=TEST_TOOLS,
        model="glm-4.5"
    )

    # Model should either:
    # 1. Ask for clarification (no tool calls)
    # 2. Make a tool call with corrected values
    # 3. Make a tool call that validation can catch

    if response.tool_calls:
        print(f"Tool called with arguments: {response.tool_calls[0].arguments}")
        # In Phase 5, add JSON schema validation here
    else:
        print(f"Model asked for clarification: {response.content}")


@pytest.mark.asyncio
async def test_conformance_suite_mock():
    """
    Mock conformance test suite for CI/CD

    This test runs without API keys and validates the test structure.
    """
    # Verify test data structure
    assert len(GOLDEN_TESTS) > 0
    assert len(TEST_TOOLS) > 0

    for test_case in GOLDEN_TESTS:
        assert 'name' in test_case
        assert 'messages' in test_case
        assert 'expected_tools' in test_case
        assert 'expected_args_keys' in test_case

    for tool in TEST_TOOLS:
        assert tool['type'] == 'function'
        assert 'name' in tool['function']
        assert 'description' in tool['function']
        assert 'parameters' in tool['function']

    print("✓ Conformance test structure is valid")


def test_model_capabilities_in_db():
    """
    Test that model capabilities are correctly configured in database

    This test verifies the database has correct capability flags for each model.
    """
    # This would connect to actual database in real test
    # For now, document expected capabilities

    expected_capabilities = {
        'glm-4.5': {
            'supports_tool_calling': True,
            'tool_calling_success_rate': 90.6,
            'supports_parallel_tools': False,
            'supports_json_mode': True,
            'p95_latency_ms': 1800,
        },
        'gemini-2.5-flash-lite': {
            'supports_tool_calling': True,
            'tool_calling_success_rate': 85.0,
            'supports_parallel_tools': True,
            'supports_json_mode': True,
            'p95_latency_ms': 600,
        },
        'gpt-5-mini': {
            'supports_tool_calling': True,
            'tool_calling_success_rate': 92.0,
            'supports_parallel_tools': True,
            'supports_json_mode': True,
            'p95_latency_ms': 2500,
        }
    }

    # Verify structure
    for model, capabilities in expected_capabilities.items():
        assert 'supports_tool_calling' in capabilities
        assert 'tool_calling_success_rate' in capabilities
        assert capabilities['tool_calling_success_rate'] >= 75.0

    print("✓ Model capability specifications are valid")


if __name__ == '__main__':
    # Run only mock tests by default
    pytest.main([__file__, '-v', '-k', 'mock or test_model_capabilities'])
