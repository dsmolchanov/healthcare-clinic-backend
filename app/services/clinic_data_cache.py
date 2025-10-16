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
from typing import Dict, Any, List, Optional

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
                # Use 'active' column (current schema)
                result = supabase_client.table('doctors').select(
                    'id,first_name,last_name,specialization,phone,email'
                ).eq('clinic_id', clinic_id).eq('active', True).execute()
                doctors = result.data if result.data else []
            except Exception as e:
                if 'active' in str(e):
                    logger.debug("Falling back to 'is_active' column for doctors")
                    result = supabase_client.table('doctors').select(
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
            # Final fallback - try active column
            try:
                result = supabase_client.table('doctors').select(
                    'id,first_name,last_name,specialization'
                ).eq('clinic_id', clinic_id).eq('active', True).execute()
                return result.data if result.data else []
            except:
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
                result = supabase_client.schema('healthcare').table('services').select(
                    'id,name,description,base_price,category,duration_minutes,currency,code'
                ).eq('clinic_id', clinic_id).eq('active', True).execute()
                services = result.data if result.data else []
            except Exception as e:
                if 'active' in str(e):
                    logger.debug("Falling back to 'is_active' column for services")
                    result = supabase_client.schema('healthcare').table('services').select(
                        'id,name,description,base_price,category,duration_minutes,currency,code'
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
