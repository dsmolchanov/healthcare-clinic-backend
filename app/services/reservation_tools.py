"""
Reservation Management Tools for LangGraph Agents

This module provides reservation tools for healthcare appointment management,
integrating with existing calendar systems and appointment services.
"""

import logging
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import re
import asyncio
import json
from enum import Enum

# Phase C: Removed deprecated AppointmentBookingService import
# from app.services.appointment_booking_service import AppointmentBookingService
from app.services.unified_appointment_service import (
    UnifiedAppointmentService,
    AppointmentRequest,
    AppointmentType
)
from app.services.external_calendar_service import ExternalCalendarService
from app.services.intelligent_scheduler import IntelligentScheduler, SchedulingStrategy
from app.services.realtime_conflict_detector import RealtimeConflictDetector
from app.services.redis_session_manager import RedisSessionManager
from app.services.clinic_data_cache import ClinicDataCache
from app.database import create_supabase_client
from app.config import get_redis_client

logger = logging.getLogger(__name__)

MORNING_CUTOFF_HOUR = 12
WEEKDAY_TOKENS = {
    0: ["monday", "понедельник", "понед", "ponedel"],
    1: ["tuesday", "вторник", "втор", "vtorn"],
    2: ["wednesday", "среда", "сред", "sred"],
    3: ["thursday", "четверг", "четвер", "chetver"],
    4: ["friday", "пятница", "пятниц", "pyatn"],
    5: ["saturday", "суббота", "суббот", "subbot"],
    6: ["sunday", "воскресенье", "воскрес", "voskres"]
}
NEXT_WEEK_TOKENS = ["next week", "следующей неделе", "следующую неделю", "proxima semana", "próxima semana"]
NEXT_TOKENS = ["next", "следующ", "proximo", "próximo"]

class ReservationStatus(Enum):
    """Status of a reservation"""
    AVAILABLE = "available"
    HELD = "held"
    BOOKED = "booked"
    CANCELLED = "cancelled"
    EXPIRED = "expired"

class ReservationTools:
    """
    Reservation management tools for LangGraph agents.
    Provides methods for checking availability, booking appointments,
    managing holds, and handling cancellations.
    """

    def __init__(
        self,
        clinic_id: str,
        patient_id: Optional[str] = None,
        business_hours: Optional[Dict] = None,
        clinic_timezone: Optional[str] = None,
        redis_client=None
    ):
        """
        Initialize reservation tools with clinic context.

        Args:
            clinic_id: ID of the clinic
            patient_id: Optional patient ID for personalized operations
            business_hours: Pre-loaded business hours from clinic warmup (avoids DB fetch)
            clinic_timezone: Pre-loaded timezone from clinic warmup (avoids DB fetch)
            redis_client: Redis client for cache access (if None, uses get_redis_client())
        """
        self.clinic_id = clinic_id
        self.patient_id = patient_id
        self.business_hours = business_hours or {}
        self.clinic_timezone = clinic_timezone
        self.supabase = create_supabase_client()

        # Initialize cache for doctor/service lookups
        self.redis_client = redis_client or get_redis_client()
        self.cache = ClinicDataCache(self.redis_client, default_ttl=3600) if self.redis_client else None

        # Initialize services with pre-loaded business hours and timezone
        # Phase C: Removed deprecated AppointmentBookingService
        # self.booking_service = AppointmentBookingService(supabase_client=self.supabase)
        self.unified_service = UnifiedAppointmentService(
            supabase=self.supabase,
            clinic_id=clinic_id,
            business_hours=business_hours,
            clinic_timezone=clinic_timezone
        )
        self.calendar_service = ExternalCalendarService(supabase=self.supabase)
        self.scheduler = IntelligentScheduler(
            supabase=self.supabase,
            clinic_id=clinic_id,
            business_hours=business_hours,
            clinic_timezone=clinic_timezone
        )
        self.conflict_detector = RealtimeConflictDetector()
        self.session_manager = RedisSessionManager()

        logger.info(f"Initialized ReservationTools for clinic {clinic_id} (tz={clinic_timezone})")

    async def _get_cached_doctors(self) -> List[Dict[str, Any]]:
        """Get doctors from cache, fallback to database."""
        if self.cache:
            try:
                doctors = await self.cache.get_doctors(self.clinic_id, self.supabase)
                if doctors:
                    logger.debug(f"✅ Cache HIT: {len(doctors)} doctors for clinic {self.clinic_id}")
                    return doctors
            except Exception as e:
                logger.warning(f"Cache error for doctors: {e}, falling back to DB")

        # Fallback to direct DB query
        try:
            result = self.supabase.schema('healthcare').table('doctors').select(
                'id, first_name, last_name, specialization, phone, email'
            ).eq('clinic_id', self.clinic_id).eq('active', True).execute()
            return result.data or []
        except Exception as e:
            logger.error(f"Failed to fetch doctors: {e}")
            return []

    async def _get_cached_services(self) -> List[Dict[str, Any]]:
        """Get services from cache, fallback to database."""
        if self.cache:
            try:
                services = await self.cache.get_services(self.clinic_id, self.supabase)
                if services:
                    logger.debug(f"✅ Cache HIT: {len(services)} services for clinic {self.clinic_id}")
                    return services
            except Exception as e:
                logger.warning(f"Cache error for services: {e}, falling back to DB")

        # Fallback to direct DB query
        try:
            result = self.supabase.schema('healthcare').table('services').select('*').eq(
                'clinic_id', self.clinic_id
            ).eq('active', True).execute()
            return result.data or []
        except Exception as e:
            logger.error(f"Failed to fetch services: {e}")
            return []

    def _find_doctor_by_name(self, doctors: List[Dict], doctor_name: str) -> Optional[Dict]:
        """Find a doctor by name with transliteration support."""
        doctor_name_lower = doctor_name.lower().strip()

        # Transliterate Cyrillic to Latin for matching
        cyrillic_to_latin = {
            'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd', 'е': 'e', 'ё': 'e',
            'ж': 'zh', 'з': 'z', 'и': 'i', 'й': 'y', 'к': 'k', 'л': 'l', 'м': 'm',
            'н': 'n', 'о': 'o', 'п': 'p', 'р': 'r', 'с': 's', 'т': 't', 'у': 'u',
            'ф': 'f', 'х': 'h', 'ц': 'ts', 'ч': 'ch', 'ш': 'sh', 'щ': 'sch',
            'ъ': '', 'ы': 'y', 'ь': '', 'э': 'e', 'ю': 'yu', 'я': 'ya'
        }
        doctor_name_transliterated = ''.join(
            cyrillic_to_latin.get(c, c) for c in doctor_name_lower
        )

        for doc in doctors:
            first_name = (doc.get('first_name') or '').lower()
            last_name = (doc.get('last_name') or '').lower()
            full_name = f"{first_name} {last_name}"

            # Check various matching patterns (both original and transliterated)
            if (doctor_name_transliterated in first_name or
                doctor_name_transliterated in last_name or
                doctor_name_transliterated in full_name or
                first_name in doctor_name_transliterated or
                last_name in doctor_name_transliterated or
                doctor_name_lower in first_name or
                doctor_name_lower in last_name):
                return doc
        return None

    def _find_service_by_name(self, services: List[Dict], service_name: str) -> Optional[Dict]:
        """Find a service by name with fuzzy matching."""
        service_name_lower = service_name.lower().strip()

        for service in services:
            name = (service.get('name') or '').lower()
            name_ru = (service.get('name_ru') or '').lower()
            name_en = (service.get('name_en') or '').lower()

            if (service_name_lower in name or
                service_name_lower in name_ru or
                service_name_lower in name_en or
                name in service_name_lower):
                return service
        return None

    def _get_clinic_tzinfo(self) -> ZoneInfo:
        """Return clinic timezone info, falling back to UTC."""
        tz_name = self.clinic_timezone or 'UTC'
        try:
            return ZoneInfo(tz_name)
        except Exception:
            logger.warning(f"Invalid timezone '{tz_name}', falling back to UTC")
            return ZoneInfo('UTC')

    def _extract_weekday(self, date_str: str) -> Optional[int]:
        """Detect weekday mention in a date phrase."""
        lowered = date_str.lower()
        for weekday, tokens in WEEKDAY_TOKENS.items():
            for token in tokens:
                if token in lowered:
                    return weekday
        return None

    def _has_next_marker(self, date_str: str) -> bool:
        """Detect 'next' markers in multiple languages."""
        lowered = date_str.lower()
        return any(token in lowered for token in NEXT_TOKENS)

    def _resolve_weekday_target(
        self,
        target_weekday: int,
        now_local: datetime,
        force_next_week: bool
    ) -> datetime.date:
        """Resolve weekday to a concrete date with cutoff handling."""
        current_weekday = now_local.weekday()
        days_ahead = (target_weekday - current_weekday) % 7

        if force_next_week:
            if days_ahead == 0:
                days_ahead = 7
            elif target_weekday > current_weekday:
                days_ahead += 7
        elif days_ahead == 0 and now_local.hour >= MORNING_CUTOFF_HOUR:
            days_ahead = 7

        return now_local.date() + timedelta(days=days_ahead)

    def _parse_natural_date(
        self,
        date_str: str,
        now_local: Optional[datetime] = None
    ) -> Optional[datetime]:
        """
        Parse natural language date to datetime object in clinic timezone.

        Supports:
        - ISO format: "2026-01-07"
        - English/Russian/Spanish weekdays
        - Relative phrases like "tomorrow", "next week"
        """
        if not date_str:
            return None

        date_lower = date_str.strip().lower()
        now_local = now_local or datetime.now(self._get_clinic_tzinfo())
        today = now_local.date()

        # ISO format fast path
        try:
            parsed_date = datetime.strptime(date_lower.split('T')[0], "%Y-%m-%d").date()
            return datetime.combine(parsed_date, datetime.min.time(), tzinfo=now_local.tzinfo)
        except ValueError:
            pass

        iso_match = re.search(r"\d{4}-\d{2}-\d{2}", date_lower)
        if iso_match:
            try:
                parsed_date = datetime.strptime(iso_match.group(0), "%Y-%m-%d").date()
                return datetime.combine(parsed_date, datetime.min.time(), tzinfo=now_local.tzinfo)
            except ValueError:
                pass

        if any(token in date_lower for token in ['today', 'сегодня', 'hoy', 'hoje']):
            return now_local
        if any(token in date_lower for token in ['tomorrow', 'завтра', 'mañana', 'amanhã']):
            return now_local + timedelta(days=1)

        matched_weekday = self._extract_weekday(date_lower)
        if matched_weekday is not None:
            force_next_week = self._has_next_marker(date_lower)
            target_date = self._resolve_weekday_target(matched_weekday, now_local, force_next_week)
            return datetime.combine(target_date, now_local.time(), tzinfo=now_local.tzinfo)

        if any(token in date_lower for token in NEXT_WEEK_TOKENS):
            return now_local + timedelta(days=7)

        # Fallback to dateparser if installed
        try:
            import dateparser
            parsed = dateparser.parse(
                date_str,
                settings={
                    'PREFER_DATES_FROM': 'future',
                    'LANGUAGES': ['en', 'ru', 'es'],
                    'RELATIVE_BASE': now_local,
                    'TIMEZONE': self.clinic_timezone or 'UTC',
                    'RETURN_AS_TIMEZONE_AWARE': True
                }
            )
            if parsed:
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=now_local.tzinfo)
                logger.info(f"Parsed natural date '{date_str}' -> {parsed.strftime('%Y-%m-%d')}")
                return parsed
        except ImportError:
            logger.warning("dateparser not installed, falling back to manual parsing")
        except Exception as e:
            logger.warning(f"dateparser failed for '{date_str}': {e}")

        logger.warning(f"Could not parse date: '{date_str}'")
        return None

    def _build_clarification_response(
        self,
        message: str,
        preferred_date: Optional[str],
        user_date_phrase: Optional[str],
        computed_date: Optional[datetime.date],
        timezone_name: str
    ) -> Dict[str, Any]:
        """Return a stable envelope requiring clarification."""
        resolved_date = computed_date or datetime.now(self._get_clinic_tzinfo()).date()
        return {
            "success": False,
            "status": "needs_clarification",
            "requires_clarification": True,
            "message": message,
            "preferred_date": preferred_date,
            "user_date_phrase": user_date_phrase,
            "computed_date": resolved_date.isoformat(),
            "date": resolved_date.isoformat(),
            "timezone": timezone_name,
            "weekday_local": resolved_date.strftime("%A"),
            "display_date_local": resolved_date.strftime("%B %d, %Y"),
            "available_slots": []
        }

    async def check_availability_tool(
        self,
        service_name: str,
        preferred_date: Optional[str] = None,
        user_date_phrase: Optional[str] = None,
        time_preference: Optional[str] = None,
        doctor_id: Optional[str] = None,
        flexibility_days: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Check availability for a service with intelligent slot finding.

        Args:
            service_name: Name or type of service
            preferred_date: Preferred date (YYYY-MM-DD format)
            user_date_phrase: Original user date phrase for guardrails
            time_preference: Time preference (morning/afternoon/evening)
            doctor_id: Specific doctor ID if requested
            flexibility_days: Number of days to search for availability
                              If not specified, auto-determined based on user specificity:
                              - Specific date + time: 1 day
                              - Specific date only: 2 days
                              - No date preference: 7 days

        Returns:
            Dictionary with available slots and recommendations
        """
        # Smart default for flexibility_days based on user specificity
        if flexibility_days is None:
            if preferred_date and time_preference:
                # User specified both date AND time - very specific, search just that day
                flexibility_days = 1
                logger.info(f"User specified date+time, using flexibility_days=1")
            elif preferred_date:
                # User specified date but not time - search 2 days for flexibility
                flexibility_days = 2
                logger.info(f"User specified date only, using flexibility_days=2")
            else:
                # No specific date - use wider search
                flexibility_days = 7
                logger.info(f"No date preference, using flexibility_days=7")

        tzinfo = self._get_clinic_tzinfo()
        now_local = datetime.now(tzinfo)
        timezone_name = getattr(tzinfo, "key", None) or self.clinic_timezone or 'UTC'
        user_date_phrase = user_date_phrase or preferred_date
        # P0 GUARD: Fail fast if doctor_id is None or "None" string
        if doctor_id is not None and (doctor_id == "None" or str(doctor_id).strip() == ""):
            logger.warning(
                f"⚠️ check_availability called with invalid doctor_id: {doctor_id}. "
                "This likely indicates an intent routing error."
            )
            return {
                "success": False,
                "error": "missing_doctor_context",
                "message": "I need to know which doctor you'd like to see first. Could you specify the service or doctor?",
                "requires_clarification": True,
                "suggested_action": "ask_which_doctor",
                "available_slots": []
            }

        # Check if doctor_id is a UUID or a doctor name
        if doctor_id is not None:
            try:
                import uuid as uuid_module
                uuid_module.UUID(doctor_id)
                # Valid UUID, use as-is
            except (ValueError, AttributeError):
                # Not a UUID - try to look up doctor by name using cache
                logger.info(f"doctor_id '{doctor_id}' is not a UUID, looking up by name")
                doctors = await self._get_cached_doctors()
                matched_doctor = self._find_doctor_by_name(doctors, doctor_id)

                if matched_doctor:
                    doctor_id = matched_doctor['id']
                    logger.info(f"Resolved doctor name to UUID: {matched_doctor.get('first_name')} {matched_doctor.get('last_name')} → {doctor_id}")
                else:
                    logger.warning(f"Could not find doctor matching '{doctor_id}'")
                    return {
                        "success": False,
                        "error": "doctor_not_found",
                        "message": f"I couldn't find a doctor named '{doctor_id}'. Please check the name or ask me to show available doctors.",
                        "requires_clarification": True,
                        "available_slots": []
                    }

        try:
            # Parse preferred date - FIX: Support natural language dates
            if preferred_date:
                matched_weekday = self._extract_weekday(preferred_date)
                start_date = self._parse_natural_date(preferred_date, now_local=now_local)
                if start_date is None:
                    logger.warning(f"Could not parse date '{preferred_date}'")
                    return self._build_clarification_response(
                        message="I couldn't understand the date. Which day should I check?",
                        preferred_date=preferred_date,
                        user_date_phrase=user_date_phrase,
                        computed_date=None,
                        timezone_name=timezone_name
                    )
                if matched_weekday is not None and start_date.weekday() != matched_weekday:
                    return self._build_clarification_response(
                        message="Just to confirm, which day do you mean?",
                        preferred_date=preferred_date,
                        user_date_phrase=user_date_phrase,
                        computed_date=start_date.date(),
                        timezone_name=timezone_name
                    )
            else:
                start_date = now_local

            end_date = start_date + timedelta(days=flexibility_days)

            # Get service details
            service = await self._get_service_by_name(service_name)
            if not service or not isinstance(service, dict):
                return {
                    "success": False,
                    "error": f"Service '{service_name}' not found",
                    "available_slots": []
                }

            # Check if multi-stage service (with defensive null checks)
            stage_config = service.get('stage_config') if service else {}
            stage_config = stage_config if isinstance(stage_config, dict) else {}
            is_multi_stage = stage_config.get('total_stages', 1) > 1

            # Handle "no doctor preference" case with workload-based selection
            eligible_doctors = None
            recommended_doctor = None

            if doctor_id is None:
                logger.info(f"No doctor preference specified - finding eligible doctors for service {service.get('id')}")

                # Find all eligible doctors for this service
                eligible_doctors = await self._get_eligible_doctors_for_service(
                    clinic_id=self.clinic_id,
                    service_id=service.get('id')
                )

                if not eligible_doctors:
                    return {
                        "success": False,
                        "error": "no_eligible_doctors",
                        "message": f"No doctors are currently available for {service_name}. Please contact the clinic.",
                        "available_slots": []
                    }

                # Calculate workloads for each eligible doctor
                eligible_doctors = await self._calculate_doctor_workloads(
                    doctors=eligible_doctors,
                    target_date=start_date
                )

                # Sort by workload (least-busy first, i.e., highest workload_score)
                eligible_doctors = sorted(
                    eligible_doctors,
                    key=lambda d: d.get('workload_score', 0),
                    reverse=True
                )

                # Recommended doctor is the least-busy one
                recommended_doctor = eligible_doctors[0] if eligible_doctors else None

                logger.info(
                    f"Found {len(eligible_doctors)} eligible doctors. "
                    f"Recommended: {recommended_doctor.get('name') if recommended_doctor else 'None'} "
                    f"(workload: {recommended_doctor.get('workload_label', 'unknown') if recommended_doctor else 'N/A'})"
                )

                # Aggregate slots from top 3 eligible doctors (sorted by least-busy)
                # This limits calendar queries from N*8days to 3*8days for faster response
                slots = []
                strategy = SchedulingStrategy.AI_OPTIMIZED
                top_doctors = eligible_doctors[:3]  # Already sorted by workload_score DESC

                logger.info(f"Checking availability for top {len(top_doctors)} doctors (of {len(eligible_doctors)} eligible)")

                # PARALLEL: Fire all doctor slot queries concurrently
                async def get_doctor_slots(doctor):
                    """Fetch slots for a single doctor and annotate with doctor info."""
                    try:
                        doctor_slots = await self.scheduler.find_available_slots(
                            service_id=service.get('id') if service else None,
                            start_date=start_date,
                            end_date=end_date,
                            doctor_id=doctor['id'],
                            duration_minutes=service.get('duration_minutes', 30) if service else 30,
                            strategy=strategy
                        )

                        # Annotate slots with doctor info
                        annotated_slots = []
                        for slot in (doctor_slots or []):
                            if isinstance(slot, dict):
                                slot['doctor_id'] = doctor['id']
                                slot['doctor_name'] = doctor['name']
                                slot['doctor_specialization'] = doctor.get('specialization', '')
                                slot['workload_score'] = doctor.get('workload_score', 0.5)
                                slot['workload_label'] = doctor.get('workload_label', 'unknown')
                                annotated_slots.append(slot)
                        return annotated_slots
                    except Exception as e:
                        logger.error(f"Error getting slots for doctor {doctor['id']}: {e}")
                        return []

                # Run all doctor queries in parallel
                slot_results = await asyncio.gather(
                    *[get_doctor_slots(doctor) for doctor in top_doctors],
                    return_exceptions=True
                )

                # Flatten results
                for result in slot_results:
                    if isinstance(result, Exception):
                        logger.error(f"Parallel slot query failed: {result}")
                        continue
                    if result:
                        slots.extend(result)

                # Sort slots by workload_score (higher = less busy = better) then by datetime
                slots.sort(
                    key=lambda s: (-s.get('workload_score', 0), s.get('datetime', ''))
                )

            else:
                # Existing path: specific doctor requested
                strategy = SchedulingStrategy.AI_OPTIMIZED
                slots = await self.scheduler.find_available_slots(
                    service_id=service.get('id') if service else None,
                    start_date=start_date,
                    end_date=end_date,
                    doctor_id=doctor_id,
                    duration_minutes=service.get('duration_minutes', 30) if service else 30,
                    strategy=strategy
                )

            # Ensure slots is a list (defensive check)
            if not slots or not isinstance(slots, list):
                slots = []

            # Filter by time preference if provided
            if time_preference and slots:
                slots = self._filter_by_time_preference(slots, time_preference)

            # Check conflicts with external calendars
            verified_slots = []
            for slot in slots[:10]:  # Limit to top 10 slots
                # Defensive check: ensure slot is a dictionary
                if not slot or not isinstance(slot, dict):
                    continue

                # Use ask-hold-reserve pattern to verify availability
                try:
                    is_available = await self.calendar_service.ask_availability(
                        datetime_str=slot.get('datetime') if slot else None,
                        duration_minutes=service.get('duration_minutes', 30) if service else 30,
                        doctor_id=slot.get('doctor_id') if slot else None
                    )
                except Exception as calendar_error:
                    logger.warning(f"Calendar availability check failed: {calendar_error}")
                    # Continue without calendar verification
                    is_available = True

                if is_available:
                    slot['verified'] = True
                    slot['conflicts'] = []
                    verified_slots.append(slot)

            # Format response
            response = {
                "success": True,
                "status": "ok",
                "service": {
                    "id": service['id'],
                    "name": service['name'],
                    "duration_minutes": service.get('duration_minutes', 30),
                    "base_price": service.get('base_price'),
                    "is_multi_stage": is_multi_stage,
                    "stage_config": stage_config if is_multi_stage else None
                },
                "available_slots": verified_slots,
                "total_slots_found": len(slots),
                "date": start_date.date().isoformat(),
                "timezone": timezone_name,
                "weekday_local": start_date.strftime("%A"),
                "display_date_local": start_date.strftime("%B %d, %Y"),
                "preferred_date": preferred_date,
                "user_date_phrase": user_date_phrase,
                "search_parameters": {
                    "start_date": start_date.isoformat(),
                    "end_date": end_date.isoformat(),
                    "time_preference": time_preference,
                    "doctor_id": doctor_id
                },
                "recommendation": verified_slots[0] if verified_slots else None
            }

            # Add doctor selection info when no doctor preference was specified
            if eligible_doctors is not None:
                response["available_doctors"] = [
                    {
                        "id": d['id'],
                        "name": d['name'],
                        "specialization": d.get('specialization', ''),
                        "workload_score": d.get('workload_score', 0.5),
                        "workload_label": d.get('workload_label', 'unknown'),
                        "appointment_count": d.get('appointment_count', 0)
                    }
                    for d in eligible_doctors
                ]
                response["recommended_doctor"] = {
                    "id": recommended_doctor['id'],
                    "name": recommended_doctor['name'],
                    "specialization": recommended_doctor.get('specialization', ''),
                    "workload_label": recommended_doctor.get('workload_label', 'unknown'),
                    "reason": "least_busy"
                } if recommended_doctor else None
                response["doctor_selection_mode"] = "workload_balanced"

            return response

        except Exception as e:
            logger.error(f"Error checking availability: {str(e)}")
            return {
                "success": False,
                "error": f"Failed to check availability: {str(e)}",
                "available_slots": []
            }

    async def book_appointment_tool(
        self,
        patient_info: Dict[str, Any],
        service_id: str,
        datetime_str: str,
        doctor_id: Optional[str] = None,
        notes: Optional[str] = None,
        hold_id: Optional[str] = None,
        idempotency_key: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Book an appointment with automatic hold management.

        Args:
            patient_info: Patient information (name, phone, email)
            service_id: ID of the service (UUID) or service name (will be looked up)
            datetime_str: Appointment datetime in ISO format
            doctor_id: Optional doctor ID
            notes: Optional notes for the appointment
            hold_id: Optional hold ID if slot was previously held
            idempotency_key: Optional key for idempotent booking

        Returns:
            Dictionary with booking confirmation details
        """
        try:
            # Check idempotency if key provided
            if idempotency_key:
                idem_check = await self._check_idempotency(idempotency_key)
                if idem_check['already_processed']:
                    logger.info(f"⚡ Idempotency: Request already processed for key {idempotency_key}")
                    return idem_check['response_payload']

                # Record idempotency attempt
                await self._record_idempotency_attempt(idempotency_key)

            # Parse datetime
            appointment_datetime = datetime.fromisoformat(datetime_str)

            # Check if service_id is a UUID or a service name
            try:
                import uuid as uuid_module
                uuid_module.UUID(service_id)
                is_uuid = True
            except (ValueError, AttributeError):
                is_uuid = False
                logger.info(f"service_id '{service_id}' is not a UUID, looking up by name")

            # Get service details from cache
            services = await self._get_cached_services()
            service = None

            if is_uuid:
                # Find by ID in cached services
                service = next((s for s in services if s.get('id') == service_id), None)
            else:
                # Lookup by name using helper
                service = self._find_service_by_name(services, service_id)
                if service:
                    service_id = service['id']
                    logger.info(f"Resolved service name to UUID: {service_id}")

            if not service:
                return {
                    "success": False,
                    "error": "Service not found"
                }

            # Check if multi-stage service
            stage_config = service.get('stage_config', {})
            is_multi_stage = stage_config.get('total_stages', 1) > 1

            # If no hold exists, create one first
            if not hold_id:
                hold_result = await self.create_appointment_hold_tool(
                    slot_datetime=datetime_str,
                    duration_minutes=service['duration_minutes'],
                    service_id=service_id,
                    doctor_id=doctor_id
                )

                if not hold_result['success']:
                    return {
                        "success": False,
                        "error": "Failed to secure appointment slot",
                        "details": hold_result.get('error')
                    }

                hold_id = hold_result['hold_id']

            # Prepare appointment data
            appointment_data = {
                "clinic_id": self.clinic_id,
                "patient_name": patient_info.get('name'),
                "patient_phone": patient_info.get('phone'),
                "patient_email": patient_info.get('email'),
                "patient_id": self.patient_id or patient_info.get('patient_id'),
                "service_id": service_id,
                "service_name": service['name'],
                "doctor_id": doctor_id,
                "scheduled_at": appointment_datetime.isoformat(),
                "duration_minutes": service['duration_minutes'],
                "status": "scheduled",
                "notes": notes,
                "booking_channel": "langgraph_agent",
                "hold_id": hold_id,
                "idempotency_key": idempotency_key
            }

            # Handle multi-stage booking
            if is_multi_stage:
                appointments = await self._book_multi_stage_appointments(
                    appointment_data,
                    service,
                    stage_config
                )

                if not appointments:
                    # Release hold on failure
                    await self.release_appointment_hold_tool(hold_id, "Multi-stage booking failed")
                    failure_result = {
                        "success": False,
                        "error": "Failed to book all stages of the appointment"
                    }

                    # Record failure for idempotency
                    if idempotency_key:
                        await self._record_idempotency_failure(idempotency_key, failure_result.get('error', 'Multi-stage booking failed'))

                    return failure_result

                # Confirm the hold for all stages
                await self.confirm_appointment_hold_tool(hold_id, patient_info)

                multi_stage_result = {
                    "success": True,
                    "appointment_ids": [apt['id'] for apt in appointments],
                    "appointments": appointments,
                    "is_multi_stage": True,
                    "total_stages": stage_config['total_stages'],
                    "confirmation_message": self._format_multi_stage_confirmation(appointments, service)
                }

                # Record success for idempotency
                if idempotency_key:
                    await self._record_idempotency_success(idempotency_key, multi_stage_result)

                return multi_stage_result
            else:
                # Single appointment booking using unified service (Phase C)
                # Calculate end time from duration
                end_time = appointment_datetime + timedelta(minutes=service['duration_minutes'])

                # Create AppointmentRequest for unified service
                request = AppointmentRequest(
                    patient_id=appointment_data['patient_id'],
                    doctor_id=appointment_data['doctor_id'],
                    clinic_id=self.clinic_id,
                    start_time=appointment_datetime,
                    end_time=end_time,
                    appointment_type=AppointmentType.CONSULTATION,  # Map from service type if available
                    service_id=appointment_data['service_id'],
                    reason=appointment_data.get('notes'),
                    patient_phone=appointment_data.get('patient_phone'),
                    patient_email=appointment_data.get('patient_email')
                )

                # Use unified service book_appointment_v2 (Phase C implementation)
                result = await self.unified_service.book_appointment_v2(
                    request=request,
                    idempotency_key=appointment_data.get('idempotency_key'),
                    source_channel='whatsapp'  # ReservationTools is primarily used by WhatsApp
                )

                if result.success:
                    # Get appointment details for response
                    appt_result = self.supabase.schema('healthcare').table('appointments').select('*').eq(
                        'id', result.appointment_id
                    ).execute()

                    appointment_record = appt_result.data[0] if appt_result.data else {}

                    # Get clinic data for SOTA confirmation message (Phase 5)
                    clinic_result = self.supabase.schema('healthcare').table('clinics').select(
                        'id, name, address, city, state, location_data, entry_instructions_i18n'
                    ).eq('id', self.clinic_id).limit(1).execute()
                    clinic_record = clinic_result.data[0] if clinic_result.data else {}

                    booking_result = {
                        "success": True,
                        "appointment_id": result.appointment_id,
                        "appointment": appointment_record,
                        "clinic": clinic_record,  # Phase 5: Include clinic for SOTA confirmation
                        "confirmation_message": self._format_confirmation_message(appointment_record, service)
                    }

                    # Record success for idempotency (already handled by book_appointment_v2, but keeping for compatibility)
                    if idempotency_key:
                        await self._record_idempotency_success(idempotency_key, booking_result)

                    return booking_result
                else:
                    # Booking failed (hold already released by book_appointment_v2)
                    failure_result = {
                        "success": False,
                        "error": result.error or 'Failed to book appointment'
                    }

                    # Record failure for idempotency (already handled by book_appointment_v2, but keeping for compatibility)
                    if idempotency_key:
                        await self._record_idempotency_failure(idempotency_key, failure_result.get('error', 'Unknown error'))

                    return failure_result

        except Exception as e:
            logger.error(f"Error booking appointment: {str(e)}")
            if hold_id:
                await self.release_appointment_hold_tool(hold_id, f"Error: {str(e)}")

            # Record failure for idempotency
            if idempotency_key:
                await self._record_idempotency_failure(idempotency_key, str(e))

            return {
                "success": False,
                "error": f"Failed to book appointment: {str(e)}"
            }

    async def cancel_appointment_tool(
        self,
        appointment_id: str,
        cancellation_reason: str,
        cancel_all_stages: bool = False
    ) -> Dict[str, Any]:
        """
        Cancel an appointment with proper cleanup.

        Args:
            appointment_id: ID of the appointment to cancel
            cancellation_reason: Reason for cancellation
            cancel_all_stages: For multi-stage appointments, cancel all stages

        Returns:
            Dictionary with cancellation confirmation
        """
        try:
            # Get appointment details
            appointment = await self._get_appointment_by_id(appointment_id)
            if not appointment:
                return {
                    "success": False,
                    "error": "Appointment not found"
                }

            # Check if part of multi-stage appointment
            parent_id = appointment.get('parent_appointment_id')
            if parent_id or cancel_all_stages:
                # Get all related appointments
                appointments_to_cancel = await self._get_related_appointments(
                    parent_id or appointment_id
                )
            else:
                appointments_to_cancel = [appointment]

            cancelled_ids = []
            for apt in appointments_to_cancel:
                # Update appointment status
                update_result = self.supabase.table('healthcare.appointments').update({
                    'status': 'cancelled',
                    'cancellation_reason': cancellation_reason,
                    'cancelled_at': datetime.now().isoformat(),
                    'cancelled_by': 'langgraph_agent'
                }).eq('id', apt['id']).execute()

                if update_result.data:
                    cancelled_ids.append(apt['id'])

                    # Cancel in external calendars
                    await self.calendar_service.cancel_reservation(
                        appointment_id=apt['id'],
                        doctor_id=apt.get('doctor_id')
                    )

            return {
                "success": True,
                "cancelled_appointment_ids": cancelled_ids,
                "cancellation_reason": cancellation_reason,
                "message": f"Successfully cancelled {len(cancelled_ids)} appointment(s)"
            }

        except Exception as e:
            logger.error(f"Error cancelling appointment: {str(e)}")
            return {
                "success": False,
                "error": f"Failed to cancel appointment: {str(e)}"
            }

    async def reschedule_appointment_tool(
        self,
        appointment_id: str,
        new_datetime: str,
        reschedule_reason: Optional[str] = None,
        reschedule_all_stages: bool = False
    ) -> Dict[str, Any]:
        """
        Reschedule an appointment to a new time.

        Args:
            appointment_id: ID of the appointment to reschedule
            new_datetime: New datetime in ISO format
            reschedule_reason: Optional reason for rescheduling
            reschedule_all_stages: For multi-stage appointments, reschedule all stages

        Returns:
            Dictionary with rescheduling confirmation
        """
        try:
            # Get appointment details
            appointment = await self._get_appointment_by_id(appointment_id)
            if not appointment:
                return {
                    "success": False,
                    "error": "Appointment not found"
                }

            # Parse new datetime
            new_dt = datetime.fromisoformat(new_datetime)

            # Check availability for new slot
            availability = await self.check_availability_tool(
                service_name=appointment['service_name'],
                preferred_date=new_dt.date().isoformat(),
                doctor_id=appointment.get('doctor_id')
            )

            if not availability['success'] or not availability['available_slots']:
                return {
                    "success": False,
                    "error": "Requested time slot is not available"
                }

            # Check if the exact requested time is available
            slot_available = any(
                abs((datetime.fromisoformat(slot['datetime']) - new_dt).total_seconds()) < 900
                for slot in availability['available_slots']
            )

            if not slot_available:
                # Find nearest available slot
                nearest_slot = min(
                    availability['available_slots'],
                    key=lambda s: abs((datetime.fromisoformat(s['datetime']) - new_dt).total_seconds())
                )
                return {
                    "success": False,
                    "error": "Exact time not available",
                    "suggestion": nearest_slot,
                    "available_slots": availability['available_slots'][:5]
                }

            # Create hold for new slot
            hold_result = await self.create_appointment_hold_tool(
                slot_datetime=new_datetime,
                duration_minutes=appointment['duration_minutes'],
                service_id=appointment['service_id'],
                doctor_id=appointment.get('doctor_id')
            )

            if not hold_result['success']:
                return {
                    "success": False,
                    "error": "Failed to secure new appointment slot"
                }

            # Handle multi-stage rescheduling
            if reschedule_all_stages and appointment.get('parent_appointment_id'):
                appointments = await self._get_related_appointments(
                    appointment['parent_appointment_id'] or appointment_id
                )

                rescheduled_ids = []
                base_dt = new_dt

                for i, apt in enumerate(appointments):
                    if i > 0:
                        # Calculate new datetime for subsequent stages
                        days_between = apt.get('stage_config', {}).get('days_between_stages', 7)
                        stage_dt = base_dt + timedelta(days=days_between * i)
                    else:
                        stage_dt = base_dt

                    # Update appointment
                    update_result = self.supabase.table('healthcare.appointments').update({
                        'scheduled_at': stage_dt.isoformat(),
                        'status': 'rescheduled',
                        'previous_scheduled_at': apt['scheduled_at'],
                        'reschedule_reason': reschedule_reason,
                        'rescheduled_at': datetime.now().isoformat(),
                        'rescheduled_by': 'langgraph_agent'
                    }).eq('id', apt['id']).execute()

                    if update_result.data:
                        rescheduled_ids.append(apt['id'])

                # Confirm hold
                await self.confirm_appointment_hold_tool(
                    hold_result['hold_id'],
                    {"appointment_ids": rescheduled_ids}
                )

                return {
                    "success": True,
                    "rescheduled_appointment_ids": rescheduled_ids,
                    "new_datetime": new_datetime,
                    "message": f"Successfully rescheduled {len(rescheduled_ids)} appointment(s)"
                }
            else:
                # Single appointment rescheduling
                update_result = self.supabase.table('healthcare.appointments').update({
                    'scheduled_at': new_dt.isoformat(),
                    'status': 'rescheduled',
                    'previous_scheduled_at': appointment['scheduled_at'],
                    'reschedule_reason': reschedule_reason,
                    'rescheduled_at': datetime.now().isoformat(),
                    'rescheduled_by': 'langgraph_agent'
                }).eq('id', appointment_id).execute()

                if update_result.data:
                    # Confirm hold
                    await self.confirm_appointment_hold_tool(
                        hold_result['hold_id'],
                        {"appointment_id": appointment_id}
                    )

                    # Update external calendars
                    await self.calendar_service.reschedule_reservation(
                        appointment_id=appointment_id,
                        old_datetime=appointment['scheduled_at'],
                        new_datetime=new_datetime,
                        doctor_id=appointment.get('doctor_id')
                    )

                    return {
                        "success": True,
                        "appointment_id": appointment_id,
                        "old_datetime": appointment['scheduled_at'],
                        "new_datetime": new_datetime,
                        "message": "Appointment successfully rescheduled"
                    }
                else:
                    await self.release_appointment_hold_tool(
                        hold_result['hold_id'],
                        "Rescheduling failed"
                    )
                    return {
                        "success": False,
                        "error": "Failed to reschedule appointment"
                    }

        except Exception as e:
            logger.error(f"Error rescheduling appointment: {str(e)}")
            return {
                "success": False,
                "error": f"Failed to reschedule appointment: {str(e)}"
            }

    async def search_appointments_tool(
        self,
        patient_phone: Optional[str] = None,
        date_range: Optional[Dict[str, str]] = None,
        status: Optional[str] = None,
        doctor_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Search for appointments based on various criteria.

        Args:
            patient_phone: Patient phone number
            date_range: Dictionary with 'start_date' and 'end_date'
            status: Appointment status filter
            doctor_id: Doctor ID filter

        Returns:
            Dictionary with found appointments
        """
        try:
            query = self.supabase.table('healthcare.appointments').select('*')

            # Apply filters
            if patient_phone:
                query = query.eq('patient_phone', patient_phone)
            elif self.patient_id:
                query = query.eq('patient_id', self.patient_id)

            if self.clinic_id:
                query = query.eq('clinic_id', self.clinic_id)

            if status:
                query = query.eq('status', status)

            if doctor_id:
                query = query.eq('doctor_id', doctor_id)

            if date_range:
                if date_range.get('start_date'):
                    query = query.gte('scheduled_at', date_range['start_date'])
                if date_range.get('end_date'):
                    query = query.lte('scheduled_at', date_range['end_date'])

            # Execute query
            result = query.order('scheduled_at', desc=False).execute()

            appointments = result.data if result.data else []

            # Group multi-stage appointments
            grouped = self._group_multi_stage_appointments(appointments)

            return {
                "success": True,
                "appointments": grouped,
                "total_count": len(grouped),
                "search_criteria": {
                    "patient_phone": patient_phone,
                    "date_range": date_range,
                    "status": status,
                    "doctor_id": doctor_id
                }
            }

        except Exception as e:
            logger.error(f"Error searching appointments: {str(e)}")
            return {
                "success": False,
                "error": f"Failed to search appointments: {str(e)}",
                "appointments": []
            }

    # Hold Management Tools

    async def create_appointment_hold_tool(
        self,
        slot_datetime: str,
        duration_minutes: int,
        service_id: Optional[str] = None,
        doctor_id: Optional[str] = None,
        hold_duration_minutes: int = 15
    ) -> Dict[str, Any]:
        """
        Create a temporary hold on an appointment slot using unified resource_reservations.

        Args:
            slot_datetime: Datetime of the slot to hold
            duration_minutes: Duration of the appointment
            service_id: Optional service ID
            doctor_id: Optional doctor ID
            hold_duration_minutes: How long to hold the slot (default 15 minutes)

        Returns:
            Dictionary with hold details
        """
        try:
            slot_dt = datetime.fromisoformat(slot_datetime)
            end_dt = slot_dt + timedelta(minutes=duration_minutes)
            expire_at = datetime.utcnow() + timedelta(minutes=hold_duration_minutes)

            # Create hold in unified resource_reservations substrate with state='HOLD'
            hold_data = {
                "clinic_id": self.clinic_id,
                "patient_id": self.patient_id,
                "service_id": service_id,
                "reservation_date": slot_dt.date().isoformat(),
                "start_time": slot_dt.time().isoformat(),
                "end_time": end_dt.time().isoformat(),
                "state": "HOLD",
                "hold_expires_at": expire_at.isoformat(),
                "hold_created_for": f"whatsapp_session_{self.clinic_id}",
                "status": "pending",  # Original status field for compatibility
                "created_at": datetime.utcnow().isoformat()
            }

            result = self.supabase.table('healthcare.resource_reservations').insert(hold_data).execute()

            if result.data:
                hold = result.data[0]

                # If doctor specified, link via reservation_resources junction table
                if doctor_id:
                    await self._link_doctor_to_hold(hold['id'], doctor_id)

                # Also create hold in external calendars
                await self.calendar_service.hold_slot(
                    datetime_str=slot_datetime,
                    duration_minutes=duration_minutes,
                    doctor_id=doctor_id,
                    hold_id=hold['id']
                )

                return {
                    "success": True,
                    "hold_id": hold['id'],
                    "slot_datetime": slot_datetime,
                    "expire_at": expire_at.isoformat(),
                    "duration_minutes": hold_duration_minutes
                }
            else:
                return {
                    "success": False,
                    "error": "Failed to create appointment hold"
                }

        except Exception as e:
            logger.error(f"Error creating appointment hold: {str(e)}")
            return {
                "success": False,
                "error": f"Failed to create hold: {str(e)}"
            }

    async def confirm_appointment_hold_tool(
        self,
        hold_id: str,
        confirmation_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Confirm a held appointment slot by transitioning state from HOLD to CONFIRMED.

        Args:
            hold_id: ID of the hold to confirm
            confirmation_data: Additional confirmation data

        Returns:
            Dictionary with confirmation status
        """
        try:
            # Verify hold exists and hasn't expired
            hold_result = self.supabase.table('healthcare.resource_reservations').select('*').eq(
                'id', hold_id
            ).eq('state', 'HOLD').execute()

            if not hold_result.data:
                return {
                    "success": False,
                    "error": "Hold not found or already confirmed/expired"
                }

            hold = hold_result.data[0]

            # Check if expired
            if hold.get('hold_expires_at'):
                expires_at = datetime.fromisoformat(hold['hold_expires_at'])
                if expires_at < datetime.utcnow():
                    # Mark as expired
                    self.supabase.table('healthcare.resource_reservations').update({
                        "state": "EXPIRED"
                    }).eq('id', hold_id).execute()
                    return {
                        "success": False,
                        "error": "Hold has expired"
                    }

            # Create appointment first if not already linked
            appointment_id = None
            if confirmation_data and not hold.get('confirmed_appointment_id'):
                appointment_id = await self._create_appointment_from_hold(hold, confirmation_data)

            # Transition hold to CONFIRMED state
            update_data = {
                "state": "CONFIRMED",
                "status": "confirmed",  # Keep old status for compatibility
                "updated_at": datetime.utcnow().isoformat()
            }

            if appointment_id:
                update_data["confirmed_appointment_id"] = appointment_id

            update_result = self.supabase.table('healthcare.resource_reservations').update(
                update_data
            ).eq('id', hold_id).execute()

            if update_result.data:
                return {
                    "success": True,
                    "hold_id": hold_id,
                    "appointment_id": appointment_id,
                    "state": "CONFIRMED",
                    "message": "Appointment hold confirmed successfully"
                }
            else:
                return {
                    "success": False,
                    "error": "Failed to confirm appointment hold"
                }

        except Exception as e:
            logger.error(f"Error confirming appointment hold: {str(e)}")
            return {
                "success": False,
                "error": f"Failed to confirm hold: {str(e)}"
            }

    async def release_appointment_hold_tool(
        self,
        hold_id: str,
        reason: str
    ) -> Dict[str, Any]:
        """
        Release a held appointment slot by transitioning state to RELEASED.

        Args:
            hold_id: ID of the hold to release
            reason: Reason for releasing the hold

        Returns:
            Dictionary with release confirmation
        """
        try:
            # Get hold details first
            hold_result = self.supabase.table('healthcare.resource_reservations').select('*').eq(
                'id', hold_id
            ).eq('state', 'HOLD').execute()

            if not hold_result.data:
                return {
                    "success": False,
                    "error": "Hold not found or already released"
                }

            hold = hold_result.data[0]

            # Transition state to RELEASED
            update_result = self.supabase.table('healthcare.resource_reservations').update({
                "state": "RELEASED",
                "status": "released",  # Keep old status for compatibility
                "updated_at": datetime.utcnow().isoformat()
            }).eq('id', hold_id).execute()

            if update_result.data:
                # Release in external calendars
                await self.calendar_service.release_hold(
                    hold_id=hold_id,
                    doctor_id=hold.get('doctor_id')
                )

                return {
                    "success": True,
                    "hold_id": hold_id,
                    "status": "released",
                    "reason": reason,
                    "message": "Appointment hold released successfully"
                }
            else:
                return {
                    "success": False,
                    "error": "Failed to release appointment hold"
                }

        except Exception as e:
            logger.error(f"Error releasing appointment hold: {str(e)}")
            return {
                "success": False,
                "error": f"Failed to release hold: {str(e)}"
            }

    # Helper methods

    async def _get_eligible_doctors_for_service(
        self,
        clinic_id: str,
        service_id: str
    ) -> List[Dict[str, Any]]:
        """
        Get all active doctors who can provide a specific service at this clinic.

        Args:
            clinic_id: Clinic ID
            service_id: Service ID

        Returns:
            List of doctor dictionaries with id, name, and specialization
        """
        try:
            # Query doctor_services join table to find eligible doctors
            result = self.supabase.schema('healthcare').table('doctor_services').select(
                'doctor_id, doctors(id, first_name, last_name, specialization, active)'
            ).eq('service_id', service_id).execute()

            doctors = []
            for row in result.data or []:
                doctor_data = row.get('doctors')
                if doctor_data and doctor_data.get('active', True):
                    doctors.append({
                        'id': doctor_data['id'],
                        'name': f"Dr. {doctor_data.get('first_name', '')} {doctor_data.get('last_name', '')}".strip(),
                        'specialization': doctor_data.get('specialization', '')
                    })

            # If no doctors found via doctor_services, try to find all active doctors at clinic
            # (fallback for clinics without explicit service-doctor mappings)
            if not doctors:
                logger.warning(
                    f"No doctor-service mappings found for service {service_id}, "
                    f"falling back to all active doctors at clinic {clinic_id}"
                )
                # Use cached doctors instead of direct DB query
                cached_doctors = await self._get_cached_doctors()

                for doc in cached_doctors:
                    doctors.append({
                        'id': doc['id'],
                        'name': f"Dr. {doc.get('first_name', '')} {doc.get('last_name', '')}".strip(),
                        'specialization': doc.get('specialization', '')
                    })

            logger.info(f"Found {len(doctors)} eligible doctors for service {service_id}")
            return doctors

        except Exception as e:
            logger.error(f"Error getting eligible doctors for service: {e}")
            return []

    async def _calculate_doctor_workloads(
        self,
        doctors: List[Dict[str, Any]],
        target_date: datetime
    ) -> List[Dict[str, Any]]:
        """
        Calculate workload scores for doctors based on their appointment count.

        Uses the same logic as PreferenceScorer.score_least_busy():
        - 0 appointments = 1.0 (least busy)
        - 8+ appointments = 0.0 (most busy)

        Args:
            doctors: List of doctor dictionaries with 'id' field
            target_date: Target date to check appointments

        Returns:
            List of doctors with workload_score and appointment_count added
        """
        try:
            doctor_ids = [d['id'] for d in doctors]

            if not doctor_ids:
                return doctors

            # Get appointment counts for each doctor on the target date
            date_str = target_date.strftime('%Y-%m-%d')

            # Query appointments for all eligible doctors on the target date
            result = self.supabase.schema('healthcare').table('appointments').select(
                'doctor_id'
            ).in_('doctor_id', doctor_ids).eq(
                'appointment_date', date_str
            ).neq('status', 'cancelled').execute()

            # Count appointments per doctor
            appointment_counts = {}
            for apt in result.data or []:
                doc_id = apt['doctor_id']
                appointment_counts[doc_id] = appointment_counts.get(doc_id, 0) + 1

            # Calculate workload scores (matching PreferenceScorer.score_least_busy logic)
            MAX_APPOINTMENTS = 8

            for doctor in doctors:
                count = appointment_counts.get(doctor['id'], 0)
                # Score: 0 appointments = 1.0, 8+ = 0.0
                workload_score = max(0.0, 1.0 - (count / MAX_APPOINTMENTS))

                doctor['appointment_count'] = count
                doctor['workload_score'] = workload_score
                doctor['workload_label'] = (
                    'light' if workload_score > 0.7 else
                    'moderate' if workload_score > 0.3 else
                    'heavy'
                )

            logger.info(
                f"Calculated workloads for {len(doctors)} doctors on {date_str}: "
                f"counts={appointment_counts}"
            )

            return doctors

        except Exception as e:
            logger.error(f"Error calculating doctor workloads: {e}")
            # Return doctors without workload info on error
            for doctor in doctors:
                doctor['appointment_count'] = 0
                doctor['workload_score'] = 0.5
                doctor['workload_label'] = 'unknown'
            return doctors

    async def _link_doctor_to_hold(self, hold_id: str, doctor_id: str):
        """Link doctor resource to hold via reservation_resources junction table"""
        try:
            # First get the doctor's resource_id
            resource_result = self.supabase.table('healthcare.resources').select('id').eq(
                'doctor_id', doctor_id
            ).eq('resource_type', 'doctor').execute()

            if resource_result.data:
                resource_id = resource_result.data[0]['id']

                # Create link in reservation_resources junction table
                link_data = {
                    "reservation_id": hold_id,
                    "resource_id": resource_id,
                    "resource_role": "primary"
                }
                self.supabase.table('healthcare.reservation_resources').insert(link_data).execute()
                logger.info(f"✅ Linked doctor {doctor_id} to hold {hold_id}")
            else:
                logger.warning(f"⚠️ Doctor resource not found for doctor_id: {doctor_id}")
        except Exception as e:
            logger.warning(f"Failed to link doctor to hold: {str(e)}")
            # Non-fatal - hold still exists

    async def _create_appointment_from_hold(self, hold: Dict, confirmation_data: Dict) -> Optional[str]:
        """Create appointment record from hold data"""
        try:
            appointment_datetime = datetime.fromisoformat(
                f"{hold['reservation_date']} {hold['start_time']}"
            )

            appointment_data = {
                "clinic_id": hold['clinic_id'],
                "patient_id": hold.get('patient_id') or confirmation_data.get('patient_id'),
                "service_id": hold.get('service_id'),
                "appointment_date": hold['reservation_date'],
                "start_time": hold['start_time'],
                "end_time": hold['end_time'],
                "status": "scheduled",
                "appointment_type": confirmation_data.get('appointment_type', 'consultation'),
                "reason_for_visit": confirmation_data.get('reason'),
                "created_at": datetime.utcnow().isoformat()
            }

            result = self.supabase.table('healthcare.appointments').insert(appointment_data).execute()
            appointment_id = result.data[0]['id'] if result.data else None

            if appointment_id:
                logger.info(f"✅ Created appointment {appointment_id} from hold {hold['id']}")

            return appointment_id
        except Exception as e:
            logger.error(f"Error creating appointment from hold: {str(e)}")
            return None

    async def _check_idempotency(self, idempotency_key: str) -> Dict[str, Any]:
        """Check if request has already been processed via idempotency key"""
        try:
            import hashlib
            key_hash = hashlib.sha256(idempotency_key.encode()).hexdigest()

            result = self.supabase.table('healthcare.booking_idempotency').select('*').eq(
                'key_hash', key_hash
            ).eq('tenant_id', self.clinic_id).execute()

            if result.data:
                record = result.data[0]
                # Check if completed
                if record.get('status') == 'completed':
                    return {
                        'already_processed': True,
                        'response_payload': record.get('response_payload', {})
                    }

            return {'already_processed': False}
        except Exception as e:
            logger.warning(f"Error checking idempotency: {str(e)}")
            # On error, proceed with request (fail open)
            return {'already_processed': False}

    async def _record_idempotency_attempt(self, idempotency_key: str):
        """Record initial idempotency attempt"""
        try:
            import hashlib
            key_hash = hashlib.sha256(idempotency_key.encode()).hexdigest()

            # Try to insert, ignore if exists (race condition)
            try:
                self.supabase.table('healthcare.booking_idempotency').insert({
                    'key_hash': key_hash,
                    'idempotency_key': idempotency_key,
                    'tenant_id': self.clinic_id,
                    'channel': 'whatsapp',
                    'status': 'processing',
                    'created_at': datetime.utcnow().isoformat()
                }).execute()
            except Exception:
                # Already exists, that's fine
                pass
        except Exception as e:
            logger.warning(f"Error recording idempotency attempt: {str(e)}")

    async def _record_idempotency_success(self, idempotency_key: str, response: Dict):
        """Record successful booking in idempotency table"""
        try:
            import hashlib
            key_hash = hashlib.sha256(idempotency_key.encode()).hexdigest()

            self.supabase.table('healthcare.booking_idempotency').update({
                'status': 'completed',
                'response_payload': response,
                'response_timestamp': datetime.utcnow().isoformat()
            }).eq('key_hash', key_hash).execute()

            logger.info(f"✅ Recorded idempotency success for key {idempotency_key}")
        except Exception as e:
            logger.warning(f"Error recording idempotency success: {str(e)}")

    async def _record_idempotency_failure(self, idempotency_key: str, error: str):
        """Record failed booking attempt"""
        try:
            import hashlib
            key_hash = hashlib.sha256(idempotency_key.encode()).hexdigest()

            self.supabase.table('healthcare.booking_idempotency').update({
                'status': 'failed',
                'error_message': error
            }).eq('key_hash', key_hash).execute()

            logger.warning(f"⚠️ Recorded idempotency failure for key {idempotency_key}: {error}")
        except Exception as e:
            logger.warning(f"Error recording idempotency failure: {str(e)}")

    async def _get_service_by_name(
        self,
        service_name: str,
        language: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """Get service details by name using hybrid search"""
        try:
            # Initialize hybrid search service (lazy init)
            if not hasattr(self, 'hybrid_search'):
                from app.config import get_redis_client
                from app.services.hybrid_search_service import HybridSearchService, EntityType
                redis = get_redis_client()
                self.hybrid_search = HybridSearchService(self.clinic_id, redis, self.supabase)

            # Search using hybrid service
            search_result = await self.hybrid_search.search(
                query=service_name,
                entity_type=EntityType.SERVICE,
                language=language,
                limit=1
            )

            if search_result['success'] and search_result['results']:
                logger.info(
                    f"✅ Service found via {search_result['search_metadata']['search_stage']}: "
                    f"{search_result['results'][0]['name']}"
                )
                return search_result['results'][0]

            logger.error(f"❌ Service '{service_name}' not found via hybrid search")
            return None

        except Exception as e:
            logger.error(f"Error getting service: {str(e)}")
            return None

    async def _get_appointment_by_id(self, appointment_id: str) -> Optional[Dict[str, Any]]:
        """Get appointment details by ID"""
        try:
            result = self.supabase.table('healthcare.appointments').select('*').eq('id', appointment_id).execute()
            return result.data[0] if result.data else None
        except Exception as e:
            logger.error(f"Error getting appointment: {str(e)}")
            return None

    async def _get_related_appointments(self, parent_id: str) -> List[Dict[str, Any]]:
        """Get all appointments related to a parent appointment"""
        try:
            result = self.supabase.table('healthcare.appointments').select('*').or_(
                f'id.eq.{parent_id},parent_appointment_id.eq.{parent_id}'
            ).order('stage_number', desc=False).execute()
            return result.data if result.data else []
        except Exception as e:
            logger.error(f"Error getting related appointments: {str(e)}")
            return []

    def _filter_by_time_preference(
        self,
        slots: List[Dict[str, Any]],
        preference: str
    ) -> List[Dict[str, Any]]:
        """Filter slots by time preference"""
        filtered = []
        for slot in slots:
            dt = datetime.fromisoformat(slot['datetime'])
            hour = dt.hour

            if preference == 'morning' and 6 <= hour < 12:
                filtered.append(slot)
            elif preference == 'afternoon' and 12 <= hour < 17:
                filtered.append(slot)
            elif preference == 'evening' and 17 <= hour < 21:
                filtered.append(slot)

        return filtered

    async def _book_multi_stage_appointments(
        self,
        base_appointment_data: Dict[str, Any],
        service: Dict[str, Any],
        stage_config: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """Book multiple appointments for multi-stage services"""
        appointments = []
        base_datetime = datetime.fromisoformat(base_appointment_data['scheduled_at'])
        total_stages = stage_config.get('total_stages', 1)
        days_between = stage_config.get('days_between_stages', 7)

        try:
            # Book first appointment as parent
            first_appointment = dict(base_appointment_data)
            first_appointment['stage_number'] = 1
            first_appointment['total_stages'] = total_stages
            first_appointment['stage_config'] = json.dumps(stage_config)

            result = self.supabase.table('healthcare.appointments').insert(first_appointment).execute()
            if not result.data:
                return []

            parent_appointment = result.data[0]
            appointments.append(parent_appointment)

            # Book subsequent stages
            for stage in range(2, total_stages + 1):
                stage_datetime = base_datetime + timedelta(days=days_between * (stage - 1))

                stage_appointment = dict(base_appointment_data)
                stage_appointment['scheduled_at'] = stage_datetime.isoformat()
                stage_appointment['stage_number'] = stage
                stage_appointment['total_stages'] = total_stages
                stage_appointment['parent_appointment_id'] = parent_appointment['id']
                stage_appointment['stage_config'] = json.dumps(stage_config)

                result = self.supabase.table('healthcare.appointments').insert(stage_appointment).execute()
                if result.data:
                    appointments.append(result.data[0])
                else:
                    # Rollback on failure
                    for apt in appointments:
                        self.supabase.table('healthcare.appointments').delete().eq('id', apt['id']).execute()
                    return []

            return appointments

        except Exception as e:
            logger.error(f"Error booking multi-stage appointments: {str(e)}")
            return []

    def _group_multi_stage_appointments(
        self,
        appointments: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Group multi-stage appointments together"""
        grouped = []
        seen_parents = set()

        for apt in appointments:
            parent_id = apt.get('parent_appointment_id')

            if parent_id and parent_id not in seen_parents:
                # This is a child appointment, skip if parent already processed
                continue
            elif not parent_id and apt.get('total_stages', 1) > 1:
                # This is a parent appointment with stages
                seen_parents.add(apt['id'])

                # Find all related stages
                stages = [apt]
                for other in appointments:
                    if other.get('parent_appointment_id') == apt['id']:
                        stages.append(other)

                # Sort by stage number
                stages.sort(key=lambda x: x.get('stage_number', 1))

                grouped.append({
                    "id": apt['id'],
                    "is_multi_stage": True,
                    "stages": stages,
                    "total_stages": apt['total_stages'],
                    "service_name": apt['service_name'],
                    "patient_name": apt['patient_name'],
                    "first_appointment_date": stages[0]['scheduled_at'],
                    "last_appointment_date": stages[-1]['scheduled_at']
                })
            else:
                # Single appointment
                grouped.append(apt)

        return grouped

    def _format_confirmation_message(
        self,
        appointment: Dict[str, Any],
        service: Dict[str, Any]
    ) -> str:
        """Format appointment confirmation message"""
        dt = datetime.fromisoformat(appointment['scheduled_at'])
        return (
            f"Appointment confirmed!\n"
            f"Service: {service['name']}\n"
            f"Date: {dt.strftime('%B %d, %Y')}\n"
            f"Time: {dt.strftime('%I:%M %p')}\n"
            f"Duration: {service.get('duration_minutes', 30)} minutes\n"
            f"Appointment ID: {appointment['id'][:8]}..."
        )

    def _format_multi_stage_confirmation(
        self,
        appointments: List[Dict[str, Any]],
        service: Dict[str, Any]
    ) -> str:
        """Format multi-stage appointment confirmation message"""
        messages = [f"Multi-stage appointment confirmed for {service['name']}!\n"]

        for apt in appointments:
            dt = datetime.fromisoformat(apt['scheduled_at'])
            messages.append(
                f"Stage {apt.get('stage_number', 1)}: "
                f"{dt.strftime('%B %d, %Y at %I:%M %p')}"
            )

        messages.append(f"\nTotal stages: {len(appointments)}")
        messages.append(f"Main appointment ID: {appointments[0]['id'][:8]}...")

        return "\n".join(messages)
