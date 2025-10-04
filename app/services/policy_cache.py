"""
Policy Cache Service
Multi-layer caching for compiled policies with data freshness tracking
"""

import json
import hashlib
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional, Tuple
from collections import OrderedDict
import logging
import redis.asyncio as redis
from supabase import Client

logger = logging.getLogger(__name__)


class LRUCache:
    """Simple in-memory LRU cache implementation"""
    
    def __init__(self, max_size: int = 100):
        self.cache = OrderedDict()
        self.max_size = max_size

    def get(self, key: str) -> Optional[Dict]:
        if key not in self.cache:
            return None
        # Move to end (most recently used)
        self.cache.move_to_end(key)
        return self.cache[key]

    def set(self, key: str, value: Dict):
        if key in self.cache:
            # Update existing and move to end
            self.cache.move_to_end(key)
        self.cache[key] = value
        # Remove oldest if over capacity
        if len(self.cache) > self.max_size:
            self.cache.popitem(last=False)

    def delete(self, key: str):
        if key in self.cache:
            del self.cache[key]

    def clear(self):
        self.cache.clear()

    def size(self) -> int:
        return len(self.cache)


class PolicyCache:
    """
    Multi-layer cache for policy snapshots with data freshness tracking
    """
    
    # Cache configuration
    TTL_SECONDS = 300  # 5 minutes
    LRU_MAX_SIZE = 50
    REDIS_PREFIX = "policy:v1:"
    
    def __init__(self, supabase: Client, redis_client: Optional[redis.Redis] = None):
        self.supabase = supabase
        self.redis_client = redis_client
        self.memory_cache = LRUCache(self.LRU_MAX_SIZE)
        self.stats = {
            "memory_hits": 0,
            "redis_hits": 0,
            "db_hits": 0,
            "misses": 0,
            "evictions": 0
        }

    def _make_cache_key(self, clinic_id: str, version: Optional[int] = None) -> str:
        """Generate cache key for a policy"""
        if version:
            return f"{self.REDIS_PREFIX}{clinic_id}:v{version}"
        return f"{self.REDIS_PREFIX}{clinic_id}:active"

    async def get(
        self,
        clinic_id: str,
        version: Optional[int] = None,
        check_freshness: bool = True
    ) -> Optional[Dict]:
        """
        Get policy from cache with multi-layer fallback
        Returns (policy, source) where source is 'memory', 'redis', or 'db'
        """
        cache_key = self._make_cache_key(clinic_id, version)
        
        # Layer 1: In-memory LRU cache
        cached = self.memory_cache.get(cache_key)
        if cached:
            if not check_freshness or await self._is_fresh(cached):
                self.stats["memory_hits"] += 1
                logger.debug(f"Memory cache hit for {cache_key}")
                return cached
            else:
                # Stale data, remove from cache
                self.memory_cache.delete(cache_key)
                logger.debug(f"Memory cache stale for {cache_key}")
        
        # Layer 2: Redis cache
        if self.redis_client:
            try:
                redis_data = await self.redis_client.get(cache_key)
                if redis_data:
                    policy = json.loads(redis_data)
                    if not check_freshness or await self._is_fresh(policy):
                        # Populate memory cache
                        self.memory_cache.set(cache_key, policy)
                        self.stats["redis_hits"] += 1
                        logger.debug(f"Redis cache hit for {cache_key}")
                        return policy
                    else:
                        # Stale data, remove from Redis
                        await self.redis_client.delete(cache_key)
                        logger.debug(f"Redis cache stale for {cache_key}")
            except Exception as e:
                logger.warning(f"Redis cache error: {e}")
        
        # Layer 3: Database
        policy = await self._fetch_from_db(clinic_id, version)
        if policy:
            # Populate both cache layers
            await self._populate_caches(cache_key, policy)
            self.stats["db_hits"] += 1
            logger.debug(f"Database hit for {cache_key}")
            return policy
        
        self.stats["misses"] += 1
        logger.debug(f"Cache miss for {cache_key}")
        return None

    async def set(
        self,
        clinic_id: str,
        policy: Dict,
        version: Optional[int] = None
    ):
        """Store policy in all cache layers"""
        cache_key = self._make_cache_key(clinic_id, version or policy.get("version"))
        
        # Add cache metadata
        policy["cached_at"] = datetime.now(timezone.utc).isoformat()
        policy["cache_version"] = "1.0"
        
        await self._populate_caches(cache_key, policy)
        logger.info(f"Policy cached: {cache_key}")

    async def invalidate(self, clinic_id: str, version: Optional[int] = None):
        """Invalidate policy in all cache layers"""
        if version:
            cache_key = self._make_cache_key(clinic_id, version)
            await self._invalidate_key(cache_key)
        else:
            # Invalidate all versions for this clinic
            pattern = f"{self.REDIS_PREFIX}{clinic_id}:*"
            
            # Clear from memory cache
            keys_to_delete = [k for k in self.memory_cache.cache.keys() if k.startswith(f"{self.REDIS_PREFIX}{clinic_id}:")]
            for key in keys_to_delete:
                self.memory_cache.delete(key)
            
            # Clear from Redis
            if self.redis_client:
                try:
                    cursor = 0
                    while True:
                        cursor, keys = await self.redis_client.scan(cursor, match=pattern)
                        if keys:
                            await self.redis_client.delete(*keys)
                        if cursor == 0:
                            break
                except Exception as e:
                    logger.warning(f"Redis invalidation error: {e}")
            
            self.stats["evictions"] += len(keys_to_delete)
            logger.info(f"Invalidated all cache entries for clinic {clinic_id}")

    async def _invalidate_key(self, cache_key: str):
        """Invalidate a specific cache key"""
        self.memory_cache.delete(cache_key)
        
        if self.redis_client:
            try:
                await self.redis_client.delete(cache_key)
            except Exception as e:
                logger.warning(f"Redis delete error: {e}")
        
        self.stats["evictions"] += 1

    async def _populate_caches(self, cache_key: str, policy: Dict):
        """Populate both memory and Redis caches"""
        # Memory cache
        self.memory_cache.set(cache_key, policy)
        
        # Redis cache with TTL
        if self.redis_client:
            try:
                await self.redis_client.setex(
                    cache_key,
                    self.TTL_SECONDS,
                    json.dumps(policy, default=str)
                )
            except Exception as e:
                logger.warning(f"Redis set error: {e}")

    async def _fetch_from_db(self, clinic_id: str, version: Optional[int]) -> Optional[Dict]:
        """Fetch policy from database"""
        try:
            query = self.supabase.from_("policy_snapshots").select("*")
            
            if version:
                query = query.eq("clinic_id", clinic_id).eq("version", version)
            else:
                query = query.eq("clinic_id", clinic_id).eq("status", "active")
            
            response = query.single().execute()
            
            if response.data:
                # Add data freshness info
                policy = response.data
                policy["data_freshness"] = await self._get_data_freshness(clinic_id)
                return policy
                
        except Exception as e:
            logger.error(f"Database fetch error: {e}")
        
        return None

    async def _is_fresh(self, policy: Dict) -> bool:
        """
        Check if cached policy is still fresh based on:
        1. Cache TTL
        2. Data freshness indicators
        3. Policy version/hash verification
        """
        # Check cache age
        cached_at = policy.get("cached_at")
        if cached_at:
            cache_age = datetime.now(timezone.utc) - datetime.fromisoformat(cached_at)
            if cache_age.total_seconds() > self.TTL_SECONDS:
                return False
        
        # Check data freshness
        freshness = policy.get("data_freshness", {})
        if freshness:
            # Check if any critical data is stale
            for source, info in freshness.items():
                if info.get("stale", False):
                    return False
                
                last_sync = info.get("last_sync")
                if last_sync:
                    sync_age = datetime.now(timezone.utc) - datetime.fromisoformat(last_sync)
                    # Consider stale if not synced in last hour
                    if sync_age.total_seconds() > 3600:
                        return False
        
        # Verify policy hash if available
        if "sha256" in policy:
            expected_hash = await self._get_current_policy_hash(policy.get("clinic_id"))
            if expected_hash and expected_hash != policy["sha256"]:
                return False
        
        return True

    async def _get_data_freshness(self, clinic_id: str) -> Dict:
        """
        Get data freshness indicators for the clinic
        """
        freshness = {}
        
        try:
            # Check calendar sync status
            calendar_response = self.supabase.from_("calendar_sync_status")\
                .select("last_sync, sync_status")\
                .eq("clinic_id", clinic_id)\
                .single()\
                .execute()
            
            if calendar_response.data:
                freshness["calendar"] = {
                    "last_sync": calendar_response.data.get("last_sync"),
                    "status": calendar_response.data.get("sync_status"),
                    "stale": calendar_response.data.get("sync_status") != "success"
                }
            
            # Check appointment data freshness
            appointment_response = self.supabase.from_("appointments")\
                .select("updated_at")\
                .eq("clinic_id", clinic_id)\
                .order("updated_at", desc=True)\
                .limit(1)\
                .execute()
            
            if appointment_response.data:
                freshness["appointments"] = {
                    "last_update": appointment_response.data[0].get("updated_at"),
                    "stale": False
                }
            
        except Exception as e:
            logger.warning(f"Error checking data freshness: {e}")
        
        return freshness

    async def _get_current_policy_hash(self, clinic_id: str) -> Optional[str]:
        """Get the current policy hash from database"""
        try:
            response = self.supabase.from_("policy_snapshots")\
                .select("sha256")\
                .eq("clinic_id", clinic_id)\
                .eq("status", "active")\
                .single()\
                .execute()
            
            if response.data:
                return response.data.get("sha256")
        except Exception as e:
            logger.warning(f"Error fetching policy hash: {e}")
        
        return None

    async def warm_cache(self, clinic_ids: list):
        """Pre-populate cache for a list of clinics"""
        logger.info(f"Warming cache for {len(clinic_ids)} clinics")
        
        for clinic_id in clinic_ids:
            try:
                policy = await self._fetch_from_db(clinic_id, None)
                if policy:
                    cache_key = self._make_cache_key(clinic_id)
                    await self._populate_caches(cache_key, policy)
            except Exception as e:
                logger.warning(f"Error warming cache for {clinic_id}: {e}")

    def get_stats(self) -> Dict:
        """Get cache statistics"""
        total_hits = self.stats["memory_hits"] + self.stats["redis_hits"] + self.stats["db_hits"]
        total_requests = total_hits + self.stats["misses"]
        
        return {
            **self.stats,
            "memory_size": self.memory_cache.size(),
            "hit_rate": (total_hits / max(total_requests, 1)) * 100,
            "memory_hit_rate": (self.stats["memory_hits"] / max(total_requests, 1)) * 100,
            "redis_hit_rate": (self.stats["redis_hits"] / max(total_requests, 1)) * 100
        }

    def reset_stats(self):
        """Reset cache statistics"""
        self.stats = {
            "memory_hits": 0,
            "redis_hits": 0,
            "db_hits": 0,
            "misses": 0,
            "evictions": 0
        }