"""
MessageProcessingPipeline - Orchestrates message processing through pipeline steps.

Features:
- Step timing for observability
- Global error handling with multilingual fallback
- Context snapshots for debugging

Phase 2A of the Agentic Flow Architecture Refactor.
"""

from typing import List
import time
import logging

from .base import PipelineStep, PipelineStepError
from .context import PipelineContext

logger = logging.getLogger(__name__)


# Error messages by language
ERROR_MESSAGES = {
    'en': "I'm sorry, I encountered an error. Please try again.",
    'es': "Lo siento, encontré un error. Por favor, intente de nuevo.",
    'ru': "Извините, произошла ошибка. Пожалуйста, попробуйте снова.",
    'he': "סליחה, אירעה שגיאה. אנא נסה שוב.",
    'pt': "Desculpe, ocorreu um erro. Por favor, tente novamente.",
}


class MessageProcessingPipeline:
    """
    Orchestrates message processing through pipeline steps.

    Features:
    - Step timing for observability
    - Global error handling with multilingual fallback
    - Context snapshots for debugging

    Usage:
        pipeline = MessageProcessingPipeline([
            SessionManagementStep(),
            ContextHydrationStep(),
            EscalationCheckStep(),
            RoutingStep(),
            ConstraintEnforcementStep(),
            LLMGenerationStep(),
            PostProcessingStep()
        ])

        ctx = await pipeline.execute(initial_context)
        response = ctx.response
    """

    def __init__(self, steps: List[PipelineStep]):
        """
        Initialize pipeline with ordered steps.

        Args:
            steps: List of PipelineStep instances in execution order
        """
        self.steps = steps

    async def execute(self, ctx: PipelineContext) -> PipelineContext:
        """
        Execute all pipeline steps in order.

        Args:
            ctx: Initial pipeline context with request data

        Returns:
            Modified context with response populated

        Note:
            - Each step can stop the pipeline by returning should_continue=False
            - Errors are caught and converted to multilingual fallback responses
            - Step timings are recorded in ctx.step_timings
        """
        total_start = time.time()

        for step in self.steps:
            step_start = time.time()
            snapshot = ctx.snapshot()  # Capture state before risky step

            try:
                ctx, should_continue = await step.execute(ctx)
                step_duration_ms = (time.time() - step_start) * 1000
                ctx.step_timings[step.name] = step_duration_ms

                logger.info(
                    f"✅ Step '{step.name}' completed in {step_duration_ms:.1f}ms"
                )

                if not should_continue:
                    logger.info(
                        f"🛑 Pipeline stopped at '{step.name}' "
                        f"(response ready: {ctx.response is not None})"
                    )
                    break

            except PipelineStepError as e:
                step_duration_ms = (time.time() - step_start) * 1000
                ctx.step_timings[step.name] = step_duration_ms

                logger.error(
                    f"❌ Step '{e.step_name}' failed after {step_duration_ms:.1f}ms: {e}",
                    extra={
                        'correlation_id': ctx.correlation_id,
                        'context_snapshot': e.context_snapshot or snapshot,
                    }
                )

                # Return safe multilingual error response
                ctx.response = self._get_error_response(ctx.detected_language)
                ctx.response_metadata['error'] = str(e)
                ctx.response_metadata['failed_step'] = e.step_name
                break

            except Exception as e:
                step_duration_ms = (time.time() - step_start) * 1000
                ctx.step_timings[step.name] = step_duration_ms

                logger.error(
                    f"❌ Step '{step.name}' failed after {step_duration_ms:.1f}ms: {e}",
                    exc_info=True,
                    extra={
                        'correlation_id': ctx.correlation_id,
                        'context_snapshot': snapshot,
                    }
                )

                # Return safe multilingual error response
                ctx.response = self._get_error_response(ctx.detected_language)
                ctx.response_metadata['error'] = str(e)
                ctx.response_metadata['failed_step'] = step.name
                break

        # Record total pipeline duration
        total_duration_ms = (time.time() - total_start) * 1000
        ctx.step_timings['_total'] = total_duration_ms

        logger.info(
            f"📊 Pipeline completed in {total_duration_ms:.1f}ms "
            f"(steps: {len(ctx.step_timings) - 1})"
        )

        return ctx

    def _get_error_response(self, language: str) -> str:
        """Get error message in user's language."""
        return ERROR_MESSAGES.get(language, ERROR_MESSAGES['en'])
