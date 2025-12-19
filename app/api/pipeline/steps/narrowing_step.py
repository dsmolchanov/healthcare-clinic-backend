"""
Narrowing Step

Runs before LLM step to compute what question to ask or tool to call.

IMPORTANT: Reads constraints from pipeline context (already hydrated by constraint_step),
NOT from Redis directly. This avoids desync and redundant Redis calls.
"""

import logging
from typing import Tuple

from ..base import PipelineStep
from ..context import PipelineContext
from app.services.preference_narrowing import PreferenceNarrowingService
from app.services.conversation_constraints import ConversationConstraints
from app.domain.preferences.narrowing import NarrowingAction

logger = logging.getLogger(__name__)


class NarrowingStep(PipelineStep):
    """
    Pipeline step that computes narrowing instruction before LLM.

    Reads constraints from context (not Redis) - constraint_step already hydrated them.
    """

    def __init__(self, supabase_client=None):
        self.narrowing_service = PreferenceNarrowingService(supabase_client)

    @property
    def name(self) -> str:
        return "narrowing"

    async def execute(self, ctx: PipelineContext) -> Tuple[PipelineContext, bool]:
        """
        Compute narrowing instruction and store in context.

        Args:
            ctx: Pipeline context with constraints already hydrated

        Returns:
            Updated context with narrowing_instruction
        """
        clinic_id = ctx.effective_clinic_id
        user_message = ctx.message or ""

        # Read constraints from context (already hydrated by constraint_step)
        # This avoids redundant Redis calls and potential desync
        constraints = ctx.constraints
        if constraints is None:
            constraints = ConversationConstraints()
            logger.warning("No constraints in context - using empty constraints")

        # Compute narrowing instruction
        try:
            instruction = await self.narrowing_service.decide(
                constraints=constraints,
                clinic_id=clinic_id,
                user_message=user_message,
                clinic_strategy="service_first"  # Hardcode for Shtern Dental
            )

            # Store in context
            ctx.narrowing_instruction = instruction

            logger.info(
                f"Narrowing decision: case={instruction.case}, "
                f"action={instruction.action}, "
                f"doctor_count={instruction.eligible_doctor_count}"
            )

        except Exception as e:
            logger.error(f"Narrowing step failed: {e}")
            # Don't fail pipeline, let LLM proceed without instruction
            ctx.narrowing_instruction = None

        return ctx, True
