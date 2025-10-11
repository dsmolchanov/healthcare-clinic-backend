"""
Preference Scorer for Scheduling.

Scores slots based on soft preferences and clinic policies.
"""

import logging
from datetime import datetime, time, timedelta
from typing import Dict, List, Any, Optional
from uuid import UUID

logger = logging.getLogger(__name__)


class PreferenceScorer:
    """
    Scores appointment slots based on soft preferences.

    Soft preferences are weighted scoring factors that affect slot ranking
    but don't eliminate slots. Higher scores indicate better slots.
    """

    def __init__(self, settings: Dict[str, Any]):
        """
        Initialize preference scorer with clinic settings.

        Args:
            settings: Scheduling settings from sched_settings table
        """
        self.settings = settings
        self.weights = settings.get("preference_weights", {})

        # Default weights if not configured
        self.weights.setdefault("least_busy", 0.3)
        self.weights.setdefault("pack_schedule", 0.25)
        self.weights.setdefault("room_preference", 0.2)
        self.weights.setdefault("time_of_day", 0.15)
        self.weights.setdefault("patient_preference", 0.1)

    def score_least_busy(
        self,
        doctor_id: UUID,
        slot_time: datetime,
        doctor_appointments: Dict[UUID, List[Dict]]
    ) -> float:
        """
        Score based on doctor workload - prefer less busy doctors.

        Args:
            doctor_id: Doctor UUID
            slot_time: Proposed slot time
            doctor_appointments: Dict mapping doctor_id to list of appointments

        Returns:
            Score 0.0-1.0 (higher = less busy = better)
        """
        try:
            appointments = doctor_appointments.get(doctor_id, [])

            # Count appointments on the same day
            slot_date = slot_time.date()
            same_day_count = sum(
                1 for apt in appointments
                if datetime.fromisoformat(apt["start_time"]).date() == slot_date
            )

            # Normalize: 0 appointments = 1.0, 8+ appointments = 0.0
            max_appointments = 8
            score = max(0.0, 1.0 - (same_day_count / max_appointments))

            logger.debug(
                f"Doctor {doctor_id} has {same_day_count} appointments on {slot_date}, "
                f"least_busy score: {score:.2f}"
            )
            return score

        except Exception as e:
            logger.error(f"Error scoring least_busy: {e}")
            return 0.5  # Neutral score on error

    def score_pack_schedule(
        self,
        doctor_id: UUID,
        slot_time: datetime,
        doctor_appointments: Dict[UUID, List[Dict]],
        duration_minutes: int = 30
    ) -> float:
        """
        Score based on schedule packing - prefer slots adjacent to existing appointments.

        Args:
            doctor_id: Doctor UUID
            slot_time: Proposed slot time
            doctor_appointments: Dict mapping doctor_id to list of appointments
            duration_minutes: Appointment duration

        Returns:
            Score 0.0-1.0 (higher = more tightly packed = better)
        """
        try:
            appointments = doctor_appointments.get(doctor_id, [])
            slot_end = slot_time + timedelta(minutes=duration_minutes)

            # Find adjacent appointments (within 1 hour before/after)
            adjacent_count = 0
            buffer_hours = 1

            for apt in appointments:
                apt_start = datetime.fromisoformat(apt["start_time"])
                apt_end = datetime.fromisoformat(apt["end_time"])

                # Check if appointment is adjacent (within buffer)
                time_before = (slot_time - apt_end).total_seconds() / 3600
                time_after = (apt_start - slot_end).total_seconds() / 3600

                if 0 < time_before <= buffer_hours or 0 < time_after <= buffer_hours:
                    adjacent_count += 1

            # Normalize: 2+ adjacent = 1.0, 0 adjacent = 0.0
            score = min(1.0, adjacent_count / 2.0)

            logger.debug(
                f"Doctor {doctor_id} has {adjacent_count} adjacent appointments, "
                f"pack_schedule score: {score:.2f}"
            )
            return score

        except Exception as e:
            logger.error(f"Error scoring pack_schedule: {e}")
            return 0.5

    def score_room_preference(
        self,
        doctor_id: UUID,
        room_id: UUID,
        room_preferences: Dict[UUID, UUID]
    ) -> float:
        """
        Score based on doctor's room preference.

        Args:
            doctor_id: Doctor UUID
            room_id: Proposed room UUID
            room_preferences: Dict mapping doctor_id to preferred room_id

        Returns:
            Score 0.0-1.0 (1.0 if preferred room, 0.5 otherwise)
        """
        try:
            preferred_room = room_preferences.get(doctor_id)

            if preferred_room and preferred_room == room_id:
                logger.debug(f"Room {room_id} is preferred for doctor {doctor_id}")
                return 1.0
            else:
                return 0.5  # Neutral if not preferred

        except Exception as e:
            logger.error(f"Error scoring room_preference: {e}")
            return 0.5

    def score_time_of_day(
        self,
        slot_time: datetime,
        patient_preferences: Optional[Dict[str, Any]] = None
    ) -> float:
        """
        Score based on time of day preferences.

        Args:
            slot_time: Proposed slot time
            patient_preferences: Optional patient preferences (e.g., "morning", "afternoon")

        Returns:
            Score 0.0-1.0
        """
        try:
            hour = slot_time.hour

            # Default clinic preferences (morning slightly preferred)
            if 8 <= hour < 12:  # Morning
                base_score = 0.8
                time_category = "morning"
            elif 12 <= hour < 17:  # Afternoon
                base_score = 0.7
                time_category = "afternoon"
            elif 17 <= hour < 20:  # Evening
                base_score = 0.6
                time_category = "evening"
            else:
                base_score = 0.3  # Early morning or late night
                time_category = "other"

            # Adjust for patient preference
            if patient_preferences:
                preferred_time = patient_preferences.get("time_of_day")
                if preferred_time and preferred_time == time_category:
                    base_score = min(1.0, base_score + 0.2)  # Bonus for matching

            logger.debug(
                f"Slot at {hour}:00 ({time_category}) scored {base_score:.2f}"
            )
            return base_score

        except Exception as e:
            logger.error(f"Error scoring time_of_day: {e}")
            return 0.5

    def score_patient_preference(
        self,
        doctor_id: UUID,
        patient_preferences: Optional[Dict[str, Any]] = None
    ) -> float:
        """
        Score based on patient's preferred doctor.

        Args:
            doctor_id: Proposed doctor UUID
            patient_preferences: Optional patient preferences

        Returns:
            Score 0.0-1.0 (1.0 if preferred doctor, 0.7 otherwise)
        """
        try:
            if not patient_preferences:
                return 0.7  # Neutral if no preference

            preferred_doctors = patient_preferences.get("preferred_doctors", [])

            if str(doctor_id) in [str(d) for d in preferred_doctors]:
                logger.debug(f"Doctor {doctor_id} is patient's preferred doctor")
                return 1.0
            else:
                return 0.7  # Slight penalty for non-preferred

        except Exception as e:
            logger.error(f"Error scoring patient_preference: {e}")
            return 0.7

    def calculate_total_score(
        self,
        slot: Dict[str, Any],
        components: Dict[str, float]
    ) -> float:
        """
        Calculate weighted total score from component scores.

        Args:
            slot: Slot data (for logging)
            components: Dict of component scores (e.g., {"least_busy": 0.8, ...})

        Returns:
            Total weighted score 0.0-100.0
        """
        try:
            total = 0.0
            explanations = []

            for factor, score in components.items():
                weight = self.weights.get(factor, 0.0)
                weighted_score = score * weight
                total += weighted_score

                # Generate explanation if significant
                if weighted_score > 0.1:
                    explanations.append(
                        f"{factor.replace('_', ' ').title()}: "
                        f"{score:.2f} (weight: {weight:.2f})"
                    )

            # Normalize to 0-100 scale
            total_score = total * 100

            logger.debug(
                f"Total score: {total_score:.2f} | Components: {components}"
            )

            return total_score

        except Exception as e:
            logger.error(f"Error calculating total score: {e}")
            return 50.0  # Neutral score on error

    def generate_explanations(
        self,
        components: Dict[str, float],
        top_n: int = 3
    ) -> List[str]:
        """
        Generate human-readable explanations for top scoring factors.

        Args:
            components: Component scores
            top_n: Number of top factors to explain

        Returns:
            List of explanation strings
        """
        try:
            # Sort by score (descending)
            sorted_factors = sorted(
                components.items(),
                key=lambda x: x[1],
                reverse=True
            )

            explanations = []
            factor_names = {
                "least_busy": "Doctor has lighter schedule",
                "pack_schedule": "Adjacent to other appointments",
                "room_preference": "Doctor's preferred room",
                "time_of_day": "Optimal time of day",
                "patient_preference": "Patient's preferred doctor"
            }

            for factor, score in sorted_factors[:top_n]:
                if score > 0.6:  # Only explain high-scoring factors
                    explanation = factor_names.get(
                        factor,
                        factor.replace('_', ' ').title()
                    )
                    explanations.append(explanation)

            return explanations

        except Exception as e:
            logger.error(f"Error generating explanations: {e}")
            return ["Recommended slot"]
