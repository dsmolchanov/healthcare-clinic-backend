"""
Pipeline Architecture for Message Processing.

This module implements a modular pipeline that breaks down the 631-line
process_message() method into discrete, testable steps.

Phase 2A of the Agentic Flow Architecture Refactor.
"""

from .context import PipelineContext
from .base import PipelineStep, PipelineStepError
from .orchestrator import MessageProcessingPipeline

__all__ = [
    'PipelineContext',
    'PipelineStep',
    'PipelineStepError',
    'MessageProcessingPipeline',
]
