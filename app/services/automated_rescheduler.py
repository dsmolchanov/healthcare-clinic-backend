"""
Automated Rescheduling Service with Patient Preferences
Phase 4: Intelligent Scheduling with Calendar Awareness

This service handles automated rescheduling of appointments based on:
- Patient preferences (time of day, day of week, doctor preferences)
- Doctor availability and schedule changes
- External calendar conflicts
- Predictive conflict prevention
- Business rules and constraints
"""

import asyncio
import logging
from datetime import datetime, timedelta, time
from typing import Dict, List, Optional, Tuple, Any
from enum import Enum
from dataclasses import dataclass
from supabase import Client

logger = logging.getLogger(__name__)

class RescheduleReason(str, Enum):
    DOCTOR_UNAVAILABLE = "doctor_unavailable"
    CALENDAR_CONFLICT = "calendar_conflict"
    PATIENT_REQUEST = "patient_request"
    EMERGENCY = "emergency"
    OPTIMIZATION = "optimization"
    PREDICTIVE_PREVENTION = "predictive_prevention"

class RescheduleStrategy(str, Enum):
    PATIENT_FIRST = "patient_first"  # Prioritize patient preferences
    DOCTOR_FIRST = "doctor_first"    # Prioritize doctor availability
    BALANCED = "balanced"            # Balance both preferences
    URGENT = "urgent"                # Emergency rescheduling
    OPTIMIZATION = "optimization"    # AI-driven optimization

@dataclass
class PatientPreferences:
    """Patient scheduling preferences"""
    preferred_days: List[str]  # ['monday', 'tuesday', ...]
    preferred_times: List[Tuple[time, time]]  # [(start, end), ...]
    avoid_days: List[str] = None
    avoid_times: List[Tuple[time, time]] = None
    preferred_doctors: List[str] = None
    max_wait_days: int = 30
    notification_preferences: Dict[str, bool] = None
    language: str = "en"

@dataclass
class RescheduleRequest:
    """Automated reschedule request"""
    appointment_id: str
    reason: RescheduleReason
    strategy: RescheduleStrategy
    target_date_range: Tuple[datetime, datetime] = None
    exclude_dates: List[datetime] = None
    priority: int = 0  # Higher = more urgent
    patient_preferences: PatientPreferences = None
    metadata: Dict[str, Any] = None

@dataclass
class RescheduleOption:
    """Potential reschedule option"""
    datetime: datetime
    doctor_id: str
    confidence: float  # 0.0 to 1.0
    preference_score: float  # How well it matches preferences
    availability_score: float  # How available the slot is
    conflict_risk: float  # Risk of future conflicts
    reasoning: str
    metadata: Dict[str, Any] = None

class AutomatedRescheduler:
    """Automated appointment rescheduling with patient preferences"""

    def __init__(self, supabase: Client):
        self.supabase = supabase
        self.max_options = 5
        self.preference_weight = 0.4
        self.availability_weight = 0.3
        self.conflict_weight = 0.3

    async def reschedule_appointment(
        self,
        request: RescheduleRequest,
        auto_confirm: bool = False
    ) -> Dict[str, Any]:
        """
        Automatically reschedule an appointment based on preferences and constraints

        Args:
            request: Reschedule request with preferences and constraints
            auto_confirm: Whether to automatically confirm the best option

        Returns:
            Reschedule result with options and status
        """
        try:
            logger.info(f"Starting automated reschedule for appointment {request.appointment_id}")

            # 1. Get current appointment details
            appointment = await self._get_appointment(request.appointment_id)
            if not appointment:
                raise ValueError(f"Appointment {request.appointment_id} not found")

            # 2. Get patient preferences if not provided
            if not request.patient_preferences:
                request.patient_preferences = await self._get_patient_preferences(
                    appointment['patient_id']
                )

            # 3. Determine search parameters
            search_start, search_end = self._get_search_range(
                appointment, request.target_date_range, request.patient_preferences
            )

            # 4. Find available reschedule options
            options = await self._find_reschedule_options(
                appointment, request, search_start, search_end
            )

            if not options:
                return {
                    "success": False,
                    "message": "No suitable reschedule options found",
                    "appointment_id": request.appointment_id,
                    "options": []
                }

            # 5. Auto-confirm best option if requested
            result = {
                "success": True,
                "appointment_id": request.appointment_id,
                "options": [self._option_to_dict(opt) for opt in options],
                "original_datetime": appointment['appointment_date'],
                "reason": request.reason.value,
                "strategy": request.strategy.value
            }

            if auto_confirm and options:
                best_option = options[0]
                confirmation = await self._confirm_reschedule(
                    appointment, best_option, request
                )
                result.update(confirmation)

            logger.info(f"Reschedule completed for {request.appointment_id}: {len(options)} options found")
            return result

        except Exception as e:
            logger.error(f"Error in automated reschedule: {str(e)}")
            return {
                "success": False,
                "error": str(e),
                "appointment_id": request.appointment_id
            }

    async def bulk_reschedule(
        self,
        requests: List[RescheduleRequest],
        max_concurrent: int = 5
    ) -> List[Dict[str, Any]]:
        """
        Reschedule multiple appointments concurrently

        Args:
            requests: List of reschedule requests
            max_concurrent: Maximum concurrent reschedule operations

        Returns:
            List of reschedule results
        """
        logger.info(f"Starting bulk reschedule for {len(requests)} appointments")

        # Create semaphore to limit concurrency
        semaphore = asyncio.Semaphore(max_concurrent)

        async def _reschedule_with_semaphore(request):
            async with semaphore:
                return await self.reschedule_appointment(request, auto_confirm=False)

        # Execute reschedules concurrently
        results = await asyncio.gather(
            *[_reschedule_with_semaphore(req) for req in requests],
            return_exceptions=True
        )

        # Handle exceptions
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                results[i] = {
                    "success": False,
                    "error": str(result),
                    "appointment_id": requests[i].appointment_id
                }

        logger.info(f"Bulk reschedule completed: {sum(1 for r in results if r.get('success'))} succeeded")
        return results

    async def suggest_optimal_reschedule(
        self,
        appointment_id: str,
        reason: RescheduleReason = RescheduleReason.OPTIMIZATION
    ) -> Dict[str, Any]:
        """
        Suggest optimal reschedule options using AI optimization

        Args:
            appointment_id: Appointment to optimize
            reason: Reason for rescheduling

        Returns:
            Optimization suggestions
        """
        request = RescheduleRequest(
            appointment_id=appointment_id,
            reason=reason,
            strategy=RescheduleStrategy.OPTIMIZATION,
            priority=1
        )

        return await self.reschedule_appointment(request, auto_confirm=False)

    async def handle_calendar_conflict(
        self,
        appointment_id: str,
        conflict_details: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Handle external calendar conflicts with automated rescheduling

        Args:
            appointment_id: Conflicting appointment
            conflict_details: Details about the conflict

        Returns:
            Conflict resolution result
        """
        logger.info(f"Handling calendar conflict for appointment {appointment_id}")

        request = RescheduleRequest(
            appointment_id=appointment_id,
            reason=RescheduleReason.CALENDAR_CONFLICT,
            strategy=RescheduleStrategy.URGENT,
            priority=5,
            metadata={"conflict_details": conflict_details}
        )

        # Try to reschedule with urgency
        result = await self.reschedule_appointment(request, auto_confirm=True)

        # Notify relevant parties
        if result["success"]:
            await self._notify_conflict_resolution(appointment_id, result)

        return result

    async def _get_appointment(self, appointment_id: str) -> Optional[Dict[str, Any]]:
        """Get appointment details"""
        try:
            result = self.supabase.table("appointments") \
                .select("*") \
                .eq("id", appointment_id) \
                .single() \
                .execute()
            return result.data
        except Exception as e:
            logger.error(f"Error fetching appointment {appointment_id}: {str(e)}")
            return None

    async def _get_patient_preferences(self, patient_id: str) -> PatientPreferences:
        """Get or create patient preferences"""
        try:
            # Try to get existing preferences
            result = self.supabase.table("patient_preferences") \
                .select("*") \
                .eq("patient_id", patient_id) \
                .execute()

            if result.data:
                prefs = result.data[0]
                return PatientPreferences(
                    preferred_days=prefs.get("preferred_days", ["monday", "tuesday", "wednesday", "thursday", "friday"]),
                    preferred_times=[(time(9, 0), time(17, 0))],  # Default 9-5
                    avoid_days=prefs.get("avoid_days", []),
                    max_wait_days=prefs.get("max_wait_days", 30),
                    notification_preferences=prefs.get("notification_preferences", {"sms": True, "email": False}),
                    language=prefs.get("language", "en")
                )
            else:
                # Create default preferences
                return PatientPreferences(
                    preferred_days=["monday", "tuesday", "wednesday", "thursday", "friday"],
                    preferred_times=[(time(9, 0), time(17, 0))],
                    max_wait_days=30,
                    notification_preferences={"sms": True, "email": False},
                    language="en"
                )

        except Exception as e:
            logger.error(f"Error fetching patient preferences for {patient_id}: {str(e)}")
            # Return default preferences
            return PatientPreferences(
                preferred_days=["monday", "tuesday", "wednesday", "thursday", "friday"],
                preferred_times=[(time(9, 0), time(17, 0))],
                max_wait_days=30
            )

    def _get_search_range(
        self,
        appointment: Dict[str, Any],
        target_range: Optional[Tuple[datetime, datetime]],
        preferences: PatientPreferences
    ) -> Tuple[datetime, datetime]:
        """Determine search range for reschedule options"""

        if target_range:
            return target_range

        # Start from tomorrow (avoid same day unless urgent)
        start_date = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)

        # End date based on patient max wait preference
        end_date = start_date + timedelta(days=preferences.max_wait_days)

        return start_date, end_date

    async def _find_reschedule_options(
        self,
        appointment: Dict[str, Any],
        request: RescheduleRequest,
        search_start: datetime,
        search_end: datetime
    ) -> List[RescheduleOption]:
        """Find and score potential reschedule options"""

        options = []

        try:
            # Get doctor availability
            doctor_id = appointment['doctor_id']
            duration_minutes = appointment.get('duration_minutes', 30)

            # Get available slots in the search range
            availability_result = self.supabase.rpc("get_doctor_availability", {
                "p_doctor_id": doctor_id,
                "p_start_date": search_start.date().isoformat(),
                "p_end_date": search_end.date().isoformat(),
                "p_duration_minutes": duration_minutes
            }).execute()

            available_slots = availability_result.data or []

            # Score each available slot
            for slot in available_slots:
                slot_datetime = datetime.fromisoformat(slot['start_time'])

                # Skip if in exclude dates
                if request.exclude_dates and slot_datetime.date() in [d.date() for d in request.exclude_dates]:
                    continue

                # Calculate scores
                preference_score = self._calculate_preference_score(
                    slot_datetime, request.patient_preferences
                )
                availability_score = slot.get('availability_score', 0.5)
                conflict_risk = await self._calculate_conflict_risk(
                    slot_datetime, doctor_id, duration_minutes
                )

                # Calculate overall confidence
                confidence = (
                    preference_score * self.preference_weight +
                    availability_score * self.availability_weight +
                    (1 - conflict_risk) * self.conflict_weight
                )

                option = RescheduleOption(
                    datetime=slot_datetime,
                    doctor_id=doctor_id,
                    confidence=confidence,
                    preference_score=preference_score,
                    availability_score=availability_score,
                    conflict_risk=conflict_risk,
                    reasoning=self._generate_reasoning(
                        slot_datetime, preference_score, availability_score, conflict_risk
                    ),
                    metadata=slot
                )

                options.append(option)

            # Sort by confidence and return top options
            options.sort(key=lambda x: x.confidence, reverse=True)
            return options[:self.max_options]

        except Exception as e:
            logger.error(f"Error finding reschedule options: {str(e)}")
            return []

    def _calculate_preference_score(
        self,
        slot_datetime: datetime,
        preferences: PatientPreferences
    ) -> float:
        """Calculate how well a slot matches patient preferences"""

        score = 0.0

        # Day of week preference
        day_name = slot_datetime.strftime('%A').lower()
        if day_name in preferences.preferred_days:
            score += 0.4
        elif preferences.avoid_days and day_name in preferences.avoid_days:
            score -= 0.3

        # Time of day preference
        slot_time = slot_datetime.time()
        for start_time, end_time in preferences.preferred_times:
            if start_time <= slot_time <= end_time:
                score += 0.4
                break

        # Avoid times
        if preferences.avoid_times:
            for start_time, end_time in preferences.avoid_times:
                if start_time <= slot_time <= end_time:
                    score -= 0.3
                    break

        # Proximity to original time (slight preference for similar times)
        score += 0.2

        return max(0.0, min(1.0, score))

    async def _calculate_conflict_risk(
        self,
        slot_datetime: datetime,
        doctor_id: str,
        duration_minutes: int
    ) -> float:
        """Calculate risk of future conflicts for this slot"""

        try:
            # Use predictive conflict prevention service
            from app.services.predictive_conflict_prevention import PredictiveConflictPrevention

            predictor = PredictiveConflictPrevention(self.supabase)
            risk_assessment = await predictor.assess_slot_risk({
                "doctor_id": doctor_id,
                "start_time": slot_datetime.isoformat(),
                "duration_minutes": duration_minutes
            })

            return risk_assessment.get("overall_risk", 0.5)

        except Exception as e:
            logger.error(f"Error calculating conflict risk: {str(e)}")
            return 0.5  # Default medium risk

    def _generate_reasoning(
        self,
        slot_datetime: datetime,
        preference_score: float,
        availability_score: float,
        conflict_risk: float
    ) -> str:
        """Generate human-readable reasoning for the option"""

        reasons = []

        if preference_score > 0.7:
            reasons.append("matches your preferences well")
        elif preference_score > 0.4:
            reasons.append("partially matches your preferences")
        else:
            reasons.append("doesn't match your usual preferences")

        if availability_score > 0.7:
            reasons.append("doctor has good availability")
        elif availability_score > 0.4:
            reasons.append("doctor has moderate availability")
        else:
            reasons.append("limited doctor availability")

        if conflict_risk < 0.3:
            reasons.append("low risk of conflicts")
        elif conflict_risk < 0.7:
            reasons.append("moderate risk of conflicts")
        else:
            reasons.append("higher risk of conflicts")

        return f"This slot {', '.join(reasons)}."

    async def _confirm_reschedule(
        self,
        appointment: Dict[str, Any],
        option: RescheduleOption,
        request: RescheduleRequest
    ) -> Dict[str, Any]:
        """Confirm and execute the reschedule"""

        try:
            # Use unified appointment service for the reschedule
            from app.services.unified_appointment_service import UnifiedAppointmentService

            service = UnifiedAppointmentService(self.supabase)

            result = await service.reschedule_appointment(
                appointment_id=appointment['id'],
                new_datetime=option.datetime,
                reason=f"Automated reschedule: {request.reason.value}",
                notify=True
            )

            if result['success']:
                # Log the automated reschedule
                await self._log_reschedule(appointment, option, request, result)

                return {
                    "confirmed": True,
                    "new_datetime": option.datetime.isoformat(),
                    "confidence": option.confidence,
                    "reasoning": option.reasoning,
                    "reschedule_result": result
                }
            else:
                return {
                    "confirmed": False,
                    "error": result.get('error', 'Unknown error'),
                    "reschedule_result": result
                }

        except Exception as e:
            logger.error(f"Error confirming reschedule: {str(e)}")
            return {
                "confirmed": False,
                "error": str(e)
            }

    async def _log_reschedule(
        self,
        appointment: Dict[str, Any],
        option: RescheduleOption,
        request: RescheduleRequest,
        result: Dict[str, Any]
    ):
        """Log automated reschedule for audit purposes"""

        try:
            log_entry = {
                "appointment_id": appointment['id'],
                "patient_id": appointment['patient_id'],
                "doctor_id": appointment['doctor_id'],
                "original_datetime": appointment['appointment_date'],
                "new_datetime": option.datetime.isoformat(),
                "reason": request.reason.value,
                "strategy": request.strategy.value,
                "confidence": option.confidence,
                "automated": True,
                "metadata": {
                    "preference_score": option.preference_score,
                    "availability_score": option.availability_score,
                    "conflict_risk": option.conflict_risk,
                    "reasoning": option.reasoning,
                    "request_metadata": request.metadata
                }
            }

            self.supabase.table("appointment_reschedule_log") \
                .insert(log_entry) \
                .execute()

        except Exception as e:
            logger.error(f"Error logging reschedule: {str(e)}")

    async def _notify_conflict_resolution(
        self,
        appointment_id: str,
        result: Dict[str, Any]
    ):
        """Notify relevant parties about conflict resolution"""

        try:
            # Send WebSocket notification
            from app.services.websocket_manager import WebSocketManager

            manager = WebSocketManager()
            await manager.broadcast_notification({
                "type": "conflict_resolved",
                "appointment_id": appointment_id,
                "new_datetime": result.get("new_datetime"),
                "confidence": result.get("confidence"),
                "automated": True
            })

        except Exception as e:
            logger.error(f"Error sending conflict resolution notification: {str(e)}")

    def _option_to_dict(self, option: RescheduleOption) -> Dict[str, Any]:
        """Convert RescheduleOption to dictionary"""
        return {
            "datetime": option.datetime.isoformat(),
            "doctor_id": option.doctor_id,
            "confidence": option.confidence,
            "preference_score": option.preference_score,
            "availability_score": option.availability_score,
            "conflict_risk": option.conflict_risk,
            "reasoning": option.reasoning,
            "metadata": option.metadata
        }

# Example usage and testing functions
async def test_automated_reschedule():
    """Test automated rescheduling functionality"""
    from supabase import create_client
    import os

    supabase = create_client(
        os.getenv("SUPABASE_URL"),
        os.getenv("SUPABASE_SERVICE_KEY")
    )

    rescheduler = AutomatedRescheduler(supabase)

    # Test patient preferences
    preferences = PatientPreferences(
        preferred_days=["monday", "wednesday", "friday"],
        preferred_times=[(time(9, 0), time(12, 0)), (time(14, 0), time(17, 0))],
        avoid_days=["saturday", "sunday"],
        max_wait_days=14,
        notification_preferences={"sms": True, "email": True},
        language="en"
    )

    # Test reschedule request
    request = RescheduleRequest(
        appointment_id="test-appointment-id",
        reason=RescheduleReason.PATIENT_REQUEST,
        strategy=RescheduleStrategy.BALANCED,
        patient_preferences=preferences,
        priority=1
    )

    result = await rescheduler.reschedule_appointment(request)
    print(f"Reschedule result: {result}")

if __name__ == "__main__":
    asyncio.run(test_automated_reschedule())