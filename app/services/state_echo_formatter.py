"""
State Echo Formatter
Formats user-visible state confirmations after corrections
"""

from typing import Dict
from app.services.conversation_constraints import ConversationConstraints


class StateEchoFormatter:
    """Formats user-visible state confirmations in multiple languages"""

    TEMPLATES = {
        'ru': {
            'correction_acknowledged': (
                "Понял. Фиксирую:\n"
                "{service_line}"
                "{doctor_line}"
                "{exclusions_line}"
                "{time_line}"
                "Проверяю доступность..."
            ),
            'service_set': "• Услуга — **{service}**\n",
            'doctor_set': "• Предпочитаемый врач — **{doctor}**\n",
            'exclusions': "• Не предлагаю: {items}\n",
            'time_window': "• Период — **{window}**\n"
        },
        'en': {
            'correction_acknowledged': (
                "Understood. Locking in:\n"
                "{service_line}"
                "{doctor_line}"
                "{exclusions_line}"
                "{time_line}"
                "Checking availability..."
            ),
            'service_set': "• Service: **{service}**\n",
            'doctor_set': "• Preferred doctor: **{doctor}**\n",
            'exclusions': "• Will not suggest: {items}\n",
            'time_window': "• Time window: **{window}**\n"
        },
        'es': {
            'correction_acknowledged': (
                "Entendido. Fijando:\n"
                "{service_line}"
                "{doctor_line}"
                "{exclusions_line}"
                "{time_line}"
                "Verificando disponibilidad..."
            ),
            'service_set': "• Servicio: **{service}**\n",
            'doctor_set': "• Doctor preferido: **{doctor}**\n",
            'exclusions': "• No sugeriré: {items}\n",
            'time_window': "• Período: **{window}**\n"
        },
        'he': {
            'correction_acknowledged': (
                "הבנתי. קובע:\n"
                "{service_line}"
                "{doctor_line}"
                "{exclusions_line}"
                "{time_line}"
                "בודק זמינות..."
            ),
            'service_set': "• שירות: **{service}**\n",
            'doctor_set': "• רופא מועדף: **{doctor}**\n",
            'exclusions': "• לא אציע: {items}\n",
            'time_window': "• תקופה: **{window}**\n"
        }
    }

    def format_correction_acknowledgment(
        self,
        constraints: ConversationConstraints,
        language: str = 'ru'
    ) -> str:
        """Format state echo after user correction"""

        templates = self.TEMPLATES.get(language, self.TEMPLATES['en'])

        lines = {
            'service_line': '',
            'doctor_line': '',
            'exclusions_line': '',
            'time_line': ''
        }

        # Service
        if constraints.desired_service:
            lines['service_line'] = templates['service_set'].format(
                service=constraints.desired_service
            )

        # Doctor
        if constraints.desired_doctor:
            lines['doctor_line'] = templates['doctor_set'].format(
                doctor=constraints.desired_doctor
            )

        # Exclusions
        excluded_items = []
        if constraints.excluded_doctors:
            if language == 'ru':
                excluded_items.extend([f"врача {d}" for d in constraints.excluded_doctors])
            elif language == 'en':
                excluded_items.extend([f"Dr. {d}" for d in constraints.excluded_doctors])
            elif language == 'es':
                excluded_items.extend([f"Dr. {d}" for d in constraints.excluded_doctors])
            elif language == 'he':
                excluded_items.extend([f"ד\"ר {d}" for d in constraints.excluded_doctors])

        if constraints.excluded_services:
            if language == 'ru':
                excluded_items.extend([f"услугу {s}" for s in constraints.excluded_services])
            else:
                excluded_items.extend(list(constraints.excluded_services))

        if excluded_items:
            items_str = ", ".join(excluded_items)
            lines['exclusions_line'] = templates['exclusions'].format(items=items_str)

        # Time window
        if constraints.time_window_display:
            lines['time_line'] = templates['time_window'].format(
                window=constraints.time_window_display
            )

        return templates['correction_acknowledged'].format(**lines)

    def format_response(
        self,
        ai_response: str,
        constraints: ConversationConstraints,
        language: str = 'en'
    ) -> str:
        """
        Format AI response with state echo prepended.

        This is the main entry point used by MultilingualMessageProcessor.
        It prepends a state acknowledgment to the AI response when constraints
        are present.

        Args:
            ai_response: The AI-generated response text
            constraints: Current conversation constraints
            language: ISO language code (en, es, ru, he)

        Returns:
            AI response with state echo prepended if constraints exist,
            otherwise returns the original ai_response unchanged.
        """
        # Check if we have meaningful constraints to echo
        has_meaningful_constraints = (
            constraints.excluded_doctors
            or constraints.excluded_services
            or constraints.desired_service
            or constraints.desired_doctor
            or constraints.time_window_start
        )

        if not has_meaningful_constraints:
            return ai_response

        # Generate the state echo acknowledgment
        state_echo = self.format_correction_acknowledgment(constraints, language)

        # If state echo is empty or just whitespace, return original
        if not state_echo or not state_echo.strip():
            return ai_response

        # Prepend state echo to AI response with double newline separator
        return f"{state_echo}\n\n{ai_response}"
