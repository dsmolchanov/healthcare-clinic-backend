"""
Scheduling Rule Creation API
============================

Provides an LLM-assisted workflow for creating clinic scheduling rules by
chatting with an agent. The agent gathers requirements, produces a structured
JSON rule definition, and persists the final rule to Supabase once approved.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.db.supabase_client import get_supabase_client
from app.services.llm.llm_factory import LLMFactory

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/scheduling-rules", tags=["Scheduling Rules"])

SYSTEM_PROMPT = """You are the Scheduling Rule Creation Agent for Plaintalk.
Work with clinic administrators to define scheduling guardrails and preferences.

Mandatory fields for every rule:
- name: concise 3-5 word title.
- description: one or two sentences summarising the behaviour.
- scope: one of ["clinic", "organization", "service:<id>", "doctor:<id>", "global"].
- category: choose from ["preference", "guardrail", "escalation", "compliance", "custom"].
- weight: integer 0-20 representing relative importance.
- precedence: integer 0-100 indicating ordering priority (higher = evaluated earlier).
- enabled: boolean.

Optional but recommended:
- triggers: array of bullet strings describing when to apply the rule.
- actions: array of bullet strings describing what to do when triggered.
- notes: additional free-form guidance.

Workflow:
1. Ask targeted questions to capture intent, impacted services/doctors, guardrails, etc.
2. Summarise back what you heard and confirm accuracy.
3. When the user requests finalisation or you have enough detail, produce a JSON
   object that includes ALL mandatory fields plus any optional fields provided.
   Return the JSON inside a ```json code block so it can be parsed.
4. After the JSON, provide a short natural language summary (<60 words).

Never invent clinic-specific identifiers; if you need a concrete ID, ask the
user or use placeholders like "service:cleaning-basic". Do not submit the JSON
until the user approves the draft."""


class CreateSessionRequest(BaseModel):
    clinic_id: Optional[str] = Field(default=None, alias="clinicId")
    clinic_name: Optional[str] = Field(default=None, alias="clinicName")
    administrator_id: Optional[str] = Field(default=None, alias="administratorId")
    administrator_name: Optional[str] = Field(default=None, alias="administratorName")

    class Config:
        allow_population_by_field_name = True


class SendMessageRequest(BaseModel):
    message: str


class FinaliseRequest(BaseModel):
    force: bool = Field(
        default=False,
        description="Persist even if the agent has not produced structured JSON yet.",
    )


class SessionNotFoundError(Exception):
    """Raised when a chat session is not found."""


@dataclass
class SchedulingRuleChatState:
    session_id: str
    clinic_id: Optional[str]
    clinic_name: Optional[str]
    administrator_id: Optional[str]
    administrator_name: Optional[str]
    messages: List[Dict[str, str]] = field(default_factory=list)
    draft_rule: Optional[Dict[str, Any]] = None
    closed: bool = False

    def visible_messages(self) -> List[Dict[str, str]]:
        return [msg for msg in self.messages if msg.get("role") != "system"]


_sessions: Dict[str, SchedulingRuleChatState] = {}
_llm_factory: Optional[LLMFactory] = None
_supabase_client = None


async def _ensure_dependencies() -> LLMFactory:
    global _llm_factory, _supabase_client
    if _llm_factory is None or _supabase_client is None:
        supabase_client = get_supabase_client(schema="healthcare")
        _supabase_client = supabase_client
        _llm_factory = LLMFactory(supabase_client)
        logger.info("Scheduling rule chat API initialised with LLMFactory.")
    return _llm_factory


def _extract_rule_json(reply: str) -> Optional[Dict[str, Any]]:
    if not reply:
        return None

    # Look for ```json ... ``` blocks first
    json_block = re.search(r"```json\s*(\{.*?\})\s*```", reply, flags=re.DOTALL)
    payload_str = json_block.group(1) if json_block else None

    if not payload_str:
        # Try to parse entire reply if it's raw JSON
        trimmed = reply.strip()
        if trimmed.startswith("{") and trimmed.endswith("}"):
            payload_str = trimmed

    if not payload_str:
        return None

    try:
        parsed = json.loads(payload_str)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError as exc:
        logger.debug("Failed to parse rule JSON from reply: %s", exc)

    return None


def _normalise_rule(rule: Dict[str, Any]) -> Dict[str, Any]:
    """Ensure the rule dict contains the canonical fields with sane defaults."""
    def _get_int(name: str, default: int, min_value: int, max_value: int) -> int:
        value = rule.get(name, default)
        try:
            value_int = int(value)
        except (TypeError, ValueError):
            value_int = default
        return max(min_value, min(max_value, value_int))

    normalised = {
        "name": str(rule.get("name", "Untitled Rule")).strip() or "Untitled Rule",
        "description": str(rule.get("description", "")).strip(),
        "category": (str(rule.get("category", "custom")).strip() or "custom").lower(),
        "scope": str(rule.get("scope", "clinic")).strip() or "clinic",
        "enabled": bool(rule.get("enabled", True)),
        "weight": _get_int("weight", default=10, min_value=0, max_value=20),
        "precedence": _get_int("precedence", default=50, min_value=0, max_value=100),
        "triggers": rule.get("triggers") or [],
        "actions": rule.get("actions") or [],
        "notes": rule.get("notes"),
        "raw": rule,
    }
    return normalised


def _get_session(session_id: str) -> SchedulingRuleChatState:
    state = _sessions.get(session_id)
    if state is None or state.closed:
        raise SessionNotFoundError(session_id)
    return state


async def _generate_assistant_reply(state: SchedulingRuleChatState) -> str:
    factory = await _ensure_dependencies()
    response = await factory.generate(
        messages=state.messages,
        temperature=0.35,
        max_tokens=700,
        prefer_speed=True,
    )
    reply = response.content or ""
    logger.debug("LLM reply for session %s: %s", state.session_id, reply)
    return reply


def _serialise_state(state: SchedulingRuleChatState) -> Dict[str, Any]:
    payload = {
        "sessionId": state.session_id,
        "messages": state.visible_messages(),
    }
    if state.draft_rule:
        payload["draftRule"] = _normalise_rule(state.draft_rule)
    return payload


@router.post("/session")
async def create_session(payload: CreateSessionRequest):
    await _ensure_dependencies()

    session_id = str(uuid.uuid4())
    state = SchedulingRuleChatState(
        session_id=session_id,
        clinic_id=payload.clinic_id,
        clinic_name=payload.clinic_name,
        administrator_id=payload.administrator_id,
        administrator_name=payload.administrator_name,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
        ],
    )

    # Seed the conversation with an opening assistant turn
    opening_user_prompt = (
        "Introduce yourself as the Plaintalk scheduling rule builder. "
        "Ask the administrator what rule or guardrail they want to design first."
    )
    state.messages.append({"role": "user", "content": opening_user_prompt})
    assistant_reply = await _generate_assistant_reply(state)

    # Replace the synthetic user turn with the generated assistant reply
    state.messages.pop()  # remove synthetic user prompt
    state.messages.append({"role": "assistant", "content": assistant_reply})

    _sessions[session_id] = state
    logger.info("Started scheduling rule chat session %s", session_id)

    return _serialise_state(state)


@router.post("/session/{session_id}/messages")
async def send_message(session_id: str, payload: SendMessageRequest):
    try:
        state = _get_session(session_id)
    except SessionNotFoundError:
        raise HTTPException(status_code=404, detail="Session not found. Start a new conversation.")

    user_message = payload.message.strip()
    if not user_message:
        raise HTTPException(status_code=400, detail="Message cannot be empty.")

    state.messages.append({"role": "user", "content": user_message})
    assistant_reply = await _generate_assistant_reply(state)
    state.messages.append({"role": "assistant", "content": assistant_reply})

    parsed_rule = _extract_rule_json(assistant_reply)
    if parsed_rule:
        state.draft_rule = parsed_rule
        logger.info("Captured draft rule for session %s", session_id)

    return _serialise_state(state)


@router.post("/session/{session_id}/finalise")
async def finalise_rule(session_id: str, payload: FinaliseRequest = FinaliseRequest()):
    global _supabase_client
    try:
        state = _get_session(session_id)
    except SessionNotFoundError:
        raise HTTPException(status_code=404, detail="Session not found. Start a new conversation.")

    if not state.draft_rule and not payload.force:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The assistant has not produced a structured rule yet.",
        )

    if not state.clinic_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Clinic ID is required to save a scheduling rule.",
        )

    rule_payload = state.draft_rule or {}
    normalised = _normalise_rule(rule_payload)

    test_payload = {}
    if isinstance(rule_payload, dict):
        candidate = rule_payload.get("testPayload") or rule_payload.get("test_payload")
        if isinstance(candidate, (dict, list)):
            test_payload = candidate

    record = {
        "clinic_id": state.clinic_id,
        "name": normalised["name"],
        "description": normalised["description"],
        "category": normalised["category"],
        "scope": normalised["scope"],
        "enabled": normalised["enabled"],
        "weight": normalised["weight"],
        "precedence": normalised["precedence"],
        "rule_definition": normalised["raw"],
        "test_payload": test_payload,
        "conflict_checks": {"status": "not_run"},
        "conversation_log": state.visible_messages(),
    }

    try:
        response = (
            _supabase_client.schema("healthcare")
            .table("sched_custom_rules")
            .insert(record)
            .execute()
        )
        saved_rule = response.data[0] if response.data else record
        state.closed = True
        logger.info("Persisted scheduling rule %s for clinic %s", saved_rule.get("id"), state.clinic_id)
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("Failed to persist scheduling rule: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to save scheduling rule.") from exc

    return {
        "sessionId": session_id,
        "savedRule": saved_rule,
        "draftRule": normalised,
    }
