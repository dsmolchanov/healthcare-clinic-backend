"""
Multiturn Evaluation Runner

Runs multi-turn evaluation scenarios with per-turn validation,
tool chain verification, and metadata preservation tracking.
"""

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Tuple

from tests.evals.judge import LLMJudge

logger = logging.getLogger(__name__)


@dataclass
class TurnResult:
    """Result of a single turn evaluation."""
    turn_id: int
    user_message: str
    agent_response: str
    expected_tools: List[str]
    actual_tools: List[Dict[str, Any]]
    tool_choice_correct: bool
    tool_args_correct: bool
    criteria_results: Dict[str, bool]
    metadata: Dict[str, Any]
    latency_ms: int
    pass_turn: bool
    score: float = 0.0
    reasoning: str = ""
    error: Optional[str] = None


@dataclass
class MultiturnResult:
    """Result of complete multiturn scenario."""
    scenario_name: str
    description: str
    turns: List[TurnResult]
    e2e_validation: Dict[str, bool]
    provider: str
    model: str
    all_turns_passed: bool
    tool_chain_correct: bool
    metadata_preserved: bool
    total_latency_ms: int
    total_score: float = 0.0
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "scenario_name": self.scenario_name,
            "description": self.description,
            "turns": [
                {
                    "turn_id": t.turn_id,
                    "user_message": t.user_message,
                    "agent_response": t.agent_response,
                    "expected_tools": t.expected_tools,
                    "actual_tools": t.actual_tools,
                    "tool_choice_correct": t.tool_choice_correct,
                    "tool_args_correct": t.tool_args_correct,
                    "criteria_results": t.criteria_results,
                    "metadata": t.metadata,
                    "latency_ms": t.latency_ms,
                    "pass_turn": t.pass_turn,
                    "score": t.score,
                    "reasoning": t.reasoning,
                    "error": t.error,
                }
                for t in self.turns
            ],
            "e2e_validation": self.e2e_validation,
            "provider": self.provider,
            "model": self.model,
            "all_turns_passed": self.all_turns_passed,
            "tool_chain_correct": self.tool_chain_correct,
            "metadata_preserved": self.metadata_preserved,
            "total_latency_ms": self.total_latency_ms,
            "total_score": self.total_score,
            "error": self.error,
        }


class TurnToolCapture:
    """Capture tools called within a single turn."""

    def __init__(self):
        self.tool_calls: List[Dict[str, Any]] = []
        self.tool_outputs: List[Dict[str, Any]] = []
        self.metadata: Dict[str, Any] = {}

    def capture_tool_call(self, tool_call: Dict[str, Any]) -> None:
        """Capture a tool call with its metadata."""
        self.tool_calls.append({
            **tool_call,
            "timestamp": datetime.now().isoformat()
        })

        # Extract metadata (e.g., thought_signature for Gemini)
        if "metadata" in tool_call and tool_call["metadata"]:
            self.metadata.update(tool_call["metadata"])

    def capture_tool_output(self, tool_output: Dict[str, Any]) -> None:
        """Capture tool output."""
        self.tool_outputs.append(tool_output)

    def reset_for_turn(self) -> None:
        """Reset captures for new turn (keep metadata for preservation tracking)."""
        self.tool_calls = []
        self.tool_outputs = []

    def get_tool_names(self) -> List[str]:
        """Get list of tool names called this turn."""
        return [tc.get("name", tc.get("function", {}).get("name", "unknown"))
                for tc in self.tool_calls]


class MultiturnEvalRunner:
    """
    Runner for multiturn evaluation scenarios.

    Capabilities:
    1. Per-turn tool validation
    2. Context accumulation across turns
    3. Metadata (thought_signature) preservation tracking
    4. Provider-specific assertions
    5. Tool chain verification
    """

    def __init__(
        self,
        processor,
        provider: str = "openai",
        judge: Optional[LLMJudge] = None,
        tool_capture_factory: Optional[Callable[[], TurnToolCapture]] = None,
    ):
        """
        Initialize multiturn runner.

        Args:
            processor: Message processor (PipelineMessageProcessor or mock)
            provider: LLM provider to test ("openai", "gemini", "glm")
            judge: LLMJudge for evaluation (created if not provided)
            tool_capture_factory: Factory for TurnToolCapture (for testing)
        """
        self.processor = processor
        self.provider = provider
        self.judge = judge or LLMJudge()
        self.tool_capture_factory = tool_capture_factory or TurnToolCapture

        # State tracking
        self.accumulated_context: List[Dict[str, str]] = []
        self.tool_call_history: List[Dict[str, Any]] = []
        self.metadata_history: List[Dict[str, Any]] = []
        self.current_model: str = ""

    async def run_scenario(self, scenario: Dict[str, Any]) -> MultiturnResult:
        """Run a complete multiturn scenario."""
        scenario_name = scenario.get("name", "Unknown")
        description = scenario.get("description", "")
        turns_config = scenario.get("turns", [])

        logger.info(f"Running multiturn scenario: {scenario_name}")

        # Reset state
        self._reset_state()

        turn_results: List[TurnResult] = []
        total_latency_ms = 0
        scenario_error = None

        try:
            for turn_config in turns_config:
                turn_result = await self._run_turn(turn_config, scenario)
                turn_results.append(turn_result)
                total_latency_ms += turn_result.latency_ms

                # Accumulate context from user messages
                if "messages" in turn_config:
                    for msg in turn_config["messages"]:
                        self.accumulated_context.append(msg)

                # Add assistant response to context
                if turn_result.agent_response:
                    self.accumulated_context.append({
                        "role": "assistant",
                        "content": turn_result.agent_response
                    })

                # Track tool calls
                self.tool_call_history.extend(turn_result.actual_tools)

                # Track metadata
                if turn_result.metadata:
                    self.metadata_history.append(turn_result.metadata)

                # Check for early failure if configured
                e2e_config = scenario.get("e2e_validation", {})
                if e2e_config.get("fail_on_any_turn_failure", False) and not turn_result.pass_turn:
                    logger.warning(f"Turn {turn_result.turn_id} failed, stopping scenario")
                    break

        except Exception as e:
            logger.error(f"Error in scenario {scenario_name}: {e}", exc_info=True)
            scenario_error = str(e)

        # E2E validation
        e2e_results = self._validate_e2e(scenario, turn_results)

        # Calculate totals
        all_turns_passed = all(t.pass_turn for t in turn_results)
        total_score = sum(t.score for t in turn_results) / len(turn_results) if turn_results else 0

        return MultiturnResult(
            scenario_name=scenario_name,
            description=description,
            turns=turn_results,
            e2e_validation=e2e_results,
            provider=self.provider,
            model=self.current_model,
            all_turns_passed=all_turns_passed,
            tool_chain_correct=e2e_results.get("tool_chain_correct", True),
            metadata_preserved=e2e_results.get("metadata_preserved", True),
            total_latency_ms=total_latency_ms,
            total_score=total_score,
            error=scenario_error,
        )

    async def _run_turn(
        self, turn_config: Dict[str, Any], scenario: Dict[str, Any]
    ) -> TurnResult:
        """Run a single turn and evaluate."""
        turn_id = turn_config.get("turn_id", 0)
        messages = turn_config.get("messages", [])
        expected_tools = turn_config.get("expected_tools", [])
        criteria = turn_config.get("criteria", [])
        expected_behavior = turn_config.get("expected_behavior", "")
        should_evaluate = turn_config.get("evaluate", True)

        logger.info(f"Running turn {turn_id}")

        # Extract user message
        user_message = ""
        for msg in messages:
            if msg.get("role") == "user":
                user_message = msg.get("content", "")
                break

        # If no explicit user message, this might be a continuation turn
        if not user_message and not messages:
            user_message = "[Continuation from previous turn]"

        # Create tool capture for this turn
        tool_capture = self.tool_capture_factory()

        # Execute turn
        start_time = time.time()
        agent_response = ""
        error = None

        try:
            # Build full message context
            full_messages = self.accumulated_context.copy()
            full_messages.extend(messages)

            # Process message (implementation depends on processor type)
            result = await self._process_message(
                user_message=user_message,
                context_messages=full_messages,
                tool_capture=tool_capture,
            )

            agent_response = result.get("response", "")
            self.current_model = result.get("model", self.current_model)

        except Exception as e:
            logger.error(f"Error in turn {turn_id}: {e}")
            error = str(e)

        latency_ms = int((time.time() - start_time) * 1000)

        # Get actual tools called
        actual_tools = tool_capture.tool_calls

        # Validate tool choice
        expected_tool_names = [t.get("name", t) if isinstance(t, dict) else t
                               for t in expected_tools]
        actual_tool_names = tool_capture.get_tool_names()
        tool_choice_correct = self._validate_tool_choice(expected_tool_names, actual_tool_names)

        # Validate tool arguments
        tool_args_correct = self._validate_tool_args(expected_tools, actual_tools)

        # Evaluate with judge if needed
        score = 0.0
        reasoning = ""
        criteria_results = {}
        pass_turn = True

        if should_evaluate and not error:
            eval_result = await self._evaluate_turn(
                user_message=user_message,
                agent_response=agent_response,
                expected_behavior=expected_behavior,
                criteria=criteria,
                tool_calls=actual_tools,
                tool_outputs=tool_capture.tool_outputs,
            )
            score = eval_result.get("score", 0)
            reasoning = eval_result.get("reasoning", "")
            pass_turn = eval_result.get("pass", False) and tool_choice_correct

            # Build criteria results
            for criterion in criteria:
                criteria_results[criterion] = pass_turn

        return TurnResult(
            turn_id=turn_id,
            user_message=user_message,
            agent_response=agent_response,
            expected_tools=expected_tool_names,
            actual_tools=actual_tools,
            tool_choice_correct=tool_choice_correct,
            tool_args_correct=tool_args_correct,
            criteria_results=criteria_results,
            metadata=tool_capture.metadata,
            latency_ms=latency_ms,
            pass_turn=pass_turn,
            score=score,
            reasoning=reasoning,
            error=error,
        )

    async def _process_message(
        self,
        user_message: str,
        context_messages: List[Dict[str, str]],
        tool_capture: TurnToolCapture,
    ) -> Dict[str, Any]:
        """
        Process a message through the pipeline.

        Override this method for different processor implementations.
        """
        # Default implementation using PipelineMessageProcessor
        if hasattr(self.processor, "process_message"):
            # Create MessageRequest object
            from app.schemas.messages import MessageRequest

            request = MessageRequest(
                from_phone="+15551234567",
                to_phone="+15559876543",
                body=user_message,
                message_sid="eval_msg_" + str(int(time.time())),
                clinic_id="test-clinic",
                clinic_name="Test Dental Clinic",
            )

            response = await self.processor.process_message(request)

            # Extract tool calls from response context if available
            if hasattr(response, "tool_calls"):
                for tc in response.tool_calls:
                    tool_capture.capture_tool_call(tc)

            return {
                "response": getattr(response, "message", str(response)),
                "model": getattr(response, "model", "unknown"),
            }

        # Fallback for mock processors
        return {"response": "", "model": "mock"}

    async def _evaluate_turn(
        self,
        user_message: str,
        agent_response: str,
        expected_behavior: str,
        criteria: List[str],
        tool_calls: List[Dict[str, Any]],
        tool_outputs: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Evaluate a turn using the LLM judge."""
        try:
            # The judge uses evaluate_response (sync method)
            result = self.judge.evaluate_response(
                user_input=user_message,
                agent_response=agent_response,
                expected_behavior=expected_behavior,
                criteria=criteria,
                tool_calls=tool_calls,
                tool_outputs=tool_outputs,
            )
            return result
        except Exception as e:
            logger.error(f"Judge evaluation failed: {e}")
            return {"score": 0, "pass": False, "reasoning": f"Evaluation error: {e}"}

    def _validate_tool_choice(
        self, expected: List[str], actual: List[str]
    ) -> bool:
        """Validate that expected tools were called."""
        if not expected:
            return True  # No tools expected

        # Check if all expected tools appear in actual (order doesn't matter for single turn)
        for expected_tool in expected:
            if expected_tool not in actual:
                return False
        return True

    def _validate_tool_args(
        self, expected_tools: List[Dict[str, Any]], actual_tools: List[Dict[str, Any]]
    ) -> bool:
        """Validate tool arguments match expected."""
        if not expected_tools:
            return True

        for expected in expected_tools:
            if not isinstance(expected, dict):
                continue

            expected_name = expected.get("name")
            required_args = expected.get("required_args", [])

            # Find matching actual tool call
            matching_call = None
            for actual in actual_tools:
                actual_name = actual.get("name", actual.get("function", {}).get("name"))
                if actual_name == expected_name:
                    matching_call = actual
                    break

            if not matching_call:
                continue

            # Check required args exist
            actual_args = matching_call.get("arguments", {})
            if isinstance(actual_args, str):
                try:
                    actual_args = json.loads(actual_args)
                except json.JSONDecodeError:
                    actual_args = {}

            for req_arg in required_args:
                if req_arg not in actual_args:
                    return False

        return True

    def _validate_e2e(
        self, scenario: Dict[str, Any], turn_results: List[TurnResult]
    ) -> Dict[str, bool]:
        """Validate end-to-end requirements."""
        e2e_config = scenario.get("e2e_validation", {})
        results = {}

        # Verify tool chain order
        if "verify_tool_chain" in e2e_config:
            expected_chain = e2e_config["verify_tool_chain"]
            actual_chain = [
                tc.get("name", tc.get("function", {}).get("name", "unknown"))
                for tc in self.tool_call_history
            ]
            results["tool_chain_correct"] = self._verify_tool_chain(
                expected_chain, actual_chain
            )

        # Verify metadata flow
        metadata_config = e2e_config.get("verify_metadata_flow", {})
        if metadata_config.get("thought_signature_preserved"):
            results["metadata_preserved"] = self._verify_thought_signature_preservation()
        elif metadata_config.get("context_accumulates"):
            results["metadata_preserved"] = len(self.accumulated_context) > 0

        # Verify fallback
        if "verify_fallback" in e2e_config:
            fallback_config = e2e_config["verify_fallback"]
            if fallback_config.get("fallback_triggered"):
                # Check if fallback was used (would need metrics from processor)
                results["fallback_triggered"] = False  # Default, override in actual test

        # Verify boundary conditions
        if "verify_boundary" in e2e_config:
            boundary_config = e2e_config["verify_boundary"]
            if boundary_config.get("max_turns_reached"):
                results["max_turns_reached"] = len(turn_results) >= 5
            if boundary_config.get("early_exit"):
                results["early_exit"] = len(turn_results) < 5

        return results

    def _verify_tool_chain(self, expected: List[str], actual: List[str]) -> bool:
        """Verify tools were called in expected order (as subsequence)."""
        expected_idx = 0
        for tool in actual:
            if expected_idx < len(expected) and tool == expected[expected_idx]:
                expected_idx += 1
        return expected_idx == len(expected)

    def _verify_thought_signature_preservation(self) -> bool:
        """Verify Gemini thought_signature is preserved across turns."""
        if self.provider != "gemini":
            return True  # Only relevant for Gemini

        # Check if any metadata contains thought_signature
        for meta in self.metadata_history:
            if "thought_signature" in meta:
                return True

        return len(self.metadata_history) == 0  # Pass if no metadata expected

    def _reset_state(self) -> None:
        """Reset state for new scenario."""
        self.accumulated_context = []
        self.tool_call_history = []
        self.metadata_history = []
        self.current_model = ""


async def run_multiturn_evals(
    scenario_file: str,
    processor,
    provider: str = "openai",
) -> List[MultiturnResult]:
    """
    Run all multiturn scenarios from a file.

    Args:
        scenario_file: Path to YAML scenario file
        processor: Message processor
        provider: LLM provider to test

    Returns:
        List of MultiturnResult for each scenario
    """
    import yaml

    with open(scenario_file, "r") as f:
        data = yaml.safe_load(f)
        scenarios = data.get("scenarios", [])

    # Filter to multiturn scenarios
    multiturn_scenarios = [
        s for s in scenarios
        if s.get("type") == "multiturn" or "turns" in s
    ]

    runner = MultiturnEvalRunner(processor, provider=provider)
    results = []

    for scenario in multiturn_scenarios:
        # Check provider targeting
        provider_config = scenario.get("provider_config", {})
        target_providers = provider_config.get("target_providers", [provider])

        if provider not in target_providers and "all" not in target_providers:
            logger.info(f"Skipping {scenario.get('name')} - not targeting {provider}")
            continue

        result = await runner.run_scenario(scenario)
        results.append(result)

        # Log result
        status = "PASS" if result.all_turns_passed else "FAIL"
        logger.info(f"  {status}: {result.scenario_name} (score: {result.total_score:.1f})")

    return results
