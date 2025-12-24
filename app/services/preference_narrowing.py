"""
Preference Narrowing Service

Deterministic logic for deciding what to ask or search based on constraints.
"""

import logging
import re
from typing import Optional, List, Dict, Any, Tuple

from app.domain.preferences.narrowing import (
    NarrowingAction, NarrowingCase, UrgencyLevel, QuestionType,
    NarrowingInstruction, ToolCallPlan
)
from app.services.conversation_constraints import ConversationConstraints

logger = logging.getLogger(__name__)


# Urgency phrase patterns (case-insensitive) - multilingual
URGENT_PATTERNS = [
    # English
    r'\basap\b', r'\burgent\b', r'\bemergency\b', r'\bhurts?\b', r'\bpain\b',
    r'\bimmediately\b', r'\bright away\b', r'\bstat\b',
    # Spanish
    r'\bhoy\b', r'\bahora\b', r'\bdolor\b', r'\bduele\b', r'\burgente\b',
    r'\bemergencia\b',
    # Russian
    r'\bсрочно\b', r'\bнемедленно\b', r'\bболит\b', r'\bболь\b'
]

SOON_PATTERNS = [
    r'\bthis week\b', r'\besta semana\b', r'\bэту неделю\b',
    r'\bsoon\b', r'\bpronto\b', r'\bскоро\b'
]


class PreferenceNarrowingService:
    """
    Transforms ConversationConstraints into a NarrowingInstruction.

    This is the "decision engine" that ensures consistent agent behavior.
    """

    def __init__(self, supabase_client=None):
        self.supabase = supabase_client

    def classify_urgency(self, user_message: str) -> UrgencyLevel:
        """Detect urgency level from user message text."""
        if not user_message:
            return UrgencyLevel.ROUTINE

        msg_lower = user_message.lower()

        # Check urgent patterns first
        for pattern in URGENT_PATTERNS:
            if re.search(pattern, msg_lower):
                return UrgencyLevel.URGENT

        # Check soon patterns
        for pattern in SOON_PATTERNS:
            if re.search(pattern, msg_lower):
                return UrgencyLevel.SOON

        return UrgencyLevel.ROUTINE

    def classify_case(self, constraints: ConversationConstraints) -> NarrowingCase:
        """
        Classify the conversation state into a canonical case.

        Args:
            constraints: Current conversation constraints

        Returns:
            NarrowingCase enum value
        """
        has_service = bool(constraints.desired_service or constraints.desired_service_id)
        has_doctor = bool(constraints.desired_doctor or constraints.desired_doctor_id)
        has_time = bool(constraints.time_window_start or constraints.time_window_end)

        # Determine case based on what we know
        if has_service and has_doctor and has_time:
            return NarrowingCase.FULLY_SPECIFIED
        elif has_service and has_time:
            return NarrowingCase.SERVICE_AND_TIME
        elif has_service and has_doctor:
            return NarrowingCase.SERVICE_AND_DOCTOR
        elif has_service:
            return NarrowingCase.SERVICE_ONLY
        elif has_doctor:
            return NarrowingCase.DOCTOR_ONLY
        elif has_time:
            return NarrowingCase.TIME_ONLY
        else:
            return NarrowingCase.NOTHING_KNOWN

    async def get_eligible_doctors_with_count(
        self,
        service_name: str,
        clinic_id: str,
        excluded_doctor_ids: set = None
    ) -> Tuple[Optional[int], List[Dict[str, Any]]]:
        """
        Get ALL eligible doctors and their count.

        Returns:
            (count, doctors) where:
            - count is None on RPC error (vs 0 for genuinely no doctors)
            - doctors is list of doctor dicts (may be empty on error)

        IMPORTANT: Returns ALL doctors, caller should slice for display.
        This fixes the doctor_count bug where limit=5 caused misclassification.
        """
        if not self.supabase or not service_name or not clinic_id:
            return None, []  # None signals "couldn't check", not "zero doctors"

        try:
            # First, resolve service_name to service_id
            service_result = self.supabase.schema('healthcare').table('services').select('id').eq('clinic_id', clinic_id).or_(
                f'name.ilike.%{service_name}%,'
                f'name_ru.ilike.%{service_name}%,'
                f'name_en.ilike.%{service_name}%,'
                f'name_es.ilike.%{service_name}%'
            ).limit(1).execute()

            if not service_result.data:
                logger.warning(f"Service not found for name: {service_name}")
                return None, []

            service_id = service_result.data[0]['id']
            logger.debug(f"Resolved service '{service_name}' to ID: {service_id}")

            result = self.supabase.rpc(
                'get_doctors_by_service_v2',
                {
                    'p_clinic_id': clinic_id,
                    'p_service_id': service_id,
                    'p_patient_id': None,
                    'p_limit': 100  # Get all eligible doctors
                }
            ).execute()

            doctors = result.data or []

            # Apply exclusions
            if excluded_doctor_ids:
                doctors = [d for d in doctors if d.get('doctor_id') not in excluded_doctor_ids]

            return len(doctors), doctors  # Return FULL count, not sliced

        except Exception as e:
            logger.error(f"RPC failed to get eligible doctors: {e}")
            return None, []  # None = error, distinguish from 0 = no doctors

    async def decide(
        self,
        constraints: ConversationConstraints,
        clinic_id: str,
        user_message: str = "",
        clinic_strategy: str = "service_first"  # or "doctor_first"
    ) -> NarrowingInstruction:
        """
        Main decision method. Returns what the agent should do next.

        Args:
            constraints: Current conversation constraints
            clinic_id: UUID of the clinic
            user_message: Latest user message (for urgency detection)
            clinic_strategy: "service_first" or "doctor_first"

        Returns:
            NarrowingInstruction with action (ask/call) and details
        """
        case = self.classify_case(constraints)
        urgency = self.classify_urgency(user_message)

        logger.info(f"Narrowing decision: case={case}, urgency={urgency}")

        # Handle urgency override
        if urgency == UrgencyLevel.URGENT and case == NarrowingCase.NOTHING_KNOWN:
            case = NarrowingCase.URGENT_NO_TIME

        # Get eligible doctors if we have a service
        # doctor_count is None on RPC error, 0 if genuinely no doctors
        doctor_count: Optional[int] = None
        doctors: List[Dict] = []
        if constraints.desired_service:
            doctor_count, doctors = await self.get_eligible_doctors_with_count(
                constraints.desired_service,
                clinic_id,
                constraints.excluded_doctor_ids
            )

        # Build instruction based on case
        return self._build_instruction(
            case=case,
            constraints=constraints,
            urgency=urgency,
            doctors=doctors[:5],  # Slice for display, but count uses full list
            doctor_count=doctor_count,
            clinic_strategy=clinic_strategy
        )

    def _build_instruction(
        self,
        case: NarrowingCase,
        constraints: ConversationConstraints,
        urgency: UrgencyLevel,
        doctors: List[Dict],
        doctor_count: Optional[int],  # None = RPC error
        clinic_strategy: str
    ) -> NarrowingInstruction:
        """
        Build the narrowing instruction based on case and context.

        Uses language-neutral QuestionType enums - LLM handles localization.
        """
        # ===== CASE: FULLY_SPECIFIED =====
        if case == NarrowingCase.FULLY_SPECIFIED:
            return NarrowingInstruction(
                action=NarrowingAction.CALL_TOOL,
                case=case,
                tool_call=ToolCallPlan(
                    tool_name="check_availability",
                    params={
                        "service_name": constraints.desired_service,
                        "doctor_id": constraints.desired_doctor_id,
                        "preferred_date": constraints.time_window_start,
                        "flexibility_days": 1
                    }
                ),
                eligible_doctor_count=doctor_count,
                urgency=urgency
            )

        # ===== CASE: SERVICE_AND_TIME =====
        if case == NarrowingCase.SERVICE_AND_TIME:
            flex_days = 1 if urgency == UrgencyLevel.URGENT else 2
            return NarrowingInstruction(
                action=NarrowingAction.CALL_TOOL,
                case=case,
                tool_call=ToolCallPlan(
                    tool_name="check_availability",
                    params={
                        "service_name": constraints.desired_service,
                        "preferred_date": constraints.time_window_start,
                        "flexibility_days": flex_days
                    }
                ),
                eligible_doctor_count=doctor_count,
                urgency=urgency
            )

        # ===== CASE: SERVICE_AND_DOCTOR =====
        if case == NarrowingCase.SERVICE_AND_DOCTOR:
            return NarrowingInstruction(
                action=NarrowingAction.ASK_QUESTION,
                case=case,
                question_type=QuestionType.ASK_TIME_WITH_SERVICE,
                question_args={
                    "service_name": constraints.desired_service,
                    "doctor_name": constraints.desired_doctor
                },
                question_context="Have service+doctor. Need time.",
                eligible_doctor_count=doctor_count,
                urgency=urgency
            )

        # ===== CASE: SERVICE_ONLY =====
        if case == NarrowingCase.SERVICE_ONLY:
            # Handle RPC error: doctor_count is None
            if doctor_count is None:
                return NarrowingInstruction(
                    action=NarrowingAction.ASK_QUESTION,
                    case=case,
                    question_type=QuestionType.ASK_FOR_TIME,
                    question_args={"service_name": constraints.desired_service},
                    question_context="RPC error - couldn't check doctors. Ask time, let tool handle selection.",
                    eligible_doctor_count=None,
                    urgency=urgency
                )

            # Genuinely no doctors for this service
            if doctor_count == 0:
                return NarrowingInstruction(
                    action=NarrowingAction.ASK_QUESTION,
                    case=case,
                    question_type=QuestionType.SUGGEST_CONSULTATION,
                    question_args={"service_name": constraints.desired_service},
                    question_context="No eligible doctors. Suggest consultation.",
                    eligible_doctor_count=0,
                    urgency=urgency
                )

            # Single doctor
            if doctor_count == 1:
                doc_name = doctors[0].get('doctor_name', 'the doctor')
                return NarrowingInstruction(
                    action=NarrowingAction.ASK_QUESTION,
                    case=case,
                    question_type=QuestionType.ASK_TIME_WITH_DOCTOR,
                    question_args={
                        "doctor_name": doc_name,
                        "service_name": constraints.desired_service
                    },
                    question_context=f"1 doctor: {doc_name}. Ask time.",
                    eligible_doctor_count=1,
                    urgency=urgency
                )

            # 2-3 doctors: offer choice
            if doctor_count <= 3:
                doc_names = [d.get('doctor_name', 'doctor') for d in doctors]
                return NarrowingInstruction(
                    action=NarrowingAction.ASK_QUESTION,
                    case=case,
                    question_type=QuestionType.ASK_DOCTOR_OR_FIRST_AVAILABLE,
                    question_args={
                        "doctor_names": doc_names,
                        "service_name": constraints.desired_service
                    },
                    question_context=f"2-3 doctors: {doc_names}. Ask preference.",
                    eligible_doctor_count=doctor_count,
                    urgency=urgency
                )

            # >3 doctors: ask time to narrow
            return NarrowingInstruction(
                action=NarrowingAction.ASK_QUESTION,
                case=case,
                question_type=QuestionType.ASK_FOR_TIME,
                question_args={"service_name": constraints.desired_service},
                question_context="Many doctors. Ask time to narrow.",
                eligible_doctor_count=doctor_count,
                urgency=urgency
            )

        # ===== CASE: DOCTOR_ONLY =====
        if case == NarrowingCase.DOCTOR_ONLY:
            if clinic_strategy == "service_first":
                return NarrowingInstruction(
                    action=NarrowingAction.ASK_QUESTION,
                    case=case,
                    question_type=QuestionType.ASK_FOR_SERVICE,
                    question_args={"doctor_name": constraints.desired_doctor},
                    question_context="Doctor known. Ask service.",
                    eligible_doctor_count=doctor_count,
                    urgency=urgency
                )
            else:
                return NarrowingInstruction(
                    action=NarrowingAction.ASK_QUESTION,
                    case=case,
                    question_type=QuestionType.ASK_TIME_WITH_DOCTOR,
                    question_args={"doctor_name": constraints.desired_doctor},
                    question_context="Doctor first. Assume consult, ask time.",
                    eligible_doctor_count=doctor_count,
                    urgency=urgency
                )

        # ===== CASE: TIME_ONLY =====
        if case == NarrowingCase.TIME_ONLY:
            return NarrowingInstruction(
                action=NarrowingAction.ASK_QUESTION,
                case=case,
                question_type=QuestionType.ASK_FOR_SERVICE,
                question_args={},
                question_context="Time known. Need service.",
                eligible_doctor_count=None,
                urgency=urgency
            )

        # ===== CASE: URGENT_NO_TIME =====
        if case == NarrowingCase.URGENT_NO_TIME:
            return NarrowingInstruction(
                action=NarrowingAction.ASK_QUESTION,
                case=case,
                question_type=QuestionType.ASK_TODAY_OR_TOMORROW,
                question_args={},
                question_context="Urgent. Narrow to today/tomorrow.",
                eligible_doctor_count=None,
                urgency=urgency
            )

        # ===== CASE: NOTHING_KNOWN (Default) =====
        return NarrowingInstruction(
            action=NarrowingAction.ASK_QUESTION,
            case=NarrowingCase.NOTHING_KNOWN,
            question_type=QuestionType.ASK_FOR_SERVICE,
            question_args={},
            question_context="Start with service (service-first strategy).",
            eligible_doctor_count=None,
            urgency=urgency
        )
