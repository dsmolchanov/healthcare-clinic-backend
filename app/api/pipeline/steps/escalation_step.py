"""
EscalationCheckStep - Check if conversation should be escalated to human.

Extracted from process_message() lines 443-484.

Phase 2A of the Agentic Flow Architecture Refactor.
"""

import logging
from typing import Tuple

from ..base import PipelineStep
from ..context import PipelineContext

logger = logging.getLogger(__name__)


class EscalationCheckStep(PipelineStep):
    """
    Check if conversation should be escalated to human agent.

    Responsibilities:
    1. Analyze conversation context for escalation triggers
    2. If escalation needed, store holding message and stop pipeline
    3. Return escalation response without proceeding to LLM
    """

    def __init__(self, escalation_handler=None, memory_manager=None):
        """
        Initialize with EscalationHandler.

        Args:
            escalation_handler: EscalationHandler for checking/performing escalation
            memory_manager: ConversationMemory for storing escalation message
        """
        self._escalation_handler = escalation_handler
        self._memory_manager = memory_manager

    @property
    def name(self) -> str:
        return "escalation_check"

    async def execute(self, ctx: PipelineContext) -> Tuple[PipelineContext, bool]:
        """
        Check if conversation should be escalated.

        If escalation is triggered:
        - Sets ctx.response to holding message
        - Sets ctx.should_escalate = True
        - Sets ctx.escalation_reason
        - Returns should_continue=False to stop pipeline

        Otherwise:
        - Returns should_continue=True to proceed
        """
        if not self._escalation_handler:
            return ctx, True

        # Build conversation context for analysis
        conversation_context = "\n".join([
            f"{msg['role']}: {msg['content']}"
            for msg in ctx.session_messages[-5:]
        ])

        # Check if should escalate
        escalation_check = await self._escalation_handler.check_if_should_escalate(
            conversation_context=conversation_context,
            user_message=ctx.message
        )

        if not escalation_check['should_escalate']:
            return ctx, True

        # Escalation triggered
        logger.warning(f"⚠️ Escalating conversation: {escalation_check['reason']}")

        ctx.should_escalate = True
        ctx.escalation_reason = escalation_check['reason']

        # Perform escalation
        escalation_result = await self._escalation_handler.escalate_conversation(
            session_id=ctx.session_id,
            reason=escalation_check['reason'],
            metadata={'confidence': escalation_check['confidence']}
        )

        ctx.escalation_result = escalation_result

        # Store escalation message
        if self._memory_manager:
            await self._memory_manager.store_message(
                session_id=ctx.session_id,
                role='assistant',
                content=escalation_result['holding_message'],
                phone_number=ctx.from_phone,
                metadata={
                    'escalated': True,
                    'reason': escalation_check['reason']
                }
            )

        # Set response and stop pipeline
        ctx.response = escalation_result['holding_message']
        ctx.detected_language = "multilingual"  # Holding messages are usually multilingual
        ctx.response_metadata = {
            'escalated': True,
            'reason': escalation_check['reason']
        }

        logger.info(f"🆘 Escalation complete, returning holding message")

        # Stop pipeline - response is ready
        return ctx, False
