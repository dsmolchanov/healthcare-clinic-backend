"""
Predictive Conflict Prevention System
Implements Phase 4: Intelligent Scheduling with Calendar Awareness
Proactively prevents scheduling conflicts using predictive analytics and ML
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass
from enum import Enum

from .intelligent_scheduler import intelligent_scheduler
from .realtime_conflict_detector import conflict_detector, ConflictType, ConflictSeverity
from .websocket_manager import websocket_manager, NotificationType
from .unified_appointment_service import UnifiedAppointmentService

logger = logging.getLogger(__name__)

class PreventionStrategy(Enum):
    BUFFER_TIME = "buffer_time"
    ALTERNATIVE_SLOTS = "alternative_slots"
    LOAD_BALANCING = "load_balancing"
    EARLY_WARNING = "early_warning"
    AUTO_ADJUST = "auto_adjust"
    PATTERN_BREAKING = "pattern_breaking"

class RiskLevel(Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

@dataclass
class ConflictRisk:
    """Represents a predicted conflict risk"""
    risk_id: str
    doctor_id: str
    risk_level: RiskLevel
    predicted_date: datetime
    conflict_type: ConflictType
    probability: float
    contributing_factors: List[str]
    prevention_strategies: List[PreventionStrategy]
    early_warning_sent: bool = False
    prevented: bool = False

@dataclass
class PreventionAction:
    """Action taken to prevent a conflict"""
    action_id: str
    risk_id: str
    strategy: PreventionStrategy
    description: str
    automatic: bool
    timestamp: datetime
    success: bool = False
    impact_metrics: Dict[str, Any] = None

class PredictiveConflictPrevention:
    """
    Proactive system that predicts and prevents scheduling conflicts
    Uses machine learning patterns and heuristics to identify risks early
    """

    def __init__(self, supabase=None):
        self.supabase = supabase
        self.appointment_service = UnifiedAppointmentService(supabase)

        # Active risk monitoring
        self.active_risks: Dict[str, ConflictRisk] = {}
        self.prevention_actions: Dict[str, PreventionAction] = {}

        # Risk prediction models and thresholds
        self.risk_thresholds = {
            RiskLevel.LOW: 0.3,
            RiskLevel.MEDIUM: 0.5,
            RiskLevel.HIGH: 0.7,
            RiskLevel.CRITICAL: 0.9
        }

        # Prevention strategy effectiveness (learned over time)
        self.strategy_effectiveness = {
            PreventionStrategy.BUFFER_TIME: 0.85,
            PreventionStrategy.ALTERNATIVE_SLOTS: 0.75,
            PreventionStrategy.LOAD_BALANCING: 0.70,
            PreventionStrategy.EARLY_WARNING: 0.60,
            PreventionStrategy.AUTO_ADJUST: 0.80,
            PreventionStrategy.PATTERN_BREAKING: 0.65
        }

        # Background monitoring task
        self.monitoring_task = None

    async def start_predictive_monitoring(self, doctor_ids: List[str] = None):
        """Start continuous predictive monitoring for conflict prevention"""
        try:
            logger.info("Starting predictive conflict prevention monitoring")

            if self.monitoring_task and not self.monitoring_task.done():
                logger.warning("Monitoring already running")
                return

            self.monitoring_task = asyncio.create_task(
                self._continuous_monitoring_loop(doctor_ids)
            )

        except Exception as e:
            logger.error(f"Failed to start predictive monitoring: {e}")

    async def stop_predictive_monitoring(self):
        """Stop predictive monitoring"""
        if self.monitoring_task:
            self.monitoring_task.cancel()
            try:
                await self.monitoring_task
            except asyncio.CancelledError:
                pass
            logger.info("Predictive monitoring stopped")

    async def analyze_booking_request(
        self,
        doctor_id: str,
        requested_time: datetime,
        duration_minutes: int,
        patient_history: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Analyze a booking request for potential conflicts before scheduling
        Returns risk assessment and prevention recommendations
        """
        try:
            logger.info(f"Analyzing booking request for doctor {doctor_id} at {requested_time}")

            end_time = requested_time + timedelta(minutes=duration_minutes)

            # Calculate various risk factors
            risk_factors = await self._calculate_booking_risk_factors(
                doctor_id, requested_time, end_time, patient_history
            )

            # Calculate overall risk score
            overall_risk = self._calculate_overall_risk(risk_factors)

            # Determine risk level
            risk_level = self._determine_risk_level(overall_risk)

            # Generate prevention recommendations
            prevention_recommendations = await self._generate_prevention_recommendations(
                doctor_id, requested_time, risk_factors, risk_level
            )

            analysis = {
                "risk_assessment": {
                    "overall_risk_score": overall_risk,
                    "risk_level": risk_level.value,
                    "risk_factors": risk_factors,
                    "confidence": min(overall_risk + 0.2, 1.0)
                },
                "prevention_recommendations": prevention_recommendations,
                "alternative_suggestions": [],
                "booking_advice": self._generate_booking_advice(risk_level, risk_factors)
            }

            # If risk is high, suggest alternative times
            if risk_level in [RiskLevel.HIGH, RiskLevel.CRITICAL]:
                alternatives = await self._suggest_alternative_times(
                    doctor_id, requested_time, duration_minutes
                )
                analysis["alternative_suggestions"] = alternatives

            return analysis

        except Exception as e:
            logger.error(f"Failed to analyze booking request: {e}")
            return {"error": str(e)}

    async def prevent_predicted_conflict(
        self,
        risk_id: str,
        preferred_strategies: List[PreventionStrategy] = None
    ) -> Dict[str, Any]:
        """
        Execute prevention strategies for a predicted conflict
        """
        try:
            if risk_id not in self.active_risks:
                return {"success": False, "error": "Risk not found"}

            risk = self.active_risks[risk_id]
            logger.info(f"Preventing predicted conflict {risk_id} for doctor {risk.doctor_id}")

            # Select prevention strategies
            strategies_to_use = preferred_strategies or risk.prevention_strategies

            prevention_results = []
            conflict_prevented = False

            for strategy in strategies_to_use:
                action_result = await self._execute_prevention_strategy(risk, strategy)
                prevention_results.append(action_result)

                if action_result["success"]:
                    conflict_prevented = True

                # If one strategy succeeds, we might not need others
                if action_result["success"] and strategy in [
                    PreventionStrategy.AUTO_ADJUST,
                    PreventionStrategy.ALTERNATIVE_SLOTS
                ]:
                    break

            # Update risk status
            if conflict_prevented:
                risk.prevented = True

            # Broadcast prevention notification
            await websocket_manager.broadcast_calendar_conflict(
                conflict_data={
                    "risk_id": risk_id,
                    "prevention_attempted": True,
                    "prevention_successful": conflict_prevented,
                    "strategies_used": [s.value for s in strategies_to_use],
                    "results": prevention_results
                },
                affected_doctors=[risk.doctor_id],
                source="predictive_prevention"
            )

            return {
                "success": conflict_prevented,
                "risk_id": risk_id,
                "strategies_executed": len(strategies_to_use),
                "prevention_results": prevention_results,
                "conflict_prevented": conflict_prevented
            }

        except Exception as e:
            logger.error(f"Failed to prevent predicted conflict: {e}")
            return {"success": False, "error": str(e)}

    async def learn_from_outcome(
        self,
        risk_id: str,
        actual_outcome: str,
        conflict_occurred: bool,
        patient_satisfaction: Optional[float] = None
    ):
        """
        Learn from actual outcomes to improve prediction accuracy
        This is where the ML model would be updated
        """
        try:
            logger.info(f"Learning from outcome for risk {risk_id}: conflict={conflict_occurred}")

            if risk_id not in self.active_risks:
                return

            risk = self.active_risks[risk_id]

            # Update strategy effectiveness based on outcomes
            for action_id, action in self.prevention_actions.items():
                if action.risk_id == risk_id:
                    if conflict_occurred:
                        # Strategy didn't work as expected
                        current_effectiveness = self.strategy_effectiveness[action.strategy]
                        self.strategy_effectiveness[action.strategy] = max(
                            current_effectiveness * 0.95, 0.1  # Slight decrease, minimum 0.1
                        )
                    else:
                        # Strategy worked well
                        current_effectiveness = self.strategy_effectiveness[action.strategy]
                        self.strategy_effectiveness[action.strategy] = min(
                            current_effectiveness * 1.05, 1.0  # Slight increase, maximum 1.0
                        )

            # Store learning data for future ML model training
            learning_data = {
                "risk_id": risk_id,
                "predicted_probability": risk.probability,
                "actual_conflict": conflict_occurred,
                "prevention_attempted": risk_id in [a.risk_id for a in self.prevention_actions.values()],
                "patient_satisfaction": patient_satisfaction,
                "outcome": actual_outcome,
                "timestamp": datetime.now().isoformat()
            }

            # In a real implementation, this would be stored in a database
            # for machine learning model training
            logger.info(f"Learning data collected: {learning_data}")

        except Exception as e:
            logger.error(f"Failed to learn from outcome: {e}")

    # Private helper methods

    async def _continuous_monitoring_loop(self, doctor_ids: List[str] = None):
        """Continuous monitoring loop for predictive conflict prevention"""
        try:
            while True:
                await self._run_prediction_cycle(doctor_ids)
                await asyncio.sleep(300)  # Run every 5 minutes

        except asyncio.CancelledError:
            logger.info("Monitoring loop cancelled")
        except Exception as e:
            logger.error(f"Monitoring loop error: {e}")

    async def _run_prediction_cycle(self, doctor_ids: List[str] = None):
        """Run a single prediction cycle"""
        try:
            # Get list of doctors to monitor
            if not doctor_ids:
                # In a real implementation, get all active doctors
                doctor_ids = ["11111111-1111-1111-1111-111111111111"]  # Sample

            for doctor_id in doctor_ids:
                await self._predict_conflicts_for_doctor(doctor_id)

        except Exception as e:
            logger.error(f"Prediction cycle error: {e}")

    async def _predict_conflicts_for_doctor(self, doctor_id: str):
        """Predict conflicts for a specific doctor"""
        try:
            # Look ahead 7 days
            start_date = datetime.now()
            end_date = start_date + timedelta(days=7)

            # Get existing predictions from intelligent scheduler
            predictions = await intelligent_scheduler.predict_scheduling_conflicts(
                doctor_id=doctor_id,
                date_range=(start_date, end_date),
                probability_threshold=0.4  # Lower threshold for early detection
            )

            # Convert predictions to risks and generate prevention strategies
            for prediction in predictions:
                risk_id = f"risk_{doctor_id}_{prediction['date']}_{int(datetime.now().timestamp())}"

                if risk_id not in self.active_risks:
                    risk = ConflictRisk(
                        risk_id=risk_id,
                        doctor_id=doctor_id,
                        risk_level=self._determine_risk_level(prediction['risk_score']),
                        predicted_date=datetime.fromisoformat(prediction['date']),
                        conflict_type=ConflictType(prediction.get('predicted_conflict_type', 'double_booking')),
                        probability=prediction['risk_score'],
                        contributing_factors=prediction.get('contributing_factors', []),
                        prevention_strategies=await self._select_prevention_strategies(prediction)
                    )

                    self.active_risks[risk_id] = risk

                    # Send early warning if risk is medium or higher
                    if risk.risk_level in [RiskLevel.MEDIUM, RiskLevel.HIGH, RiskLevel.CRITICAL]:
                        await self._send_early_warning(risk)

        except Exception as e:
            logger.error(f"Failed to predict conflicts for doctor {doctor_id}: {e}")

    async def _calculate_booking_risk_factors(
        self,
        doctor_id: str,
        start_time: datetime,
        end_time: datetime,
        patient_history: Optional[Dict[str, Any]]
    ) -> Dict[str, float]:
        """Calculate various risk factors for a booking request"""
        try:
            risk_factors = {}

            # Schedule density risk
            density = await self._calculate_schedule_density_risk(doctor_id, start_time)
            risk_factors["schedule_density"] = density

            # Time slot risk (based on historical conflicts)
            time_risk = await self._calculate_time_slot_risk(doctor_id, start_time)
            risk_factors["time_slot_risk"] = time_risk

            # Patient history risk
            if patient_history:
                patient_risk = self._calculate_patient_history_risk(patient_history)
                risk_factors["patient_history"] = patient_risk
            else:
                risk_factors["patient_history"] = 0.3  # Unknown patient, moderate risk

            # External calendar sync risk
            sync_risk = await self._calculate_sync_risk(doctor_id, start_time)
            risk_factors["external_sync"] = sync_risk

            # Day of week patterns
            day_risk = self._calculate_day_of_week_risk(start_time)
            risk_factors["day_of_week"] = day_risk

            return risk_factors

        except Exception as e:
            logger.error(f"Error calculating risk factors: {e}")
            return {"error": 1.0}

    def _calculate_overall_risk(self, risk_factors: Dict[str, float]) -> float:
        """Calculate overall risk score from individual factors"""
        if "error" in risk_factors:
            return 0.5  # Default moderate risk

        # Weighted combination of risk factors
        weights = {
            "schedule_density": 0.3,
            "time_slot_risk": 0.25,
            "patient_history": 0.15,
            "external_sync": 0.2,
            "day_of_week": 0.1
        }

        total_risk = 0.0
        total_weight = 0.0

        for factor, risk_value in risk_factors.items():
            if factor in weights:
                total_risk += risk_value * weights[factor]
                total_weight += weights[factor]

        return total_risk / max(total_weight, 0.1)

    def _determine_risk_level(self, risk_score: float) -> RiskLevel:
        """Determine risk level from risk score"""
        if risk_score >= self.risk_thresholds[RiskLevel.CRITICAL]:
            return RiskLevel.CRITICAL
        elif risk_score >= self.risk_thresholds[RiskLevel.HIGH]:
            return RiskLevel.HIGH
        elif risk_score >= self.risk_thresholds[RiskLevel.MEDIUM]:
            return RiskLevel.MEDIUM
        else:
            return RiskLevel.LOW

    async def _generate_prevention_recommendations(
        self,
        doctor_id: str,
        requested_time: datetime,
        risk_factors: Dict[str, float],
        risk_level: RiskLevel
    ) -> List[Dict[str, Any]]:
        """Generate prevention recommendations based on risk analysis"""
        recommendations = []

        try:
            if risk_level == RiskLevel.LOW:
                recommendations.append({
                    "strategy": "monitor",
                    "description": "Continue with normal booking, monitor for changes",
                    "priority": "low",
                    "automatic": True
                })

            elif risk_level == RiskLevel.MEDIUM:
                recommendations.extend([
                    {
                        "strategy": "buffer_time",
                        "description": "Add 5-minute buffer before and after appointment",
                        "priority": "medium",
                        "automatic": True
                    },
                    {
                        "strategy": "early_warning",
                        "description": "Set up early warning notifications",
                        "priority": "medium",
                        "automatic": True
                    }
                ])

            elif risk_level == RiskLevel.HIGH:
                recommendations.extend([
                    {
                        "strategy": "alternative_slots",
                        "description": "Suggest alternative time slots with lower risk",
                        "priority": "high",
                        "automatic": False
                    },
                    {
                        "strategy": "load_balancing",
                        "description": "Redistribute appointments to balance load",
                        "priority": "high",
                        "automatic": True
                    },
                    {
                        "strategy": "early_warning",
                        "description": "Immediate notification to clinic staff",
                        "priority": "high",
                        "automatic": True
                    }
                ])

            else:  # CRITICAL
                recommendations.extend([
                    {
                        "strategy": "block_booking",
                        "description": "Block this time slot and suggest alternatives",
                        "priority": "critical",
                        "automatic": False
                    },
                    {
                        "strategy": "manual_review",
                        "description": "Require manual review before booking",
                        "priority": "critical",
                        "automatic": False
                    },
                    {
                        "strategy": "immediate_adjustment",
                        "description": "Immediately adjust surrounding appointments",
                        "priority": "critical",
                        "automatic": True
                    }
                ])

        except Exception as e:
            logger.error(f"Error generating prevention recommendations: {e}")

        return recommendations

    def _generate_booking_advice(self, risk_level: RiskLevel, risk_factors: Dict[str, float]) -> str:
        """Generate human-readable booking advice"""
        if risk_level == RiskLevel.LOW:
            return "This time slot appears safe for booking with minimal conflict risk."

        elif risk_level == RiskLevel.MEDIUM:
            return "Moderate conflict risk detected. Consider adding buffer time or monitoring closely."

        elif risk_level == RiskLevel.HIGH:
            high_risk_factors = [factor for factor, value in risk_factors.items() if value > 0.7]
            return f"High conflict risk due to: {', '.join(high_risk_factors)}. Alternative times recommended."

        else:  # CRITICAL
            return "Critical conflict risk detected. This time slot should be avoided. Manual review required."

    async def _suggest_alternative_times(
        self,
        doctor_id: str,
        requested_time: datetime,
        duration_minutes: int
    ) -> List[Dict[str, Any]]:
        """Suggest alternative time slots with lower risk"""
        try:
            alternatives = []

            # Get recommendations from intelligent scheduler
            recommendations = await intelligent_scheduler.find_optimal_appointments(
                doctor_id=doctor_id,
                appointment_type=None,  # Any type
                duration_minutes=duration_minutes,
                max_recommendations=3
            )

            for rec in recommendations:
                # Calculate risk for this alternative
                alt_risk_factors = await self._calculate_booking_risk_factors(
                    doctor_id, rec.slot.start_time, rec.slot.end_time, None
                )
                alt_risk_score = self._calculate_overall_risk(alt_risk_factors)

                alternatives.append({
                    "start_time": rec.slot.start_time.isoformat(),
                    "end_time": rec.slot.end_time.isoformat(),
                    "risk_score": alt_risk_score,
                    "confidence": rec.confidence_score,
                    "reasoning": rec.reasoning
                })

            return sorted(alternatives, key=lambda x: x["risk_score"])

        except Exception as e:
            logger.error(f"Error suggesting alternative times: {e}")
            return []

    # Additional helper method implementations (simplified)

    async def _calculate_schedule_density_risk(self, doctor_id: str, time: datetime) -> float:
        """Calculate risk based on schedule density around the requested time"""
        # Simplified implementation
        return 0.4

    async def _calculate_time_slot_risk(self, doctor_id: str, time: datetime) -> float:
        """Calculate risk based on historical conflicts at this time"""
        # Simplified implementation
        hour = time.hour
        if hour in [8, 12, 17]:  # High-risk hours
            return 0.7
        return 0.3

    def _calculate_patient_history_risk(self, patient_history: Dict[str, Any]) -> float:
        """Calculate risk based on patient's history"""
        no_show_rate = patient_history.get("no_show_rate", 0.1)
        cancellation_rate = patient_history.get("cancellation_rate", 0.05)
        return min((no_show_rate + cancellation_rate) * 2, 1.0)

    async def _calculate_sync_risk(self, doctor_id: str, time: datetime) -> float:
        """Calculate risk based on external calendar sync status"""
        # Simplified implementation
        return 0.2

    def _calculate_day_of_week_risk(self, time: datetime) -> float:
        """Calculate risk based on day of the week patterns"""
        day = time.weekday()
        if day == 0:  # Monday
            return 0.6  # Higher risk due to weekend backlog
        elif day == 4:  # Friday
            return 0.5  # Moderate risk
        return 0.3

    async def _select_prevention_strategies(self, prediction: Dict[str, Any]) -> List[PreventionStrategy]:
        """Select appropriate prevention strategies for a prediction"""
        strategies = [PreventionStrategy.EARLY_WARNING]

        risk_score = prediction.get("risk_score", 0.5)
        if risk_score > 0.7:
            strategies.extend([
                PreventionStrategy.BUFFER_TIME,
                PreventionStrategy.ALTERNATIVE_SLOTS
            ])

        if risk_score > 0.8:
            strategies.append(PreventionStrategy.AUTO_ADJUST)

        return strategies

    async def _send_early_warning(self, risk: ConflictRisk):
        """Send early warning notification about predicted conflict"""
        try:
            await websocket_manager.broadcast_calendar_conflict(
                conflict_data={
                    "type": "early_warning",
                    "risk_id": risk.risk_id,
                    "predicted_date": risk.predicted_date.isoformat(),
                    "risk_level": risk.risk_level.value,
                    "probability": risk.probability,
                    "prevention_needed": True
                },
                affected_doctors=[risk.doctor_id],
                source="predictive_prevention"
            )

            risk.early_warning_sent = True
            logger.info(f"Early warning sent for risk {risk.risk_id}")

        except Exception as e:
            logger.error(f"Failed to send early warning: {e}")

    async def _execute_prevention_strategy(
        self,
        risk: ConflictRisk,
        strategy: PreventionStrategy
    ) -> Dict[str, Any]:
        """Execute a specific prevention strategy"""
        try:
            action_id = f"action_{risk.risk_id}_{strategy.value}_{int(datetime.now().timestamp())}"

            action = PreventionAction(
                action_id=action_id,
                risk_id=risk.risk_id,
                strategy=strategy,
                description=f"Executing {strategy.value} for risk {risk.risk_id}",
                automatic=True,
                timestamp=datetime.now()
            )

            if strategy == PreventionStrategy.BUFFER_TIME:
                # Add buffer time around appointments
                success = await self._add_buffer_time(risk)
                action.success = success

            elif strategy == PreventionStrategy.ALTERNATIVE_SLOTS:
                # Suggest alternative slots
                alternatives = await self._suggest_alternative_times(
                    risk.doctor_id, risk.predicted_date, 30
                )
                action.success = len(alternatives) > 0
                action.impact_metrics = {"alternatives_found": len(alternatives)}

            elif strategy == PreventionStrategy.AUTO_ADJUST:
                # Automatically adjust surrounding appointments
                success = await self._auto_adjust_schedule(risk)
                action.success = success

            else:
                # Other strategies (simplified)
                action.success = True

            self.prevention_actions[action_id] = action

            return {
                "action_id": action_id,
                "strategy": strategy.value,
                "success": action.success,
                "description": action.description,
                "impact_metrics": action.impact_metrics or {}
            }

        except Exception as e:
            logger.error(f"Failed to execute prevention strategy {strategy}: {e}")
            return {"success": False, "error": str(e)}

    async def _add_buffer_time(self, risk: ConflictRisk) -> bool:
        """Add buffer time around the risky appointment"""
        # Simplified implementation
        logger.info(f"Adding buffer time for risk {risk.risk_id}")
        return True

    async def _auto_adjust_schedule(self, risk: ConflictRisk) -> bool:
        """Automatically adjust schedule to prevent conflict"""
        # Simplified implementation
        logger.info(f"Auto-adjusting schedule for risk {risk.risk_id}")
        return True

# Global predictive conflict prevention instance
predictive_prevention = PredictiveConflictPrevention()