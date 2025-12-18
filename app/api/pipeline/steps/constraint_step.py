"""
ConstraintEnforcementStep - Extract and enforce conversation constraints.

Extracted from process_message() lines 582-631.

Phase 2A of the Agentic Flow Architecture Refactor.
"""

import logging
from typing import Tuple
from datetime import datetime

from ..base import PipelineStep
from ..context import PipelineContext

logger = logging.getLogger(__name__)


class ConstraintEnforcementStep(PipelineStep):
    """
    Extract and enforce conversation constraints.

    Responsibilities:
    1. Handle meta-reset commands (clear all constraints)
    2. Extract exclusions, switches, and time windows from message
    3. Update constraint storage
    4. Pass constraints to LLM step for prompt injection
    """

    def __init__(
        self,
        constraint_extractor=None,
        constraints_manager=None,
        profile_manager=None,
        memory_manager=None
    ):
        """
        Initialize with constraint services.

        Args:
            constraint_extractor: ConstraintExtractor for parsing message
            constraints_manager: ConstraintsManager for storage
            profile_manager: ProfileManager for clearing constraints
            memory_manager: ConversationMemory for storing reset messages
        """
        self._extractor = constraint_extractor
        self._manager = constraints_manager
        self._profile_manager = profile_manager
        self._memory_manager = memory_manager

    @property
    def name(self) -> str:
        return "constraint_enforcement"

    async def execute(self, ctx: PipelineContext) -> Tuple[PipelineContext, bool]:
        """
        Execute constraint enforcement step.

        Sets on context:
        - constraints
        - constraints_changed

        If meta-reset:
        - Clears all constraints
        - Sets response and stops pipeline
        """
        # 1. Handle meta-reset command
        if ctx.is_meta_reset:
            return await self._handle_meta_reset(ctx)

        # 2. Extract and update constraints
        if self._extractor and self._manager:
            ctx.constraints, ctx.constraints_changed = await self._extract_and_update_constraints(
                session_id=ctx.session_id,
                message=ctx.message,
                detected_language=ctx.detected_language
            )

            # Log active constraints for observability
            if ctx.constraints and (ctx.constraints.excluded_doctors or ctx.constraints.excluded_services):
                logger.info(
                    f"📋 Active constraints: "
                    f"desired={ctx.constraints.desired_service}, "
                    f"excluded_docs={list(ctx.constraints.excluded_doctors)}, "
                    f"excluded_svc={list(ctx.constraints.excluded_services)}, "
                    f"changed={ctx.constraints_changed}"
                )
        else:
            # No constraint management available
            from app.services.conversation_constraints import ConversationConstraints
            ctx.constraints = ConversationConstraints()
            ctx.constraints_changed = False

        return ctx, True

    async def _handle_meta_reset(self, ctx: PipelineContext) -> Tuple[PipelineContext, bool]:
        """Handle meta-reset command - clear all constraints and return confirmation."""
        logger.info("🔄 Meta-reset triggered, clearing all constraints")

        # Clear all conversation state
        if self._profile_manager:
            await self._profile_manager.clear_constraints(ctx.session_id)

        # Return confirmation message in user's language
        reset_messages = {
            'ru': 'Понял, начинаем с чистого листа! О чём вы хотите поговорить?',
            'en': 'Understood, starting fresh! What would you like to discuss?',
            'es': 'Entendido, empezamos de nuevo! ¿De qué quieres hablar?',
            'he': 'הבנתי, מתחילים מחדש! על מה תרצה לדבר?',
            'pt': 'Entendido, começando de novo! O que você gostaria de discutir?'
        }

        ctx.response = reset_messages.get(ctx.detected_language, reset_messages['en'])
        ctx.response_metadata = {'reset': True}

        # Store reset message
        if self._memory_manager:
            await self._memory_manager.store_message(
                session_id=ctx.session_id,
                role='assistant',
                content=ctx.response,
                phone_number=ctx.from_phone
            )

        logger.info("✅ Meta-reset complete")

        # Stop pipeline - response ready
        return ctx, False

    async def _extract_and_update_constraints(
        self,
        session_id: str,
        message: str,
        detected_language: str
    ) -> Tuple['ConversationConstraints', bool]:
        """
        Extract constraints from user message and update storage.

        Returns:
            Tuple of (constraints, constraints_changed)
        """
        from app.services.conversation_constraints import ConversationConstraints

        constraints_changed = False

        # Detect forget/exclusion patterns
        entities_to_exclude = self._extractor.detect_forget_pattern(message, detected_language)

        if entities_to_exclude:
            logger.info(f"🚫 Detected exclusions: {entities_to_exclude}")
            constraints_changed = True

            for entity in entities_to_exclude:
                await self._manager.update_constraints(
                    session_id,
                    exclude_doctor=entity,
                    exclude_service=entity
                )

        # Detect switch patterns ("instead of X, want Y")
        switch_result = self._extractor.detect_switch_pattern(message, detected_language)

        if switch_result and len(switch_result) == 2:
            exclude_entity, desired_entity = switch_result
            logger.info(f"🔄 Detected switch: {exclude_entity} → {desired_entity}")
            constraints_changed = True

            await self._manager.update_constraints(
                session_id,
                desired_service=desired_entity,
                exclude_service=exclude_entity
            )

        # Detect time window normalization
        time_window = self._extractor.normalize_time_window(
            message,
            datetime.now(),
            detected_language
        )

        if time_window:
            logger.info(f"📅 Normalized time window: {time_window[2]}")
            constraints_changed = True
            await self._manager.update_constraints(
                session_id,
                time_window=time_window
            )

        # Return updated constraints
        constraints = await self._manager.get_constraints(session_id)
        return constraints, constraints_changed
