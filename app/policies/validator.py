"""
Rule bundle validation utilities.

Provides JSON Schema validation plus additional semantic checks to ensure
deterministic evaluation and safe activation of rule bundles.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError as JsonSchemaValidationError

SCHEMA_FILENAME = "rule_schema_v1.json"
SCHEMA_PATH = Path(__file__).with_name(SCHEMA_FILENAME)


@dataclass
class ValidationProblem:
    """Represents any validation issue discovered."""

    location: str
    message: str

    def format(self) -> str:
        return f"{self.location}: {self.message}"


class RuleBundleValidator:
    """Validator for rule bundles covering schema + semantic constraints."""

    def __init__(self, schema_path: Path = SCHEMA_PATH):
        self.schema_path = schema_path
        self._validator = self._build_validator(schema_path)

    @staticmethod
    def _build_validator(schema_path: Path) -> Draft202012Validator:
        if not schema_path.exists():
            raise FileNotFoundError(f"Rule schema missing at {schema_path}")

        with schema_path.open("r", encoding="utf-8") as fh:
            schema = json.load(fh)

        return Draft202012Validator(schema, format_checker=FormatChecker())

    def validate_dict(self, bundle: Dict[str, Any]) -> List[ValidationProblem]:
        """Validate an in-memory bundle and return discovered issues."""
        problems: List[ValidationProblem] = []

        # JSON Schema validation
        for error in sorted(
            self._validator.iter_errors(bundle), key=lambda e: e.path
        ):
            location = self._format_schema_path(error.path)
            problems.append(ValidationProblem(location, error.message))

        # Semantic validation runs even if schema errors occur
        problems.extend(self._semantic_checks(bundle))

        return problems

    def validate_file(self, path: Path) -> List[ValidationProblem]:
        """Load a bundle file and validate its contents."""
        try:
            with path.open("r", encoding="utf-8") as fh:
                bundle = json.load(fh)
        except FileNotFoundError:
            return [ValidationProblem(str(path), "File not found")]
        except json.JSONDecodeError as exc:
            return [
                ValidationProblem(
                    str(path),
                    f"Invalid JSON at line {exc.lineno} column {exc.colno}: {exc.msg}",
                )
            ]

        return self.validate_dict(bundle)

    @staticmethod
    def _format_schema_path(path: Iterable[Any]) -> str:
        parts: List[str] = []
        for part in path:
            part_str = str(part)
            if part_str.isdigit():
                parts.append(f"[{part_str}]")
            else:
                if parts:
                    parts.append(".")
                parts.append(part_str)
        return "".join(parts) or "<root>"

    def _semantic_checks(self, bundle: Dict[str, Any]) -> List[ValidationProblem]:
        problems: List[ValidationProblem] = []

        rules = bundle.get("rules")
        if not isinstance(rules, Sequence):
            return problems

        seen_rule_ids: Dict[str, int] = {}
        seen_precedence: Dict[int, str] = {}
        all_rule_ids: List[str] = []

        for idx, rule in enumerate(rules):
            if not isinstance(rule, dict):
                continue

            rule_id = rule.get("rule_id")
            if isinstance(rule_id, str):
                all_rule_ids.append(rule_id)
                if rule_id in seen_rule_ids:
                    problems.append(
                        ValidationProblem(
                            f"rules[{idx}].rule_id",
                            f"Duplicate rule_id '{rule_id}' also used at rules[{seen_rule_ids[rule_id]}].rule_id",
                        )
                    )
                else:
                    seen_rule_ids[rule_id] = idx

            precedence = rule.get("precedence")
            if isinstance(precedence, int):
                if precedence in seen_precedence:
                    problems.append(
                        ValidationProblem(
                            f"rules[{idx}].precedence",
                            (
                                f"Precedence {precedence} reused by rule "
                                f"'{seen_precedence[precedence]}' – precedences must be unique."
                            ),
                        )
                    )
                else:
                    seen_precedence[precedence] = rule_id or f"index-{idx}"

        known_ids = set(all_rule_ids)
        for idx, rule in enumerate(rules):
            if not isinstance(rule, dict):
                continue
            deps = rule.get("dependencies") or []
            if not isinstance(deps, Sequence):
                continue
            for dep in deps:
                if dep not in known_ids:
                    problems.append(
                        ValidationProblem(
                            f"rules[{idx}].dependencies",
                            f"Unknown dependency '{dep}' – rule id not present in bundle.",
                        )
                    )

        return problems


def load_validator() -> RuleBundleValidator:
    return RuleBundleValidator()


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate scheduling rule bundles against the v1 schema."
    )
    parser.add_argument(
        "paths",
        nargs="+",
        help="JSON bundle files to validate.",
    )
    parser.add_argument(
        "--summary",
        action="store_true",
        help="Only print summary results instead of detailed errors.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    validator = load_validator()

    had_errors = False
    for raw_path in args.paths:
        path = Path(raw_path)
        problems = validator.validate_file(path)
        if not problems:
            if not args.summary:
                print(f"{path}: OK")
            continue

        had_errors = True
        if args.summary:
            print(f"{path}: {len(problems)} issue(s) found")
        else:
            print(f"{path}:")
            for problem in problems:
                print(f"  - {problem.format()}")

    return 1 if had_errors else 0


if __name__ == "__main__":
    sys.exit(main())

