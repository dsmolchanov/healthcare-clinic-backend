"""
SlotManager Module

Manages slot extraction, validation, and evidence tracking for the FSM system.
Provides timezone-aware date validation, doctor name extraction, and slot freshness checks.

Key Features:
- Timezone-aware date validation using clinic-specific timezones
- Doctor name extraction with regex (captures actual name, not "доктор" keyword)
- Slot evidence tracking with provenance and confirmation status
- Slot freshness detection to prevent stale data from being used
- Database integration for clinic timezone and doctor validation
"""

from datetime import datetime, date, timedelta, timezone
from typing import Dict, Tuple, Optional, Any, List
import re
import pytz
import logging
import time

from .models import FSMState, SlotEvidence, SlotSource
from ..db.supabase_client import get_supabase_client
from .metrics import (
    record_slot_validation,
    record_context_contamination,
    record_bad_booking
)
from .logger import (
    log_slot_validation,
    log_context_contamination,
    log_bad_booking
)

logger = logging.getLogger(__name__)


class SlotManager:
    """
    Manages slot extraction, validation, and evidence tracking.

    This class provides methods for:
    - Timezone-aware date validation
    - Doctor name extraction and validation
    - Slot management with evidence tracking
    - Slot freshness detection

    Example:
        >>> manager = SlotManager()
        >>> # Validate a date
        >>> is_valid, error = await manager.validate_date_slot("завтра", "clinic_123")
        >>> # Extract doctor name
        >>> doctor = manager.extract_doctor_name("Запись к доктору Иванову")
        >>> # Add slot to state
        >>> new_state = manager.add_slot(
        ...     state,
        ...     "appointment_date",
        ...     "2025-10-20",
        ...     SlotSource.LLM_EXTRACT,
        ...     confidence=0.95
        ... )
    """

    def __init__(self):
        """Initialize SlotManager with Supabase client."""
        self.supabase = get_supabase_client(schema='healthcare')
        logger.info("SlotManager initialized with Supabase client")

    async def get_clinic_timezone(self, clinic_id: str) -> pytz.timezone:
        """
        Fetch clinic timezone from database.

        Queries the clinics table for the timezone setting. Falls back to UTC
        if the clinic is not found or timezone is not configured.

        Args:
            clinic_id: Unique identifier for the clinic

        Returns:
            pytz.timezone: Timezone object for the clinic (defaults to UTC)

        Example:
            >>> tz = await manager.get_clinic_timezone("clinic_123")
            >>> print(tz)  # <DstTzInfo 'Europe/Moscow' LMT+2:30:00 STD>
        """
        try:
            result = self.supabase.table('clinics').select('timezone').eq('id', clinic_id).single().execute()

            if not result.data or 'timezone' not in result.data:
                logger.warning(f"Clinic {clinic_id} not found or timezone not set, defaulting to UTC")
                return pytz.UTC

            timezone_str = result.data['timezone']
            logger.info(f"Clinic {clinic_id} timezone: {timezone_str}")
            return pytz.timezone(timezone_str)

        except Exception as e:
            logger.error(f"Error fetching clinic timezone for {clinic_id}: {e}", exc_info=True)
            return pytz.UTC

    async def validate_date_slot(
        self,
        date_value: str,
        clinic_id: str
    ) -> Tuple[bool, Optional[str]]:
        """
        Validate date slot using clinic timezone.

        Parses the date string and validates it against clinic timezone:
        - Rejects past dates
        - Rejects dates more than 90 days in the future
        - Handles Russian relative dates ("завтра", "сегодня", "послезавтра")
        - Handles DD.MM and DD.MM.YYYY formats

        Args:
            date_value: Date string to validate (e.g., "завтра", "15.10", "15.10.2024")
            clinic_id: Clinic identifier for timezone lookup

        Returns:
            Tuple of (is_valid, error_message). If valid, error_message is None.

        Example:
            >>> is_valid, error = await manager.validate_date_slot("вчера", "clinic_123")
            >>> print(is_valid, error)
            False, "Дата вчера уже прошла. Пожалуйста, укажите будущую дату."
        """
        start_time = time.time()
        clinic_tz = await self.get_clinic_timezone(clinic_id)

        try:
            # Parse date (supports "завтра", "сегодня", "15.10", etc.)
            parsed_date = self._parse_date_string(date_value, clinic_tz)

            # Get current date in clinic timezone
            now_clinic = datetime.now(clinic_tz).date()

            if parsed_date < now_clinic:
                error_msg = f"Дата {date_value} уже прошла. Пожалуйста, укажите будущую дату."
                duration_seconds = time.time() - start_time

                # Record bad booking attempt
                record_bad_booking("past_date", clinic_id)
                record_slot_validation("appointment_date", False, clinic_id, duration_seconds)
                log_slot_validation(
                    conversation_id="unknown",  # Not available in this context
                    clinic_id=clinic_id,
                    state="collecting_slots",
                    slot_name="appointment_date",
                    slot_value=date_value,
                    is_valid=False,
                    error_message=error_msg,
                    duration_ms=duration_seconds * 1000
                )

                return False, error_msg

            # Check if date is too far in future (>90 days)
            max_days_ahead = 90
            if (parsed_date - now_clinic).days > max_days_ahead:
                error_msg = f"Нельзя записаться более чем на {max_days_ahead} дней вперёд."
                duration_seconds = time.time() - start_time

                # Record bad booking attempt
                record_bad_booking("date_too_far", clinic_id)
                record_slot_validation("appointment_date", False, clinic_id, duration_seconds)
                log_slot_validation(
                    conversation_id="unknown",
                    clinic_id=clinic_id,
                    state="collecting_slots",
                    slot_name="appointment_date",
                    slot_value=date_value,
                    is_valid=False,
                    error_message=error_msg,
                    duration_ms=duration_seconds * 1000
                )

                return False, error_msg

            duration_seconds = time.time() - start_time

            # Record successful validation
            record_slot_validation("appointment_date", True, clinic_id, duration_seconds)
            log_slot_validation(
                conversation_id="unknown",
                clinic_id=clinic_id,
                state="collecting_slots",
                slot_name="appointment_date",
                slot_value=date_value,
                is_valid=True,
                duration_ms=duration_seconds * 1000
            )

            logger.info(f"Date {date_value} validated successfully for clinic {clinic_id}")
            return True, None

        except ValueError as e:
            duration_seconds = time.time() - start_time
            error_msg = f"Не могу распознать дату: {date_value}. Попробуйте формат ДД.ММ или 'завтра'."

            # Record validation failure
            record_bad_booking("invalid_date_format", clinic_id)
            record_slot_validation("appointment_date", False, clinic_id, duration_seconds)
            log_slot_validation(
                conversation_id="unknown",
                clinic_id=clinic_id,
                state="collecting_slots",
                slot_name="appointment_date",
                slot_value=date_value,
                is_valid=False,
                error_message=error_msg,
                duration_ms=duration_seconds * 1000
            )

            logger.warning(f"Date parsing failed for '{date_value}': {e}")
            return False, error_msg

    def _parse_date_string(self, date_str: str, clinic_tz: pytz.timezone) -> date:
        """
        Parse various date formats relative to clinic timezone.

        Supports:
        - Relative dates: "сегодня", "завтра", "послезавтра", "today", "tomorrow"
        - DD.MM format (assumes current year)
        - DD.MM.YYYY format

        Args:
            date_str: Date string to parse
            clinic_tz: Clinic timezone for relative date calculations

        Returns:
            date: Parsed date object

        Raises:
            ValueError: If date format is not recognized or invalid

        Example:
            >>> tz = pytz.timezone('Europe/Moscow')
            >>> parsed = manager._parse_date_string("завтра", tz)
            >>> print(parsed)  # Tomorrow's date
        """
        date_str = date_str.lower().strip()
        now_clinic = datetime.now(clinic_tz)

        # Relative dates
        if date_str in ["сегодня", "today"]:
            return now_clinic.date()
        elif date_str in ["завтра", "tomorrow"]:
            return (now_clinic + timedelta(days=1)).date()
        elif date_str in ["послезавтра"]:
            return (now_clinic + timedelta(days=2)).date()

        # Parse DD.MM or DD.MM.YYYY
        patterns = [
            r'^(\d{1,2})\.(\d{1,2})\.(\d{4})$',  # 15.10.2024
            r'^(\d{1,2})\.(\d{1,2})$',  # 15.10 (assume current year)
        ]

        for pattern in patterns:
            match = re.match(pattern, date_str)
            if match:
                day = int(match.group(1))
                month = int(match.group(2))
                year = int(match.group(3)) if len(match.groups()) == 3 else now_clinic.year

                try:
                    return date(year, month, day)
                except ValueError as e:
                    raise ValueError(f"Invalid date: {date_str} - {e}")

        raise ValueError(f"Unrecognized date format: {date_str}")

    def extract_doctor_name(self, message: str) -> Optional[str]:
        """
        Extract doctor name from message.

        Captures the actual doctor's name, NOT the word "доктор" itself.
        Supports both Russian and English patterns.

        Patterns matched:
        - "доктор <Name>" or "доктору <Name>"
        - "к доктору <Name>"
        - "dr. <Name>" or "dr <Name>"
        - "to dr. <Name>"

        Args:
            message: User message containing doctor name

        Returns:
            Optional[str]: Extracted doctor name, or None if not found

        Example:
            >>> name = manager.extract_doctor_name("Запись к доктору Иванову")
            >>> print(name)  # "Иванову"
            >>> name = manager.extract_doctor_name("Запись к доктору")
            >>> print(name)  # None (no actual name after "доктору")
        """
        # Pattern: "доктор <Name>" or "к доктору <Name>"
        # Captures Russian names (Cyrillic with capitalization)
        patterns = [
            r'(?:к\s+)?доктору?\s+([А-ЯЁ][а-яё]+(?:\s+[А-ЯЁ][а-яё]+)?)',  # Russian
            r'(?:to\s+)?dr\.?\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)',  # English
        ]

        for pattern in patterns:
            match = re.search(pattern, message, re.IGNORECASE)
            if match:
                doctor_name = match.group(1).strip()
                logger.info(f"Extracted doctor name: {doctor_name}")
                return doctor_name

        logger.debug(f"No doctor name found in message: {message}")
        return None

    async def validate_doctor_name(
        self,
        doctor_name: str,
        clinic_id: str
    ) -> Tuple[bool, Optional[str], Optional[str]]:
        """
        Validate doctor name against clinic's doctor list.

        Performs fuzzy matching (case-insensitive, partial match) to handle
        variations in how users type doctor names.

        Args:
            doctor_name: Doctor name extracted from user message
            clinic_id: Clinic identifier for doctor lookup

        Returns:
            Tuple of (is_valid, error_message, doctor_id).
            If valid: (True, None, doctor_uuid)
            If invalid: (False, error_message_with_suggestions, None)

        Example:
            >>> is_valid, error, doctor_id = await manager.validate_doctor_name("Иванов", "clinic_123")
            >>> print(is_valid, error, doctor_id)
            True, None, "550e8400-e29b-41d4-a716-446655440000"
            >>> is_valid, error, doctor_id = await manager.validate_doctor_name("Неизвестный", "clinic_123")
            >>> print(is_valid, error, doctor_id)
            False, "Доктор 'Неизвестный' не найден. Доступны: Иванов, Петров, Сидоров", None
        """
        start_time = time.time()

        try:
            # Fetch doctors from database with both name and id
            result = self.supabase.table('doctors').select('id, name').eq('clinic_id', clinic_id).execute()

            if not result.data:
                duration_seconds = time.time() - start_time
                error_msg = "Не могу найти список докторов для этой клиники."

                record_bad_booking("no_doctors_configured", clinic_id)
                record_slot_validation("doctor_name", False, clinic_id, duration_seconds)
                log_slot_validation(
                    conversation_id="unknown",
                    clinic_id=clinic_id,
                    state="collecting_slots",
                    slot_name="doctor_name",
                    slot_value=doctor_name,
                    is_valid=False,
                    error_message=error_msg,
                    duration_ms=duration_seconds * 1000
                )

                logger.warning(f"No doctors found for clinic {clinic_id}")
                return False, error_msg, None

            # Fuzzy match (case-insensitive, partial match)
            doctor_name_lower = doctor_name.lower()
            for doctor in result.data:
                db_name_lower = doctor['name'].lower()
                if doctor_name_lower in db_name_lower or db_name_lower in doctor_name_lower:
                    duration_seconds = time.time() - start_time

                    # Record successful validation
                    record_slot_validation("doctor_name", True, clinic_id, duration_seconds)
                    log_slot_validation(
                        conversation_id="unknown",
                        clinic_id=clinic_id,
                        state="collecting_slots",
                        slot_name="doctor_name",
                        slot_value=doctor_name,
                        is_valid=True,
                        duration_ms=duration_seconds * 1000
                    )

                    logger.info(f"Doctor {doctor_name} validated successfully for clinic {clinic_id}, id={doctor['id']}")
                    return True, None, doctor['id']  # Return doctor UUID

            # Not found - return list of available doctors (up to 5)
            duration_seconds = time.time() - start_time
            available = ", ".join([d['name'] for d in result.data[:5]])
            error_msg = f"Доктор '{doctor_name}' не найден. Доступны: {available}"

            record_bad_booking("invalid_doctor", clinic_id)
            record_slot_validation("doctor_name", False, clinic_id, duration_seconds)
            log_slot_validation(
                conversation_id="unknown",
                clinic_id=clinic_id,
                state="collecting_slots",
                slot_name="doctor_name",
                slot_value=doctor_name,
                is_valid=False,
                error_message=error_msg,
                duration_ms=duration_seconds * 1000
            )

            logger.warning(f"Doctor {doctor_name} not found for clinic {clinic_id}")
            return False, error_msg, None

        except Exception as e:
            duration_seconds = time.time() - start_time
            error_msg = "Ошибка при проверке имени доктора. Попробуйте позже."

            record_slot_validation("doctor_name", False, clinic_id, duration_seconds)
            log_slot_validation(
                conversation_id="unknown",
                clinic_id=clinic_id,
                state="collecting_slots",
                slot_name="doctor_name",
                slot_value=doctor_name,
                is_valid=False,
                error_message=error_msg,
                duration_ms=duration_seconds * 1000
            )

            logger.error(f"Error validating doctor name for clinic {clinic_id}: {e}", exc_info=True)
            return False, error_msg, None

    def add_slot(
        self,
        state: FSMState,
        slot_name: str,
        value: Any,
        source: SlotSource,
        confidence: float = 1.0,
        confirmed: bool = False
    ) -> FSMState:
        """
        Add slot with evidence to FSM state.

        Creates a new state object with the slot added (does not mutate original state).
        Uses deep copy to prevent unintended state mutations.

        Args:
            state: Current FSM state
            slot_name: Name of the slot (e.g., "appointment_date", "doctor_name")
            value: Slot value (can be any JSON-serializable type)
            source: How the value was obtained (LLM_EXTRACT, USER_CONFIRM, DB_LOOKUP)
            confidence: LLM confidence score (0.0 to 1.0)
            confirmed: Whether user explicitly confirmed this value

        Returns:
            FSMState: Updated state with new slot (NOT saved to database)

        Example:
            >>> new_state = manager.add_slot(
            ...     state,
            ...     "appointment_date",
            ...     "2025-10-20",
            ...     SlotSource.LLM_EXTRACT,
            ...     confidence=0.95,
            ...     confirmed=False
            ... )
            >>> print(new_state.slots["appointment_date"].value)
            "2025-10-20"
        """
        updated_state = state.model_copy(deep=True)
        updated_state.slots[slot_name] = SlotEvidence(
            value=value,
            source=source,
            confidence=confidence,
            extracted_at=datetime.now(timezone.utc),
            confirmed=confirmed
        )
        updated_state.updated_at = datetime.now(timezone.utc)
        logger.info(f"Added slot {slot_name} with value {value} (source: {source}, confirmed: {confirmed})")
        return updated_state

    def confirm_slot(
        self,
        state: FSMState,
        slot_name: str
    ) -> FSMState:
        """
        Mark slot as confirmed by user.

        Updates the slot's confirmation status without changing its value.
        Creates a new state object (does not mutate original state).

        Args:
            state: Current FSM state
            slot_name: Name of the slot to confirm

        Returns:
            FSMState: Updated state with confirmed slot (NOT saved to database)

        Example:
            >>> new_state = manager.confirm_slot(state, "appointment_date")
            >>> print(new_state.slots["appointment_date"].confirmed)
            True
        """
        updated_state = state.model_copy(deep=True)
        if slot_name in updated_state.slots:
            updated_state.slots[slot_name].confirmed = True
            updated_state.updated_at = datetime.now(timezone.utc)
            logger.info(f"Confirmed slot {slot_name}")
        else:
            logger.warning(f"Attempted to confirm non-existent slot {slot_name}")
        return updated_state

    def check_slots_stale(
        self,
        state: FSMState,
        max_age_seconds: int = 300
    ) -> List[str]:
        """
        Check which slots are stale (older than max_age).

        Identifies slots that are older than the specified age threshold.
        These slots should be re-confirmed with the user before proceeding
        with booking to ensure data freshness.

        Args:
            state: Current FSM state
            max_age_seconds: Maximum age in seconds before slot is considered stale
                           (default: 300 seconds / 5 minutes)

        Returns:
            List[str]: List of stale slot names

        Example:
            >>> stale = manager.check_slots_stale(state, max_age_seconds=300)
            >>> print(stale)  # ["appointment_date", "doctor_name"]
            >>> if stale:
            ...     print(f"Please re-confirm: {', '.join(stale)}")
        """
        stale_slots = []
        for slot_name, slot_evidence in state.slots.items():
            if slot_evidence.is_stale(max_age_seconds):
                stale_slots.append(slot_name)

                # Calculate age for metrics
                age_seconds = (datetime.now(timezone.utc) - slot_evidence.extracted_at).total_seconds()

                # Record context contamination
                record_context_contamination(slot_name, state.clinic_id)
                log_context_contamination(
                    conversation_id=state.conversation_id,
                    clinic_id=state.clinic_id,
                    state=state.current_state.value,
                    slot_name=slot_name,
                    age_seconds=age_seconds
                )

                logger.info(f"Slot {slot_name} is stale (age > {max_age_seconds}s)")

        if stale_slots:
            logger.warning(f"Found {len(stale_slots)} stale slots: {stale_slots}")
        return stale_slots

    def has_required_slots(
        self,
        state: FSMState,
        required: List[str]
    ) -> bool:
        """
        Check if all required slots are present and confirmed.

        Verifies that all required slots exist in the state and have been
        explicitly confirmed by the user. Unconfirmed slots are considered
        missing even if they have a value.

        Args:
            state: Current FSM state
            required: List of required slot names

        Returns:
            bool: True if all required slots are present and confirmed, False otherwise

        Example:
            >>> required = ["appointment_date", "doctor_name", "patient_name"]
            >>> has_all = manager.has_required_slots(state, required)
            >>> if not has_all:
            ...     print("Missing required slots, cannot proceed with booking")
        """
        for slot_name in required:
            if slot_name not in state.slots:
                logger.debug(f"Required slot {slot_name} is missing")
                return False
            if not state.slots[slot_name].confirmed:
                logger.debug(f"Required slot {slot_name} is not confirmed")
                return False

        logger.info(f"All required slots present and confirmed: {required}")
        return True
