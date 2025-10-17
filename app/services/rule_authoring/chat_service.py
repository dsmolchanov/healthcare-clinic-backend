"""High-level service wrapping the rule authoring orchestrator with storage and compliance logging."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.services.audit_logger import AuditLogger, AuditEventType, AuditSeverity
from app.services.rule_authoring.orchestrator import (
    RuleAuthoringOrchestrator,
    RuleAuthoringSessionState,
)
from app.services.rule_authoring.storage import RuleAuthoringTranscriptRepository


class RuleAuthoringChatService:
    """Composite service that orchestrates chat, storage, and compliance logging."""

    def __init__(
        self,
        supabase_client,
        llm_factory,
        tool_handlers: Optional[Dict[str, Any]] = None,
        orchestrator: Optional[RuleAuthoringOrchestrator] = None,
        repository: Optional[RuleAuthoringTranscriptRepository] = None,
    ):
        self.supabase = supabase_client
        self.repository = repository or RuleAuthoringTranscriptRepository(supabase_client)
        self.orchestrator = orchestrator or RuleAuthoringOrchestrator(llm_factory, tool_handlers=tool_handlers)
        self.tool_handlers = tool_handlers or {}

    async def start_chat(
        self,
        clinic_name: str,
        administrator_name: str,
        *,
        clinic_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
        administrator_id: Optional[str] = None,
        starter_pack_enabled: bool = True,
        supplements: Optional[List[str]] = None,
        additional_context: Optional[str] = None,
    ) -> RuleAuthoringSessionState:
        state = await self.orchestrator.start_session(
            clinic_name=clinic_name,
            administrator_name=administrator_name,
            clinic_id=clinic_id,
            tenant_id=tenant_id,
            starter_pack_enabled=starter_pack_enabled,
            supplements=supplements,
            additional_context=additional_context,
        )

        session_id = await self.repository.create_session(
            clinic_id=clinic_id,
            administrator_id=administrator_id,
            config={
                "model": state.config.reasoning_model,
                "max_turns": state.config.max_conversation_turns,
                "budget_usd": state.config.budget_usd_per_session,
                "max_tool_calls": state.config.max_tool_calls,
            },
        )
        state.session_id = session_id

        await self.repository.append_messages(session_id, state.messages)
        state.stored_messages = len(state.messages)

        audit_logger = AuditLogger(clinic_id=clinic_id)
        await audit_logger.log_event(
            event_type=AuditEventType.SESSION_CREATED,
            action="rule_authoring_session_start",
            result="success",
            user_id=administrator_id,
            severity=AuditSeverity.INFO,
            resource=session_id,
            details={
                "clinic_name": clinic_name,
                "administrator_name": administrator_name,
                "starter_pack": starter_pack_enabled,
            },
        )
        state.metadata["audit_logger"] = audit_logger

        return state

    async def send_user_message(
        self,
        state: RuleAuthoringSessionState,
        message: str,
        *,
        user_id: Optional[str] = None
    ) -> str:
        if state.session_id is None:
            raise ValueError("Session must be started before sending messages.")

        previous_count = state.stored_messages
        reply = await self.orchestrator.process_user_message(state, message)
        await self.repository.append_messages(
            state.session_id,
            state.messages[previous_count:]
        )
        state.stored_messages = len(state.messages)

        await self.repository.update_usage(
            state.session_id,
            usd_spent=state.usd_spent,
            input_tokens=state.input_tokens_total,
            output_tokens=state.output_tokens_total,
        )

        audit_logger: AuditLogger = state.metadata.get("audit_logger", AuditLogger(clinic_id=state.clinic_id))
        await audit_logger.log_event(
            event_type=AuditEventType.MESSAGE_SENT,
            action="rule_authoring_user_message",
            result="success",
            user_id=user_id,
            resource=state.session_id,
            details={"message": message[:2000]},
        )
        await audit_logger.log_event(
            event_type=AuditEventType.MESSAGE_RECEIVED,
            action="rule_authoring_assistant_reply",
            result="success",
            user_id=user_id,
            resource=state.session_id,
            details={"reply": reply[:2000]},
        )

        return reply
