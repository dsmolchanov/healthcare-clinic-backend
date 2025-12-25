"""
Semantic Adapter - Bridges LLM outputs to system inputs.

This adapter solves the fundamental mismatch between:
- **LLM outputs**: Natural language names, relative dates, fuzzy references
- **System inputs**: UUIDs, ISO datetime strings, exact identifiers

Pattern from Opinion 3:
> "The reason you have a mismatch is that you are exposing System-Level Methods
> directly to the LLM. System View needs patient_id (UUID), doctor_id (UUID).
> LLM View knows patient_name, doctor_name, 'next Tuesday at 2pm'.
> You need a Middle Layer (Adapter)."

Usage:
    adapter = SemanticAdapter(clinic_id, context, supabase_client)

    # Resolve doctor name to UUID
    doctor_id = await adapter.resolve_doctor("Dr. Maria")

    # Parse natural language datetime
    iso_time = adapter.parse_datetime("next Tuesday at 2pm")

    # Adapt full tool arguments
    adapted_args = await adapt_tool_arguments("book_appointment", raw_args, adapter)
"""

from typing import Optional, Dict, Any, List
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import re
import logging

logger = logging.getLogger(__name__)


class SemanticAdapter:
    """
    Resolves fuzzy LLM inputs to concrete system values.

    Responsibilities:
    1. Doctor name → doctor_id (UUID) resolution
    2. Natural language datetime → ISO datetime parsing
    3. Patient ID injection from session context
    4. Service name → service_id resolution (future)

    Example:
        adapter = SemanticAdapter(clinic_id, context)
        doctor_id = await adapter.resolve_doctor("Dr. Maria")
        iso_time = adapter.parse_datetime("next Tuesday at 2pm")
    """

    def __init__(
        self,
        clinic_id: str,
        context: Dict[str, Any],
        supabase_client: Optional[Any] = None,
        clinic_timezone: str = "UTC"
    ):
        """
        Initialize semantic adapter.

        Args:
            clinic_id: UUID of the clinic
            context: Session context containing cached data (doctors, patient_profile, etc.)
            supabase_client: Optional Supabase client for database lookups
            clinic_timezone: Clinic's timezone for date parsing (e.g., "America/New_York")
        """
        self.clinic_id = clinic_id
        self.context = context
        self.supabase = supabase_client
        self.clinic_timezone = clinic_timezone

        # Pre-cache doctors from context if available
        self.doctors_cache: List[Dict] = context.get('clinic_doctors', [])
        self.services_cache: List[Dict] = context.get('clinic_services', [])

    async def resolve_doctor(
        self,
        doctor_ref: str,
    ) -> Optional[str]:
        """
        Resolve doctor name/reference to UUID.

        Handles various input formats:
        - "Dr. Maria" → matches first_name "Maria"
        - "Maria Garcia" → matches full name
        - "Garcia" → matches last_name
        - "any" / "anyone" → returns first available doctor
        - UUID string → returned as-is

        Args:
            doctor_ref: Doctor name, partial name, "any", or UUID

        Returns:
            Doctor UUID or None if not found

        Examples:
            >>> await adapter.resolve_doctor("Dr. Maria")
            "uuid-1234-5678"
            >>> await adapter.resolve_doctor("any")
            "uuid-first-available"
        """
        if not doctor_ref:
            return None

        # Check if already a UUID
        if self._is_uuid(doctor_ref):
            logger.debug(f"[adapter] Doctor ref is already UUID: {doctor_ref}")
            return doctor_ref

        # Handle "any" / "anyone" / "first available"
        if doctor_ref.lower() in ['any', 'anyone', 'first available', 'cualquier', 'любой']:
            if self.doctors_cache:
                doctor_id = self.doctors_cache[0].get('id')
                logger.info(f"[adapter] Resolved 'any' doctor → {doctor_id}")
                return doctor_id
            return None

        # Normalize search term
        search_term = doctor_ref.lower().strip()
        search_term = re.sub(r'^(dr\.?|doctor|доктор|dra?\.?)\s*', '', search_term, flags=re.IGNORECASE)
        search_term = search_term.strip()

        if not search_term:
            return None

        # Search in cache first (fast path)
        for doctor in self.doctors_cache:
            first_name = (doctor.get('first_name') or '').lower()
            last_name = (doctor.get('last_name') or '').lower()
            full_name = f"{first_name} {last_name}".strip()
            display_name = (doctor.get('name') or '').lower()

            # Check various matching strategies
            if (search_term == first_name or
                search_term == last_name or
                search_term == full_name or
                search_term in display_name or
                first_name.startswith(search_term) or
                last_name.startswith(search_term) or
                full_name.startswith(search_term)):

                doctor_id = doctor.get('id')
                logger.info(f"[adapter] Resolved doctor '{doctor_ref}' → {doctor_id} (cache)")
                return doctor_id

        # Fallback: fuzzy search in database
        if self.supabase:
            try:
                # Try first_name match
                result = await self._db_doctor_search(search_term)
                if result:
                    logger.info(f"[adapter] Resolved doctor '{doctor_ref}' → {result} (DB)")
                    return result
            except Exception as e:
                logger.warning(f"[adapter] Doctor DB lookup failed: {e}")

        logger.warning(f"[adapter] Could not resolve doctor: '{doctor_ref}'")
        return None

    async def _db_doctor_search(self, search_term: str) -> Optional[str]:
        """Search database for doctor by name."""
        if not self.supabase:
            return None

        try:
            # Try first_name ilike
            result = self.supabase.schema('healthcare').table('doctors').select(
                'id, first_name, last_name'
            ).eq('clinic_id', self.clinic_id).eq(
                'active', True
            ).ilike('first_name', f'%{search_term}%').limit(1).execute()

            if result.data:
                return result.data[0]['id']

            # Try last_name ilike
            result = self.supabase.schema('healthcare').table('doctors').select(
                'id, first_name, last_name'
            ).eq('clinic_id', self.clinic_id).eq(
                'active', True
            ).ilike('last_name', f'%{search_term}%').limit(1).execute()

            if result.data:
                return result.data[0]['id']

            return None

        except Exception as e:
            logger.error(f"[adapter] DB doctor search failed: {e}")
            return None

    def parse_datetime(
        self,
        datetime_ref: str,
        default_hour: int = 9,
        default_minute: int = 0,
    ) -> Optional[str]:
        """
        Parse natural language datetime to ISO format.

        Handles various input formats:
        - ISO format: "2024-12-31T14:00:00" → passthrough
        - Date only: "2024-12-31" → adds default time
        - Relative: "tomorrow", "today", "next week"
        - Day names: "next Tuesday", "this Friday"
        - Time expressions: "at 2pm", "at 14:00"

        Args:
            datetime_ref: Datetime string (ISO or natural language)
            default_hour: Default hour if time not specified (0-23)
            default_minute: Default minute if time not specified (0-59)

        Returns:
            ISO datetime string or None if unparseable

        Examples:
            >>> adapter.parse_datetime("2024-12-31T14:00:00")
            "2024-12-31T14:00:00"
            >>> adapter.parse_datetime("tomorrow at 2pm")
            "2024-12-25T14:00:00"
            >>> adapter.parse_datetime("next Tuesday")
            "2024-12-31T09:00:00"
        """
        if not datetime_ref:
            return None

        datetime_ref = datetime_ref.strip()

        # Already ISO format with time
        if re.match(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?$', datetime_ref):
            return datetime_ref

        # Already date format, add default time
        if re.match(r'^\d{4}-\d{2}-\d{2}$', datetime_ref):
            return f"{datetime_ref}T{default_hour:02d}:{default_minute:02d}:00"

        # Parse natural language
        try:
            now = datetime.now(ZoneInfo(self.clinic_timezone))
        except Exception:
            now = datetime.now()

        lower_ref = datetime_ref.lower()

        # Extract time component first
        hour, minute = self._extract_time(lower_ref, default_hour, default_minute)

        # Parse date component
        target_date = self._parse_date_component(lower_ref, now)

        if target_date is None:
            logger.warning(f"[adapter] Could not parse datetime: '{datetime_ref}'")
            return None

        # Combine date and time
        result = target_date.replace(hour=hour, minute=minute, second=0, microsecond=0)
        iso_result = result.strftime("%Y-%m-%dT%H:%M:%S")

        logger.info(f"[adapter] Parsed datetime '{datetime_ref}' → {iso_result}")
        return iso_result

    def _extract_time(
        self,
        text: str,
        default_hour: int,
        default_minute: int
    ) -> tuple[int, int]:
        """Extract hour and minute from time expression."""
        # Pattern: "at 2pm", "at 14:00", "2:30pm", "14:30"
        time_patterns = [
            # "at 2:30 pm" or "2:30pm"
            r'(?:at\s+)?(\d{1,2}):(\d{2})\s*(am|pm)?',
            # "at 2 pm" or "2pm"
            r'(?:at\s+)?(\d{1,2})\s*(am|pm)',
            # "at 14:00" (24-hour)
            r'(?:at\s+)?(\d{2}):(\d{2})(?!\s*[ap]m)',
        ]

        for pattern in time_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                groups = match.groups()

                if len(groups) == 3 and groups[1] is not None:
                    # HH:MM am/pm format
                    hour = int(groups[0])
                    minute = int(groups[1])
                    ampm = groups[2]
                    if ampm and ampm.lower() == 'pm' and hour < 12:
                        hour += 12
                    elif ampm and ampm.lower() == 'am' and hour == 12:
                        hour = 0
                    return hour, minute

                elif len(groups) == 2:
                    hour = int(groups[0])
                    ampm_or_min = groups[1]

                    if ampm_or_min and ampm_or_min.lower() in ('am', 'pm'):
                        # "2pm" format
                        if ampm_or_min.lower() == 'pm' and hour < 12:
                            hour += 12
                        elif ampm_or_min.lower() == 'am' and hour == 12:
                            hour = 0
                        return hour, 0
                    else:
                        # "14:00" format
                        return hour, int(ampm_or_min) if ampm_or_min else 0

        return default_hour, default_minute

    def _parse_date_component(self, text: str, now: datetime) -> Optional[datetime]:
        """Parse date component from natural language."""
        # Today
        if 'today' in text or 'hoy' in text or 'сегодня' in text:
            return now

        # Tomorrow
        if 'tomorrow' in text or 'mañana' in text or 'завтра' in text:
            return now + timedelta(days=1)

        # Day after tomorrow
        if 'day after tomorrow' in text or 'pasado mañana' in text or 'послезавтра' in text:
            return now + timedelta(days=2)

        # Next week
        if 'next week' in text or 'próxima semana' in text or 'следующей неделе' in text:
            return now + timedelta(weeks=1)

        # This week / this weekend
        if 'this weekend' in text or 'este fin de semana' in text:
            # Find next Saturday
            days_until_saturday = (5 - now.weekday()) % 7
            if days_until_saturday == 0:
                days_until_saturday = 7
            return now + timedelta(days=days_until_saturday)

        # Day names: "next Tuesday", "this Friday", "on Monday"
        day_names = {
            'monday': 0, 'tuesday': 1, 'wednesday': 2, 'thursday': 3,
            'friday': 4, 'saturday': 5, 'sunday': 6,
            # Spanish
            'lunes': 0, 'martes': 1, 'miércoles': 2, 'jueves': 3,
            'viernes': 4, 'sábado': 5, 'domingo': 6,
            # Russian
            'понедельник': 0, 'вторник': 1, 'среда': 2, 'четверг': 3,
            'пятница': 4, 'суббота': 5, 'воскресенье': 6,
        }

        for day_name, day_num in day_names.items():
            if day_name in text:
                days_ahead = day_num - now.weekday()

                # "next" means next week's occurrence
                if 'next' in text or 'próximo' in text or 'следующ' in text:
                    if days_ahead <= 0:
                        days_ahead += 7
                # "this" means this week (could be today)
                elif 'this' in text or 'este' in text or 'эт' in text:
                    if days_ahead < 0:
                        days_ahead += 7
                else:
                    # Default: next occurrence
                    if days_ahead <= 0:
                        days_ahead += 7

                return now + timedelta(days=days_ahead)

        # In X days
        match = re.search(r'in\s+(\d+)\s+days?', text)
        if match:
            days = int(match.group(1))
            return now + timedelta(days=days)

        # Try to parse explicit date formats
        date_patterns = [
            (r'(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})', '%m/%d/%Y'),  # MM/DD/YYYY
            (r'(\d{4})[/.-](\d{1,2})[/.-](\d{1,2})', '%Y/%m/%d'),  # YYYY/MM/DD
            (r'(\d{1,2})\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*\s*(\d{4})?', 'named_month'),
        ]

        for pattern, fmt in date_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                try:
                    if fmt == 'named_month':
                        day = int(match.group(1))
                        month_names = {
                            'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'may': 5, 'jun': 6,
                            'jul': 7, 'aug': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12
                        }
                        month = month_names.get(match.group(2).lower()[:3], 1)
                        year = int(match.group(3)) if match.group(3) else now.year
                        return datetime(year, month, day)
                    else:
                        date_str = match.group(0).replace('-', '/').replace('.', '/')
                        return datetime.strptime(date_str, fmt.replace('-', '/').replace('.', '/'))
                except ValueError:
                    continue

        return None

    def inject_patient_id(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """
        Inject patient_id from session context if missing.

        The LLM should never guess patient IDs - they come from the session.
        This method ensures patient_id is always present for booking operations.

        Args:
            arguments: Tool call arguments dict

        Returns:
            Arguments dict with patient_id injected if missing
        """
        if 'patient_id' not in arguments or not arguments['patient_id']:
            patient_id = self.context.get('patient_profile', {}).get('id')
            if patient_id:
                arguments = arguments.copy()
                arguments['patient_id'] = patient_id
                logger.debug(f"[adapter] Injected patient_id from context: {patient_id}")
        return arguments

    async def resolve_service(self, service_ref: str) -> Optional[str]:
        """
        Resolve service name to service_id UUID.

        Args:
            service_ref: Service name or partial name

        Returns:
            Service UUID or None if not found
        """
        if not service_ref:
            return None

        if self._is_uuid(service_ref):
            return service_ref

        search_term = service_ref.lower().strip()

        # Search in cache
        for service in self.services_cache:
            name = (service.get('name') or '').lower()
            name_ru = (service.get('name_ru') or '').lower()
            name_en = (service.get('name_en') or '').lower()

            if (search_term in name or
                search_term in name_ru or
                search_term in name_en or
                name.startswith(search_term)):
                service_id = service.get('id')
                logger.info(f"[adapter] Resolved service '{service_ref}' → {service_id}")
                return service_id

        logger.warning(f"[adapter] Could not resolve service: '{service_ref}'")
        return None

    @staticmethod
    def _is_uuid(value: str) -> bool:
        """Check if string looks like a UUID."""
        return bool(re.match(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', value.lower()))


async def adapt_tool_arguments(
    tool_name: str,
    raw_arguments: Dict[str, Any],
    adapter: SemanticAdapter,
) -> Dict[str, Any]:
    """
    Adapt raw LLM arguments to system-ready arguments.

    This is the main entry point for semantic adaptation.
    Called by executor_node before tool execution.

    Flow:
        LLM outputs → adapt_tool_arguments() → validate_tool_call() → tool execution

    Args:
        tool_name: Name of the tool being called
        raw_arguments: Raw arguments from LLM tool call
        adapter: SemanticAdapter instance with context

    Returns:
        Adapted arguments ready for validation and execution

    Example:
        >>> adapter = SemanticAdapter(clinic_id, context, supabase)
        >>> raw_args = {"doctor_id": "Dr. Maria", "datetime_str": "tomorrow at 2pm"}
        >>> adapted = await adapt_tool_arguments("book_appointment", raw_args, adapter)
        >>> # adapted = {"doctor_id": "uuid-1234", "datetime_str": "2024-12-25T14:00:00"}
    """
    args = raw_arguments.copy()

    # Inject patient_id for booking/cancellation operations
    if tool_name in ['book_appointment', 'cancel_appointment', 'reschedule_appointment']:
        args = adapter.inject_patient_id(args)

    # Resolve doctor reference to UUID
    if 'doctor_id' in args and args['doctor_id']:
        # Only resolve if not already a UUID
        if not SemanticAdapter._is_uuid(str(args['doctor_id'])):
            resolved = await adapter.resolve_doctor(str(args['doctor_id']))
            if resolved:
                logger.info(f"[adapt] Resolved doctor_id: '{args['doctor_id']}' → {resolved}")
                args['doctor_id'] = resolved
            else:
                logger.warning(f"[adapt] Could not resolve doctor_id: '{args['doctor_id']}'")
                # Keep original value - validation will catch if invalid

    # Parse datetime fields
    datetime_fields = ['datetime_str', 'new_datetime']
    for field in datetime_fields:
        if field in args and args[field]:
            parsed = adapter.parse_datetime(str(args[field]))
            if parsed:
                logger.debug(f"[adapt] Parsed {field}: '{args[field]}' → {parsed}")
                args[field] = parsed

    # Parse date-only fields
    if 'date' in args and args['date']:
        parsed = adapter.parse_datetime(str(args['date']))
        if parsed:
            # Extract date portion only
            args['date'] = parsed.split('T')[0]

    # Resolve service reference
    if 'service_id' in args and args['service_id']:
        if not SemanticAdapter._is_uuid(str(args['service_id'])):
            resolved = await adapter.resolve_service(str(args['service_id']))
            if resolved:
                args['service_id'] = resolved

    return args


# =============================================================================
# Exports
# =============================================================================

__all__ = [
    'SemanticAdapter',
    'adapt_tool_arguments',
]
