"""Persistence helpers for rule authoring transcripts."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4


logger = logging.getLogger(__name__)


class RuleAuthoringTranscriptRepository:
    """Stores session transcripts and usage metrics in Supabase."""

    def __init__(self, supabase_client):
        self.supabase = supabase_client

    async def create_session(
        self,
        clinic_id: Optional[str],
        administrator_id: Optional[str],
        config: Dict[str, Any]
    ) -> str:
        session_id = str(uuid4())
        payload = {
            "id": session_id,
            "clinic_id": clinic_id,
            "administrator_id": administrator_id,
            "config": config,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        await self._execute_insert("rule_authoring_sessions", payload)
        return session_id

    async def append_messages(
        self,
        session_id: str,
        messages: List[Dict[str, Any]]
    ) -> None:
        if not messages:
            return

        payload = []
        for msg in messages:
            entry = {
                "id": str(uuid4()),
                "session_id": session_id,
                "role": msg.get("role"),
                "content": msg.get("content"),
                "metadata": {
                    key: value
                    for key, value in msg.items()
                    if key not in {"role", "content"}
                },
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            payload.append(entry)

        await self._execute_insert("rule_authoring_messages", payload)

    async def update_usage(
        self,
        session_id: str,
        *,
        usd_spent: float,
        input_tokens: int,
        output_tokens: int
    ) -> None:
        payload = {
            "usd_spent": usd_spent,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        await self._execute_update("rule_authoring_sessions", session_id, payload)

    async def _execute_insert(self, table: str, payload: Any) -> None:
        if self.supabase is None:
            return

        def _insert():
            self.supabase.table(table).insert(payload).execute()

        try:
            await asyncio.to_thread(_insert)
        except Exception as exc:  # pragma: no cover - best effort logging
            logger.warning("Failed to insert rule authoring payload into %s: %s", table, exc)

    async def _execute_update(self, table: str, row_id: str, payload: Dict[str, Any]) -> None:
        if self.supabase is None:
            return

        def _update():
            self.supabase.table(table).update(payload).eq("id", row_id).execute()

        try:
            await asyncio.to_thread(_update)
        except Exception as exc:  # pragma: no cover
            logger.warning("Failed to update rule authoring usage for %s: %s", row_id, exc)

