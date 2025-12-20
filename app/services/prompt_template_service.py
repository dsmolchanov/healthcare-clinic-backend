"""
Service for managing database-backed prompt templates.

Phase 2B-2 of Agentic Flow Architecture Refactor.
Enables per-clinic prompt customization without code deployments.
"""

import logging
from typing import Dict, Optional, List, Any
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


# Valid component keys (must match Python constants in app/prompts/components.py)
VALID_COMPONENT_KEYS = {
    'base_persona',
    'clinic_context',
    'date_time_context',
    'date_rules',
    'booking_policy',
    'patient_profile_template',
    'constraints_template',
}

# Descriptions for each component (used in API responses)
COMPONENT_DESCRIPTIONS = {
    'base_persona': 'Core assistant persona and behavioral rules',
    'clinic_context': 'Clinic information template (name, location, services, doctors, hours)',
    'date_time_context': 'Current date/time context section',
    'date_rules': 'Date calculation rules and hallucination prevention',
    'booking_policy': 'Booking flow instructions and tool usage guidance',
    'patient_profile_template': 'Patient profile and medical history section',
    'constraints_template': 'Conversation constraints format (excluded doctors, services, etc.)',
}


class PromptTemplateService:
    """
    Service for loading prompt templates from database.

    Uses in-memory caching to minimize DB calls. Falls back to Python defaults
    when no override exists or when DB is unavailable.

    Usage:
        service = PromptTemplateService()
        templates = await service.get_clinic_templates(clinic_id)
        # templates = {'base_persona': '...', 'booking_policy': '...'}
    """

    def __init__(self, cache_ttl_seconds: int = 300):
        """
        Initialize service with cache TTL.

        Args:
            cache_ttl_seconds: How long to cache templates (default 5 minutes)
        """
        self._cache: Dict[str, tuple] = {}  # {clinic_id: (templates, expires_at)}
        self._cache_ttl = timedelta(seconds=cache_ttl_seconds)

    async def get_clinic_templates(self, clinic_id: str) -> Dict[str, str]:
        """
        Get all active prompt templates for a clinic.

        Returns dict of {component_key: content}.
        Uses cache with TTL. Returns empty dict if no overrides exist.

        Args:
            clinic_id: UUID of the clinic

        Returns:
            Dict mapping component_key to template content
        """
        # Check cache
        if clinic_id in self._cache:
            templates, expires_at = self._cache[clinic_id]
            if datetime.now() < expires_at:
                logger.debug(f"Cache hit for clinic {clinic_id} templates")
                return templates

        # Fetch from DB
        templates = await self._fetch_from_db(clinic_id)

        # Update cache
        self._cache[clinic_id] = (templates, datetime.now() + self._cache_ttl)
        logger.debug(f"Cached {len(templates)} templates for clinic {clinic_id}")

        return templates

    async def _fetch_from_db(self, clinic_id: str) -> Dict[str, str]:
        """Fetch templates from Supabase."""
        try:
            from app.supabase_client import get_supabase_client
            client = get_supabase_client()

            response = client.schema('healthcare').table('prompt_templates').select(
                'component_key, content'
            ).eq('clinic_id', clinic_id).eq('is_active', True).execute()

            if response.data:
                return {row['component_key']: row['content'] for row in response.data}

            return {}

        except Exception as e:
            logger.warning(f"Failed to fetch prompt templates for clinic {clinic_id}: {e}")
            return {}

    def invalidate_cache(self, clinic_id: str):
        """
        Invalidate cache for a clinic.

        Call this after updates to ensure changes take effect immediately.

        Args:
            clinic_id: UUID of the clinic to invalidate
        """
        if clinic_id in self._cache:
            del self._cache[clinic_id]
            logger.debug(f"Invalidated cache for clinic {clinic_id}")

    def invalidate_all_cache(self):
        """Invalidate all cached templates."""
        self._cache.clear()
        logger.debug("Invalidated all prompt template cache")

    async def save_template(
        self,
        clinic_id: str,
        component_key: str,
        content: str,
        user_id: Optional[str] = None,
        description: Optional[str] = None
    ) -> bool:
        """
        Save or update a prompt template.

        Uses upsert to handle both insert and update cases.

        Args:
            clinic_id: UUID of the clinic
            component_key: Which component to override
            content: The template content
            user_id: Optional user ID for audit trail
            description: Optional description/help text

        Returns:
            True if successful, False otherwise

        Raises:
            ValueError: If component_key is invalid
        """
        if component_key not in VALID_COMPONENT_KEYS:
            raise ValueError(
                f"Invalid component_key: {component_key}. "
                f"Must be one of: {', '.join(sorted(VALID_COMPONENT_KEYS))}"
            )

        try:
            from app.supabase_client import get_supabase_client
            client = get_supabase_client()

            # Build data for upsert
            data = {
                'clinic_id': clinic_id,
                'component_key': component_key,
                'content': content,
                'is_active': True,
            }

            if user_id:
                data['updated_by'] = user_id
            if description:
                data['description'] = description

            # Upsert (insert or update on conflict)
            response = client.schema('healthcare').table('prompt_templates').upsert(
                data,
                on_conflict='clinic_id,component_key'
            ).execute()

            # Invalidate cache
            self.invalidate_cache(clinic_id)

            logger.info(f"Saved prompt template {component_key} for clinic {clinic_id}")
            return True

        except Exception as e:
            logger.error(f"Failed to save prompt template: {e}")
            return False

    async def delete_template(self, clinic_id: str, component_key: str) -> bool:
        """
        Delete (deactivate) a prompt template.

        This causes the clinic to fall back to Python defaults for this component.

        Args:
            clinic_id: UUID of the clinic
            component_key: Which component to delete

        Returns:
            True if successful, False otherwise
        """
        try:
            from app.supabase_client import get_supabase_client
            client = get_supabase_client()

            # Soft delete by setting is_active = false
            response = client.schema('healthcare').table('prompt_templates').update(
                {'is_active': False}
            ).eq('clinic_id', clinic_id).eq('component_key', component_key).execute()

            # Invalidate cache
            self.invalidate_cache(clinic_id)

            logger.info(f"Deleted prompt template {component_key} for clinic {clinic_id}")
            return True

        except Exception as e:
            logger.error(f"Failed to delete prompt template: {e}")
            return False

    async def list_templates(
        self,
        clinic_id: str,
        include_inactive: bool = False
    ) -> List[Dict[str, Any]]:
        """
        List all templates for a clinic.

        Args:
            clinic_id: UUID of the clinic
            include_inactive: Whether to include deactivated templates

        Returns:
            List of template dicts with full metadata
        """
        try:
            from app.supabase_client import get_supabase_client
            client = get_supabase_client()

            query = client.schema('healthcare').table('prompt_templates').select(
                'id, component_key, content, description, is_active, version, created_at, updated_at'
            ).eq('clinic_id', clinic_id).order('component_key')

            if not include_inactive:
                query = query.eq('is_active', True)

            response = query.execute()

            return response.data or []

        except Exception as e:
            logger.error(f"Failed to list prompt templates: {e}")
            return []

    async def get_template(
        self,
        clinic_id: str,
        component_key: str
    ) -> Optional[Dict[str, Any]]:
        """
        Get a single template by clinic and component key.

        Args:
            clinic_id: UUID of the clinic
            component_key: Which component to fetch

        Returns:
            Template dict or None if not found
        """
        try:
            from app.supabase_client import get_supabase_client
            client = get_supabase_client()

            response = client.schema('healthcare').table('prompt_templates').select(
                'id, component_key, content, description, is_active, version, created_at, updated_at'
            ).eq('clinic_id', clinic_id).eq('component_key', component_key).eq(
                'is_active', True
            ).limit(1).execute()

            if response.data:
                return response.data[0]

            return None

        except Exception as e:
            logger.error(f"Failed to get prompt template: {e}")
            return None


# Singleton instance
_prompt_template_service: Optional[PromptTemplateService] = None


def get_prompt_template_service() -> PromptTemplateService:
    """
    Get singleton PromptTemplateService instance.

    Returns:
        The shared PromptTemplateService instance
    """
    global _prompt_template_service
    if _prompt_template_service is None:
        _prompt_template_service = PromptTemplateService()
    return _prompt_template_service


def reset_prompt_template_service():
    """Reset the singleton (useful for testing)."""
    global _prompt_template_service
    _prompt_template_service = None
