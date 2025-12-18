"""
Pipeline Base Classes - Abstract step interface and error handling.

Phase 2A of the Agentic Flow Architecture Refactor.
"""

from abc import ABC, abstractmethod
from typing import Tuple, Dict, Any
import logging

from .context import PipelineContext

logger = logging.getLogger(__name__)


class PipelineStepError(Exception):
    """
    Raised when a pipeline step fails.

    Captures step name and context snapshot for debugging.
    """

    def __init__(
        self,
        step_name: str,
        message: str,
        context_snapshot: Dict[str, Any] = None
    ):
        self.step_name = step_name
        self.context_snapshot = context_snapshot or {}
        super().__init__(f"[{step_name}] {message}")


class PipelineStep(ABC):
    """
    Base class for pipeline steps.

    Each step:
    1. Receives a mutable PipelineContext
    2. Performs its operation (modifying context in place)
    3. Returns (context, should_continue) tuple

    If should_continue is False, the pipeline stops and returns
    the current response from context.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """
        Step name for logging and metrics.

        Should be a lowercase, underscore-separated identifier
        (e.g., 'session_management', 'context_hydration').
        """
        pass

    @abstractmethod
    async def execute(self, ctx: PipelineContext) -> Tuple[PipelineContext, bool]:
        """
        Execute this pipeline step.

        Args:
            ctx: Mutable pipeline context (modify in place)

        Returns:
            Tuple of (context, should_continue)
            - context: The modified context
            - should_continue: If False, pipeline stops and returns response

        Raises:
            PipelineStepError: If the step fails in a recoverable way
            Exception: For unexpected errors (will be caught by orchestrator)
        """
        pass

    def _log_start(self, ctx: PipelineContext):
        """Log step start with correlation ID."""
        logger.info(
            f"▶️ Starting step '{self.name}' "
            f"[session={ctx.session_id or 'new'}]"
        )

    def _log_complete(self, ctx: PipelineContext, duration_ms: float):
        """Log step completion with timing."""
        logger.info(
            f"✅ Step '{self.name}' completed in {duration_ms:.1f}ms"
        )

    def _log_error(self, ctx: PipelineContext, error: Exception):
        """Log step error with context."""
        logger.error(
            f"❌ Step '{self.name}' failed: {error}",
            exc_info=True,
            extra={
                'correlation_id': ctx.correlation_id,
                'session_id': ctx.session_id,
                'step_name': self.name,
            }
        )
