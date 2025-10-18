"""
Rule Authoring API
==================

Provides demo endpoints for the rule authoring experience. The implementation
integrates the orchestrator, tracks guardrail utilisation, and emits activation
warnings so the frontend summary cards can display live data.
"""

from __future__ import annotations

import logging
import json
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.policies.starter_pack import get_starter_pack_bundle
from app.services.rule_authoring.analysis import (
    diff_against_starter_pack,
    summarise_guardrail_usage,
)
from app.services.rule_authoring.chat_service import RuleAuthoringChatService
from app.services.rule_authoring.demo_llm_factory import DemoLLMFactory
from app.services.rule_authoring.rules_repository import RuleBundleRPC
from app.services.llm.llm_factory import LLMFactory
from app.db.supabase_client import get_supabase_client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/rule-authoring", tags=["Rule Authoring"])

_chat_service: Optional[RuleAuthoringChatService] = None
_rule_repo: Optional[RuleBundleRPC] = None
_active_sessions: Dict[str, RuleAuthoringSessionState] = {}


class CreateSessionRequest(BaseModel):
    clinic_id: Optional[str] = Field(default=None, alias="clinicId")
    administrator_id: Optional[str] = Field(default=None, alias="administratorId")
    clinic_name: str = Field(alias="clinicName")
    administrator_name: str = Field(alias="administratorName")
    starter_pack_enabled: bool = Field(default=True, alias="starterPackEnabled")
    supplements: Optional[list[str]] = None
    additional_context: Optional[str] = Field(default=None, alias="additionalContext")

    class Config:
        allow_population_by_field_name = True


class SendMessageRequest(BaseModel):
    message: str


async def _get_chat_service() -> RuleAuthoringChatService:
    global _chat_service, _rule_repo
    if _chat_service is None:
        supabase_client = None
        llm_factory = None

        try:
            supabase_client = get_supabase_client()
            llm_factory = LLMFactory(supabase_client)
            logger.info("Rule authoring API initialised with production LLMFactory.")
            _rule_repo = RuleBundleRPC(supabase_client)
        except Exception as exc:  # pragma: no cover - safety fallback
            logger.warning(
                "Falling back to DemoLLMFactory for rule authoring API: %s",
                exc,
            )
            llm_factory = DemoLLMFactory()
            _rule_repo = None

        _chat_service = RuleAuthoringChatService(
            supabase_client=supabase_client,
            llm_factory=llm_factory,
            tool_handlers={},  # TODO: wire real tool handlers (list_entities, fetch_current_rules, etc.)
        )
    return _chat_service


def _serialise_message(payload: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "role": payload.get("role"),
        "content": payload.get("content"),
        **(
            {"metadata": payload.get("metadata")}
            if payload.get("metadata")
            else {}
        ),
    }


def _serialise_usage(state: RuleAuthoringSessionState) -> Dict[str, Any]:
    usage = summarise_guardrail_usage(state)
    return {
        "turnsUsed": usage.turns_used,
        "maxTurns": usage.max_turns,
        "toolCallsUsed": usage.tool_calls_used,
        "maxToolCalls": usage.max_tool_calls,
        "usdSpent": usage.usd_spent,
        "budgetUsd": usage.budget_usd,
    }


def _activation_payload(state: RuleAuthoringSessionState) -> Dict[str, Any]:
    bundle = state.metadata.get("draft_bundle")
    warnings = diff_against_starter_pack(bundle)
    return {
        "bundle": bundle,
        "warnings": warnings,
    }


def _capture_structured_payload(state: RuleAuthoringSessionState, reply: str) -> bool:
    if not reply:
        return False

    try:
        payload = json.loads(reply)
    except (TypeError, json.JSONDecodeError):
        return False

    if isinstance(payload, dict):
        state.metadata["draft_bundle"] = payload
        validation_messages = payload.get("validationMessages") or payload.get("validation_messages")
        if isinstance(validation_messages, list):
            state.metadata["validation_messages"] = [
                str(message) for message in validation_messages
            ]
        return True

    return False


async def _load_existing_bundle(state: RuleAuthoringSessionState, clinic_id: Optional[str]) -> None:
    if not clinic_id or _rule_repo is None:
        return

    try:
        response = await _rule_repo.fetch_clinic_bundle(
            clinic_id,
            include_history=False,
            include_metadata=True,
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Failed to fetch existing rule bundle for clinic %s: %s", clinic_id, exc)
        return

    active_bundle = (response or {}).get("active_bundle")
    if isinstance(active_bundle, dict):
        bundle = active_bundle.get("bundle")
        if isinstance(bundle, dict):
            state.metadata["draft_bundle"] = bundle
            state.metadata["current_snapshot"] = active_bundle

    if isinstance(response, dict) and response.get("history"):
        state.metadata["bundle_history"] = response["history"]


async def _persist_bundle_from_state(state: RuleAuthoringSessionState) -> Optional[Dict[str, Any]]:
    if _rule_repo is None:
        return None

    clinic_id = state.metadata.get("clinic_id")
    if not clinic_id:
        return None

    bundle = state.metadata.get("draft_bundle")
    if not isinstance(bundle, dict):
        return None

    metadata = {
        "source": "rule_authoring_api",
        "session_id": state.session_id,
        "administrator_id": state.metadata.get("administrator_id"),
        "validation_messages": state.metadata.get("validation_messages", []),
    }

    actor_id = state.metadata.get("administrator_id")

    try:
        result = await _rule_repo.upsert_clinic_bundle(
            clinic_id=clinic_id,
            bundle=bundle,
            status="draft",
            metadata=metadata,
            actor_id=actor_id,
        )
        state.metadata["last_snapshot"] = result
        return result
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Failed to upsert rule bundle for clinic %s: %s", clinic_id, exc)
        return None


@router.post("/session")
async def start_rule_authoring_session(payload: CreateSessionRequest):
    service = await _get_chat_service()

    state = await service.start_chat(
        clinic_name=payload.clinic_name,
        administrator_name=payload.administrator_name,
        clinic_id=payload.clinic_id,
        tenant_id=None,
        administrator_id=payload.administrator_id,
        starter_pack_enabled=payload.starter_pack_enabled,
        supplements=payload.supplements,
        additional_context=payload.additional_context,
    )

    if not state.session_id:
        raise HTTPException(status_code=500, detail="Failed to create rule authoring session.")

    state.metadata["clinic_id"] = payload.clinic_id
    state.metadata["administrator_id"] = payload.administrator_id

    if payload.clinic_id:
        await _load_existing_bundle(state, payload.clinic_id)

    if "draft_bundle" not in state.metadata or not isinstance(state.metadata.get("draft_bundle"), dict):
        # Seed with the starter pack bundle so activation warnings are immediately available.
        state.metadata["draft_bundle"] = get_starter_pack_bundle()

    _active_sessions[state.session_id] = state

    return {
        "sessionId": state.session_id,
        "messages": [_serialise_message(msg) for msg in state.messages],
        "guardrailUsage": _serialise_usage(state),
        "activationSummary": _activation_payload(state),
    }


@router.post("/session/{session_id}/messages")
async def send_rule_authoring_message(session_id: str, payload: SendMessageRequest):
    service = await _get_chat_service()
    state = _active_sessions.get(session_id)
    if state is None:
        raise HTTPException(status_code=404, detail="Session not found. Start a new conversation.")

    try:
        reply = await service.send_user_message(state, payload.message)
    except RuleAuthoringGuardrailError as exc:
        usage = _serialise_usage(state)
        logger.warning("Guardrail triggered for session %s: %s", session_id, exc)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"error": str(exc), "guardrailUsage": usage},
        ) from exc
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("Failed to process rule authoring message: {0}", exc)
        raise HTTPException(status_code=500, detail="Failed to process the message.") from exc

    # For the demo flow we simply re-use the baseline bundle. In a full integration this
    # would be replaced by the assistant's proposed bundle returned from the LLM.
    if "draft_bundle" not in state.metadata:
        state.metadata["draft_bundle"] = get_starter_pack_bundle()

    captured = _capture_structured_payload(state, reply)

    if captured:
        await _persist_bundle_from_state(state)

    response_payload = {
        "reply": reply,
        "messages": [_serialise_message(msg) for msg in state.messages],
        "bundle": state.metadata.get("draft_bundle"),
        "validationMessages": state.metadata.get("validation_messages", []),
        "guardrailUsage": _serialise_usage(state),
        "activationSummary": _activation_payload(state),
    }

    return response_payload
