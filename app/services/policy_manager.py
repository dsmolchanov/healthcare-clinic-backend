"""
Policy manager that loads compiled policies with metadata and caching.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, Optional
from uuid import UUID

from app.policies import PolicyCompiler
from app.policies.compiler import CompiledPolicy
from app.policies.starter_pack import get_starter_pack_bundle


@dataclass(frozen=True)
class ActivePolicy:
    clinic_id: UUID
    policy: CompiledPolicy
    snapshot_id: Optional[UUID]
    version: Optional[int]
    bundle_sha: Optional[str]
    bundle: Dict[str, Any]


class PolicyManager:
    """Loads active policy snapshots and compiles them with caching."""

    def __init__(self, db, compiler: Optional[PolicyCompiler] = None):
        self.db = db
        self.compiler = compiler or PolicyCompiler()
        self._cache: Dict[str, Dict[str, Any]] = {}

    async def get_active_policy(self, clinic_id: UUID) -> ActivePolicy:
        cache_key = str(clinic_id)
        cached = self._cache.get(cache_key)
        if cached:
            fetched_at = cached.get("fetched_at")
            if fetched_at and datetime.utcnow() - fetched_at < timedelta(minutes=5):
                return cached["entry"]

        entry = self._load_policy(clinic_id)
        self._cache[cache_key] = {
            "entry": entry,
            "fetched_at": datetime.utcnow()
        }
        return entry

    def _load_policy(self, clinic_id: UUID) -> ActivePolicy:
        try:
            result = self.db.table("policy_snapshots")\
                .select("id, version, bundle, bundle_sha256")\
                .eq("clinic_id", str(clinic_id))\
                .eq("active", True)\
                .limit(1)\
                .execute()

            if result.data:
                row = result.data[0]
                bundle = row.get("bundle") or {}
                snapshot_id = UUID(row["id"])
                version = row.get("version")
                bundle_sha = row.get("bundle_sha256")
            else:
                bundle = get_starter_pack_bundle(bundle_id=f"{clinic_id}-starter")
                snapshot_id = None
                version = None
                bundle_sha = None
        except Exception as exc:
            # Fallback to starter pack on any failure
            bundle = get_starter_pack_bundle(bundle_id=f"{clinic_id}-starter")
            snapshot_id = None
            version = None
            bundle_sha = None

        if not bundle_sha:
            canonical = json.dumps(bundle, sort_keys=True, separators=(",", ":"))
            bundle_sha = hashlib.sha256(canonical.encode("utf-8")).hexdigest()

        compiled = self.compiler.get_or_compile(bundle)
        return ActivePolicy(
            clinic_id=clinic_id,
            policy=compiled,
            snapshot_id=snapshot_id,
            version=version,
            bundle_sha=bundle_sha,
            bundle=bundle
        )

