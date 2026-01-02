"""
ContextHydrationStep - Hydrate clinic, patient, and conversation context.

Extracted from process_message() lines 315-430.

Phase 2A of the Agentic Flow Architecture Refactor.
"""

import logging
from typing import Tuple
from datetime import datetime, timezone

from ..base import PipelineStep
from ..context import PipelineContext

logger = logging.getLogger(__name__)


class ContextHydrationStep(PipelineStep):
    """
    Hydrate clinic, patient, and conversation context.

    Responsibilities:
    1. Fetch clinic profile, services, doctors, FAQs
    2. Fetch patient profile and conversation state
    3. Fetch conversation history
    4. Build session messages for LLM context
    5. Build additional context (pending actions, etc.)
    """

    def __init__(self, context_hydrator=None):
        """
        Initialize with MessageContextHydrator.

        Args:
            context_hydrator: MessageContextHydrator instance
        """
        self._hydrator = context_hydrator

    @property
    def name(self) -> str:
        return "context_hydration"

    async def execute(self, ctx: PipelineContext) -> Tuple[PipelineContext, bool]:
        """
        Execute context hydration step.

        Sets on context:
        - clinic_profile, clinic_services, clinic_doctors, clinic_faqs
        - patient_profile, patient_name, patient_id
        - conversation_history, session_messages
        - user_preferences, profile, conversation_state
        - additional_context
        """
        if not self._hydrator:
            logger.warning("ContextHydrator not available, skipping hydration")
            return ctx, True

        # Hydrate all context in parallel
        context = await self._hydrator.hydrate(
            clinic_id=ctx.effective_clinic_id,
            phone_number=ctx.from_phone,
            session_id=ctx.session_id,
            is_new_conversation=ctx.is_new_session
        )

        # Unpack clinic context (coerce None to empty list for safety)
        ctx.clinic_profile = context.get('clinic') or {}
        ctx.clinic_services = context.get('services') or []
        ctx.clinic_doctors = context.get('doctors') or []
        ctx.clinic_faqs = context.get('faqs') or []

        # Unpack patient context
        ctx.patient_profile = context.get('patient', {})
        ctx.user_preferences = context.get('preferences', {})
        ctx.profile = context.get('profile')  # PatientProfile object
        ctx.conversation_state = context.get('conversation_state')

        # Extract patient identifiers
        if ctx.patient_profile:
            ctx.patient_id = ctx.patient_profile.get('id')
            ctx.patient_name = self._extract_patient_name(ctx.patient_profile)

            # Phase 1B: Update profile_name with DB patient name for personalized greetings
            # This ensures FSM orchestrator receives proper patient name, not just WhatsApp pushName
            if ctx.patient_name:
                ctx.profile_name = ctx.patient_name
                logger.debug(f"[Hydration] Updated profile_name from DB: {ctx.profile_name}")

            # Add to preferences if not already set
            if ctx.patient_name and not ctx.user_preferences.get('preferred_name'):
                ctx.user_preferences['preferred_name'] = ctx.patient_name

            # Phase 5.2: Fallback for session language from patient profile
            # Only set if not already loaded from session
            if not ctx.session_language:
                # Try hard_preferences.preferred_language first
                hard_prefs = ctx.patient_profile.get('hard_preferences', {})
                if isinstance(hard_prefs, dict) and hard_prefs.get('preferred_language'):
                    ctx.session_language = hard_prefs['preferred_language']
                    logger.info(f"[Language] Loaded session_language from patient profile: {ctx.session_language}")
                # Then try language_preference at top level
                elif ctx.patient_profile.get('language_preference'):
                    ctx.session_language = ctx.patient_profile['language_preference']
                    logger.info(f"[Language] Loaded session_language from language_preference: {ctx.session_language}")

        # Unpack conversation history
        ctx.conversation_history = context.get('history', [])

        # Build session messages for LLM context
        ctx.session_messages = self._build_session_messages(ctx.conversation_history)

        # Resolve clinic name
        resolved_clinic_name = (
            ctx.clinic_profile.get('name')
            or (ctx.session.get('name') if isinstance(ctx.session, dict) else None)
            or ctx.clinic_name
            or "Clinic"
        )
        ctx.clinic_name = resolved_clinic_name

        # Build additional context based on session state
        ctx.additional_context = self._build_additional_context(ctx)

        logger.info(
            f"📚 Context hydrated: "
            f"services={len(ctx.clinic_services)}, "
            f"doctors={len(ctx.clinic_doctors)}, "
            f"history={len(ctx.conversation_history)}"
        )

        return ctx, True

    def _extract_patient_name(self, patient_profile: dict) -> str | None:
        """Extract patient name from profile, filtering generic names."""
        first_name = (patient_profile.get('first_name') or '').strip()
        last_name = (patient_profile.get('last_name') or '').strip()

        generic_names = {'whatsapp', 'unknown', 'user'}
        first_is_generic = first_name.lower() in generic_names
        last_is_generic = last_name.lower() in generic_names or not last_name

        if first_name and not first_is_generic:
            if last_name and not last_is_generic:
                return f"{first_name} {last_name}".strip()
            return first_name

        return None

    def _build_session_messages(self, conversation_history: list) -> list:
        """Build session messages list for LLM context."""
        session_messages = []
        for msg in conversation_history:
            # Database column is 'message_content', fallback to 'content' for compatibility
            content = msg.get('message_content') or msg.get('content', '')
            session_messages.append({
                'role': msg.get('role', 'user'),
                'content': content
            })
        return session_messages

    def _build_additional_context(self, ctx: PipelineContext) -> str:
        """Build additional context based on session state."""
        additional_context = ""

        # Check for pending agent action
        if ctx.turn_status == 'agent_action_pending' and ctx.last_agent_action:
            time_pending = ""
            if ctx.pending_since:
                try:
                    pending_dt = datetime.fromisoformat(ctx.pending_since.replace('Z', '+00:00'))
                    hours_pending = (datetime.now(timezone.utc) - pending_dt).total_seconds() / 3600
                    time_pending = f" (pending for {hours_pending:.1f} hours)"
                except Exception:
                    pass

            additional_context = f"""

⚠️ CRITICAL CONTEXT - YOU PREVIOUSLY PROMISED TO FOLLOW UP:
In your last message, you told the user: "{ctx.last_agent_action}"{time_pending}

The user is now following up. You MUST:
1. Acknowledge you said you'd get back to them
2. Provide the answer or information you promised
3. If you still don't have the answer, apologize and escalate to a human

DO NOT say "let me check" again. Either provide substantive information or escalate.
"""
            logger.warning(f"⚠️ Injecting pending action context: {ctx.last_agent_action}")

        elif ctx.turn_status == 'escalated':
            additional_context = """

This conversation has been escalated to a human agent.
Provide a brief acknowledgment that their request is being handled by the team.
DO NOT attempt to answer complex questions yourself.
"""

        # Add conversation state context
        is_new_conversation = len(ctx.conversation_history) == 0
        conversation_state_context = (
            "This is the first turn with this user. Provide a warm introduction, confirm clinic details, and collect any necessary intake information before addressing their request."
            if is_new_conversation else
            "The user has chatted with the clinic before. Maintain continuity, reference any relevant prior context, and move quickly to the substance of their request."
        )

        if additional_context:
            additional_context += f"\n\n{conversation_state_context}"
        else:
            additional_context = conversation_state_context

        return additional_context
