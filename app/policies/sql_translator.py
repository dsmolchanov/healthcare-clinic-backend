"""
Translate rule bundles into SQL predicates for historical simulation queries.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Tuple


@dataclass
class RulePredicate:
    rule_id: str
    effect_type: str
    predicate_sql: str


JsonPath = str


def _json_field(path: str, context_column: str) -> JsonPath:
    parts = path.split(".")
    json_path = "{" + ",".join(parts) + "}"
    return f"{context_column} #>> '{json_path}'"


def _format_value(value: Any) -> str:
    if isinstance(value, bool):
        return "'true'" if value else "'false'"
    if value is None:
        return "NULL"
    if isinstance(value, (int, float)):
        return str(value)
    return f"'{str(value)}'"


def _combine(expressions: Iterable[str], operator: str) -> str:
    exprs = [expr for expr in expressions if expr]
    if not exprs:
        return "TRUE"
    if len(exprs) == 1:
        return exprs[0]
    joined = f" {operator} ".join(exprs)
    return f"({joined})"


def _negate(expr: str) -> str:
    if not expr:
        return "TRUE"
    return f"(NOT ({expr}))"


def _comparison_sql(field_sql: str, operator: str, value: Any) -> str:
    formatted = _format_value(value)

    def _coalesce_text(expr: str) -> str:
        return f"COALESCE({expr}, '')"

    if operator == "equals":
        return f"({field_sql} = {formatted})"
    if operator == "not_equals":
        return f"({field_sql} <> {formatted})"
    if operator == "greater_than":
        return f"(({field_sql})::numeric > {formatted})"
    if operator == "greater_or_equal":
        return f"(({field_sql})::numeric >= {formatted})"
    if operator == "less_than":
        return f"(({field_sql})::numeric < {formatted})"
    if operator == "less_or_equal":
        return f"(({field_sql})::numeric <= {formatted})"
    if operator == "contains":
        return f"(POSITION({formatted} IN {_coalesce_text(field_sql)}) > 0)"
    if operator == "not_contains":
        return (
            f"(POSITION({formatted} IN {_coalesce_text(field_sql)}) = 0 "
            f"OR {field_sql} IS NULL)"
        )
    if operator == "starts_with":
        prefix = formatted.strip("'")
        return f"({field_sql} LIKE '{prefix}%')"
    if operator == "ends_with":
        suffix = formatted.strip("'")
        return f"({field_sql} LIKE '%{suffix}')"
    if operator == "regex":
        return f"({field_sql} ~ {formatted})"
    if operator == "in":
        values = ", ".join(_format_value(v) for v in value)
        return f"({field_sql} IN ({values}))"
    if operator == "not_in":
        values = ", ".join(_format_value(v) for v in value)
        return f"({field_sql} NOT IN ({values}))"
    if operator == "between":
        lower, upper = value
        return (
            f"(({field_sql})::numeric >= {_format_value(lower)} "
            f"AND ({field_sql})::numeric <= {_format_value(upper)})"
        )
    return "TRUE"


def condition_to_sql(
    condition: Dict[str, Any], context_column: str = "context"
) -> str:
    if "all" in condition:
        return _combine(
            (condition_to_sql(child, context_column) for child in condition["all"]),
            "AND",
        )

    if "any" in condition:
        return _combine(
            (condition_to_sql(child, context_column) for child in condition["any"]),
            "OR",
        )

    if "none" in condition:
        expr = _combine(
            (condition_to_sql(child, context_column) for child in condition["none"]),
            "OR",
        )
        return _negate(expr)

    if "not" in condition:
        child_expr = condition_to_sql(condition["not"], context_column)
        return _negate(child_expr)

    field_sql = _json_field(condition["field"], context_column)
    operator = condition["operator"]
    value = condition.get("value")

    if operator == "is_null":
        return f"({field_sql} IS NULL)"
    if operator == "is_not_null":
        return f"({field_sql} IS NOT NULL)"

    return _comparison_sql(field_sql, operator, value)


def bundle_to_predicates(
    bundle: Dict[str, Any], context_column: str = "context"
) -> List[RulePredicate]:
    predicates: List[RulePredicate] = []
    for rule in bundle.get("rules", []):
        condition = rule.get("conditions") or {"all": []}
        predicate = condition_to_sql(condition, context_column)
        predicates.append(
            RulePredicate(
                rule_id=rule["rule_id"],
                effect_type=rule["effect"]["type"],
                predicate_sql=predicate,
            )
        )
    return predicates


def materialized_view_sql(
    bundle: Dict[str, Any],
    source_table: str,
    view_name: str,
    context_column: str = "context",
) -> str:
    predicates = bundle_to_predicates(bundle, context_column)
    select_clauses = [
        f"    ({predicate.predicate_sql}) AS {predicate.rule_id.lower()}"
        for predicate in predicates
    ]
    select_section = ",\n".join(
        ["    t.*"] + select_clauses
    )
    return (
        f"CREATE MATERIALIZED VIEW {view_name} AS\n"
        f"SELECT\n{select_section}\n"
        f"FROM {source_table} AS t;"
    )
