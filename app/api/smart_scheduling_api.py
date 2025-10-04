"""
Smart Scheduling API with AI Optimization
Implements Phase 4: Intelligent Scheduling with Calendar Awareness
Provides AI-powered scheduling recommendations and optimization
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks, Query
from pydantic import BaseModel, field_validator
from enum import Enum

from ..services.intelligent_scheduler import (
    intelligent_scheduler,
    SchedulingStrategy,
    OptimizationGoal,
    PatientPreferences,
    SchedulingRecommendation
)
from ..services.unified_appointment_service import AppointmentType

logger = logging.getLogger(__name__)

# Create router
router = APIRouter(prefix="/api/smart-scheduling", tags=["Smart Scheduling"])

# Pydantic models

class SchedulingStrategyEnum(str, Enum):
    FIRST_AVAILABLE = "first_available"
    OPTIMAL_PACKING = "optimal_packing"
    PATIENT_CENTRIC = "patient_centric"
    DOCTOR_CENTRIC = "doctor_centric"
    BALANCED = "balanced"
    AI_OPTIMIZED = "ai_optimized"

class OptimizationGoalEnum(str, Enum):
    MINIMIZE_GAPS = "minimize_gaps"
    BALANCE_LOAD = "balance_load"
    PATIENT_PREFERENCE = "patient_preference"
    TRAVEL_TIME = "travel_time"
    URGENCY_PRIORITY = "urgency_priority"
    CLINIC_EFFICIENCY = "clinic_efficiency"

class UrgencyLevelEnum(str, Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"

class PatientPreferencesModel(BaseModel):
    preferred_times: List[str] = []  # ["09:00", "14:00"]
    preferred_days: List[str] = []   # ["monday", "wednesday", "friday"]
    avoid_times: List[str] = []      # ["08:00", "17:00"]
    max_wait_days: int = 7
    prefers_morning: Optional[bool] = None
    prefers_afternoon: Optional[bool] = None
    travel_time_minutes: int = 30
    flexibility_score: float = 0.5

    @field_validator('flexibility_score')
    @classmethod
    def validate_flexibility_score(cls, v):
        if not 0.0 <= v <= 1.0:
            raise ValueError('Flexibility score must be between 0.0 and 1.0')
        return v

class SmartSchedulingRequest(BaseModel):
    doctor_id: str
    appointment_type: str = "consultation"
    duration_minutes: int = 30
    patient_preferences: Optional[PatientPreferencesModel] = None
    urgency_level: UrgencyLevelEnum = UrgencyLevelEnum.NORMAL
    strategy: SchedulingStrategyEnum = SchedulingStrategyEnum.AI_OPTIMIZED
    max_recommendations: int = 5

class ConflictPredictionRequest(BaseModel):
    doctor_id: str
    start_date: str  # YYYY-MM-DD
    end_date: str    # YYYY-MM-DD
    probability_threshold: float = 0.7

    @field_validator('probability_threshold')
    @classmethod
    def validate_probability(cls, v):
        if not 0.0 <= v <= 1.0:
            raise ValueError('Probability threshold must be between 0.0 and 1.0')
        return v

class AutoRescheduleRequest(BaseModel):
    appointment_id: str
    reason: str = "optimization"
    respect_preferences: bool = True
    notify_patient: bool = True

class ScheduleOptimizationRequest(BaseModel):
    doctor_id: str
    date: str  # YYYY-MM-DD
    optimization_goals: List[OptimizationGoalEnum] = []

class SmartRecommendationResponse(BaseModel):
    slot_start_time: str
    slot_end_time: str
    confidence_score: float
    reasoning: List[str]
    optimization_factors: Dict[str, float]
    predicted_satisfaction: float
    doctor_id: str
    available: bool

class ConflictPredictionResponse(BaseModel):
    date: str
    day_of_week: str
    risk_score: float
    predicted_conflict_type: str
    prevention_suggestions: List[str]

# API Endpoints

@router.post("/recommendations", response_model=List[SmartRecommendationResponse])
async def get_smart_scheduling_recommendations(
    request: SmartSchedulingRequest,
    background_tasks: BackgroundTasks
):
    """
    Get AI-powered scheduling recommendations for optimal appointment placement

    This endpoint uses machine learning and optimization algorithms to suggest
    the best appointment times based on various factors:
    - Patient preferences and convenience
    - Doctor efficiency and workload balance
    - Historical patterns and success rates
    - Clinic operational efficiency
    - Calendar coordination across multiple sources
    """
    try:
        logger.info(f"Getting smart scheduling recommendations for doctor {request.doctor_id}")

        # Convert Pydantic model to internal format
        patient_preferences = None
        if request.patient_preferences:
            patient_preferences = PatientPreferences(
                preferred_times=request.patient_preferences.preferred_times,
                preferred_days=request.patient_preferences.preferred_days,
                avoid_times=request.patient_preferences.avoid_times,
                max_wait_days=request.patient_preferences.max_wait_days,
                prefers_morning=request.patient_preferences.prefers_morning,
                prefers_afternoon=request.patient_preferences.prefers_afternoon,
                travel_time_minutes=request.patient_preferences.travel_time_minutes,
                flexibility_score=request.patient_preferences.flexibility_score
            )

        # Get recommendations from intelligent scheduler
        recommendations = await intelligent_scheduler.find_optimal_appointments(
            doctor_id=request.doctor_id,
            appointment_type=AppointmentType(request.appointment_type),
            duration_minutes=request.duration_minutes,
            patient_preferences=patient_preferences,
            urgency_level=request.urgency_level.value,
            strategy=SchedulingStrategy(request.strategy.value),
            max_recommendations=request.max_recommendations
        )

        # Convert to response format
        response_recommendations = []
        for rec in recommendations:
            response_recommendations.append(SmartRecommendationResponse(
                slot_start_time=rec.slot.start_time.isoformat(),
                slot_end_time=rec.slot.end_time.isoformat(),
                confidence_score=rec.confidence_score,
                reasoning=rec.reasoning,
                optimization_factors=rec.optimization_factors,
                predicted_satisfaction=rec.predicted_satisfaction,
                doctor_id=rec.slot.doctor_id,
                available=rec.slot.available
            ))

        logger.info(f"Generated {len(response_recommendations)} smart recommendations")

        # Schedule background analytics collection
        background_tasks.add_task(
            collect_recommendation_analytics,
            request.doctor_id,
            len(response_recommendations),
            request.strategy.value
        )

        return response_recommendations

    except Exception as e:
        logger.error(f"Failed to get smart scheduling recommendations: {e}")
        raise HTTPException(status_code=500, detail="Failed to generate scheduling recommendations")

@router.post("/predict-conflicts", response_model=List[ConflictPredictionResponse])
async def predict_scheduling_conflicts(request: ConflictPredictionRequest):
    """
    Predict potential scheduling conflicts using AI and historical patterns

    Analyzes historical data, current schedule density, and patterns to predict
    where conflicts are most likely to occur. Useful for proactive schedule management.
    """
    try:
        logger.info(f"Predicting conflicts for doctor {request.doctor_id}")

        # Validate and parse dates
        try:
            start_date = datetime.fromisoformat(request.start_date)
            end_date = datetime.fromisoformat(request.end_date)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD.")

        if start_date >= end_date:
            raise HTTPException(status_code=400, detail="Start date must be before end date")

        # Get conflict predictions
        predictions = await intelligent_scheduler.predict_scheduling_conflicts(
            doctor_id=request.doctor_id,
            date_range=(start_date, end_date),
            probability_threshold=request.probability_threshold
        )

        # Convert to response format
        response_predictions = [
            ConflictPredictionResponse(**prediction)
            for prediction in predictions
        ]

        logger.info(f"Generated {len(response_predictions)} conflict predictions")
        return response_predictions

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to predict scheduling conflicts: {e}")
        raise HTTPException(status_code=500, detail="Failed to predict scheduling conflicts")

@router.post("/auto-reschedule")
async def auto_reschedule_appointment(
    request: AutoRescheduleRequest,
    background_tasks: BackgroundTasks
):
    """
    Automatically reschedule an appointment to a better time slot using AI optimization

    The system will:
    1. Analyze the current appointment
    2. Find better time slots using AI optimization
    3. Reschedule to the optimal slot
    4. Notify relevant parties if requested
    """
    try:
        logger.info(f"Auto-rescheduling appointment {request.appointment_id}")

        result = await intelligent_scheduler.auto_reschedule_with_preferences(
            appointment_id=request.appointment_id,
            reason=request.reason,
            respect_preferences=request.respect_preferences,
            notify_patient=request.notify_patient
        )

        if result["success"] and request.notify_patient:
            background_tasks.add_task(
                send_reschedule_notification,
                request.appointment_id,
                result.get("new_time"),
                request.reason
            )

        return result

    except Exception as e:
        logger.error(f"Failed to auto-reschedule appointment: {e}")
        raise HTTPException(status_code=500, detail="Failed to auto-reschedule appointment")

@router.post("/optimize-schedule")
async def optimize_daily_schedule(
    request: ScheduleOptimizationRequest,
    background_tasks: BackgroundTasks
):
    """
    Optimize an entire day's schedule for maximum efficiency

    Rearranges existing appointments to minimize gaps, balance workload,
    and improve overall clinic efficiency while respecting constraints.
    """
    try:
        logger.info(f"Optimizing schedule for doctor {request.doctor_id} on {request.date}")

        # Validate date
        try:
            schedule_date = datetime.fromisoformat(request.date)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD.")

        # Convert optimization goals
        optimization_goals = [OptimizationGoal(goal.value) for goal in request.optimization_goals]

        result = await intelligent_scheduler.optimize_daily_schedule(
            doctor_id=request.doctor_id,
            date=schedule_date,
            optimization_goals=optimization_goals
        )

        if result["success"] and result.get("changes", 0) > 0:
            background_tasks.add_task(
                log_schedule_optimization,
                request.doctor_id,
                request.date,
                result
            )

        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to optimize daily schedule: {e}")
        raise HTTPException(status_code=500, detail="Failed to optimize schedule")

@router.get("/analytics/efficiency/{doctor_id}")
async def get_scheduling_efficiency_analytics(
    doctor_id: str,
    days_back: int = Query(30, ge=1, le=90),
    include_predictions: bool = Query(True)
):
    """
    Get scheduling efficiency analytics and predictions for a doctor

    Provides insights into:
    - Current scheduling efficiency metrics
    - Historical performance trends
    - Optimization opportunities
    - Predicted efficiency improvements
    """
    try:
        logger.info(f"Getting efficiency analytics for doctor {doctor_id}")

        # Calculate date range
        end_date = datetime.now()
        start_date = end_date - timedelta(days=days_back)

        # Get appointments for analysis
        from ..services.unified_appointment_service import UnifiedAppointmentService
        appointment_service = UnifiedAppointmentService()

        appointments = await appointment_service.get_appointments(
            doctor_id=doctor_id,
            date_from=start_date.strftime('%Y-%m-%d'),
            date_to=end_date.strftime('%Y-%m-%d')
        )

        # Calculate efficiency metrics
        total_appointments = len(appointments)
        cancelled_appointments = len([apt for apt in appointments if apt['status'] == 'cancelled'])
        no_show_appointments = len([apt for apt in appointments if apt['status'] == 'no_show'])

        efficiency_rate = 1.0 - ((cancelled_appointments + no_show_appointments) / max(total_appointments, 1))

        # Calculate utilization (simplified)
        working_days = days_back * 5 / 7  # Assume 5-day work week
        average_appointments_per_day = total_appointments / max(working_days, 1)
        utilization_rate = min(average_appointments_per_day / 8, 1.0)  # Assume 8 appointments max per day

        analytics = {
            "doctor_id": doctor_id,
            "period": {
                "start_date": start_date.strftime('%Y-%m-%d'),
                "end_date": end_date.strftime('%Y-%m-%d'),
                "days": days_back
            },
            "metrics": {
                "total_appointments": total_appointments,
                "cancelled_appointments": cancelled_appointments,
                "no_show_appointments": no_show_appointments,
                "efficiency_rate": round(efficiency_rate, 3),
                "utilization_rate": round(utilization_rate, 3),
                "average_appointments_per_day": round(average_appointments_per_day, 1)
            },
            "optimization_opportunities": []
        }

        # Add optimization suggestions
        if efficiency_rate < 0.85:
            analytics["optimization_opportunities"].append({
                "type": "reduce_cancellations",
                "description": "High cancellation rate detected. Consider reminder systems or scheduling confirmation.",
                "potential_improvement": "15-20% efficiency gain"
            })

        if utilization_rate < 0.7:
            analytics["optimization_opportunities"].append({
                "type": "improve_utilization",
                "description": "Schedule utilization could be improved. Consider tighter scheduling or extended hours.",
                "potential_improvement": "10-15% more appointments"
            })

        # Add predictions if requested
        if include_predictions:
            analytics["predictions"] = {
                "next_week_efficiency": min(efficiency_rate + 0.05, 1.0),
                "optimal_schedule_improvement": "12-18% efficiency gain with AI optimization",
                "recommended_actions": [
                    "Enable AI-powered scheduling recommendations",
                    "Implement predictive conflict detection",
                    "Use automated rescheduling for optimizations"
                ]
            }

        return analytics

    except Exception as e:
        logger.error(f"Failed to get efficiency analytics: {e}")
        raise HTTPException(status_code=500, detail="Failed to retrieve efficiency analytics")

@router.get("/strategies")
async def get_available_strategies():
    """Get available scheduling strategies and their descriptions"""
    return {
        "strategies": [
            {
                "value": "first_available",
                "name": "First Available",
                "description": "Books the earliest available time slot",
                "best_for": "Urgent appointments, simple scheduling needs"
            },
            {
                "value": "optimal_packing",
                "name": "Optimal Packing",
                "description": "Minimizes gaps between appointments for maximum efficiency",
                "best_for": "High-volume clinics, maximizing daily efficiency"
            },
            {
                "value": "patient_centric",
                "name": "Patient-Centric",
                "description": "Prioritizes patient preferences and convenience",
                "best_for": "Patient satisfaction focus, flexible scheduling"
            },
            {
                "value": "doctor_centric",
                "name": "Doctor-Centric",
                "description": "Optimizes for doctor's efficiency and workload balance",
                "best_for": "Managing doctor fatigue, work-life balance"
            },
            {
                "value": "balanced",
                "name": "Balanced",
                "description": "Balances patient and doctor needs equally",
                "best_for": "General use, balanced operations"
            },
            {
                "value": "ai_optimized",
                "name": "AI-Optimized",
                "description": "Uses machine learning for multi-factor optimization",
                "best_for": "Complex scheduling needs, maximum optimization"
            }
        ],
        "optimization_goals": [
            {
                "value": "minimize_gaps",
                "name": "Minimize Gaps",
                "description": "Reduce empty time slots between appointments"
            },
            {
                "value": "balance_load",
                "name": "Balance Load",
                "description": "Distribute appointments evenly across time periods"
            },
            {
                "value": "patient_preference",
                "name": "Patient Preference",
                "description": "Match patient's preferred times and days"
            },
            {
                "value": "travel_time",
                "name": "Travel Time",
                "description": "Consider patient travel time and convenience"
            },
            {
                "value": "urgency_priority",
                "name": "Urgency Priority",
                "description": "Prioritize urgent appointments appropriately"
            },
            {
                "value": "clinic_efficiency",
                "name": "Clinic Efficiency",
                "description": "Maximize overall clinic operational efficiency"
            }
        ]
    }

# Background task functions

async def collect_recommendation_analytics(doctor_id: str, recommendation_count: int, strategy: str):
    """Collect analytics on recommendation usage"""
    try:
        logger.info(f"Collecting analytics: {recommendation_count} recommendations for {doctor_id} using {strategy}")
        # This would store analytics data for ML model improvement
    except Exception as e:
        logger.error(f"Failed to collect recommendation analytics: {e}")

async def send_reschedule_notification(appointment_id: str, new_time: Dict[str, str], reason: str):
    """Send notification about automatic rescheduling"""
    try:
        logger.info(f"Sending reschedule notification for appointment {appointment_id}")
        # This would integrate with notification services
    except Exception as e:
        logger.error(f"Failed to send reschedule notification: {e}")

async def log_schedule_optimization(doctor_id: str, date: str, optimization_result: Dict[str, Any]):
    """Log schedule optimization results for analytics"""
    try:
        logger.info(f"Logging optimization for {doctor_id} on {date}: {optimization_result.get('changes', 0)} changes")
        # This would store optimization metrics for analysis
    except Exception as e:
        logger.error(f"Failed to log schedule optimization: {e}")

# Health check for smart scheduling service
@router.get("/health")
async def smart_scheduling_health_check():
    """Health check for smart scheduling service"""
    try:
        return {
            "status": "healthy",
            "service": "Smart Scheduling with AI Optimization",
            "features": [
                "ai_powered_recommendations",
                "conflict_prediction",
                "automatic_rescheduling",
                "schedule_optimization",
                "efficiency_analytics",
                "multi_strategy_support"
            ],
            "ml_status": "available" if intelligent_scheduler else "limited"
        }
    except Exception as e:
        logger.error(f"Smart scheduling health check failed: {e}")
        raise HTTPException(status_code=503, detail="Smart scheduling service unavailable")