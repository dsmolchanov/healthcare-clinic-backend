"""
System prompt composer for healthcare assistant.

Phase 2B of Agentic Flow Architecture Refactor.
Phase 2B-1: Composes system prompts from modular Python constants.
Phase 2B-2: Adds database template support with fallback to constants.

Composes system prompts using simple .format() - no Jinja2.
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional

from .components import (
    BASE_PERSONA,
    CLINIC_CONTEXT,
    DATE_TIME_CONTEXT,
    DATE_RULES,
    BOOKING_POLICY,
    NARROWING_ASK_QUESTION_TEMPLATE,
    NARROWING_CALL_TOOL_TEMPLATE,
    NARROWING_PASS_THROUGH_TEMPLATE,
    QUESTION_TEMPLATES,
    build_constraints_section,
    build_doctors_text,
    build_profile_section,
    build_conversation_summary,
)

logger = logging.getLogger(__name__)

# Map component keys to Python defaults
DEFAULT_TEMPLATES = {
    'base_persona': BASE_PERSONA,
    'clinic_context': CLINIC_CONTEXT,
    'date_time_context': DATE_TIME_CONTEXT,
    'date_rules': DATE_RULES,
    'booking_policy': BOOKING_POLICY,
}


class PromptComposer:
    """
    Composes system prompts from modular components.

    Phase 2B-2: Now supports database overrides per clinic.
    Priority: DB template > Python constant

    Usage:
        # Sync (Python constants only):
        composer = PromptComposer()
        system_prompt = composer.compose(ctx)

        # Async (DB templates with fallback):
        composer = PromptComposer(use_db_templates=True)
        system_prompt = await composer.compose_async(ctx)

    The composer extracts relevant data from PipelineContext and builds
    a complete system prompt from modular components.
    """

    def __init__(self, use_db_templates: bool = False):
        """
        Initialize composer.

        Args:
            use_db_templates: Whether to load templates from DB (requires async)
        """
        self._use_db_templates = use_db_templates
        self._template_service = None

    def _get_template_service(self):
        """Lazy-load template service to avoid import cycles."""
        if self._template_service is None and self._use_db_templates:
            from app.services.prompt_template_service import get_prompt_template_service
            self._template_service = get_prompt_template_service()
        return self._template_service

    async def compose_async(
        self,
        ctx,  # PipelineContext
        include_booking_policy: bool = True,
        tool_mode: bool = False,  # Whether this prompt will be used with generate_with_tools()
    ) -> str:
        """
        Compose system prompt with database template support.

        Loads clinic-specific template overrides from DB, falling back
        to Python constants when no override exists.

        Args:
            ctx: PipelineContext with all conversation data
            include_booking_policy: Whether to include booking flow instructions
            tool_mode: Whether this prompt will be used with generate_with_tools().
                       When False, strips tool-specific instructions to prevent
                       the LLM from hallucinating tool calls.

        Returns:
            Complete system prompt string
        """
        # Load DB templates for this clinic
        db_templates: Dict[str, str] = {}
        if self._use_db_templates:
            service = self._get_template_service()
            if service:
                try:
                    db_templates = await service.get_clinic_templates(ctx.effective_clinic_id)
                    if db_templates:
                        logger.debug(
                            f"Loaded {len(db_templates)} DB templates for clinic {ctx.effective_clinic_id}: "
                            f"{list(db_templates.keys())}"
                        )
                except Exception as e:
                    logger.warning(f"Failed to load DB templates, using defaults: {e}")

        # Helper to get template with DB override
        def get_template(key: str) -> str:
            return db_templates.get(key, DEFAULT_TEMPLATES.get(key, ''))

        # Build context dict from pipeline context
        context = self._build_context_dict(ctx)

        sections = []

        # 1. Base persona (with DB override)
        base_persona = get_template('base_persona')
        if base_persona:
            sections.append(base_persona.format(**context))

        # 2. Clinic context (with DB override)
        clinic_context = get_template('clinic_context')
        if clinic_context:
            sections.append(clinic_context.format(**context))

        # 3. Date/time context (with DB override)
        date_time_context = get_template('date_time_context')
        if date_time_context:
            sections.append(date_time_context.format(**context))

        # 4. Date rules (with DB override)
        date_rules = get_template('date_rules')
        if date_rules:
            sections.append(date_rules.format(**context))

        # 5. Booking policy (with DB override, optional)
        if include_booking_policy:
            booking_policy = get_template('booking_policy')
            if booking_policy:
                formatted_policy = booking_policy.format(**context)

                # If not in tool_mode, strip tool-specific instructions to prevent
                # the LLM from hallucinating tool calls when tools aren't available
                if not tool_mode:
                    lines = formatted_policy.split('\n')
                    filtered_lines = [
                        line for line in lines
                        if not any(kw in line for kw in [
                            'MUST call query_service_prices',
                            'MUST call check_availability',
                            'MANDATORY TOOL CALLS',
                            'YOU DO NOT know any prices',
                            'YOU DO NOT know availability',
                            'CALL THE TOOL FIRST',
                        ])
                    ]
                    formatted_policy = '\n'.join(filtered_lines)

                sections.append(formatted_policy)

        # 6. Patient profile (uses helper function, no DB override yet)
        profile_section = build_profile_section(ctx.profile, ctx.conversation_state)
        if profile_section:
            sections.append(profile_section)

        # 7. Conversation summary
        summary = build_conversation_summary(ctx.session_messages or [])
        if summary:
            sections.append(summary)

        # 8. Previous session summary (if available)
        if ctx.previous_session_summary:
            previous_section = (
                f"\nPREVIOUS SESSION CONTEXT:\n{ctx.previous_session_summary}\n"
                "(Use this context if relevant, but prioritize current user request)"
            )
            sections.append(previous_section)

        # 9. Additional context (arbitrary extra context)
        if ctx.additional_context:
            sections.append(ctx.additional_context)

        # Join all sections
        system_prompt = "\n\n".join(s for s in sections if s)

        # 10. Add constraints section
        if ctx.constraints:
            constraints_section = build_constraints_section(ctx.constraints)
            if constraints_section:
                system_prompt += f"\n\n{constraints_section}"

        # 11. Add narrowing control block at the beginning (most important)
        if ctx.narrowing_instruction:
            control_block = self._build_narrowing_control_block(ctx.narrowing_instruction)
            if control_block:
                system_prompt = control_block + "\n\n" + system_prompt

        return system_prompt

    def compose(
        self,
        ctx,  # PipelineContext
        include_booking_policy: bool = True,
    ) -> str:
        """
        Compose system prompt from pipeline context.

        Args:
            ctx: PipelineContext with all conversation data
            include_booking_policy: Whether to include booking flow instructions

        Returns:
            Complete system prompt string
        """
        # Build context dict from pipeline context
        context = self._build_context_dict(ctx)

        sections = []

        # 1. Base persona
        sections.append(BASE_PERSONA.format(**context))

        # 2. Clinic context
        sections.append(CLINIC_CONTEXT.format(**context))

        # 3. Date/time context
        sections.append(DATE_TIME_CONTEXT.format(**context))

        # 4. Date rules (hallucination guard)
        sections.append(DATE_RULES.format(**context))

        # 5. Booking policy (optional)
        if include_booking_policy:
            sections.append(BOOKING_POLICY.format(**context))

        # 6. Patient profile (if available)
        profile_section = build_profile_section(ctx.profile, ctx.conversation_state)
        if profile_section:
            sections.append(profile_section)

        # 7. Conversation summary
        summary = build_conversation_summary(ctx.session_messages or [])
        if summary:
            sections.append(summary)

        # 8. Previous session summary (if available)
        if ctx.previous_session_summary:
            previous_section = (
                f"\nPREVIOUS SESSION CONTEXT:\n{ctx.previous_session_summary}\n"
                "(Use this context if relevant, but prioritize current user request)"
            )
            sections.append(previous_section)

        # 9. Additional context (arbitrary extra context)
        if ctx.additional_context:
            sections.append(ctx.additional_context)

        # Join all sections
        system_prompt = "\n\n".join(s for s in sections if s)

        # 10. Add constraints section
        if ctx.constraints:
            constraints_section = build_constraints_section(ctx.constraints)
            if constraints_section:
                system_prompt += f"\n\n{constraints_section}"

        # 11. Add narrowing control block at the beginning (most important)
        if ctx.narrowing_instruction:
            control_block = self._build_narrowing_control_block(ctx.narrowing_instruction)
            if control_block:
                system_prompt = control_block + "\n\n" + system_prompt

        return system_prompt

    def _build_context_dict(self, ctx) -> Dict[str, Any]:
        """
        Build context dictionary from PipelineContext.

        Extracts all necessary values for prompt template formatting.
        """
        clinic_profile = ctx.clinic_profile or {}

        # Location
        location_parts = []
        if clinic_profile.get('city'):
            location_parts.append(clinic_profile['city'])
        if clinic_profile.get('state'):
            location_parts.append(clinic_profile['state'])
        if clinic_profile.get('country'):
            location_parts.append(clinic_profile['country'])

        clinic_location = (
            clinic_profile.get('location')
            or ', '.join([p for p in location_parts if p])
            or clinic_profile.get('timezone')
            or 'Unknown'
        )

        # Services
        services_list = clinic_profile.get('services') or []
        services_text = ', '.join(services_list[:6]) if services_list else "Information available upon request"

        # Doctors
        doctors_list = clinic_profile.get('doctors') or []
        doctors_text = build_doctors_text(doctors_list)

        # Business hours
        hours = clinic_profile.get('business_hours') or clinic_profile.get('hours') or {}
        weekday_hours = hours.get('weekdays') or hours.get('monday') or "Not provided"
        saturday_hours = hours.get('saturday') or "Not provided"
        sunday_hours = hours.get('sunday') or "Not provided"

        # Current date/time
        now = datetime.now()
        current_date = now.strftime('%Y-%m-%d')
        current_day = now.strftime('%A')
        current_time = now.strftime('%H:%M')
        tomorrow = now + timedelta(days=1)
        tomorrow_date = tomorrow.strftime('%Y-%m-%d')
        tomorrow_day = tomorrow.strftime('%A')

        # Today's hours
        day_lower = current_day.lower()
        if day_lower == 'sunday':
            todays_hours = sunday_hours
        elif day_lower == 'saturday':
            todays_hours = saturday_hours
        else:
            todays_hours = weekday_hours

        return {
            'clinic_name': ctx.clinic_name,
            'clinic_id': ctx.effective_clinic_id,
            'clinic_location': clinic_location,
            'services_text': services_text,
            'doctors_text': doctors_text,
            'weekday_hours': weekday_hours,
            'saturday_hours': saturday_hours,
            'sunday_hours': sunday_hours,
            'current_date': current_date,
            'current_day': current_day,
            'current_time': current_time,
            'tomorrow_date': tomorrow_date,
            'tomorrow_day': tomorrow_day,
            'todays_hours': todays_hours,
            'from_phone': ctx.from_phone,
        }

    def _build_narrowing_control_block(self, instruction) -> str:
        """
        Build control block for LLM based on narrowing instruction.

        Args:
            instruction: NarrowingInstruction object

        Returns:
            Formatted control block string
        """
        if not instruction:
            return ""

        # Import here to avoid circular imports
        from app.domain.preferences.narrowing import NarrowingAction

        if instruction.action == NarrowingAction.ASK_QUESTION:
            # Build question guidance from type + args
            question_type_str = instruction.question_type.value if instruction.question_type else ""
            template = QUESTION_TEMPLATES.get(question_type_str, "Ask a clarifying question")

            # Format template with args, handling missing keys gracefully
            try:
                question_guidance = template.format(**instruction.question_args)
            except KeyError:
                question_guidance = template

            return NARROWING_ASK_QUESTION_TEMPLATE.format(
                case=instruction.case,
                question_type=instruction.question_type,
                question_guidance=question_guidance,
                question_args=instruction.question_args,
            )

        elif instruction.action == NarrowingAction.CALL_TOOL:
            params = instruction.tool_call.params if instruction.tool_call else {}
            return NARROWING_CALL_TOOL_TEMPLATE.format(
                case=instruction.case,
                params=params,
            )

        elif instruction.action == NarrowingAction.PASS_THROUGH:
            return NARROWING_PASS_THROUGH_TEMPLATE.format(
                case=instruction.case,
                note=getattr(instruction, 'note', 'Proceeding without narrowing'),
            )

        return ""


def compose_system_prompt(
    ctx,
    include_booking_policy: bool = True,
) -> str:
    """
    Convenience function to compose system prompt.

    Args:
        ctx: PipelineContext with all conversation data
        include_booking_policy: Whether to include booking flow instructions

    Returns:
        Complete system prompt string
    """
    composer = PromptComposer()
    return composer.compose(ctx, include_booking_policy=include_booking_policy)
