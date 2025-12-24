"""
Acceptable Score evaluation metric.
Tracks tool choice correctness + argument correctness + final decision correctness.

Per Opinion 4, Section 6.1:
"Acceptable Score: % of test records where all required tools are called
with correct arguments and the final decision is correct."

This is a comprehensive metric that ensures:
1. The agent calls the RIGHT tools (not just any tool)
2. The agent passes the RIGHT arguments (slot IDs, dates, etc.)
3. The agent makes the RIGHT decision (book, escalate, info, etc.)

Usage:
    # Load test scenarios
    scenarios = load_scenarios("tests/evals/scenarios.yaml")

    # Run evaluation
    for scenario in scenarios:
        result = await run_scenario(scenario)
        eval_result = evaluate_acceptable_score(scenario, result)

        if not eval_result.acceptable:
            print(f"FAIL: {eval_result.scenario_id}")
            print(f"  Tool choice: {eval_result.tool_choice_correct}")
            print(f"  Tool args: {eval_result.tool_args_correct}")
            print(f"  Decision: {eval_result.decision_correct}")
"""
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
import logging

logger = logging.getLogger(__name__)


@dataclass
class ToolCallEval:
    """Evaluation of a single tool call."""
    expected_tool: str
    actual_tool: Optional[str]
    tool_correct: bool
    expected_args: Dict[str, Any]
    actual_args: Dict[str, Any]
    args_correct: bool  # Lenient: required args present and correct
    args_strict_correct: bool  # Strict: all args exact match
    missing_args: List[str] = field(default_factory=list)
    wrong_args: List[str] = field(default_factory=list)


@dataclass
class AcceptableScoreResult:
    """Result of acceptable score evaluation."""
    scenario_id: str
    lane: str
    clinic_type: str
    language: str

    # Tool correctness
    tool_calls_expected: List[str]
    tool_calls_actual: List[str]
    tool_choice_correct: bool

    # Argument correctness
    tool_args_correct: bool

    # Final decision
    expected_decision: str  # "book", "escalate", "info", etc.
    actual_decision: str
    decision_correct: bool

    # Overall
    acceptable: bool  # All three correct

    # Fields with defaults must come last
    tool_call_evals: List[ToolCallEval] = field(default_factory=list)
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "scenario_id": self.scenario_id,
            "lane": self.lane,
            "clinic_type": self.clinic_type,
            "language": self.language,
            "tool_calls_expected": self.tool_calls_expected,
            "tool_calls_actual": self.tool_calls_actual,
            "tool_choice_correct": self.tool_choice_correct,
            "tool_args_correct": self.tool_args_correct,
            "expected_decision": self.expected_decision,
            "actual_decision": self.actual_decision,
            "decision_correct": self.decision_correct,
            "acceptable": self.acceptable,
            "error": self.error,
        }


def evaluate_acceptable_score(
    scenario: Dict[str, Any],
    result: Dict[str, Any],
) -> AcceptableScoreResult:
    """
    Evaluate a single scenario for acceptable score.

    Args:
        scenario: Test scenario with expected values
        result: Actual result from running the scenario

    Returns:
        AcceptableScoreResult with detailed breakdown
    """
    scenario_id = scenario.get('id', 'unknown')

    try:
        # Extract expected values
        expected_tools = scenario.get('expected_tool_calls', [])
        expected_decision = scenario.get('expected_decision', '')
        expected_tool_calls_with_args = scenario.get('expected_tool_calls_with_args', [])

        # Extract actual values from result
        actual_tools = [tc['name'] for tc in result.get('tool_calls', [])]
        actual_decision = result.get('final_decision', '') or result.get('intent', '')

        # Normalize decisions for comparison
        actual_decision = _normalize_decision(actual_decision)
        expected_decision = _normalize_decision(expected_decision)

        # Tool choice correctness (lenient - order doesn't matter)
        tool_choice_correct = set(expected_tools) == set(actual_tools)

        # Tool args correctness (check required args)
        tool_args_correct = True
        tool_call_evals = []

        for expected_call in expected_tool_calls_with_args:
            expected_tool_name = expected_call.get('name')
            required_args = expected_call.get('required_args', {})

            # Find matching actual call
            actual_call = next(
                (tc for tc in result.get('tool_calls', []) if tc.get('name') == expected_tool_name),
                None
            )

            if actual_call:
                actual_args = actual_call.get('arguments', {})
                missing_args = []
                wrong_args = []

                # Check each required arg
                for key, expected_value in required_args.items():
                    actual_value = actual_args.get(key)
                    if actual_value is None:
                        missing_args.append(key)
                        tool_args_correct = False
                    elif actual_value != expected_value:
                        # Allow fuzzy matching for some types
                        if not _fuzzy_match(expected_value, actual_value):
                            wrong_args.append(key)
                            tool_args_correct = False

                eval_result = ToolCallEval(
                    expected_tool=expected_tool_name,
                    actual_tool=expected_tool_name,
                    tool_correct=True,
                    expected_args=required_args,
                    actual_args=actual_args,
                    args_correct=len(missing_args) == 0 and len(wrong_args) == 0,
                    args_strict_correct=actual_args == required_args,
                    missing_args=missing_args,
                    wrong_args=wrong_args,
                )
            else:
                eval_result = ToolCallEval(
                    expected_tool=expected_tool_name,
                    actual_tool=None,
                    tool_correct=False,
                    expected_args=required_args,
                    actual_args={},
                    args_correct=False,
                    args_strict_correct=False,
                    missing_args=list(required_args.keys()),
                    wrong_args=[],
                )
                tool_args_correct = False

            tool_call_evals.append(eval_result)

        # Decision correctness
        decision_correct = expected_decision == actual_decision

        return AcceptableScoreResult(
            scenario_id=scenario_id,
            lane=scenario.get('lane', 'unknown'),
            clinic_type=scenario.get('clinic_type', 'dental'),
            language=scenario.get('language', 'en'),
            tool_calls_expected=expected_tools,
            tool_calls_actual=actual_tools,
            tool_choice_correct=tool_choice_correct,
            tool_call_evals=tool_call_evals,
            tool_args_correct=tool_args_correct,
            expected_decision=expected_decision,
            actual_decision=actual_decision,
            decision_correct=decision_correct,
            acceptable=tool_choice_correct and tool_args_correct and decision_correct,
        )

    except Exception as e:
        logger.error(f"Error evaluating scenario {scenario_id}: {e}")
        return AcceptableScoreResult(
            scenario_id=scenario_id,
            lane=scenario.get('lane', 'unknown'),
            clinic_type=scenario.get('clinic_type', 'dental'),
            language=scenario.get('language', 'en'),
            tool_calls_expected=[],
            tool_calls_actual=[],
            tool_choice_correct=False,
            tool_args_correct=False,
            expected_decision="",
            actual_decision="",
            decision_correct=False,
            acceptable=False,
            error=str(e),
        )


def _normalize_decision(decision: str) -> str:
    """Normalize decision string for comparison."""
    if not decision:
        return ""

    decision = decision.lower().strip()

    # Map common variations
    mappings = {
        "schedule": "book",
        "schedule_appointment": "book",
        "book_appointment": "book",
        "booking": "book",
        "appointment": "book",
        "scheduling": "book",
        "escalate": "escalate",
        "escalation": "escalate",
        "human": "escalate",
        "info": "info",
        "information": "info",
        "faq": "info",
        "price": "info",
        "cancel": "cancel",
        "cancel_appointment": "cancel",
        "cancellation": "cancel",
        "reschedule": "reschedule",
        "reschedule_appointment": "reschedule",
        "exit": "exit",
        "end": "exit",
        "goodbye": "exit",
    }

    return mappings.get(decision, decision)


def _fuzzy_match(expected: Any, actual: Any) -> bool:
    """Fuzzy matching for argument values."""
    # String normalization
    if isinstance(expected, str) and isinstance(actual, str):
        return expected.lower().strip() == actual.lower().strip()

    # Date normalization (handle different formats)
    if isinstance(expected, str) and isinstance(actual, str):
        # Check if both are dates in different formats
        try:
            from datetime import datetime
            formats = ["%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ", "%m/%d/%Y"]
            for fmt in formats:
                try:
                    exp_date = datetime.strptime(expected, fmt)
                    for fmt2 in formats:
                        try:
                            act_date = datetime.strptime(actual, fmt2)
                            if exp_date.date() == act_date.date():
                                return True
                        except ValueError:
                            continue
                except ValueError:
                    continue
        except Exception:
            pass

    return expected == actual


def aggregate_acceptable_scores(results: List[AcceptableScoreResult]) -> Dict[str, Any]:
    """
    Aggregate acceptable score results into summary metrics.

    Returns:
        Dictionary with:
        - acceptable_score: Overall % acceptable
        - tool_choice_accuracy: % with correct tool choice
        - tool_args_accuracy: % with correct tool args
        - decision_accuracy: % with correct decision
        - breakdown_by_lane: Per-lane metrics
        - breakdown_by_language: Per-language metrics
    """
    total = len(results)
    if total == 0:
        return {
            "acceptable_score": 0.0,
            "tool_choice_accuracy": 0.0,
            "tool_args_accuracy": 0.0,
            "decision_accuracy": 0.0,
            "total_scenarios": 0,
        }

    acceptable = sum(1 for r in results if r.acceptable)
    tool_correct = sum(1 for r in results if r.tool_choice_correct)
    args_correct = sum(1 for r in results if r.tool_args_correct)
    decision_correct = sum(1 for r in results if r.decision_correct)

    # Breakdown by lane
    lanes = {}
    for r in results:
        if r.lane not in lanes:
            lanes[r.lane] = {"total": 0, "acceptable": 0}
        lanes[r.lane]["total"] += 1
        if r.acceptable:
            lanes[r.lane]["acceptable"] += 1

    lane_breakdown = {
        lane: {
            "acceptable_score": data["acceptable"] / data["total"] if data["total"] > 0 else 0,
            "total": data["total"],
        }
        for lane, data in lanes.items()
    }

    # Breakdown by language
    languages = {}
    for r in results:
        if r.language not in languages:
            languages[r.language] = {"total": 0, "acceptable": 0}
        languages[r.language]["total"] += 1
        if r.acceptable:
            languages[r.language]["acceptable"] += 1

    language_breakdown = {
        lang: {
            "acceptable_score": data["acceptable"] / data["total"] if data["total"] > 0 else 0,
            "total": data["total"],
        }
        for lang, data in languages.items()
    }

    return {
        "acceptable_score": acceptable / total,
        "tool_choice_accuracy": tool_correct / total,
        "tool_args_accuracy": args_correct / total,
        "decision_accuracy": decision_correct / total,
        "total_scenarios": total,
        "acceptable_count": acceptable,
        "breakdown_by_lane": lane_breakdown,
        "breakdown_by_language": language_breakdown,
    }
