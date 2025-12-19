"""
Intelligent Scheduling Engine with AI Optimization
Implements Phase 4: Intelligent Scheduling with Calendar Awareness
Uses machine learning and heuristics to optimize appointment scheduling
"""

import os
import json
import logging
import asyncio
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, asdict
from enum import Enum
import math

from supabase import create_client, Client

# Optional ML imports - graceful degradation if not available
try:
    import numpy as np
    NUMPY_AVAILABLE = True
except ImportError:
    NUMPY_AVAILABLE = False
    logger = logging.getLogger(__name__)
    logger.warning("NumPy not available. Some optimization features will be limited.")

from .unified_appointment_service import UnifiedAppointmentService, TimeSlot, AppointmentType
from .external_calendar_service import ExternalCalendarService
from .websocket_manager import websocket_manager, NotificationType

logger = logging.getLogger(__name__)

class OptimizationGoal(Enum):
    MINIMIZE_GAPS = "minimize_gaps"
    BALANCE_LOAD = "balance_load"
    PATIENT_PREFERENCE = "patient_preference"
    TRAVEL_TIME = "travel_time"
    URGENCY_PRIORITY = "urgency_priority"
    CLINIC_EFFICIENCY = "clinic_efficiency"

class SchedulingStrategy(Enum):
    FIRST_AVAILABLE = "first_available"
    OPTIMAL_PACKING = "optimal_packing"
    PATIENT_CENTRIC = "patient_centric"
    DOCTOR_CENTRIC = "doctor_centric"
    BALANCED = "balanced"
    AI_OPTIMIZED = "ai_optimized"

@dataclass
class PatientPreferences:
    """Patient scheduling preferences"""
    preferred_times: List[str]  # ["09:00", "14:00"]
    preferred_days: List[str]   # ["monday", "wednesday", "friday"]
    avoid_times: List[str]      # ["08:00", "17:00"]
    max_wait_days: int = 7
    prefers_morning: bool = None
    prefers_afternoon: bool = None
    travel_time_minutes: int = 30
    flexibility_score: float = 0.5  # 0 = inflexible, 1 = very flexible

@dataclass
class DoctorConstraints:
    """Doctor scheduling constraints and preferences"""
    working_hours: Dict[str, Dict[str, str]]  # {"monday": {"start": "09:00", "end": "17:00"}}
    break_times: List[Dict[str, str]]         # [{"start": "12:00", "end": "13:00"}]
    appointment_types: List[str]              # Appointment types this doctor handles
    max_consecutive_appointments: int = 8
    preferred_appointment_length: int = 30
    consultation_buffer_minutes: int = 5
    efficiency_rating: float = 1.0           # Multiplier for scheduling efficiency

@dataclass
class SchedulingContext:
    """Context information for intelligent scheduling"""
    current_date: datetime
    scheduling_horizon_days: int = 30
    clinic_capacity: int = 100
    seasonal_factors: Dict[str, float] = None
    special_events: List[Dict[str, Any]] = None
    historical_patterns: Dict[str, Any] = None

@dataclass
class SchedulingRecommendation:
    """AI-generated scheduling recommendation"""
    slot: TimeSlot
    confidence_score: float
    reasoning: List[str]
    optimization_factors: Dict[str, float]
    alternative_slots: List[TimeSlot] = None
    predicted_satisfaction: float = 0.8

class IntelligentScheduler:
    """
    AI-powered scheduling engine that optimizes appointment placement
    Uses machine learning, historical data, and heuristics for optimal scheduling
    """

    def __init__(self, supabase: Client = None, clinic_id: str = None):
        if supabase:
            self.supabase = supabase
        else:
            self.supabase: Client = create_client(
                os.environ.get("SUPABASE_URL"),
                os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
            )
        self.clinic_id = clinic_id
        self.appointment_service = UnifiedAppointmentService(supabase=self.supabase, clinic_id=clinic_id)
        self.calendar_service = ExternalCalendarService(supabase=self.supabase)

        # Scheduling weights for optimization
        self.optimization_weights = {
            OptimizationGoal.MINIMIZE_GAPS: 0.3,
            OptimizationGoal.BALANCE_LOAD: 0.2,
            OptimizationGoal.PATIENT_PREFERENCE: 0.25,
            OptimizationGoal.TRAVEL_TIME: 0.1,
            OptimizationGoal.URGENCY_PRIORITY: 0.1,
            OptimizationGoal.CLINIC_EFFICIENCY: 0.05
        }

        # Historical data cache
        self.historical_data_cache = {}
        self.cache_expiry = timedelta(hours=1)

    async def find_optimal_appointments(
        self,
        doctor_id: str,
        appointment_type: AppointmentType,
        duration_minutes: int = 30,
        patient_preferences: Optional[PatientPreferences] = None,
        urgency_level: str = "normal",
        strategy: SchedulingStrategy = SchedulingStrategy.AI_OPTIMIZED,
        max_recommendations: int = 5
    ) -> List[SchedulingRecommendation]:
        """
        Find optimal appointment slots using AI-powered scheduling
        Returns ranked recommendations with confidence scores and reasoning
        """
        try:
            logger.info(f"Finding optimal appointments for doctor {doctor_id} using {strategy.value} strategy")

            # Get scheduling context
            context = await self._build_scheduling_context(doctor_id)

            # Get doctor constraints
            doctor_constraints = await self._get_doctor_constraints(doctor_id)

            # Get available slots from the unified service
            available_slots = await self._get_extended_availability(
                doctor_id, context.scheduling_horizon_days, duration_minutes
            )

            if not available_slots:
                logger.warning(f"No available slots found for doctor {doctor_id}")
                return []

            # Apply intelligent optimization based on strategy
            if strategy == SchedulingStrategy.AI_OPTIMIZED:
                recommendations = await self._ai_optimize_slots(
                    available_slots, doctor_constraints, patient_preferences,
                    urgency_level, context
                )
            elif strategy == SchedulingStrategy.OPTIMAL_PACKING:
                recommendations = await self._optimal_packing_strategy(
                    available_slots, doctor_constraints, context
                )
            elif strategy == SchedulingStrategy.PATIENT_CENTRIC:
                recommendations = await self._patient_centric_strategy(
                    available_slots, patient_preferences, context
                )
            elif strategy == SchedulingStrategy.DOCTOR_CENTRIC:
                recommendations = await self._doctor_centric_strategy(
                    available_slots, doctor_constraints, context
                )
            elif strategy == SchedulingStrategy.BALANCED:
                recommendations = await self._balanced_strategy(
                    available_slots, doctor_constraints, patient_preferences, context
                )
            else:  # FIRST_AVAILABLE
                recommendations = await self._first_available_strategy(available_slots)

            # Limit recommendations
            recommendations = recommendations[:max_recommendations]

            logger.info(f"Generated {len(recommendations)} scheduling recommendations")
            return recommendations

        except Exception as e:
            logger.error(f"Failed to find optimal appointments: {e}")
            return []

    async def predict_scheduling_conflicts(
        self,
        doctor_id: str,
        date_range: Tuple[datetime, datetime],
        probability_threshold: float = 0.7
    ) -> List[Dict[str, Any]]:
        """
        Predict potential scheduling conflicts using historical patterns and ML
        """
        try:
            logger.info(f"Predicting scheduling conflicts for doctor {doctor_id}")

            start_date, end_date = date_range

            # Get historical conflict data
            historical_conflicts = await self._get_historical_conflicts(doctor_id)

            # Get current schedule density
            schedule_density = await self._calculate_schedule_density(doctor_id, start_date, end_date)

            # Analyze patterns
            conflict_predictions = []

            # Pattern 1: High-density time periods
            for day_offset in range((end_date - start_date).days + 1):
                check_date = start_date + timedelta(days=day_offset)
                day_name = check_date.strftime('%A').lower()

                # Check if this day/time historically has conflicts
                risk_score = await self._calculate_conflict_risk(
                    doctor_id, check_date, historical_conflicts, schedule_density
                )

                if risk_score >= probability_threshold:
                    conflict_predictions.append({
                        'date': check_date.strftime('%Y-%m-%d'),
                        'day_of_week': day_name,
                        'risk_score': risk_score,
                        'predicted_conflict_type': self._predict_conflict_type(historical_conflicts, day_name),
                        'prevention_suggestions': await self._generate_prevention_suggestions(risk_score)
                    })

            return conflict_predictions

        except Exception as e:
            logger.error(f"Failed to predict scheduling conflicts: {e}")
            return []

    async def auto_reschedule_with_preferences(
        self,
        appointment_id: str,
        reason: str = "optimization",
        respect_preferences: bool = True,
        notify_patient: bool = True
    ) -> Dict[str, Any]:
        """
        Automatically reschedule an appointment to a better time slot
        Uses AI to find optimal rescheduling options
        """
        try:
            logger.info(f"Auto-rescheduling appointment {appointment_id}")

            # Get current appointment details
            appointments = await self.appointment_service.get_appointments()
            appointment = next((apt for apt in appointments if apt['id'] == appointment_id), None)

            if not appointment:
                return {"success": False, "error": "Appointment not found"}

            # Get patient preferences if available
            patient_preferences = await self._get_patient_preferences(appointment['patient_id'])

            # Find better time slots
            recommendations = await self.find_optimal_appointments(
                doctor_id=appointment['doctor_id'],
                appointment_type=AppointmentType(appointment.get('appointment_type', 'consultation')),
                duration_minutes=30,  # Calculate from appointment times
                patient_preferences=patient_preferences,
                strategy=SchedulingStrategy.AI_OPTIMIZED,
                max_recommendations=3
            )

            if not recommendations:
                return {"success": False, "error": "No better time slots available"}

            # Select the best recommendation
            best_recommendation = recommendations[0]

            # Reschedule the appointment
            result = await self.appointment_service.reschedule_appointment(
                appointment_id=appointment_id,
                new_start_time=best_recommendation.slot.start_time,
                new_end_time=best_recommendation.slot.end_time
            )

            if result.success:
                # Broadcast optimization notification
                await websocket_manager.broadcast_appointment_update(
                    appointment_id=appointment_id,
                    notification_type=NotificationType.APPOINTMENT_RESCHEDULED,
                    appointment_data={
                        **appointment,
                        'reschedule_reason': reason,
                        'optimization_score': best_recommendation.confidence_score,
                        'ai_reasoning': best_recommendation.reasoning
                    },
                    source="ai_optimizer"
                )

                return {
                    "success": True,
                    "new_time": {
                        "start": best_recommendation.slot.start_time.isoformat(),
                        "end": best_recommendation.slot.end_time.isoformat()
                    },
                    "confidence_score": best_recommendation.confidence_score,
                    "reasoning": best_recommendation.reasoning,
                    "optimization_factors": best_recommendation.optimization_factors
                }
            else:
                return {"success": False, "error": result.error}

        except Exception as e:
            logger.error(f"Failed to auto-reschedule appointment: {e}")
            return {"success": False, "error": str(e)}

    async def optimize_daily_schedule(
        self,
        doctor_id: str,
        date: datetime,
        optimization_goals: List[OptimizationGoal] = None
    ) -> Dict[str, Any]:
        """
        Optimize an entire day's schedule for a doctor
        Rearranges appointments for maximum efficiency
        """
        try:
            logger.info(f"Optimizing daily schedule for doctor {doctor_id} on {date.strftime('%Y-%m-%d')}")

            if optimization_goals is None:
                optimization_goals = [OptimizationGoal.MINIMIZE_GAPS, OptimizationGoal.CLINIC_EFFICIENCY]

            # Get all appointments for the day
            appointments = await self.appointment_service.get_appointments(
                doctor_id=doctor_id,
                date_from=date.strftime('%Y-%m-%d'),
                date_to=date.strftime('%Y-%m-%d')
            )

            if len(appointments) < 2:
                return {"success": True, "message": "No optimization needed", "changes": 0}

            # Calculate current efficiency score
            current_efficiency = await self._calculate_schedule_efficiency(appointments)

            # Get available time slots for the day
            available_slots = await self.appointment_service.get_available_slots(
                doctor_id=doctor_id,
                date=date.strftime('%Y-%m-%d'),
                duration_minutes=30
            )

            # Run optimization algorithm
            optimized_schedule = await self._optimize_appointment_sequence(
                appointments, available_slots, optimization_goals
            )

            # Calculate new efficiency score
            new_efficiency = await self._calculate_schedule_efficiency(optimized_schedule)

            if new_efficiency <= current_efficiency:
                return {"success": True, "message": "Schedule already optimal", "changes": 0}

            # Apply optimizations (reschedule appointments)
            changes_made = 0
            for optimized_appt in optimized_schedule:
                if optimized_appt['needs_reschedule']:
                    result = await self.appointment_service.reschedule_appointment(
                        appointment_id=optimized_appt['id'],
                        new_start_time=optimized_appt['new_start_time'],
                        new_end_time=optimized_appt['new_end_time']
                    )
                    if result.success:
                        changes_made += 1

            return {
                "success": True,
                "changes": changes_made,
                "efficiency_improvement": new_efficiency - current_efficiency,
                "optimized_schedule": optimized_schedule
            }

        except Exception as e:
            logger.error(f"Failed to optimize daily schedule: {e}")
            return {"success": False, "error": str(e)}

    # Private helper methods for AI optimization

    async def _ai_optimize_slots(
        self,
        available_slots: List[TimeSlot],
        doctor_constraints: DoctorConstraints,
        patient_preferences: Optional[PatientPreferences],
        urgency_level: str,
        context: SchedulingContext
    ) -> List[SchedulingRecommendation]:
        """Use AI/ML to optimize slot selection"""
        recommendations = []

        try:
            for slot in available_slots:
                # Calculate optimization score using multiple factors
                score = 0.0
                reasoning = []
                factors = {}

                # Factor 1: Time preferences (patient)
                if patient_preferences:
                    time_score = self._calculate_time_preference_score(slot, patient_preferences)
                    score += time_score * self.optimization_weights[OptimizationGoal.PATIENT_PREFERENCE]
                    factors['time_preference'] = time_score
                    if time_score > 0.7:
                        reasoning.append("Matches patient's preferred time")

                # Factor 2: Schedule efficiency (doctor)
                efficiency_score = await self._calculate_efficiency_score(slot, doctor_constraints)
                score += efficiency_score * self.optimization_weights[OptimizationGoal.CLINIC_EFFICIENCY]
                factors['efficiency'] = efficiency_score
                if efficiency_score > 0.8:
                    reasoning.append("Optimizes doctor's schedule efficiency")

                # Factor 3: Historical success rate
                historical_score = await self._calculate_historical_success_score(slot)
                score += historical_score * 0.15
                factors['historical_success'] = historical_score

                # Factor 4: Urgency adjustment
                urgency_multiplier = {"low": 0.8, "normal": 1.0, "high": 1.2, "urgent": 1.5}
                score *= urgency_multiplier.get(urgency_level, 1.0)

                if urgency_level in ["high", "urgent"]:
                    reasoning.append(f"Prioritized for {urgency_level} urgency")

                # Factor 5: Gap minimization
                gap_score = await self._calculate_gap_minimization_score(slot, doctor_constraints.doctor_id)
                score += gap_score * self.optimization_weights[OptimizationGoal.MINIMIZE_GAPS]
                factors['gap_minimization'] = gap_score

                # Create recommendation
                recommendation = SchedulingRecommendation(
                    slot=slot,
                    confidence_score=min(score, 1.0),
                    reasoning=reasoning or ["Standard availability"],
                    optimization_factors=factors,
                    predicted_satisfaction=self._predict_satisfaction(score, factors)
                )

                recommendations.append(recommendation)

            # Sort by confidence score
            recommendations.sort(key=lambda x: x.confidence_score, reverse=True)

        except Exception as e:
            logger.error(f"AI optimization error: {e}")

        return recommendations

    async def _optimal_packing_strategy(
        self,
        available_slots: List[TimeSlot],
        doctor_constraints: DoctorConstraints,
        context: SchedulingContext
    ) -> List[SchedulingRecommendation]:
        """Pack appointments optimally to minimize gaps"""
        recommendations = []

        # Get current appointments for context
        for slot in available_slots:
            # Calculate how this slot fits with existing appointments
            packing_score = await self._calculate_packing_score(slot, doctor_constraints)

            recommendation = SchedulingRecommendation(
                slot=slot,
                confidence_score=packing_score,
                reasoning=[f"Optimal packing score: {packing_score:.2f}"],
                optimization_factors={"packing_efficiency": packing_score}
            )
            recommendations.append(recommendation)

        return sorted(recommendations, key=lambda x: x.confidence_score, reverse=True)

    async def _patient_centric_strategy(
        self,
        available_slots: List[TimeSlot],
        patient_preferences: Optional[PatientPreferences],
        context: SchedulingContext
    ) -> List[SchedulingRecommendation]:
        """Prioritize patient preferences and convenience"""
        recommendations = []

        for slot in available_slots:
            if patient_preferences:
                preference_score = self._calculate_time_preference_score(slot, patient_preferences)
                convenience_score = self._calculate_convenience_score(slot, patient_preferences)

                total_score = (preference_score + convenience_score) / 2

                recommendation = SchedulingRecommendation(
                    slot=slot,
                    confidence_score=total_score,
                    reasoning=[f"Patient preference match: {preference_score:.2f}"],
                    optimization_factors={
                        "preference_match": preference_score,
                        "convenience": convenience_score
                    }
                )
            else:
                # Default scoring without preferences
                recommendation = SchedulingRecommendation(
                    slot=slot,
                    confidence_score=0.5,
                    reasoning=["No patient preferences available"],
                    optimization_factors={"default": 0.5}
                )

            recommendations.append(recommendation)

        return sorted(recommendations, key=lambda x: x.confidence_score, reverse=True)

    async def _doctor_centric_strategy(
        self,
        available_slots: List[TimeSlot],
        doctor_constraints: DoctorConstraints,
        context: SchedulingContext
    ) -> List[SchedulingRecommendation]:
        """Optimize for doctor's efficiency and preferences"""
        recommendations = []

        for slot in available_slots:
            efficiency_score = await self._calculate_efficiency_score(slot, doctor_constraints)
            workload_score = await self._calculate_workload_balance_score(slot, doctor_constraints)

            total_score = (efficiency_score + workload_score) / 2

            recommendation = SchedulingRecommendation(
                slot=slot,
                confidence_score=total_score,
                reasoning=[f"Doctor efficiency optimization: {total_score:.2f}"],
                optimization_factors={
                    "efficiency": efficiency_score,
                    "workload_balance": workload_score
                }
            )
            recommendations.append(recommendation)

        return sorted(recommendations, key=lambda x: x.confidence_score, reverse=True)

    async def _balanced_strategy(
        self,
        available_slots: List[TimeSlot],
        doctor_constraints: DoctorConstraints,
        patient_preferences: Optional[PatientPreferences],
        context: SchedulingContext
    ) -> List[SchedulingRecommendation]:
        """Balance patient and doctor needs"""
        recommendations = []

        for slot in available_slots:
            # Equal weight to patient and doctor factors
            patient_score = 0.5  # Default
            if patient_preferences:
                patient_score = self._calculate_time_preference_score(slot, patient_preferences)

            doctor_score = await self._calculate_efficiency_score(slot, doctor_constraints)

            balanced_score = (patient_score + doctor_score) / 2

            recommendation = SchedulingRecommendation(
                slot=slot,
                confidence_score=balanced_score,
                reasoning=[f"Balanced optimization: {balanced_score:.2f}"],
                optimization_factors={
                    "patient_factors": patient_score,
                    "doctor_factors": doctor_score,
                    "balance": balanced_score
                }
            )
            recommendations.append(recommendation)

        return sorted(recommendations, key=lambda x: x.confidence_score, reverse=True)

    async def _first_available_strategy(
        self,
        available_slots: List[TimeSlot]
    ) -> List[SchedulingRecommendation]:
        """Simple first-available strategy for fallback"""
        recommendations = []

        for i, slot in enumerate(available_slots):
            # Higher score for earlier slots
            score = max(0.1, 1.0 - (i * 0.1))

            recommendation = SchedulingRecommendation(
                slot=slot,
                confidence_score=score,
                reasoning=["First available slot"],
                optimization_factors={"availability_order": score}
            )
            recommendations.append(recommendation)

        return recommendations

    # Additional helper methods for calculations

    def _calculate_time_preference_score(self, slot: TimeSlot, preferences: PatientPreferences) -> float:
        """Calculate how well a time slot matches patient preferences"""
        try:
            score = 0.5  # Base score

            slot_time = slot.start_time.strftime('%H:%M')
            slot_day = slot.start_time.strftime('%A').lower()

            # Check preferred times
            if preferences.preferred_times:
                time_matches = any(abs(self._time_to_minutes(slot_time) - self._time_to_minutes(pref_time)) <= 30
                                 for pref_time in preferences.preferred_times)
                if time_matches:
                    score += 0.3

            # Check preferred days
            if preferences.preferred_days and slot_day in preferences.preferred_days:
                score += 0.2

            # Check avoid times
            if preferences.avoid_times:
                time_conflicts = any(abs(self._time_to_minutes(slot_time) - self._time_to_minutes(avoid_time)) <= 60
                                   for avoid_time in preferences.avoid_times)
                if time_conflicts:
                    score -= 0.4

            # Morning/afternoon preferences
            hour = slot.start_time.hour
            if preferences.prefers_morning and hour < 12:
                score += 0.1
            elif preferences.prefers_afternoon and hour >= 12:
                score += 0.1

            return max(0.0, min(1.0, score))

        except Exception as e:
            logger.error(f"Error calculating time preference score: {e}")
            return 0.5

    def _time_to_minutes(self, time_str: str) -> int:
        """Convert time string to minutes since midnight"""
        try:
            hour, minute = map(int, time_str.split(':'))
            return hour * 60 + minute
        except:
            return 0

    async def _calculate_efficiency_score(self, slot: TimeSlot, constraints: DoctorConstraints) -> float:
        """Calculate efficiency score for a time slot"""
        try:
            # Base efficiency
            score = 0.6

            # Check if it's within optimal hours
            hour = slot.start_time.hour
            if 9 <= hour <= 16:  # Peak efficiency hours
                score += 0.2

            # Check for break time conflicts
            slot_time = slot.start_time.strftime('%H:%M')
            for break_time in constraints.break_times:
                if break_time['start'] <= slot_time <= break_time['end']:
                    score -= 0.5

            return max(0.0, min(1.0, score))

        except Exception as e:
            logger.error(f"Error calculating efficiency score: {e}")
            return 0.5

    def _calculate_convenience_score(self, slot: TimeSlot, preferences: PatientPreferences) -> float:
        """Calculate convenience score based on travel time and flexibility"""
        try:
            score = 0.5

            # Factor in travel time
            if preferences.travel_time_minutes <= 15:
                score += 0.2
            elif preferences.travel_time_minutes >= 60:
                score -= 0.1

            # Factor in flexibility
            score += preferences.flexibility_score * 0.3

            return max(0.0, min(1.0, score))

        except Exception as e:
            logger.error(f"Error calculating convenience score: {e}")
            return 0.5

    def _predict_satisfaction(self, optimization_score: float, factors: Dict[str, float]) -> float:
        """Predict patient satisfaction based on optimization factors"""
        try:
            # Simple satisfaction prediction model
            base_satisfaction = 0.7

            # Higher optimization score generally means higher satisfaction
            satisfaction = base_satisfaction + (optimization_score * 0.3)

            # Bonus for strong patient preference matching
            if factors.get('time_preference', 0) > 0.8:
                satisfaction += 0.1

            return max(0.0, min(1.0, satisfaction))

        except Exception as e:
            logger.error(f"Error predicting satisfaction: {e}")
            return 0.7

    # Placeholder methods for advanced features

    async def _build_scheduling_context(self, doctor_id: str) -> SchedulingContext:
        """Build scheduling context with current data"""
        return SchedulingContext(current_date=datetime.now())

    async def _get_doctor_constraints(self, doctor_id: str) -> DoctorConstraints:
        """Get doctor constraints from database"""
        return DoctorConstraints(
            working_hours={"monday": {"start": "09:00", "end": "17:00"}},
            break_times=[{"start": "12:00", "end": "13:00"}],
            appointment_types=["consultation", "procedure"],
            efficiency_rating=1.0
        )

    async def _get_extended_availability(self, doctor_id: str, days: int, duration: int) -> List[TimeSlot]:
        """Get availability for extended period"""
        slots = []
        for day_offset in range(min(days, 14)):  # Limit to 2 weeks for performance
            check_date = (datetime.now() + timedelta(days=day_offset + 1)).strftime('%Y-%m-%d')
            try:
                day_slots = await self.appointment_service.get_available_slots(
                    doctor_id, check_date, duration
                )
                slots.extend(day_slots)
            except:
                continue  # Skip days with errors
        return slots

    async def _get_patient_preferences(self, patient_id: str) -> Optional[PatientPreferences]:
        """Get patient preferences from database"""
        # Simplified - would fetch from patient preferences table
        return PatientPreferences(
            preferred_times=["10:00", "14:00"],
            preferred_days=["monday", "wednesday", "friday"],
            avoid_times=["08:00", "17:00"],
            flexibility_score=0.7
        )

    # Additional placeholder methods for complete functionality
    async def _get_historical_conflicts(self, doctor_id: str) -> List[Dict[str, Any]]:
        return []

    async def _calculate_schedule_density(self, doctor_id: str, start: datetime, end: datetime) -> float:
        return 0.5

    async def _calculate_conflict_risk(self, doctor_id: str, date: datetime, history: List, density: float) -> float:
        return 0.3

    def _predict_conflict_type(self, history: List, day_name: str) -> str:
        return "scheduling_overlap"

    async def _generate_prevention_suggestions(self, risk_score: float) -> List[str]:
        return ["Add buffer time between appointments", "Monitor external calendar changes"]

    async def _calculate_schedule_efficiency(self, appointments: List[Dict[str, Any]]) -> float:
        return 0.7

    async def _optimize_appointment_sequence(self, appointments: List, slots: List, goals: List) -> List[Dict[str, Any]]:
        return [{"needs_reschedule": False, **apt} for apt in appointments]

    async def _calculate_historical_success_score(self, slot: TimeSlot) -> float:
        return 0.8

    async def _calculate_gap_minimization_score(self, slot: TimeSlot, doctor_id: str) -> float:
        return 0.6

    async def _calculate_packing_score(self, slot: TimeSlot, constraints: DoctorConstraints) -> float:
        return 0.7

    async def _calculate_workload_balance_score(self, slot: TimeSlot, constraints: DoctorConstraints) -> float:
        return 0.6

    async def find_available_slots(
        self,
        service_id: Optional[str] = None,
        start_date: datetime = None,
        end_date: datetime = None,
        doctor_id: Optional[str] = None,
        duration_minutes: int = 30,
        strategy: SchedulingStrategy = SchedulingStrategy.AI_OPTIMIZED
    ) -> List[Dict[str, Any]]:
        """
        Find available time slots for a service within a date range.
        This method is called by ReservationTools and returns simple slot dictionaries.

        Args:
            service_id: Optional service ID (currently unused, for future use)
            start_date: Start date for availability search
            end_date: End date for availability search
            doctor_id: Optional specific doctor ID
            duration_minutes: Duration of the appointment in minutes
            strategy: Scheduling strategy to use

        Returns:
            List of slot dictionaries with 'datetime', 'doctor_id', etc.
        """
        try:
            if not start_date:
                start_date = datetime.now()
            if not end_date:
                end_date = start_date + timedelta(days=7)

            # Calculate days to search
            days_to_search = (end_date - start_date).days + 1
            days_to_search = min(days_to_search, 14)  # Limit to 2 weeks

            # PARALLEL: Get slots for all days concurrently
            async def get_slots_for_day(check_date: str):
                """Fetch slots for a single day."""
                try:
                    day_slots = await self.appointment_service.get_available_slots(
                        doctor_id=doctor_id,
                        date=check_date,
                        duration_minutes=duration_minutes
                    )

                    # Convert TimeSlot objects to dictionaries
                    result_slots = []
                    for slot in day_slots:
                        if hasattr(slot, 'start_time'):
                            slot_dict = {
                                'datetime': slot.start_time.isoformat(),
                                'doctor_id': slot.doctor_id if hasattr(slot, 'doctor_id') else doctor_id,
                                'duration_minutes': duration_minutes,
                                'available': True
                            }
                        elif isinstance(slot, dict):
                            slot_dict = slot
                        else:
                            continue
                        result_slots.append(slot_dict)
                    return result_slots
                except Exception as e:
                    logger.warning(f"Failed to get slots for {check_date}: {e}")
                    return []

            # Create list of dates to check
            dates_to_check = [
                (start_date + timedelta(days=day_offset)).strftime('%Y-%m-%d')
                for day_offset in range(days_to_search)
            ]

            # Fire all day queries in parallel
            import asyncio
            day_results = await asyncio.gather(
                *[get_slots_for_day(date) for date in dates_to_check],
                return_exceptions=True
            )

            # Flatten results
            slots = []
            for result in day_results:
                if isinstance(result, Exception):
                    logger.warning(f"Day slot query failed: {result}")
                    continue
                if result:
                    slots.extend(result)

            return slots

        except Exception as e:
            logger.error(f"Failed to find available slots: {e}")
            return []

    async def get_smart_recommendations(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """
        Get smart scheduling recommendations for testing and direct API access
        This method wraps find_optimal_appointments for easier testing
        """
        try:
            logger.info(f"Getting smart recommendations for patient {request.get('patient_id')}")

            # Extract request parameters
            patient_id = request.get('patient_id')
            appointment_type = AppointmentType(request.get('appointment_type', 'consultation'))
            duration_minutes = request.get('duration_minutes', 30)
            strategy = SchedulingStrategy(request.get('strategy', 'AI_OPTIMIZED'))

            # Get patient preferences if provided
            patient_preferences = None
            if 'patient_preferences' in request:
                prefs = request['patient_preferences']
                patient_preferences = PatientPreferences(
                    preferred_times=prefs.get('preferred_times', []),
                    preferred_days=prefs.get('preferred_days', []),
                    avoid_times=prefs.get('avoid_times', []),
                    max_wait_days=prefs.get('max_wait_days', 14),
                    flexibility_score=prefs.get('flexibility_score', 0.5)
                )

            # Get recommendations using existing method
            recommendations = await self.find_optimal_appointments(
                doctor_id=request.get('doctor_id', 'test-doctor'),
                appointment_type=appointment_type,
                duration_minutes=duration_minutes,
                patient_preferences=patient_preferences,
                urgency_level=request.get('urgency_level', 'normal'),
                strategy=strategy,
                max_recommendations=request.get('max_recommendations', 5)
            )

            # Convert to response format
            response_recommendations = []
            for rec in recommendations:
                response_recommendations.append({
                    "datetime": rec.slot.start_time.isoformat() if hasattr(rec.slot, 'start_time') else datetime.now().isoformat(),
                    "doctor_id": rec.slot.doctor_id if hasattr(rec.slot, 'doctor_id') else 'test-doctor',
                    "confidence": rec.confidence_score,
                    "reasoning": rec.reasoning,
                    "optimization_factors": rec.optimization_factors,
                    "predicted_satisfaction": rec.predicted_satisfaction
                })

            return {
                "success": True,
                "recommendations": response_recommendations,
                "strategy_used": strategy.value,
                "total_recommendations": len(response_recommendations)
            }

        except Exception as e:
            logger.error(f"Error getting smart recommendations: {str(e)}")
            return {
                "success": False,
                "error": str(e),
                "recommendations": []
            }

# Global intelligent scheduler instance
intelligent_scheduler = IntelligentScheduler()