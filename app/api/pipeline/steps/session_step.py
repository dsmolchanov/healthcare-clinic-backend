"""
SessionManagementStep - Handle session creation, phone resolution, and message storage.

Extracted from process_message() lines 230-313.

Phase 2A of the Agentic Flow Architecture Refactor.
"""

import uuid
import asyncio
import logging
from typing import Tuple, Dict, Set, Optional
from time import perf_counter

from ..base import PipelineStep
from ..context import PipelineContext

logger = logging.getLogger(__name__)


class SessionManagementStep(PipelineStep):
    """
    Handle session creation, phone resolution, and initial message storage.

    Responsibilities:
    1. Resolve phone number from metadata fallbacks
    2. Map organization_id to clinic_id
    3. Create/get session via SessionController
    4. Store incoming user message
    5. Trigger async clinic cache warming
    """

    # Class-level caches (shared across instances)
    _org_to_clinic_cache: Dict[str, str] = {}
    _known_clinic_ids: Set[str] = set()
    _clinic_cache_warm_timestamps: Dict[str, float] = {}
    _clinic_cache_inflight: Set[str] = set()
    _clinic_cache_warm_ttl: int = 900  # 15 minutes

    def __init__(
        self,
        session_controller=None,
        memory_manager=None,
        profile_manager=None,
        supabase_client=None
    ):
        """
        Initialize with required dependencies.

        Args:
            session_controller: SessionController for session management
            memory_manager: ConversationMemory for message storage
            profile_manager: ProfileManager for patient upsert
            supabase_client: Supabase client for clinic lookups
        """
        self._session_controller = session_controller
        self._memory_manager = memory_manager
        self._profile_manager = profile_manager
        self._supabase = supabase_client

    @property
    def name(self) -> str:
        return "session_management"

    async def execute(self, ctx: PipelineContext) -> Tuple[PipelineContext, bool]:
        """
        Execute session management step.

        Sets on context:
        - resolved_clinic_id
        - session_id
        - session
        - is_new_session
        - previous_session_summary
        - correlation_id
        """
        # 1. Resolve phone number from fallbacks
        self._resolve_phone_number(ctx)

        # 2. Generate correlation ID
        ctx.correlation_id = str(uuid.uuid4())[:8]

        # 3. Map organization_id to clinic_id
        ctx.resolved_clinic_id = self._get_clinic_id_from_organization(ctx.clinic_id)

        # 4. Track known clinic IDs
        if ctx.resolved_clinic_id:
            self._known_clinic_ids.add(ctx.resolved_clinic_id)

        # 5. Trigger async clinic cache warming if needed
        if self._should_warm_clinic_cache(ctx.resolved_clinic_id):
            self._clinic_cache_inflight.add(ctx.resolved_clinic_id)
            asyncio.create_task(self._warm_clinic_cache(ctx.resolved_clinic_id))

        # 6. Create/upsert patient record
        if self._profile_manager:
            await self._profile_manager.upsert_patient_from_whatsapp(
                clinic_id=ctx.effective_clinic_id,
                phone=ctx.from_phone,
                profile_name=ctx.profile_name,
                detected_language=None  # Will be detected later
            )

        # 7. Get/create session via SessionController
        if self._session_controller:
            session_ctx = await self._session_controller.manage_session(
                phone_number=ctx.from_phone,
                clinic_id=ctx.effective_clinic_id,
                message_body=ctx.message,
                channel=ctx.channel
            )

            ctx.session_id = session_ctx.session_id
            ctx.session = session_ctx.session_obj
            ctx.is_new_session = session_ctx.is_new_session
            ctx.previous_session_summary = session_ctx.previous_session_summary
        else:
            # Fallback: generate session ID if no controller
            ctx.session_id = str(uuid.uuid4())
            ctx.is_new_session = True
            logger.warning("SessionController not available, using fallback session ID")

        # 8. Store incoming user message
        if self._memory_manager and ctx.session_id:
            await self._memory_manager.store_message(
                session_id=ctx.session_id,
                role='user',
                content=ctx.message,
                phone_number=ctx.from_phone,
                metadata={
                    'message_sid': ctx.message_sid,
                    'profile_name': ctx.profile_name,
                    'clinic_id': ctx.effective_clinic_id,
                    'from_number': ctx.from_phone,
                    'channel': ctx.channel,
                    'instance_name': ctx.request_metadata.get('instance_name')
                }
            )

        # 9. Extract turn status from session
        if ctx.session:
            ctx.turn_status = ctx.session.get('turn_status', 'user_turn')
            ctx.last_agent_action = ctx.session.get('last_agent_action')
            ctx.pending_since = ctx.session.get('pending_since')

        logger.info(
            f"📋 Session: {ctx.session_id[:8] if ctx.session_id else 'none'}... "
            f"(new={ctx.is_new_session}, clinic={ctx.effective_clinic_id[:8] if ctx.effective_clinic_id else 'none'}...)"
        )

        return ctx, True

    def _resolve_phone_number(self, ctx: PipelineContext):
        """Resolve phone number from metadata fallbacks."""
        if ctx.from_phone and ctx.from_phone.lower() != 'unknown':
            return

        fallback_phone = None
        if ctx.request_metadata:
            fallback_phone = (
                ctx.request_metadata.get('from_number')
                or ctx.request_metadata.get('phone_number')
                or ctx.request_metadata.get('from')
            )

        if not fallback_phone and ctx.message_sid and ctx.message_sid.startswith('whatsapp_'):
            parts = ctx.message_sid.split('_', 2)
            if len(parts) > 1 and parts[1]:
                fallback_phone = parts[1]

        if fallback_phone:
            # Update context (note: this modifies the request data)
            # In the original, this modified request.from_phone
            ctx.from_phone = fallback_phone
            ctx.request_metadata.setdefault('from_number', fallback_phone)
            ctx.request_metadata.setdefault('phone_number', fallback_phone)
            ctx.request_metadata.setdefault('from', fallback_phone)

    def _get_clinic_id_from_organization(self, organization_id: str) -> Optional[str]:
        """Map organization_id to actual clinic_id."""
        if not organization_id:
            return organization_id

        if organization_id in self._known_clinic_ids:
            return organization_id

        if not self._supabase:
            return organization_id

        # Return cached mapping if available
        if organization_id in self._org_to_clinic_cache:
            return self._org_to_clinic_cache[organization_id]

        try:
            # First try to treat the value as a clinic_id
            by_id = self._supabase.table('clinics').select('id').eq('id', organization_id).limit(1).execute()
            if by_id.data:
                clinic_id = by_id.data[0]['id']
                self._org_to_clinic_cache[organization_id] = clinic_id
                self._known_clinic_ids.add(clinic_id)
                return clinic_id

            # Look up clinic by organization_id
            result = self._supabase.table('clinics').select('id, organization_id').eq(
                'organization_id', organization_id
            ).limit(1).execute()

            if result.data:
                clinic_id = result.data[0]['id']
                self._org_to_clinic_cache[organization_id] = clinic_id
                self._known_clinic_ids.add(clinic_id)
                return clinic_id
            else:
                # Fallback: get first clinic
                logger.warning(f"No clinic found for org {organization_id}, using first clinic")
                all_clinics = self._supabase.table('clinics').select('id').limit(1).execute()
                if all_clinics.data:
                    clinic_id = all_clinics.data[0]['id']
                    self._org_to_clinic_cache[organization_id] = clinic_id
                    self._known_clinic_ids.add(clinic_id)
                    return clinic_id

        except Exception as e:
            logger.error(f"Error mapping organization to clinic: {e}")

        return organization_id

    def _should_warm_clinic_cache(self, clinic_id: Optional[str]) -> bool:
        """Determine if clinic cache warming should be triggered."""
        if not clinic_id:
            return False

        if self._clinic_cache_warm_ttl < 0:
            return False

        if clinic_id in self._clinic_cache_inflight:
            return False

        last_warm = self._clinic_cache_warm_timestamps.get(clinic_id)
        if last_warm is None:
            return True

        if self._clinic_cache_warm_ttl == 0:
            return False

        return (perf_counter() - last_warm) > self._clinic_cache_warm_ttl

    async def _warm_clinic_cache(self, clinic_id: str):
        """Warm clinic doctors/services/FAQs into Redis asynchronously."""
        if not clinic_id:
            return

        try:
            from app.startup_warmup import warmup_clinic_data

            logger.info(f"🚀 Warming clinic cache for {clinic_id[:8]}...")
            success = await warmup_clinic_data([clinic_id])

            if success:
                self._clinic_cache_warm_timestamps[clinic_id] = perf_counter()
                logger.info(f"✅ Clinic cache warmed for {clinic_id[:8]}...")
            else:
                self._clinic_cache_warm_timestamps.pop(clinic_id, None)
        except Exception as exc:
            logger.warning(f"Clinic cache warmup failed for {clinic_id}: {exc}")
            self._clinic_cache_warm_timestamps.pop(clinic_id, None)
        finally:
            self._clinic_cache_inflight.discard(clinic_id)
