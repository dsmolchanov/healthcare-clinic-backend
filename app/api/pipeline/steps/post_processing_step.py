"""
PostProcessingStep - Handle response formatting, logging, and session updates.

Extracted from process_message() lines 650-843.

Phase 2A of the Agentic Flow Architecture Refactor.
"""

import asyncio
import time
import logging
from datetime import datetime
from typing import Tuple

from ..base import PipelineStep
from ..context import PipelineContext

logger = logging.getLogger(__name__)


class PostProcessingStep(PipelineStep):
    """
    Handle response formatting, logging, and session updates.

    Responsibilities:
    1. Format response with state echo if constraints changed
    2. Update patient profile with extracted data
    3. Log response with metrics
    4. Analyze response for turn status
    5. Update session with new turn status
    6. Schedule follow-ups if needed
    """

    def __init__(
        self,
        state_echo_formatter=None,
        profile_manager=None,
        message_logger=None,
        response_analyzer=None,
        followup_scheduler=None,
        memory_manager=None,
        supabase_client=None
    ):
        """
        Initialize with post-processing dependencies.

        Args:
            state_echo_formatter: StateEchoFormatter for constraint echoing
            profile_manager: ProfileManager for patient updates
            message_logger: AsyncMessageLogger for logging
            response_analyzer: ResponseAnalyzer for turn status
            followup_scheduler: FollowupScheduler for scheduling
            memory_manager: ConversationMemory for logging
            supabase_client: Supabase client for session updates
        """
        self._formatter = state_echo_formatter
        self._profile_manager = profile_manager
        self._message_logger = message_logger
        self._response_analyzer = response_analyzer
        self._followup_scheduler = followup_scheduler
        self._memory_manager = memory_manager
        self._supabase = supabase_client

    @property
    def name(self) -> str:
        return "post_processing"

    async def execute(self, ctx: PipelineContext) -> Tuple[PipelineContext, bool]:
        """
        Execute post-processing step.

        Modifies:
        - response (with state echo if needed)
        - response_metadata (with analysis results)
        """
        processing_start = ctx.start_time

        # 1. Format response with state echo if constraints changed
        if ctx.constraints_changed and ctx.constraints:
            if (ctx.constraints.excluded_doctors or
                ctx.constraints.excluded_services or
                ctx.constraints.desired_service or
                ctx.constraints.time_window_start):

                if self._formatter:
                    logger.info("📢 State echo triggered: constraints changed this turn")
                    ctx.response = self._formatter.format_response(
                        ctx.response,
                        ctx.constraints,
                        ctx.detected_language
                    )

        # 2. Update patient with extracted name and language
        if self._profile_manager and (ctx.extracted_first_name or ctx.detected_language):
            await self._profile_manager.upsert_patient_from_whatsapp(
                clinic_id=ctx.effective_clinic_id,
                phone=ctx.from_phone,
                profile_name=ctx.profile_name,
                detected_language=ctx.detected_language,
                extracted_first_name=ctx.extracted_first_name,
                extracted_last_name=ctx.extracted_last_name
            )

        # 3. Build response metadata
        response_metadata = {
            'detected_language': ctx.detected_language,
            'profile_loaded': bool(ctx.profile and ctx.profile.first_name),
            'constraints_active': bool(
                ctx.conversation_state and ctx.conversation_state.excluded_doctors
            ),
            'clinic_id': ctx.effective_clinic_id,
            'from_number': ctx.from_phone,
            'channel': ctx.channel
        }

        # 4. Log response with metrics
        if self._message_logger:
            total_latency_ms = int((time.time() - processing_start) * 1000)

            result = await self._message_logger.log_message_with_metrics(
                session_id=ctx.session_id,
                role='assistant',
                content=ctx.response,
                metadata=response_metadata,

                # LLM metrics
                llm_provider=ctx.llm_metrics.get('llm_provider'),
                llm_model=ctx.llm_metrics.get('llm_model'),
                llm_tokens_input=ctx.llm_metrics.get('llm_tokens_input', 0),
                llm_tokens_output=ctx.llm_metrics.get('llm_tokens_output', 0),
                llm_latency_ms=ctx.llm_metrics.get('llm_latency_ms', 0),
                llm_cost_usd=ctx.llm_metrics.get('llm_cost_usd', 0),

                # RAG metrics (no longer used)
                rag_queries=0,
                rag_chunks_retrieved=0,
                rag_latency_ms=0,

                # Memory metrics
                mem0_queries=0,
                mem0_memories_retrieved=0,

                # Total
                total_latency_ms=total_latency_ms,
                total_cost_usd=ctx.llm_metrics.get('llm_cost_usd', 0),

                # Platform events
                log_platform_events=True,
                agent_id=response_metadata.get('agent_id'),

                # Organization tracking
                organization_id=(
                    ctx.clinic_profile.get('organization_id') if ctx.clinic_profile else None
                ) or ctx.request_metadata.get('organization_id'),
                clinic_id=ctx.effective_clinic_id
            )

            if result.get('success'):
                response_metadata['message_id'] = result.get('message_id')

        # 5. Analyze response for turn status
        if self._response_analyzer:
            conversation_context = "\n".join([
                f"{msg['role']}: {msg['content']}"
                for msg in ctx.session_messages[-5:]
            ])

            response_analysis = await self._response_analyzer.analyze_agent_response(
                response=ctx.response,
                conversation_context=conversation_context
            )

            new_turn_status = response_analysis.get('turn_status', 'user_turn')
            response_metadata['turn_analysis'] = response_analysis

            # 6. Update session with new turn status
            await self._update_session_turn_status(ctx, response_analysis, new_turn_status)

            # 7. Schedule follow-ups if needed
            if (self._followup_scheduler and
                new_turn_status == 'agent_action_pending' and
                response_analysis.get('promises_followup')):

                await self._schedule_followup(ctx, response_analysis)

        # 7.5. Phase 5.2: Persist session language for language inertia
        if self._supabase and ctx.session_id and ctx.session_language:
            await self._update_session_language(ctx)

        # 8. Build final response metadata
        ctx.response_metadata = {
            "message_count": len(ctx.session_messages) + 2,
            "profile_loaded": bool(ctx.profile and ctx.profile.first_name),
            "has_history": len(ctx.conversation_history) > 0,
            "is_new_conversation": ctx.is_new_session,
            "conversation_stage": "new" if ctx.is_new_session else "continuation",
            "clinic_id": ctx.effective_clinic_id,
            "step_timings": ctx.step_timings,
        }

        if ctx.patient_id:
            ctx.response_metadata["patient_id"] = ctx.patient_id
        if ctx.patient_name:
            ctx.response_metadata["patient_name"] = ctx.patient_name

        logger.info(
            f"📤 Response ready: {len(ctx.response or '')} chars, "
            f"total_time={ctx.step_timings.get('_total', 0):.0f}ms"
        )

        return ctx, True

    async def _update_session_turn_status(
        self,
        ctx: PipelineContext,
        response_analysis: dict,
        new_turn_status: str
    ):
        """Update session with new turn status."""
        update_data = {
            'turn_status': new_turn_status,
            'updated_at': datetime.utcnow().isoformat()
        }

        if response_analysis.get('promises_followup'):
            update_data['last_agent_action'] = response_analysis.get(
                'followup_action', 'Follow up on pending request'
            )
            update_data['pending_since'] = datetime.utcnow().isoformat()
            logger.warning(f"⚠️ Agent promised follow-up: {update_data['last_agent_action']}")

        if new_turn_status == 'resolved':
            update_data['status'] = 'ended'
            update_data['ended_at'] = datetime.utcnow().isoformat()

        if self._supabase:
            try:
                self._supabase.table('conversation_sessions').update(
                    update_data
                ).eq('id', ctx.session_id).execute()
                logger.info("✅ Session turn status updated")
            except Exception as e:
                logger.error(f"Failed to update session turn status: {e}")

    async def _schedule_followup(self, ctx: PipelineContext, response_analysis: dict):
        """Schedule follow-up for agent pending action."""
        try:
            followup_schedule = await self._followup_scheduler.analyze_and_schedule_followup(
                session_id=ctx.session_id,
                last_10_messages=(
                    ctx.session_messages[-10:]
                    if len(ctx.session_messages) >= 10
                    else ctx.session_messages
                ),
                last_agent_action=response_analysis.get('followup_action', '')
            )

            if followup_schedule['should_schedule']:
                await self._followup_scheduler.store_scheduled_followup(
                    session_id=ctx.session_id,
                    followup_at=followup_schedule['followup_at'],
                    context=followup_schedule
                )
                logger.info(
                    f"✅ Follow-up scheduled for {followup_schedule['followup_at'].isoformat()}"
                )
        except Exception as e:
            logger.error(f"Failed to schedule follow-up: {e}")

    async def _update_session_language(self, ctx: PipelineContext):
        """
        Persist session language to database for language inertia.

        Phase 5.2: This ensures that the detected language persists
        across turns, preventing flip-flopping on short messages.

        Args:
            ctx: Pipeline context with session_id and session_language
        """
        if not self._supabase or not ctx.session_id or not ctx.session_language:
            return

        try:
            self._supabase.table('conversation_sessions').update({
                'session_language': ctx.session_language,
                'updated_at': datetime.utcnow().isoformat()
            }).eq('id', ctx.session_id).execute()
            logger.debug(f"[Language] Persisted session_language={ctx.session_language} to session")
        except Exception as e:
            logger.warning(f"Failed to persist session language: {e}")
