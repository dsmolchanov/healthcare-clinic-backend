"""
Runtime compiler for rule bundles.

Transforms schema-compliant rule bundles into fast in-memory evaluators that
separate hard enforcement (deny/escalate/require/limit) from soft preferences.
"""

from __future__ import annotations

import math
import re
from collections import defaultdict
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from .validator import RuleBundleValidator

ConditionFunc = Callable[[Dict[str, Any]], bool]


class RuleEffectType:
    DENY = "DENY"
    ESCALATE = "ESCALATE"
    WARN = "WARN"
    REQUIRE_FIELD = "REQUIRE_FIELD"
    ADJUST_SCORE = "ADJUST_SCORE"
    LIMIT_OCCURRENCE = "LIMIT_OCCURRENCE"

    HARD_TYPES = {DENY, ESCALATE, REQUIRE_FIELD, LIMIT_OCCURRENCE}
    SOFT_TYPES = {ADJUST_SCORE, WARN}


@dataclass
class CompiledRule:
    rule_id: str
    precedence: int
    effect_type: str
    effect_payload: Dict[str, Any]
    condition: ConditionFunc
    salience: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def matches(self, context: Dict[str, Any]) -> bool:
        return self.condition(context)


@dataclass
class CompiledPolicy:
    bundle_id: str
    schema_version: str
    hard_rules: List[CompiledRule]
    soft_rules: List[CompiledRule]
    metadata: Dict[str, Any]


def _get_nested_value(obj: Dict[str, Any], path: str) -> Any:
    value: Any = obj
    for part in path.split("."):
        if isinstance(value, dict):
            value = value.get(part)
        else:
            return None
    return value


def _coerce_iter(value: Any) -> Iterable[Any]:
    if isinstance(value, (list, tuple, set)):
        return value
    return [value]


def _compile_leaf(condition: Dict[str, Any]) -> ConditionFunc:
    field_path = condition["field"]
    operator = condition["operator"]
    case_sensitive = condition.get("case_sensitive", True)
    raw_value = condition.get("value")

    if operator in {"equals", "not_equals"} and isinstance(raw_value, str) and not case_sensitive:
        raw_value = raw_value.lower()

    if operator == "regex" and isinstance(raw_value, str):
        flags = 0 if case_sensitive else re.IGNORECASE
        regex = re.compile(raw_value, flags)

        def _regex(context: Dict[str, Any]) -> bool:
            candidate = _get_nested_value(context, field_path)
            if candidate is None:
                return False
            return bool(regex.search(str(candidate)))

        return _regex

    def comparator(context: Dict[str, Any]) -> bool:
        candidate = _get_nested_value(context, field_path)

        if operator == "is_null":
            return candidate is None
        if operator == "is_not_null":
            return candidate is not None

        if candidate is None:
            return False

        value = raw_value

        if isinstance(candidate, str) and not case_sensitive and isinstance(value, str):
            candidate = candidate.lower()

        if operator == "equals":
            return candidate == value
        if operator == "not_equals":
            return candidate != value
        if operator == "greater_than":
            return candidate > value
        if operator == "greater_or_equal":
            return candidate >= value
        if operator == "less_than":
            return candidate < value
        if operator == "less_or_equal":
            return candidate <= value
        if operator == "contains":
            return str(value) in str(candidate)
        if operator == "not_contains":
            return str(value) not in str(candidate)
        if operator == "starts_with":
            return str(candidate).startswith(str(value))
        if operator == "ends_with":
            return str(candidate).endswith(str(value))
        if operator == "in":
            return candidate in set(_coerce_iter(value))
        if operator == "not_in":
            return candidate not in set(_coerce_iter(value))
        if operator == "between":
            if isinstance(value, (list, tuple)) and len(value) == 2:
                lower, upper = value
                return lower <= candidate <= upper
            return False

        return False

    return comparator


def _compile_condition(node: Dict[str, Any]) -> ConditionFunc:
    if "all" in node:
        children = [_compile_condition(child) for child in node["all"]]

        def _all(context: Dict[str, Any]) -> bool:
            return all(child(context) for child in children)

        return _all

    if "any" in node:
        children = [_compile_condition(child) for child in node["any"]]

        def _any(context: Dict[str, Any]) -> bool:
            return any(child(context) for child in children)

        return _any

    if "none" in node:
        children = [_compile_condition(child) for child in node["none"]]

        def _none(context: Dict[str, Any]) -> bool:
            return not any(child(context) for child in children)

        return _none

    if "not" in node:
        child = _compile_condition(node["not"])

        def _negate(context: Dict[str, Any]) -> bool:
            return not child(context)

        return _negate

    return _compile_leaf(node)


def _sort_rules(rules: List[CompiledRule]) -> List[CompiledRule]:
    return sorted(
        rules,
        key=lambda rule: (rule.precedence, -rule.salience, rule.rule_id),
    )


def _split_rules(compiled: List[CompiledRule]) -> Tuple[List[CompiledRule], List[CompiledRule]]:
    hard: List[CompiledRule] = []
    soft: List[CompiledRule] = []
    for rule in compiled:
        if rule.effect_type in RuleEffectType.HARD_TYPES:
            hard.append(rule)
        elif rule.effect_type in RuleEffectType.SOFT_TYPES:
            soft.append(rule)
    return _sort_rules(hard), _sort_rules(soft)


class PolicyCompiler:
    """Compile rule bundles into runtime evaluators with caching."""

    def __init__(self) -> None:
        self.validator = RuleBundleValidator()
        self._cache = {}

    def compile(self, bundle: Dict[str, Any]) -> CompiledPolicy:

        problems = self.validator.validate_dict(bundle)
        if problems:
            formatted = "; ".join(p.format() for p in problems)
            raise ValueError(f"Rule bundle failed validation: {formatted}")

        compiled_rules: List[CompiledRule] = []

        for rule_data in bundle.get("rules", []):
            effect = rule_data["effect"]
            effect_type = effect["type"]
            condition_node = rule_data.get("conditions") or {"all": []}

            compiled_rules.append(
                CompiledRule(
                    rule_id=rule_data["rule_id"],
                    precedence=rule_data.get("precedence", math.inf),
                    salience=rule_data.get("salience", 0),
                    effect_type=effect_type,
                    effect_payload=effect,
                    condition=_compile_condition(condition_node),
                    metadata={
                        key: value
                        for key, value in rule_data.items()
                        if key
                        not in {"rule_id", "precedence", "salience", "effect", "conditions"}
                    },
                )
            )

        hard_rules, soft_rules = _split_rules(compiled_rules)

        return CompiledPolicy(
            bundle_id=bundle.get("bundle_id", "unknown"),
            schema_version=bundle.get("schema_version", "1.0.0"),
            hard_rules=hard_rules,
            soft_rules=soft_rules,
            metadata=bundle.get("metadata", {}),
        )

    @lru_cache(maxsize=256)
    def compile_cached(self, bundle_sha: str, bundle: str) -> CompiledPolicy:
        import json

        parsed = json.loads(bundle)
        return self.compile(parsed)

    def get_or_compile(self, bundle: Dict[str, Any]) -> CompiledPolicy:
        import json

        canonical = json.dumps(bundle, sort_keys=True, separators=(",", ":"))
        digest = self._compute_digest(canonical)
        return self.compile_cached(digest, canonical)

    @staticmethod
    def _compute_digest(payload: str) -> str:
        import hashlib

        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

