"""Service embedding generation for automatic updates.

This service ensures new/updated services get embeddings automatically.
"""
import logging
from typing import Dict, Any, List, Optional

from supabase import Client

logger = logging.getLogger(__name__)

# Languages to generate embeddings for
LANGUAGES = ['en', 'ru', 'es', 'pt', 'he']


class ServiceEmbeddingService:
    """Handles automatic embedding generation for services."""

    def __init__(self, supabase_client: Client):
        self.client = supabase_client
        self._generator = None

    def _get_generator(self):
        """Lazy load embedding generator to avoid import issues at startup."""
        if self._generator is None:
            from app.utils.embedding_utils import get_embedding_generator
            self._generator = get_embedding_generator()
        return self._generator

    def _get_translation(self, service: Dict, field: str, language: str, fallback_languages: List[str] = None) -> str:
        """Get translated value from JSONB or columnar fields."""
        # Try JSONB first
        i18n_field = f"{field}_i18n"
        if i18n_field in service and service[i18n_field]:
            i18n_data = service[i18n_field]
            if isinstance(i18n_data, dict):
                if language in i18n_data:
                    return i18n_data[language]
                if fallback_languages:
                    for fb_lang in fallback_languages:
                        if fb_lang in i18n_data:
                            return i18n_data[fb_lang]

        # Try columnar field
        lang_field = f"{field}_{language}"
        if lang_field in service and service[lang_field]:
            return service[lang_field]

        # Fallback to base field
        if field in service and service[field]:
            return service[field]

        return ""

    async def generate_embeddings_for_service(
        self,
        service_id: str,
        service_data: Optional[Dict[str, Any]] = None
    ) -> bool:
        """Generate and store embeddings for a single service.

        Args:
            service_id: UUID of the service
            service_data: Optional pre-fetched service data (avoids extra query)

        Returns:
            True if embeddings were generated successfully
        """
        try:
            # Fetch service if not provided
            if service_data is None:
                response = self.client.schema('healthcare').from_('services').select(
                    'id, name, name_ru, name_en, name_es, name_pt, name_he, name_i18n, is_active'
                ).eq('id', service_id).single().execute()
                service_data = response.data

            if not service_data:
                logger.warning(f"Service {service_id} not found")
                return False

            if not service_data.get('is_active', True):
                logger.info(f"Service {service_id} is inactive, skipping embedding generation")
                return False

            generator = self._get_generator()

            # Generate embeddings for each language
            success_count = 0
            for lang in LANGUAGES:
                text = self._get_translation(service_data, 'name', lang, fallback_languages=['en'])
                if not text:
                    continue

                embedding = generator.generate(text)

                if embedding.sum() == 0:
                    logger.warning(f"Zero embedding for service {service_id}/{lang}")
                    continue

                # Upsert into semantic index
                self.client.schema('healthcare').from_('service_semantic_index').upsert({
                    'service_id': service_id,
                    'language': lang,
                    'embedding': embedding.tolist(),
                    'embedded_text': text,
                    'model_version': 'text-embedding-3-small'
                }, on_conflict='service_id,language').execute()

                success_count += 1

            logger.info(f"Generated {success_count} embeddings for service {service_id}")
            return success_count > 0

        except Exception as e:
            logger.error(f"Failed to generate embeddings for service {service_id}: {e}")
            return False

    async def delete_embeddings_for_service(self, service_id: str) -> bool:
        """Delete embeddings when a service is deleted or deactivated.

        Args:
            service_id: UUID of the service

        Returns:
            True if deletion was successful
        """
        try:
            self.client.schema('healthcare').from_('service_semantic_index').delete().eq(
                'service_id', service_id
            ).execute()
            logger.info(f"Deleted embeddings for service {service_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete embeddings for service {service_id}: {e}")
            return False


def schedule_embedding_generation(
    background_tasks,
    supabase_client: Client,
    service_id: str,
    service_data: Optional[Dict[str, Any]] = None
):
    """Schedule embedding generation as a background task.

    Call this after creating or updating a service.

    Args:
        background_tasks: FastAPI BackgroundTasks instance
        supabase_client: Supabase client for database access
        service_id: UUID of the service
        service_data: Optional pre-fetched service data
    """
    import asyncio

    embedding_service = ServiceEmbeddingService(supabase_client)

    def _generate():
        asyncio.run(embedding_service.generate_embeddings_for_service(service_id, service_data))

    background_tasks.add_task(_generate)
    logger.info(f"Scheduled embedding generation for service {service_id}")


def schedule_embedding_deletion(
    background_tasks,
    supabase_client: Client,
    service_id: str
):
    """Schedule embedding deletion as a background task.

    Call this after deleting or deactivating a service.

    Args:
        background_tasks: FastAPI BackgroundTasks instance
        supabase_client: Supabase client for database access
        service_id: UUID of the service
    """
    import asyncio

    embedding_service = ServiceEmbeddingService(supabase_client)

    def _delete():
        asyncio.run(embedding_service.delete_embeddings_for_service(service_id))

    background_tasks.add_task(_delete)
    logger.info(f"Scheduled embedding deletion for service {service_id}")
