"""
Validators for Multiturn and E2E Evaluations

Provides validation logic for:
- Tool chain ordering
- State transitions
- Provider-specific assertions
- Metadata preservation
"""

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)


@dataclass
class ToolChainValidationResult:
    """Result of tool chain validation."""

    chain_complete: bool
    expected_chain: List[str]
    actual_chain: List[str]
    missing_tools: List[str]
    extra_tools: List[str]
    order_correct: bool

    def to_dict(self) -> Dict[str, Any]:
        return {
            "chain_complete": self.chain_complete,
            "expected_chain": self.expected_chain,
            "actual_chain": self.actual_chain,
            "missing_tools": self.missing_tools,
            "extra_tools": self.extra_tools,
            "order_correct": self.order_correct,
        }


class ToolChainValidator:
    """Validate tool calls follow expected chain/sequence."""

    def validate_chain(
        self, expected_chain: List[str], actual_calls: List[Dict[str, Any]]
    ) -> ToolChainValidationResult:
        """
        Validate tool chain ordering.

        Expected tools must appear in order (as subsequence),
        but other tools may be interspersed.

        Args:
            expected_chain: Expected tool names in order
            actual_calls: Actual tool call dictionaries

        Returns:
            ToolChainValidationResult
        """
        # Extract actual tool names
        actual_chain = self._extract_tool_names(actual_calls)

        # Check subsequence (expected tools appear in order)
        expected_idx = 0
        for tool in actual_chain:
            if expected_idx < len(expected_chain) and tool == expected_chain[expected_idx]:
                expected_idx += 1

        chain_complete = expected_idx == len(expected_chain)
        missing_tools = expected_chain[expected_idx:] if not chain_complete else []

        # Check for extra/unexpected tools
        expected_set = set(expected_chain)
        actual_set = set(actual_chain)
        extra_tools = list(actual_set - expected_set)

        # Check if order is strictly correct (no interleaving)
        order_correct = self._check_strict_order(expected_chain, actual_chain)

        return ToolChainValidationResult(
            chain_complete=chain_complete,
            expected_chain=expected_chain,
            actual_chain=actual_chain,
            missing_tools=missing_tools,
            extra_tools=extra_tools,
            order_correct=order_correct,
        )

    def validate_no_duplicates(
        self, tool_calls: List[Dict[str, Any]], allowed_duplicates: Optional[List[str]] = None
    ) -> bool:
        """Check that tools aren't called redundantly."""
        allowed = set(allowed_duplicates or [])
        seen: Set[str] = set()

        for call in tool_calls:
            name = self._get_tool_name(call)
            if name in seen and name not in allowed:
                return False
            seen.add(name)

        return True

    def _extract_tool_names(self, calls: List[Dict[str, Any]]) -> List[str]:
        """Extract tool names from call dictionaries."""
        return [self._get_tool_name(call) for call in calls]

    def _get_tool_name(self, call: Dict[str, Any]) -> str:
        """Get tool name from various call formats."""
        if "name" in call:
            return call["name"]
        if "function" in call:
            return call["function"].get("name", "unknown")
        return "unknown"

    def _check_strict_order(self, expected: List[str], actual: List[str]) -> bool:
        """Check if expected appears in strict order without interleaving."""
        actual_expected = [t for t in actual if t in expected]
        return actual_expected == expected


@dataclass
class StateTransitionResult:
    """Result of state transition validation."""

    valid: bool
    current_state: str
    attempted_state: str
    error: Optional[str] = None


class StateTransitionValidator:
    """Validate conversation state transitions."""

    VALID_TRANSITIONS: Dict[str, List[str]] = {
        "idle": ["gathering_info", "providing_info", "checking_availability"],
        "gathering_info": ["confirming", "gathering_info", "idle", "checking_availability"],
        "checking_availability": ["confirming", "gathering_info", "idle"],
        "providing_info": ["gathering_info", "idle"],
        "confirming": ["booked", "cancelled", "gathering_info"],
        "booked": ["idle"],
        "cancelled": ["idle"],
    }

    def __init__(self, initial_state: str = "idle"):
        self.state_history: List[str] = [initial_state]

    @property
    def current_state(self) -> str:
        return self.state_history[-1]

    def transition(self, new_state: str) -> StateTransitionResult:
        """
        Attempt state transition, return result.

        Args:
            new_state: Target state

        Returns:
            StateTransitionResult indicating success/failure
        """
        current = self.current_state
        valid_next = self.VALID_TRANSITIONS.get(current, [])

        if new_state in valid_next:
            self.state_history.append(new_state)
            return StateTransitionResult(
                valid=True,
                current_state=new_state,
                attempted_state=new_state,
            )

        return StateTransitionResult(
            valid=False,
            current_state=current,
            attempted_state=new_state,
            error=f"Invalid transition from '{current}' to '{new_state}'. "
                  f"Valid transitions: {valid_next}",
        )

    def validate_expected_transitions(
        self, expected: List[Dict[str, str]]
    ) -> Dict[str, bool]:
        """
        Validate state transitions match expected sequence.

        Args:
            expected: List of {"from_state": X, "to_state": Y} dicts

        Returns:
            Dict mapping transition descriptions to success booleans
        """
        results = {}

        for transition in expected:
            from_state = transition["from_state"]
            to_state = transition["to_state"]
            key = f"{from_state}_to_{to_state}"

            # Check if transition occurred in history
            found = False
            for j in range(len(self.state_history) - 1):
                if (
                    self.state_history[j] == from_state
                    and self.state_history[j + 1] == to_state
                ):
                    found = True
                    break

            results[key] = found

        return results

    def reset(self, initial_state: str = "idle") -> None:
        """Reset state history."""
        self.state_history = [initial_state]

    def get_history(self) -> List[str]:
        """Get state transition history."""
        return self.state_history.copy()


@dataclass
class ProviderAssertion:
    """Provider-specific assertion configuration."""

    provider: str
    assertions: Dict[str, Any] = field(default_factory=dict)

    # Gemini-specific
    require_thought_signature: bool = False
    max_thinking_tokens: int = 0

    # OpenAI-specific
    require_parallel_tools: bool = False
    require_structured_output: bool = False

    # GLM-specific
    require_thinking_mode: bool = False


@dataclass
class ProviderAssertionResult:
    """Result of provider assertion validation."""

    provider: str
    assertions_passed: Dict[str, bool]
    all_passed: bool
    errors: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "provider": self.provider,
            "assertions_passed": self.assertions_passed,
            "all_passed": self.all_passed,
            "errors": self.errors,
        }


class ProviderAssertionValidator:
    """Validate provider-specific behaviors."""

    def __init__(self, provider: str):
        self.provider = provider

    def validate_response(
        self,
        response: Any,
        assertions: ProviderAssertion,
    ) -> ProviderAssertionResult:
        """
        Validate response meets provider-specific assertions.

        Args:
            response: LLMResponse object or dict
            assertions: Provider assertions to check

        Returns:
            ProviderAssertionResult
        """
        results: Dict[str, bool] = {}
        errors: List[str] = []

        if self.provider == "gemini":
            gemini_results, gemini_errors = self._validate_gemini(response, assertions)
            results.update(gemini_results)
            errors.extend(gemini_errors)
        elif self.provider == "openai":
            openai_results, openai_errors = self._validate_openai(response, assertions)
            results.update(openai_results)
            errors.extend(openai_errors)
        elif self.provider == "glm":
            glm_results, glm_errors = self._validate_glm(response, assertions)
            results.update(glm_results)
            errors.extend(glm_errors)

        all_passed = all(results.values()) if results else True

        return ProviderAssertionResult(
            provider=self.provider,
            assertions_passed=results,
            all_passed=all_passed,
            errors=errors,
        )

    def _validate_gemini(
        self, response: Any, assertions: ProviderAssertion
    ) -> tuple[Dict[str, bool], List[str]]:
        """Gemini-specific validations."""
        results: Dict[str, bool] = {}
        errors: List[str] = []

        # Check thought_signature preservation
        if assertions.require_thought_signature:
            has_signature = self._check_thought_signature(response)
            results["thought_signature_present"] = has_signature
            if not has_signature:
                errors.append("Gemini thought_signature not found in tool call metadata")

        # Check thinking content not leaked
        content = self._get_response_content(response)
        if content and "<think>" in content:
            results["thinking_filtered"] = False
            errors.append("Gemini thinking content leaked to response")
        else:
            results["thinking_filtered"] = True

        return results, errors

    def _validate_openai(
        self, response: Any, assertions: ProviderAssertion
    ) -> tuple[Dict[str, bool], List[str]]:
        """OpenAI-specific validations."""
        results: Dict[str, bool] = {}
        errors: List[str] = []

        # Check parallel tool calling
        if assertions.require_parallel_tools:
            tool_calls = self._get_tool_calls(response)
            results["parallel_tools_used"] = len(tool_calls) > 1
            if not results["parallel_tools_used"]:
                errors.append("OpenAI parallel tool calling not used")

        return results, errors

    def _validate_glm(
        self, response: Any, assertions: ProviderAssertion
    ) -> tuple[Dict[str, bool], List[str]]:
        """GLM-specific validations."""
        results: Dict[str, bool] = {}
        errors: List[str] = []

        # Check thinking mode if required
        if assertions.require_thinking_mode:
            # GLM thinking mode detection would go here
            results["thinking_mode_active"] = True  # Placeholder

        return results, errors

    def _check_thought_signature(self, response: Any) -> bool:
        """Check if thought_signature exists in tool calls."""
        tool_calls = self._get_tool_calls(response)

        for tc in tool_calls:
            metadata = tc.get("metadata", {})
            if metadata and "thought_signature" in metadata:
                return True

        return False

    def _get_response_content(self, response: Any) -> str:
        """Extract content from response."""
        if hasattr(response, "content"):
            return response.content or ""
        if isinstance(response, dict):
            return response.get("content", "")
        return ""

    def _get_tool_calls(self, response: Any) -> List[Dict[str, Any]]:
        """Extract tool calls from response."""
        if hasattr(response, "tool_calls"):
            return [
                {
                    "name": tc.name if hasattr(tc, "name") else tc.get("name"),
                    "arguments": tc.arguments if hasattr(tc, "arguments") else tc.get("arguments"),
                    "metadata": tc.metadata if hasattr(tc, "metadata") else tc.get("metadata"),
                }
                for tc in response.tool_calls
            ]
        if isinstance(response, dict) and "tool_calls" in response:
            return response["tool_calls"]
        return []


class MetadataPreservationValidator:
    """Validate metadata preservation across turns."""

    def __init__(self):
        self.metadata_history: List[Dict[str, Any]] = []

    def track_metadata(self, metadata: Dict[str, Any]) -> None:
        """Track metadata from a turn."""
        if metadata:
            self.metadata_history.append(metadata)

    def validate_thought_signature_chain(self) -> bool:
        """
        Validate thought_signature is preserved across turns.

        Returns True if:
        1. No thought signatures expected (non-Gemini), OR
        2. Thought signatures appear in metadata history
        """
        if not self.metadata_history:
            return True  # No metadata to validate

        # Check if any metadata contains thought_signature
        has_signature = any(
            "thought_signature" in meta
            for meta in self.metadata_history
        )

        return has_signature

    def validate_tool_id_correlation(
        self, tool_calls: List[Dict[str, Any]], tool_results: List[Dict[str, Any]]
    ) -> bool:
        """Validate tool call IDs match tool result references."""
        call_ids = {tc.get("id") for tc in tool_calls if tc.get("id")}
        result_refs = {
            tr.get("tool_call_id")
            for tr in tool_results
            if tr.get("tool_call_id")
        }

        # All result refs should match call ids
        return result_refs.issubset(call_ids)

    def reset(self) -> None:
        """Reset metadata history."""
        self.metadata_history = []


class ResponseMetadataValidator:
    """Validate LLMResponse metadata fields."""

    REQUIRED_FIELDS = ["provider", "model", "usage", "latency_ms"]
    REQUIRED_USAGE_KEYS = ["input_tokens", "output_tokens", "total_tokens"]
    VALID_PROVIDERS = ["openai", "google", "glm"]
    VALID_MODELS = [
        "gpt-4o",
        "gpt-4o-mini",
        "gemini-3-flash-preview",
        "glm-4.5",
    ]

    def validate(self, response: Any) -> Dict[str, bool]:
        """
        Validate response metadata.

        Args:
            response: LLMResponse or dict

        Returns:
            Dict mapping field names to validation results
        """
        results: Dict[str, bool] = {}

        # Check required fields exist
        for field_name in self.REQUIRED_FIELDS:
            value = self._get_field(response, field_name)
            results[f"{field_name}_exists"] = value is not None

        # Validate provider
        provider = self._get_field(response, "provider")
        results["provider_valid"] = provider in self.VALID_PROVIDERS

        # Validate model
        model = self._get_field(response, "model")
        results["model_valid"] = model in self.VALID_MODELS

        # Validate usage
        usage = self._get_field(response, "usage")
        if isinstance(usage, dict):
            for key in self.REQUIRED_USAGE_KEYS:
                results[f"usage_{key}_exists"] = key in usage
        else:
            for key in self.REQUIRED_USAGE_KEYS:
                results[f"usage_{key}_exists"] = False

        # Validate latency
        latency = self._get_field(response, "latency_ms")
        results["latency_positive"] = (
            isinstance(latency, (int, float)) and latency >= 0
        )

        return results

    def _get_field(self, response: Any, field_name: str) -> Any:
        """Get field from response (object or dict)."""
        if hasattr(response, field_name):
            return getattr(response, field_name)
        if isinstance(response, dict):
            return response.get(field_name)
        return None
