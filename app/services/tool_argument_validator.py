"""
Tool Argument Validation Layer

Prevents common LLM tool calling errors:
1. Hardcoded IDs (e.g., doctor_id='1' instead of dynamic ID from context)
2. Invalid UUID formats
3. Missing required context
4. Out-of-range values

This layer validates tool arguments BEFORE execution to catch errors early.
"""

import logging
import re
import uuid
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime, date

logger = logging.getLogger(__name__)


class ToolArgumentValidator:
    """Validates tool arguments before execution"""

    # Common hardcoded values that indicate LLM errors
    SUSPICIOUS_VALUES = {
        '1', '2', '3', '123', 'test', 'example', 'demo',
        'null', 'none', 'undefined', '', 'N/A', 'TBD'
    }

    # UUID pattern
    UUID_PATTERN = re.compile(
        r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$',
        re.IGNORECASE
    )

    def __init__(self):
        self.validation_rules = {
            'doctor_id': self._validate_uuid_field,
            'patient_id': self._validate_uuid_field,
            'service_id': self._validate_uuid_field,
            'clinic_id': self._validate_uuid_field,
            'room_id': self._validate_uuid_field,
            'appointment_date': self._validate_date_field,
            'start_time': self._validate_time_field,
            'duration_minutes': self._validate_duration_field,
        }

    def validate_tool_call(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        context: Dict[str, Any]
    ) -> Tuple[bool, List[str], Optional[Dict[str, Any]]]:
        """
        Validate tool arguments before execution

        Args:
            tool_name: Name of the tool being called
            arguments: Tool arguments from LLM
            context: Available context (doctors, services, etc.)

        Returns:
            Tuple of (is_valid, errors, suggested_fixes)
        """
        errors = []
        suggestions = {}

        # Check for suspicious hardcoded values
        for arg_name, arg_value in arguments.items():
            if isinstance(arg_value, str):
                normalized = str(arg_value).strip().lower()

                # Check if value is suspicious
                if normalized in self.SUSPICIOUS_VALUES:
                    errors.append(
                        f"❌ Suspicious hardcoded value for '{arg_name}': '{arg_value}'. "
                        f"This should be a dynamic value from context."
                    )

                    # Try to suggest fix from context
                    suggested = self._suggest_value_from_context(arg_name, context)
                    if suggested:
                        suggestions[arg_name] = suggested

        # Apply field-specific validation rules
        for field_name, validator in self.validation_rules.items():
            if field_name in arguments:
                is_valid, error, suggestion = validator(
                    field_name,
                    arguments[field_name],
                    context
                )

                if not is_valid:
                    errors.append(error)
                    if suggestion:
                        suggestions[field_name] = suggestion

        # Tool-specific validation
        if tool_name == 'book_appointment':
            tool_errors, tool_suggestions = self._validate_book_appointment(
                arguments, context
            )
            errors.extend(tool_errors)
            suggestions.update(tool_suggestions)

        is_valid = len(errors) == 0

        if not is_valid:
            logger.warning(
                f"Tool validation failed for '{tool_name}': {len(errors)} error(s)\n" +
                "\n".join(errors)
            )

        return is_valid, errors, suggestions if suggestions else None

    def _validate_uuid_field(
        self,
        field_name: str,
        value: Any,
        context: Dict[str, Any]
    ) -> Tuple[bool, str, Optional[str]]:
        """Validate UUID field"""

        if not value:
            return False, f"❌ {field_name} is required", None

        value_str = str(value).strip()

        # Check if it's a valid UUID
        if not self.UUID_PATTERN.match(value_str):
            # Try to find valid UUID in context
            suggestion = self._suggest_value_from_context(field_name, context)

            return (
                False,
                f"❌ Invalid UUID format for {field_name}: '{value_str}'",
                suggestion
            )

        # Check if UUID exists in context (if applicable)
        if field_name == 'doctor_id':
            doctors = context.get('doctors', [])
            if doctors and not any(d.get('id') == value_str for d in doctors):
                available = ', '.join([d.get('id')[:8] for d in doctors[:3]])
                return (
                    False,
                    f"❌ Doctor ID not found in available doctors. Available: {available}...",
                    None
                )

        return True, "", None

    def _validate_date_field(
        self,
        field_name: str,
        value: Any,
        context: Dict[str, Any]
    ) -> Tuple[bool, str, Optional[str]]:
        """Validate date field"""

        try:
            # Try to parse as ISO date
            if isinstance(value, str):
                parsed_date = datetime.fromisoformat(value.replace('Z', '+00:00')).date()
            elif isinstance(value, datetime):
                parsed_date = value.date()
            elif isinstance(value, date):
                parsed_date = value
            else:
                return False, f"❌ Invalid date format for {field_name}: {value}", None

            # Check if date is in the future
            today = date.today()
            if parsed_date < today:
                return (
                    False,
                    f"❌ Date {parsed_date} is in the past. Appointments must be in the future.",
                    str(today)
                )

            # Check if date is too far in the future (> 1 year)
            if (parsed_date - today).days > 365:
                return (
                    False,
                    f"❌ Date {parsed_date} is too far in the future (>1 year)",
                    None
                )

            return True, "", None

        except Exception as e:
            return False, f"❌ Invalid date format for {field_name}: {str(e)}", None

    def _validate_time_field(
        self,
        field_name: str,
        value: Any,
        context: Dict[str, Any]
    ) -> Tuple[bool, str, Optional[str]]:
        """Validate time field"""

        try:
            # Accept formats: "14:30", "2:30 PM", "14:30:00"
            time_str = str(value).strip()

            # Try parsing as time
            from dateutil.parser import parse as parse_time
            parsed = parse_time(time_str).time()

            # Check business hours (e.g., 8 AM - 8 PM)
            if parsed.hour < 8 or parsed.hour >= 20:
                return (
                    False,
                    f"❌ Time {time_str} is outside business hours (8 AM - 8 PM)",
                    None
                )

            return True, "", None

        except Exception as e:
            return False, f"❌ Invalid time format for {field_name}: {str(e)}", None

    def _validate_duration_field(
        self,
        field_name: str,
        value: Any,
        context: Dict[str, Any]
    ) -> Tuple[bool, str, Optional[str]]:
        """Validate duration field"""

        try:
            duration = int(value)

            if duration <= 0:
                return False, f"❌ Duration must be positive: {duration}", None

            if duration > 480:  # 8 hours
                return (
                    False,
                    f"❌ Duration {duration} minutes is too long (max 8 hours)",
                    None
                )

            # Check if duration is reasonable (multiple of 15 min)
            if duration % 15 != 0:
                suggested = ((duration + 14) // 15) * 15
                return (
                    False,
                    f"❌ Duration should be in 15-minute intervals. Got: {duration}",
                    str(suggested)
                )

            return True, "", None

        except Exception as e:
            return False, f"❌ Invalid duration: {str(e)}", None

    def _validate_book_appointment(
        self,
        arguments: Dict[str, Any],
        context: Dict[str, Any]
    ) -> Tuple[List[str], Dict[str, Any]]:
        """Validate book_appointment tool arguments"""

        errors = []
        suggestions = {}

        # Check required fields
        required = ['patient_id', 'doctor_id', 'appointment_date', 'start_time']
        for field in required:
            if field not in arguments or not arguments[field]:
                errors.append(f"❌ Missing required field: {field}")

                # Try to suggest from context
                suggested = self._suggest_value_from_context(field, context)
                if suggested:
                    suggestions[field] = suggested

        # Check if doctor and service are compatible
        doctor_id = arguments.get('doctor_id')
        service_id = arguments.get('service_id')

        if doctor_id and service_id:
            doctors = context.get('doctors', [])
            doctor = next((d for d in doctors if d.get('id') == doctor_id), None)

            if doctor:
                # Check if doctor's specialization matches service
                specialization = doctor.get('specialization', '').lower()
                service_name = context.get('service_name', '').lower()

                # Simple heuristic - can be improved
                if service_name and specialization not in service_name and service_name not in specialization:
                    logger.warning(
                        f"Doctor specialization '{specialization}' may not match service '{service_name}'"
                    )

        return errors, suggestions

    def _suggest_value_from_context(
        self,
        field_name: str,
        context: Dict[str, Any]
    ) -> Optional[str]:
        """Try to suggest a valid value from context"""

        # Mapping of field names to context keys
        context_mapping = {
            'doctor_id': ('doctors', 'id'),
            'patient_id': ('patient', 'id'),
            'service_id': ('services', 'id'),
            'clinic_id': ('clinic', 'id'),
            'room_id': ('rooms', 'id'),
        }

        if field_name not in context_mapping:
            return None

        context_key, id_field = context_mapping[field_name]

        # Handle single object
        if context_key in context and isinstance(context[context_key], dict):
            return context[context_key].get(id_field)

        # Handle list of objects (return first)
        if context_key in context and isinstance(context[context_key], list):
            items = context[context_key]
            if items and len(items) > 0:
                return items[0].get(id_field)

        return None

    def create_error_response(
        self,
        tool_name: str,
        errors: List[str],
        suggestions: Optional[Dict[str, Any]] = None
    ) -> str:
        """Create user-friendly error response"""

        response = f"I apologize, but I need to verify some information before I can {tool_name.replace('_', ' ')}:\n\n"

        for error in errors:
            # Remove technical prefix for user
            clean_error = error.replace('❌', '').strip()
            response += f"• {clean_error}\n"

        if suggestions:
            response += "\nLet me check the available options for you..."

        return response


# Singleton instance
_tool_validator: Optional[ToolArgumentValidator] = None


def get_tool_validator() -> ToolArgumentValidator:
    """Get or create singleton ToolArgumentValidator instance"""
    global _tool_validator
    if _tool_validator is None:
        _tool_validator = ToolArgumentValidator()
    return _tool_validator
