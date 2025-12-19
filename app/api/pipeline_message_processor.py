"""
Pipeline-based Message Processor.

This is the refactored version of MultilingualMessageProcessor using
the pipeline architecture from Phase 2A.

Feature flagged via ENABLE_PIPELINE environment variable.
"""

import os
import logging
from typing import Optional

from pydantic import BaseModel, Field

from app.api.pipeline import PipelineContext, MessageProcessingPipeline
from app.api.pipeline.steps import (
    SessionManagementStep,
    ContextHydrationStep,
    EscalationCheckStep,
    RoutingStep,
    ConstraintEnforcementStep,
    NarrowingStep,
    LLMGenerationStep,
    PostProcessingStep,
)

logger = logging.getLogger(__name__)


# Re-export request/response models for compatibility
from app.api.multilingual_message_processor import MessageRequest, MessageResponse


class PipelineMessageProcessor:
    """
    Pipeline-based message processor.

    This replaces the 631-line process_message() God Method with
    a thin orchestration layer that delegates to discrete, testable steps.

    Each step is responsible for one concern:
    1. SessionManagementStep: Session creation, phone resolution, message storage
    2. ContextHydrationStep: Clinic, patient, conversation context
    3. EscalationCheckStep: Check if should escalate to human
    4. RoutingStep: Classify message and handle fast-path
    5. ConstraintEnforcementStep: Extract and enforce constraints
    6. LLMGenerationStep: Generate AI response with tools
    7. PostProcessingStep: Format, log, update session

    Usage:
        processor = PipelineMessageProcessor()
        response = await processor.process_message(request)
    """

    def __init__(self):
        """Initialize processor with all dependencies."""
        # Import dependencies lazily to avoid circular imports
        from app.memory.conversation_memory import get_memory_manager
        from app.services.profile_manager import ProfileManager
        from app.api.async_message_logger import AsyncMessageLogger
        from app.services.response_analyzer import ResponseAnalyzer
        from app.services.escalation_handler import EscalationHandler
        from app.services.followup_scheduler import FollowupScheduler
        from app.services.session_manager import SessionManager
        from app.services.conversation_constraints import ConstraintsManager
        from app.services.constraint_extractor import ConstraintExtractor
        from app.services.tool_state_gate import ToolStateGate
        from app.services.state_echo_formatter import StateEchoFormatter
        from app.services.tools.executor import ToolExecutor
        from app.services.message_context_hydrator import MessageContextHydrator
        from app.services.session_controller import SessionController
        from app.services.language_service import LanguageService
        from app.services.router_service import RouterService
        from app.services.fast_path_service import FastPathService
        from app.services.session_service import SessionService
        from app.config import get_redis_client
        from app.api.multilingual_message_processor import (
            get_supabase_client,
            get_public_supabase_client,
            get_llm_factory,
        )

        # Core services
        self.memory_manager = get_memory_manager()
        self.profile_manager = ProfileManager(get_supabase_client())

        # Logging
        strict_logging = os.getenv("CONVERSATION_LOG_FAIL_FAST", "false").lower() == "true"
        self.message_logger = AsyncMessageLogger(get_supabase_client(), strict=strict_logging)

        # Session management
        redis_client = get_redis_client()
        self.session_manager = SessionManager(redis_client, get_public_supabase_client())
        self.constraints_manager = ConstraintsManager(redis_client)
        self.constraint_extractor = ConstraintExtractor()
        self.session_controller = SessionController(
            session_manager=self.session_manager,
            memory_manager=self.memory_manager,
            constraints_manager=self.constraints_manager
        )

        # Context hydration
        self.context_hydrator = MessageContextHydrator(self.memory_manager, self.profile_manager)

        # Escalation and follow-up
        self.escalation_handler = EscalationHandler()
        self.followup_scheduler = FollowupScheduler()
        self.response_analyzer = ResponseAnalyzer()

        # Tool execution
        self.tool_executor = ToolExecutor()
        self.state_gate = ToolStateGate()
        self.state_echo_formatter = StateEchoFormatter()

        # Routing services
        self.language_service = LanguageService(redis_client)
        self.session_service = SessionService(get_supabase_client())
        self.router_service = RouterService(self.language_service, self.session_service)
        self.fast_path_service = FastPathService(self.language_service, self.session_service)

        # Store references for step initialization
        self._supabase = get_supabase_client()
        self._public_supabase = get_public_supabase_client()
        self._get_llm_factory = get_llm_factory

        logger.info("✅ PipelineMessageProcessor initialized")

    async def process_message(self, request: MessageRequest) -> MessageResponse:
        """
        Process incoming WhatsApp message through pipeline.

        Args:
            request: MessageRequest with message details

        Returns:
            MessageResponse with AI response
        """
        # Build initial context from request
        ctx = PipelineContext(
            message=request.body,
            from_phone=request.from_phone,
            to_phone=request.to_phone,
            message_sid=request.message_sid,
            clinic_id=request.clinic_id,
            clinic_name=request.clinic_name,
            message_type=request.message_type,
            media_url=request.media_url,
            channel=request.channel,
            profile_name=request.profile_name,
            request_metadata=request.metadata or {},
        )

        # Create pipeline with all steps
        pipeline = MessageProcessingPipeline([
            SessionManagementStep(
                session_controller=self.session_controller,
                memory_manager=self.memory_manager,
                profile_manager=self.profile_manager,
                supabase_client=self._supabase
            ),
            ContextHydrationStep(
                context_hydrator=self.context_hydrator
            ),
            EscalationCheckStep(
                escalation_handler=self.escalation_handler,
                memory_manager=self.memory_manager
            ),
            RoutingStep(
                language_service=self.language_service,
                router_service=self.router_service,
                fast_path_service=self.fast_path_service,
                memory_manager=self.memory_manager
            ),
            ConstraintEnforcementStep(
                constraint_extractor=self.constraint_extractor,
                constraints_manager=self.constraints_manager,
                profile_manager=self.profile_manager,
                memory_manager=self.memory_manager
            ),
            NarrowingStep(
                supabase_client=self._supabase
            ),
            LLMGenerationStep(
                llm_factory_getter=self._get_llm_factory,
                tool_executor=self.tool_executor,
                constraints_manager=self.constraints_manager,
                language_service=self.language_service
            ),
            PostProcessingStep(
                state_echo_formatter=self.state_echo_formatter,
                profile_manager=self.profile_manager,
                message_logger=self.message_logger,
                response_analyzer=self.response_analyzer,
                followup_scheduler=self.followup_scheduler,
                memory_manager=self.memory_manager,
                supabase_client=self._public_supabase
            ),
        ])

        # Execute pipeline
        ctx = await pipeline.execute(ctx)

        # Build response
        return MessageResponse(
            message=ctx.response or "",
            session_id=ctx.session_id or "",
            status="success",
            detected_language=ctx.detected_language or "unknown",
            metadata=ctx.response_metadata or {},
        )


# Feature flag for pipeline vs legacy processor
ENABLE_PIPELINE = os.getenv("ENABLE_PIPELINE", "false").lower() == "true"


async def get_message_processor():
    """
    Get the appropriate message processor based on feature flag.

    Returns:
        PipelineMessageProcessor if ENABLE_PIPELINE=true
        MultilingualMessageProcessor otherwise
    """
    if ENABLE_PIPELINE:
        logger.info("Using pipeline-based message processor")
        return PipelineMessageProcessor()
    else:
        from app.api.multilingual_message_processor import MultilingualMessageProcessor
        logger.info("Using legacy message processor")
        return MultilingualMessageProcessor()


async def handle_process_message(request: MessageRequest) -> MessageResponse:
    """
    Main endpoint handler for processing messages.

    Uses feature flag to choose between pipeline and legacy processor.
    """
    processor = await get_message_processor()
    return await processor.process_message(request)
