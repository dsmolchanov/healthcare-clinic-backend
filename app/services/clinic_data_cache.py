"""
Clinic Data Redis Cache Service

Provides Redis-based caching for frequently accessed clinic data:
- Doctors list
- Services/pricing catalog  
- FAQs

Benefits:
- Reduces database load
- Faster response times (submillisecond access)
- Consistent across multiple workers/instances
"""

import json
import logging
from typing import Dict, Any, List

from app.utils.i18n_helpers import get_translation

logger = logging.getLogger(__name__)


class ClinicDataCache:
    """Redis cache manager for clinic-related data"""

    def __init__(self, redis_client, default_ttl: int = 3600):
        """
        Initialize cache manager

        Args:
            redis_client: Redis client instance
            default_ttl: Default TTL in seconds (1 hour)
        """
        self.redis = redis_client
        self.default_ttl = default_ttl

    def _make_key(self, clinic_id: str, data_type: str) -> str:
        """Generate cache key"""
        return f"clinic:{clinic_id}:{data_type}"

    async def get_doctors(self, clinic_id: str, supabase_client) -> List[Dict[str, Any]]:
        """
        Get doctors list with Redis caching

        Returns list of doctors with: id, first_name, last_name, specialization
        """
        cache_key = self._make_key(clinic_id, "doctors")

        try:
            # Try cache first
            cached_data = self.redis.get(cache_key)
            if cached_data:
                logger.debug(f"✅ Cache HIT: doctors for clinic {clinic_id}")
                return json.loads(cached_data)

            # Cache miss - fetch from database
            logger.debug(f"❌ Cache MISS: fetching doctors for clinic {clinic_id}")

            doctors = []
            try:
                # Use 'active' column (current schema) - doctors is in healthcare schema
                result = supabase_client.schema('healthcare').table('doctors').select(
                    'id,first_name,last_name,specialization,phone,email'
                ).eq('clinic_id', clinic_id).eq('active', True).execute()
                doctors = result.data if result.data else []
            except Exception as e:
                if 'active' in str(e):
                    logger.debug("Falling back to 'is_active' column for doctors")
                    result = supabase_client.schema('healthcare').table('doctors').select(
                        'id,first_name,last_name,specialization,phone,email'
                    ).eq('clinic_id', clinic_id).eq('is_active', True).execute()
                    doctors = result.data if result.data else []
                else:
                    raise

            # Cache for default TTL
            self.redis.setex(cache_key, self.default_ttl, json.dumps(doctors))
            logger.info(f"✅ Cached {len(doctors)} doctors for clinic {clinic_id}")

            return doctors

        except Exception as e:
            logger.error(f"Error getting/caching doctors: {e}")
            # Final fallback - try active column (healthcare schema)
            try:
                result = supabase_client.schema('healthcare').table('doctors').select(
                    'id,first_name,last_name,specialization'
                ).eq('clinic_id', clinic_id).eq('active', True).execute()
                return result.data if result.data else []
            except Exception:
                return []

    async def get_services(self, clinic_id: str, supabase_client) -> List[Dict[str, Any]]:
        """
        Get services/pricing catalog with Redis caching
        """
        cache_key = self._make_key(clinic_id, "services")

        try:
            cached_data = self.redis.get(cache_key)
            if cached_data:
                logger.debug(f"✅ Cache HIT: services for clinic {clinic_id}")
                return json.loads(cached_data)

            logger.debug(f"❌ Cache MISS: fetching services for clinic {clinic_id}")
            services = []
            try:
                # Use 'active' column (current schema)
                # Include i18n fields for multi-language search support
                # Include JSONB columns for unified i18n access
                result = supabase_client.schema('healthcare').table('services').select(
                    '''
                    id,name,description,base_price,category,duration_minutes,currency,code,
                    name_ru,name_en,name_es,name_pt,name_he,
                    description_ru,description_en,description_es,description_pt,description_he,
                    name_i18n,description_i18n
                    '''
                ).eq('clinic_id', clinic_id).eq('active', True).execute()
                services = result.data if result.data else []
            except Exception as e:
                if 'active' in str(e):
                    logger.debug("Falling back to 'is_active' column for services")
                    result = supabase_client.schema('healthcare').table('services').select(
                        '''
                        id,name,description,base_price,category,duration_minutes,currency,code,
                        name_ru,name_en,name_es,name_pt,name_he,
                        description_ru,description_en,description_es,description_pt,description_he,
                        name_i18n,description_i18n
                        '''
                    ).eq('clinic_id', clinic_id).eq('is_active', True).execute()
                    services = result.data if result.data else []
                else:
                    raise
            self.redis.setex(cache_key, self.default_ttl, json.dumps(services))
            logger.info(f"✅ Cached {len(services)} services for clinic {clinic_id}")

            return services

        except Exception as e:
            logger.error(f"Error getting/caching services: {e}")
            return []

    def search_cached_services(
        self,
        cached_services: List[Dict[str, Any]],
        query: str,
        language: str = 'en'
    ) -> List[Dict[str, Any]]:
        """
        Search cached services with language-aware field matching using JSONB i18n.

        Uses get_translation() helper to access name_i18n JSONB with fallback chain.
        Falls back to columnar columns if JSONB is not populated.

        Args:
            cached_services: List of cached service dicts
            query: Normalized search query
            language: ISO 639-1 language code (en, es, ru, pt, he)

        Returns:
            List of matching services (exact + fuzzy matches)
        """
        query_lower = query.lower()
        matches = []

        for service in cached_services:
            # Get translated name using i18n helper with fallback chain
            display_name = get_translation(service, 'name', language, fallback_languages=['en'])

            if not display_name:
                # Final fallback to columnar columns if JSONB empty
                field_priority = {
                    'ru': ['name_ru', 'name', 'name_en'],
                    'es': ['name_es', 'name', 'name_en'],
                    'en': ['name_en', 'name'],
                    'pt': ['name_pt', 'name', 'name_en'],
                    'he': ['name_he', 'name', 'name_en']
                }
                for field in field_priority.get(language, ['name', 'name_en']):
                    display_name = service.get(field, '')
                    if display_name:
                        break

            if not display_name:
                continue

            # Exact match first
            if query_lower == display_name.lower():
                match_result = service.copy()
                match_result['match_type'] = 'exact'
                match_result['match_field'] = f'name_{language}'
                matches.append(match_result)
                continue

            # Substring match (fallback)
            if query_lower in display_name.lower():
                match_result = service.copy()
                match_result['match_type'] = 'substring'
                match_result['match_field'] = f'name_{language}'
                matches.append(match_result)

        return matches

    async def get_faqs(self, clinic_id: str, supabase_client) -> List[Dict[str, Any]]:
        """
        Get FAQs with Redis caching

        Queries public.faqs table with FTS support
        """
        cache_key = self._make_key(clinic_id, "faqs")

        try:
            cached_data = self.redis.get(cache_key)
            if cached_data:
                logger.debug(f"✅ Cache HIT: FAQs for clinic {clinic_id}")
                return json.loads(cached_data)

            logger.debug(f"❌ Cache MISS: fetching FAQs for clinic {clinic_id}")

            # Query public.faqs table (explicitly specify public schema)
            result = supabase_client.schema('public').table('faqs').select(
                'id,question,answer,category,subcategory,language,priority,tags,is_featured'
            ).eq('clinic_id', clinic_id).eq('is_active', True).order('priority', desc=True).execute()

            faqs = result.data if result.data else []

            # Cache the results
            self.redis.setex(cache_key, self.default_ttl, json.dumps(faqs))
            logger.info(f"✅ Cached {len(faqs)} FAQs for clinic {clinic_id}")

            return faqs

        except Exception as e:
            logger.error(f"Error getting/caching FAQs: {e}")
            return []

    def invalidate_doctors(self, clinic_id: str):
        """Invalidate doctors cache"""
        self.redis.delete(self._make_key(clinic_id, "doctors"))
        logger.info(f"🗑️ Invalidated doctors cache for clinic {clinic_id}")

    def invalidate_services(self, clinic_id: str):
        """Invalidate services cache"""
        self.redis.delete(self._make_key(clinic_id, "services"))
        logger.info(f"🗑️ Invalidated services cache for clinic {clinic_id}")

    def invalidate_faqs(self, clinic_id: str):
        """Invalidate FAQs cache"""
        self.redis.delete(self._make_key(clinic_id, "faqs"))
        logger.info(f"🗑️ Invalidated FAQs cache for clinic {clinic_id}")

    def invalidate_all(self, clinic_id: str):
        """Invalidate all cached data for a clinic"""
        self.invalidate_doctors(clinic_id)
        self.invalidate_services(clinic_id)
        self.invalidate_faqs(clinic_id)
        logger.info(f"🗑️ Invalidated ALL cache for clinic {clinic_id}")
