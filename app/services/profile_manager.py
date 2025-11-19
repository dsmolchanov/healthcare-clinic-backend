"""
Profile Manager Service
Replaces mem0 for deterministic fact retrieval

Architecture:
- Layer 1 (Patient Profile): Hard facts that NEVER change in conversation
- Layer 2 (Conversation State): Mutable state for CURRENT episode
"""

import logging
from typing import Dict, Any, Optional
from pydantic import BaseModel, Field
from supabase import Client

logger = logging.getLogger(__name__)


class PatientProfile(BaseModel):
    """Patient hard facts (Layer 1)"""
    first_name: str = ""
    last_name: str = ""
    bio_summary: str = ""
    medical_history: Dict[str, Any] = Field(default_factory=dict)
    hard_preferences: Dict[str, Any] = Field(default_factory=dict)

    @property
    def allergies(self) -> list:
        """Extract allergies list"""
        return self.medical_history.get('allergies', [])

    @property
    def hard_doctor_bans(self) -> list:
        """Extract hard doctor bans"""
        return self.hard_preferences.get('hard_doctor_bans', [])

    @property
    def preferred_language(self) -> Optional[str]:
        """Extract preferred language"""
        return self.hard_preferences.get('preferred_language')


class ConversationState(BaseModel):
    """Current episode state (Layer 2)"""
    episode_type: str = "GENERAL"
    current_constraints: Dict[str, Any] = Field(default_factory=dict)
    booking_state: Dict[str, Any] = Field(default_factory=dict)

    @property
    def desired_service(self) -> Optional[str]:
        """Extract desired service"""
        return self.current_constraints.get('desired_service')

    @property
    def excluded_doctors(self) -> list:
        """Extract excluded doctors list"""
        return self.current_constraints.get('excluded_doctors', [])

    @property
    def excluded_services(self) -> list:
        """Extract excluded services list"""
        return self.current_constraints.get('excluded_services', [])


class ProfileManager:
    """
    Manages deterministic patient facts and conversation state.

    Replaces mem0 with SQL queries for 100% reliability.
    """

    def __init__(self, supabase_client: Client):
        self.supabase = supabase_client

    async def get_patient_profile(
        self,
        phone: str,
        clinic_id: str
    ) -> PatientProfile:
        """
        Fetch patient profile (Layer 1).
        Called ONCE at conversation start.

        Returns:
            PatientProfile with medical_history, allergies, preferences
        """
        try:
            result = self.supabase.schema('healthcare').table('patients')\
                .select('first_name, last_name, bio_summary, medical_history, hard_preferences')\
                .eq('phone', phone)\
                .eq('clinic_id', clinic_id)\
                .single()\
                .execute()

            if result.data:
                logger.info(f"✅ Loaded patient profile for {phone[:4]}***")
                return PatientProfile(
                    first_name=result.data.get('first_name', ''),
                    last_name=result.data.get('last_name', ''),
                    bio_summary=result.data.get('bio_summary', ''),
                    medical_history=result.data.get('medical_history', {}),
                    hard_preferences=result.data.get('hard_preferences', {})
                )
        except Exception as e:
            logger.warning(f"Failed to fetch patient profile: {e}")

        return PatientProfile()

    async def get_conversation_state(
        self,
        session_id: str
    ) -> ConversationState:
        """
        Fetch current conversation state (Layer 2).
        Called on EVERY message for up-to-date constraints.

        Returns:
            ConversationState with episode_type, constraints, booking_state
        """
        try:
            result = self.supabase.table('conversation_sessions')\
                .select('episode_type, current_constraints, booking_state')\
                .eq('id', session_id)\
                .single()\
                .execute()

            if result.data:
                return ConversationState(
                    episode_type=result.data.get('episode_type', 'GENERAL'),
                    current_constraints=result.data.get('current_constraints', {}),
                    booking_state=result.data.get('booking_state', {})
                )
        except Exception as e:
            logger.warning(f"Failed to fetch conversation state: {e}")

        return ConversationState()

    async def update_constraints(
        self,
        session_id: str,
        constraints: Dict[str, Any],
        merge: bool = True
    ):
        """
        Update conversation constraints.

        Args:
            session_id: Current session
            constraints: New constraints to set/merge
            merge: If True, merge with existing. If False, replace entirely.
        """
        try:
            if merge:
                # Fetch current constraints
                current = await self.get_conversation_state(session_id)
                merged = {**current.current_constraints, **constraints}
                constraints_to_save = merged
            else:
                constraints_to_save = constraints

            self.supabase.table('conversation_sessions')\
                .update({'current_constraints': constraints_to_save})\
                .eq('id', session_id)\
                .execute()

            logger.info(f"✅ Updated constraints for session {session_id[:8]}...")
        except Exception as e:
            logger.error(f"Failed to update constraints: {e}")

    async def clear_constraints(self, session_id: str):
        """
        Clear ALL conversation constraints.
        This is what "Forget my previous intents" should call.
        """
        try:
            self.supabase.table('conversation_sessions')\
                .update({
                    'current_constraints': {},
                    'booking_state': {}
                })\
                .eq('id', session_id)\
                .execute()

            logger.info(f"✅ Cleared constraints for session {session_id[:8]}...")
        except Exception as e:
            logger.error(f"Failed to clear constraints: {e}")

    async def update_booking_state(
        self,
        session_id: str,
        booking_data: Dict[str, Any]
    ):
        """Update current booking attempt state"""
        try:
            self.supabase.table('conversation_sessions')\
                .update({'booking_state': booking_data})\
                .eq('id', session_id)\
                .execute()

            logger.info(f"✅ Updated booking state for session {session_id[:8]}...")
        except Exception as e:
            logger.error(f"Failed to update booking state: {e}")

    async def add_hard_doctor_ban(
        self,
        phone: str,
        clinic_id: str,
        doctor_name: str
    ):
        """
        Add permanent doctor ban to patient profile.

        Used for safety concerns (abuse, malpractice, etc.)
        """
        try:
            # Get current profile
            profile = await self.get_patient_profile(phone, clinic_id)

            # Add ban if not already present
            bans = set(profile.hard_doctor_bans)
            bans.add(doctor_name)

            # Update profile
            self.supabase.schema('healthcare').table('patients')\
                .update({
                    'hard_preferences': {
                        **profile.hard_preferences,
                        'hard_doctor_bans': list(bans)
                    }
                })\
                .eq('phone', phone)\
                .eq('clinic_id', clinic_id)\
                .execute()

            logger.warning(f"⚠️ Added permanent ban for doctor: {doctor_name}")
        except Exception as e:
            logger.error(f"Failed to add doctor ban: {e}")
