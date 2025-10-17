"""
Helpers for migrating legacy policy snapshot rows into schema v1 bundles.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from typing import Any, Dict, Optional

from .validator import RuleBundleValidator

DEFAULT_SCHEMA_VERSION = "1.0.0"


@dataclass
class SnapshotRow:
    id: str
    clinic_id: str
    version: int
    sha256: Optional[str]
    status: str
    rules: Any
    constraints: Any
    preferences: Any
    patterns: Any
    metadata: Dict[str, Any]
    compiled_by: Optional[str]
    compiled_at: Optional[datetime]
    active: bool


def build_bundle_from_snapshot(row: SnapshotRow) -> Dict[str, Any]:
    """Construct schema v1 bundle JSON from a legacy snapshot row."""
    metadata = row.metadata or {}

    bundle_id = metadata.get("bundle_id") or row.id
    generated_at = (row.compiled_at or datetime.utcnow()).isoformat()

    extensions_payload: Dict[str, Any] = {}
    if row.constraints not in (None, [], {}):
        extensions_payload["legacy_constraints"] = row.constraints
    if row.preferences not in (None, [], {}):
        extensions_payload["legacy_preferences"] = row.preferences
    if row.patterns not in (None, [], {}):
        extensions_payload["legacy_patterns"] = row.patterns
    if row.sha256:
        extensions_payload["legacy_sha256"] = row.sha256

    bundle: Dict[str, Any] = {
        "schema_version": DEFAULT_SCHEMA_VERSION,
        "bundle_id": bundle_id,
        "generated_at": generated_at,
        "clinic_id": row.clinic_id,
        "author": metadata.get("author"),
        "description": metadata.get("description"),
        "rules": row.rules or [],
        "metadata": metadata,
    }

    tenant_id = metadata.get("tenant_id")
    if tenant_id:
        bundle["tenant_id"] = tenant_id

    if extensions_payload:
        bundle["extensions"] = extensions_payload

    return bundle


def compute_bundle_digest(bundle: Dict[str, Any]) -> str:
    """Compute canonical SHA-256 digest for bundle JSON."""
    validator = RuleBundleValidator()
    problems = validator.validate_dict(bundle)
    if problems:
        formatted = ", ".join(p.format() for p in problems)
        raise ValueError(f"Bundle failed validation: {formatted}")

    import json

    canonical = json.dumps(bundle, sort_keys=True, separators=(",", ":"))
    return sha256(canonical.encode("utf-8")).hexdigest()
