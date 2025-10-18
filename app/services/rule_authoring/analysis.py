"""
Helpers for summarising guardrail usage and comparing draft bundles against baselines.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence

from app.policies.starter_pack import get_starter_pack_bundle
from app.services.rule_authoring.orchestrator import RuleAuthoringSessionState


@dataclass
class GuardrailUsage:
    turns_used: int
    max_turns: int
    tool_calls_used: int
    max_tool_calls: int
    usd_spent: float
    budget_usd: float


def summarise_guardrail_usage(state: RuleAuthoringSessionState) -> GuardrailUsage:
    """Extract guardrail usage counters from the session state."""
    config = state.config
    return GuardrailUsage(
        turns_used=state.turns,
        max_turns=config.max_conversation_turns,
        tool_calls_used=state.tool_calls_used,
        max_tool_calls=config.max_tool_calls,
        usd_spent=round(state.usd_spent, 4),
        budget_usd=config.budget_usd_per_session,
    )


def diff_against_starter_pack(bundle: Optional[Dict[str, Any]]) -> List[str]:
    """
    Produce simple warnings comparing the incoming bundle with the starter pack baseline.

    Warnings call out missing metadata (reason codes, explain templates) as well as changes
    relative to the baseline rules that ship with the product.
    """
    if not bundle or not isinstance(bundle, dict):
        return []

    rules = bundle.get("rules")
    if not isinstance(rules, Sequence):
        return []

    warnings: List[str] = []

    baseline = get_starter_pack_bundle()
    baseline_rules = {
        rule.get("rule_id"): rule
        for rule in baseline.get("rules", [])
        if isinstance(rule, dict) and rule.get("rule_id")
    }

    seen_ids: set[str] = set()

    for idx, raw_rule in enumerate(rules):
        if not isinstance(raw_rule, dict):
            warnings.append(f"Rule at index {idx} is not an object.")
            continue

        rule_id = _normalise_rule_id(raw_rule.get("rule_id"), idx)
        seen_ids.add(rule_id)

        effect = raw_rule.get("effect")
        if not isinstance(effect, dict):
            warnings.append(f"{rule_id}: Rule is missing an effect block.")
            continue

        # Metadata completeness checks
        explain_template = _coalesce(
            raw_rule.get("explain_template"), effect.get("explain_template")
        )
        if not explain_template:
            warnings.append(f"{rule_id}: Missing explain_template.")

        reason_code = _coalesce(raw_rule.get("reason_code"), effect.get("reason_code"))
        if not reason_code:
            warnings.append(f"{rule_id}: Missing reason_code.")

        precedence = raw_rule.get("precedence")
        if precedence is not None and not isinstance(precedence, (int, float)):
            warnings.append(f"{rule_id}: Precedence should be numeric if provided.")

        # Baseline comparison
        baseline_rule = baseline_rules.get(rule_id)
        if baseline_rule is None:
            warnings.append(f"{rule_id}: New rule compared to starter pack baseline.")
            continue

        baseline_effect = baseline_rule.get("effect") or {}
        if effect.get("type") != baseline_effect.get("type"):
            warnings.append(
                f"{rule_id}: Effect type changed from {baseline_effect.get('type')} to {effect.get('type')}."
            )

        baseline_reason = _coalesce(
            baseline_rule.get("reason_code"), baseline_effect.get("reason_code")
        )
        if reason_code and baseline_reason and reason_code != baseline_reason:
            warnings.append(
                f"{rule_id}: Reason code changed from {baseline_reason} to {reason_code}."
            )

        baseline_precedence = baseline_rule.get("precedence")
        if isinstance(precedence, (int, float)) and isinstance(
            baseline_precedence, (int, float)
        ):
            if precedence != baseline_precedence:
                warnings.append(
                    f"{rule_id}: Precedence changed from {baseline_precedence} to {precedence}."
                )

    missing_rule_ids = set(baseline_rules.keys()) - seen_ids
    for rule_id in sorted(missing_rule_ids):
        warnings.append(f"{rule_id}: Baseline rule not present in draft bundle.")

    # Deduplicate while preserving order
    return _deduplicate(warnings)


def _normalise_rule_id(value: Any, fallback_index: int) -> str:
    if isinstance(value, str) and value.strip():
        return value
    return f"RULE_{fallback_index + 1}"


def _deduplicate(items: Iterable[str]) -> List[str]:
    seen: set[str] = set()
    result: List[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def _coalesce(*candidates: Any) -> Optional[str]:
    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip():
            return candidate
    return None
