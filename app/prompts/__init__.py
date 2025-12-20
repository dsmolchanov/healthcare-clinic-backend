"""
Prompts module for healthcare assistant.

Phase 2B-1: Modular prompt decomposition (Python constants).
Phase 2B-2: Database-backed templates with UI editing.
"""

from .components import (
    BASE_PERSONA,
    CLINIC_CONTEXT,
    DATE_TIME_CONTEXT,
    DATE_RULES,
    BOOKING_POLICY,
    PATIENT_PROFILE_TEMPLATE,
    CONSTRAINTS_TEMPLATE,
    NARROWING_ASK_QUESTION_TEMPLATE,
    NARROWING_CALL_TOOL_TEMPLATE,
    NARROWING_PASS_THROUGH_TEMPLATE,
    QUESTION_TEMPLATES,
    build_constraints_section,
    build_doctors_text,
    build_profile_section,
    build_conversation_summary,
)

from .composer import (
    PromptComposer,
    compose_system_prompt,
    DEFAULT_TEMPLATES,
)

__all__ = [
    # Components
    'BASE_PERSONA',
    'CLINIC_CONTEXT',
    'DATE_TIME_CONTEXT',
    'DATE_RULES',
    'BOOKING_POLICY',
    'PATIENT_PROFILE_TEMPLATE',
    'CONSTRAINTS_TEMPLATE',
    'NARROWING_ASK_QUESTION_TEMPLATE',
    'NARROWING_CALL_TOOL_TEMPLATE',
    'NARROWING_PASS_THROUGH_TEMPLATE',
    'QUESTION_TEMPLATES',
    # Helper functions
    'build_constraints_section',
    'build_doctors_text',
    'build_profile_section',
    'build_conversation_summary',
    # Composer
    'PromptComposer',
    'compose_system_prompt',
    'DEFAULT_TEMPLATES',
]
