import asyncio
import logging
from typing import Any, Dict, List, Optional, Tuple

from app.services.cache_service import CacheService
from app.memory.conversation_memory import ConversationMemoryManager
from app.services.profile_manager import ProfileManager, PatientProfile, ConversationState
from app.config import (
    get_redis_client, 
    get_supabase_client,
    MESSAGE_HISTORY_DEFAULT_WINDOW, 
    MESSAGE_HISTORY_MAX_MESSAGES, 
    MESSAGE_HISTORY_MAX_TOKENS
)

logger = logging.getLogger(__name__)

class MessageContextHydrator:
    """
    Service to hydrate all necessary context for message processing.
    Combines CacheService (fast path) and MemoryManager/ProfileManager (deep context).
    """

    def __init__(self, memory_manager: ConversationMemoryManager, profile_manager: ProfileManager):
        self.memory_manager = memory_manager
        self.profile_manager = profile_manager
        self.redis_client = get_redis_client()
        self.supabase_client = get_supabase_client()
        self.cache_service = CacheService(self.redis_client, self.supabase_client)

    async def hydrate(
        self,
        clinic_id: str,
        phone_number: str,
        session_id: str,
        is_new_conversation: bool = False
    ) -> Dict[str, Any]:
        """
        Fetch all context needed for processing a message.
        
        Args:
            clinic_id: The clinic ID.
            phone_number: The user's phone number.
            session_id: The current session ID.
            is_new_conversation: Whether this is a new conversation (optimization hint).
            
        Returns:
            A dictionary containing:
            - clinic: Clinic profile
            - patient: Patient profile (basic)
            - session_state: Session state from cache
            - history: Conversation history
            - preferences: User preferences
            - profile: Detailed patient profile (mem0)
            - conversation_state: Detailed conversation state
            - services: Clinic services
            - doctors: Clinic doctors
            - faqs: Clinic FAQs
        """
        
        # 1. Fast Path Hydration (CacheService)
        # Hydrate complete context (clinic, patient, session_state)
        hydrated_task = self.cache_service.hydrate_context(
            clinic_id=clinic_id,
            phone=phone_number,
            session_id=session_id
        )

        # 2. Deep Context Hydration (Parallel)
        
        # History
        history_task = self.memory_manager.get_conversation_history(
            phone_number=phone_number,
            clinic_id=clinic_id,
            session_id=session_id,
            time_window_hours=MESSAGE_HISTORY_DEFAULT_WINDOW,
            include_all_sessions=False,
            max_messages=MESSAGE_HISTORY_MAX_MESSAGES,
            max_tokens=MESSAGE_HISTORY_MAX_TOKENS
        )

        # Preferences
        prefs_task = self.memory_manager.get_user_preferences(
            phone_number=phone_number,
            clinic_id=clinic_id
        )

        # Detailed Profile (Layer 1)
        profile_task = self.profile_manager.get_patient_profile(
            phone=phone_number,
            clinic_id=clinic_id
        )

        # Conversation State (Layer 2)
        conversation_state_task = self.profile_manager.get_conversation_state(
            session_id=session_id
        )

        # Execute all tasks in parallel
        # Note: We run cache_service in parallel with others for maximum speed
        results = await asyncio.gather(
            hydrated_task,
            history_task,
            prefs_task,
            profile_task,
            conversation_state_task,
            return_exceptions=True
        )

        # Unpack results
        hydrated = results[0] if not isinstance(results[0], Exception) else {}
        if isinstance(results[0], Exception):
            logger.error(f"Error in CacheService hydration: {results[0]}")

        history = results[1] if not isinstance(results[1], Exception) else []
        if isinstance(results[1], Exception):
            logger.error(f"Error fetching history: {results[1]}")

        preferences = results[2] if not isinstance(results[2], Exception) else {}
        if isinstance(results[2], Exception):
            logger.error(f"Error fetching preferences: {results[2]}")

        profile = results[3] if not isinstance(results[3], Exception) else None
        if isinstance(results[3], Exception):
            logger.error(f"Error fetching profile: {results[3]}")

        conversation_state = results[4] if not isinstance(results[4], Exception) else None
        if isinstance(results[4], Exception):
            logger.error(f"Error fetching conversation state: {results[4]}")

        # Default objects if missing
        if profile is None:
            profile = PatientProfile()
        
        if conversation_state is None:
            conversation_state = ConversationState()

        # Combine everything into a single context dict
        context = {
            # From CacheService
            'clinic': hydrated.get('clinic', {}),
            'patient': hydrated.get('patient', {}),
            'session_state_data': hydrated.get('session_state', {}),
            'services': hydrated.get('services', []),
            'doctors': hydrated.get('doctors', []),
            'faqs': hydrated.get('faqs', []),
            
            # From Deep Fetch
            'history': history,
            'preferences': preferences or {},
            'profile': profile,
            'conversation_state': conversation_state
        }

        return context
