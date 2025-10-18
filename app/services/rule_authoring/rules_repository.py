"""
Supabase RPC helpers for rule bundle persistence.

These functions are intended to be consumed by API handlers that need to load or
persist rule bundles (policy snapshots).  They wrap the centralized Supabase
client so we have a single place to manage payload shapes and error handling.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional, Tuple

from app.db.supabase_client import get_supabase_client

logger = logging.getLogger(__name__)


class RuleBundleRPC:
    """Convenience wrapper around Supabase RPCs for rule authoring."""

    FETCH_FUNCTION = "rule_authoring_fetch_rules"
    UPSERT_FUNCTION = "rule_authoring_upsert_rules"

    def __init__(self, supabase_client=None):
        self.supabase = supabase_client or get_supabase_client()

    async def fetch_clinic_bundle(
        self,
        clinic_id: str,
        *,
        include_history: bool = False,
        include_metadata: bool = True,
    ) -> Dict[str, Any]:
        """
        Fetch the latest rule bundle (and optionally historical snapshots) for a clinic.

        Returns whatever payload the Postgres function supplies.  The default contract
        should include an `active_bundle` key plus optional `history`.
        """
        params = {
            "p_clinic_id": str(clinic_id),
            "p_include_history": include_history,
            "p_include_metadata": include_metadata,
        }
        return await self._call_rpc(self.FETCH_FUNCTION, params)

    async def upsert_clinic_bundle(
        self,
        clinic_id: str,
        *,
        bundle: Dict[str, Any],
        status: str = "draft",
        metadata: Optional[Dict[str, Any]] = None,
        actor_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Upsert (insert or update) a rule bundle snapshot for the clinic.

        Args:
            clinic_id: UUID string for the clinic.
            bundle: JSON-serialisable rule bundle matching the rule schema.
            status: Desired snapshot status (draft, staged, active, etc.).
            metadata: Optional metadata payload stored alongside the bundle.
            actor_id: Optional user ID performing the change (for auditing).
        """
        payload = {
            "p_clinic_id": str(clinic_id),
            "p_bundle": bundle,
            "p_status": status,
            "p_metadata": metadata or {},
            "p_actor_id": actor_id,
        }
        return await self._call_rpc(self.UPSERT_FUNCTION, payload)

    async def _call_rpc(self, function_name: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """Execute a Supabase RPC in a thread to avoid blocking the event loop."""

        def _execute():
            response = self.supabase.rpc(function_name, params).execute()
            error = getattr(response, "error", None)
            if error:
                raise RuntimeError(f"{function_name} failed: {error}")
            data = getattr(response, "data", None)
            if data is None:
                logger.warning("RPC %s returned no data.", function_name)
            return data

        return await asyncio.to_thread(_execute)
