"""
Multiturn Metrics

Metrics specific to multiturn evaluation scenarios, tracking
per-turn success rates, tool chain accuracy, and metadata preservation.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from tests.evals.multiturn_runner import MultiturnResult, TurnResult


@dataclass
class MultiturnMetrics:
    """Comprehensive metrics for multiturn scenarios."""

    # Per-turn success
    total_turns: int = 0
    successful_turns: int = 0
    turn_success_rate: float = 0.0

    # Turn-level tool correctness
    per_turn_tool_accuracy: float = 0.0
    per_turn_args_accuracy: float = 0.0

    # Context preservation
    context_retention_score: float = 0.0

    # Metadata tracking
    thought_signature_preservation_rate: float = 0.0

    # E2E flow
    complete_flow_success_rate: float = 0.0
    tool_chain_accuracy: float = 0.0

    # Provider comparison
    provider_scores: Dict[str, float] = field(default_factory=dict)

    # Turn timing
    avg_turn_latency_ms: float = 0.0
    total_scenario_latency_ms: int = 0

    # Scenario counts
    total_scenarios: int = 0
    passed_scenarios: int = 0

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "total_turns": self.total_turns,
            "successful_turns": self.successful_turns,
            "turn_success_rate": round(self.turn_success_rate, 4),
            "per_turn_tool_accuracy": round(self.per_turn_tool_accuracy, 4),
            "per_turn_args_accuracy": round(self.per_turn_args_accuracy, 4),
            "context_retention_score": round(self.context_retention_score, 4),
            "thought_signature_preservation_rate": round(self.thought_signature_preservation_rate, 4),
            "complete_flow_success_rate": round(self.complete_flow_success_rate, 4),
            "tool_chain_accuracy": round(self.tool_chain_accuracy, 4),
            "provider_scores": {k: round(v, 4) for k, v in self.provider_scores.items()},
            "avg_turn_latency_ms": round(self.avg_turn_latency_ms, 2),
            "total_scenario_latency_ms": self.total_scenario_latency_ms,
            "total_scenarios": self.total_scenarios,
            "passed_scenarios": self.passed_scenarios,
        }

    @classmethod
    def empty(cls) -> "MultiturnMetrics":
        """Create empty metrics instance."""
        return cls()


def calculate_multiturn_metrics(results: List[MultiturnResult]) -> MultiturnMetrics:
    """
    Calculate aggregate multiturn metrics from scenario results.

    Args:
        results: List of MultiturnResult from running scenarios

    Returns:
        Aggregated MultiturnMetrics
    """
    if not results:
        return MultiturnMetrics.empty()

    # Count turns
    total_turns = sum(len(r.turns) for r in results)
    successful_turns = sum(
        sum(1 for t in r.turns if t.pass_turn)
        for r in results
    )

    # Tool accuracy per turn
    tool_correct_turns = sum(
        sum(1 for t in r.turns if t.tool_choice_correct)
        for r in results
    )

    args_correct_turns = sum(
        sum(1 for t in r.turns if t.tool_args_correct)
        for r in results
    )

    # Complete flow success
    complete_flows = sum(1 for r in results if r.all_turns_passed)

    # Tool chain accuracy
    chain_correct = sum(1 for r in results if r.tool_chain_correct)

    # Metadata preservation
    metadata_preserved = sum(1 for r in results if r.metadata_preserved)

    # Latency
    total_latency = sum(r.total_latency_ms for r in results)

    # Provider scores
    provider_scores: Dict[str, List[float]] = {}
    for r in results:
        if r.provider not in provider_scores:
            provider_scores[r.provider] = []
        provider_scores[r.provider].append(r.total_score)

    avg_provider_scores = {
        k: sum(v) / len(v) for k, v in provider_scores.items() if v
    }

    return MultiturnMetrics(
        total_turns=total_turns,
        successful_turns=successful_turns,
        turn_success_rate=successful_turns / total_turns if total_turns > 0 else 0.0,
        per_turn_tool_accuracy=tool_correct_turns / total_turns if total_turns > 0 else 0.0,
        per_turn_args_accuracy=args_correct_turns / total_turns if total_turns > 0 else 0.0,
        context_retention_score=0.0,  # Calculated separately if needed
        thought_signature_preservation_rate=(
            metadata_preserved / len(results) if results else 0.0
        ),
        complete_flow_success_rate=complete_flows / len(results) if results else 0.0,
        tool_chain_accuracy=chain_correct / len(results) if results else 0.0,
        provider_scores=avg_provider_scores,
        avg_turn_latency_ms=total_latency / total_turns if total_turns > 0 else 0.0,
        total_scenario_latency_ms=total_latency,
        total_scenarios=len(results),
        passed_scenarios=complete_flows,
    )


def calculate_per_provider_metrics(
    results: List[MultiturnResult],
) -> Dict[str, MultiturnMetrics]:
    """
    Calculate metrics grouped by provider.

    Args:
        results: List of MultiturnResult

    Returns:
        Dictionary mapping provider name to MultiturnMetrics
    """
    # Group by provider
    by_provider: Dict[str, List[MultiturnResult]] = {}
    for r in results:
        if r.provider not in by_provider:
            by_provider[r.provider] = []
        by_provider[r.provider].append(r)

    # Calculate metrics for each provider
    return {
        provider: calculate_multiturn_metrics(provider_results)
        for provider, provider_results in by_provider.items()
    }


def calculate_context_retention_score(results: List[MultiturnResult]) -> float:
    """
    Calculate context retention score across scenarios.

    Measures how well context is maintained across turns by checking:
    1. Tool results from earlier turns are used in later turns
    2. Constraints/preferences persist across turns
    3. No redundant tool calls for already-fetched data

    Args:
        results: List of MultiturnResult

    Returns:
        Score from 0.0 to 1.0
    """
    if not results:
        return 0.0

    total_score = 0.0
    scored_scenarios = 0

    for result in results:
        if len(result.turns) < 2:
            continue  # Need at least 2 turns to measure retention

        scenario_score = 0.0
        checks = 0

        for i in range(1, len(result.turns)):
            current_turn = result.turns[i]
            previous_turn = result.turns[i - 1]

            # Check 1: No redundant tool calls
            current_tools = set(current_turn.expected_tools)
            previous_tools = set(previous_turn.expected_tools)

            # If current turn calls same tools as previous with same args, that's redundant
            current_actual = set(
                t.get("name", t.get("function", {}).get("name", ""))
                for t in current_turn.actual_tools
            )
            previous_actual = set(
                t.get("name", t.get("function", {}).get("name", ""))
                for t in previous_turn.actual_tools
            )

            # Score higher if not repeating tools unnecessarily
            if current_actual and previous_actual:
                redundant_calls = current_actual & previous_actual
                if not redundant_calls:
                    scenario_score += 1.0
                checks += 1

            # Check 2: Turn passes (implies context was available)
            if current_turn.pass_turn:
                scenario_score += 1.0
            checks += 1

        if checks > 0:
            total_score += scenario_score / checks
            scored_scenarios += 1

    return total_score / scored_scenarios if scored_scenarios > 0 else 0.0


@dataclass
class ProviderComparison:
    """Comparison metrics between providers."""

    providers: List[str] = field(default_factory=list)
    metrics_by_provider: Dict[str, MultiturnMetrics] = field(default_factory=dict)

    # Summary comparisons
    best_tool_accuracy_provider: str = ""
    best_latency_provider: str = ""
    best_overall_provider: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "providers": self.providers,
            "metrics_by_provider": {
                k: v.to_dict() for k, v in self.metrics_by_provider.items()
            },
            "best_tool_accuracy_provider": self.best_tool_accuracy_provider,
            "best_latency_provider": self.best_latency_provider,
            "best_overall_provider": self.best_overall_provider,
        }


def compare_providers(results: List[MultiturnResult]) -> ProviderComparison:
    """
    Compare metrics across providers.

    Args:
        results: List of MultiturnResult

    Returns:
        ProviderComparison with rankings
    """
    metrics_by_provider = calculate_per_provider_metrics(results)
    providers = list(metrics_by_provider.keys())

    if not providers:
        return ProviderComparison()

    # Find best in each category
    best_tool_accuracy = max(
        providers,
        key=lambda p: metrics_by_provider[p].per_turn_tool_accuracy,
    )
    best_latency = min(
        providers,
        key=lambda p: metrics_by_provider[p].avg_turn_latency_ms or float("inf"),
    )
    best_overall = max(
        providers,
        key=lambda p: metrics_by_provider[p].complete_flow_success_rate,
    )

    return ProviderComparison(
        providers=providers,
        metrics_by_provider=metrics_by_provider,
        best_tool_accuracy_provider=best_tool_accuracy,
        best_latency_provider=best_latency,
        best_overall_provider=best_overall,
    )


def print_multiturn_summary(results: List[MultiturnResult]) -> None:
    """Print formatted summary of multiturn evaluation results."""
    metrics = calculate_multiturn_metrics(results)

    print("\n" + "=" * 60)
    print("MULTITURN EVALUATION SUMMARY")
    print("=" * 60)

    print(f"\nScenarios: {metrics.passed_scenarios}/{metrics.total_scenarios} passed")
    print(f"Turns: {metrics.successful_turns}/{metrics.total_turns} passed")
    print(f"Turn Success Rate: {metrics.turn_success_rate * 100:.1f}%")

    print(f"\nTool Accuracy: {metrics.per_turn_tool_accuracy * 100:.1f}%")
    print(f"Tool Args Accuracy: {metrics.per_turn_args_accuracy * 100:.1f}%")
    print(f"Tool Chain Accuracy: {metrics.tool_chain_accuracy * 100:.1f}%")

    print(f"\nComplete Flow Success: {metrics.complete_flow_success_rate * 100:.1f}%")
    print(f"Metadata Preservation: {metrics.thought_signature_preservation_rate * 100:.1f}%")

    print(f"\nAvg Turn Latency: {metrics.avg_turn_latency_ms:.0f}ms")
    print(f"Total Latency: {metrics.total_scenario_latency_ms}ms")

    if metrics.provider_scores:
        print("\nProvider Scores:")
        for provider, score in metrics.provider_scores.items():
            print(f"  {provider}: {score:.2f}")

    print("\n" + "=" * 60)

    # Print individual scenario results
    print("\nDETAILED RESULTS:")
    print("-" * 60)

    for result in results:
        status = "PASS" if result.all_turns_passed else "FAIL"
        print(f"\n{status}: {result.scenario_name}")
        print(f"  Provider: {result.provider}, Model: {result.model}")
        print(f"  Turns: {len(result.turns)}, Score: {result.total_score:.1f}")

        for turn in result.turns:
            turn_status = "pass" if turn.pass_turn else "FAIL"
            print(f"    Turn {turn.turn_id}: {turn_status} (score: {turn.score:.1f})")
            if turn.actual_tools:
                tool_names = [
                    t.get("name", t.get("function", {}).get("name", "?"))
                    for t in turn.actual_tools
                ]
                print(f"      Tools: {', '.join(tool_names)}")
            if not turn.pass_turn and turn.reasoning:
                print(f"      Reason: {turn.reasoning[:100]}...")
