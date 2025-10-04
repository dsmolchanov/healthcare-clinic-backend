"""
Redis Cache Manager for Performance Optimization
Implements caching layer with automatic invalidation
"""

import json
import logging
import hashlib
from typing import Any, Dict, List, Optional, Union, Callable
from datetime import datetime, timedelta
import asyncio
import pickle
from functools import wraps
from enum import Enum

import redis.asyncio as redis
from redis.asyncio.lock import Lock
from redis.exceptions import RedisError, ConnectionError as RedisConnectionError

from app.core.config import settings

logger = logging.getLogger(__name__)


class CacheTTL(Enum):
    """Standard TTL durations for different cache types"""
    SHORT = 60  # 1 minute - for frequently changing data
    MEDIUM = 300  # 5 minutes - for moderate frequency updates
    LONG = 3600  # 1 hour - for rarely changing data
    VERY_LONG = 86400  # 24 hours - for static data
    CUSTOM = 0  # Use custom TTL


class CacheNamespace(Enum):
    """Cache namespaces for organization"""
    APPOINTMENTS = "appointments"
    PATIENTS = "patients"
    DOCTORS = "doctors"
    AVAILABILITY = "availability"
    RULES = "rules"
    KNOWLEDGE = "knowledge"
    SYNC = "sync"
    AUDIT = "audit"


class RedisManager:
    """Manages Redis connections and operations"""
    
    def __init__(
        self,
        redis_url: Optional[str] = None,
        max_connections: int = 50,
        decode_responses: bool = False,
        enable_cluster: bool = False
    ):
        self.redis_url = redis_url or settings.REDIS_URL or "redis://localhost:6379"
        self.max_connections = max_connections
        self.decode_responses = decode_responses
        self.enable_cluster = enable_cluster
        self.client: Optional[redis.Redis] = None
        self.connection_pool = None
        self._lock = asyncio.Lock()
        self._is_connected = False
        
    async def connect(self) -> None:
        """Establish Redis connection"""
        async with self._lock:
            if self._is_connected:
                return
            
            try:
                # Create connection pool
                self.connection_pool = redis.ConnectionPool.from_url(
                    self.redis_url,
                    max_connections=self.max_connections,
                    decode_responses=self.decode_responses
                )
                
                # Create Redis client
                self.client = redis.Redis(
                    connection_pool=self.connection_pool
                )
                
                # Test connection
                await self.client.ping()
                
                self._is_connected = True
                logger.info(f"Connected to Redis at {self.redis_url}")
                
            except Exception as e:
                logger.error(f"Failed to connect to Redis: {e}")
                raise
    
    async def disconnect(self) -> None:
        """Close Redis connection"""
        async with self._lock:
            if self.client:
                await self.client.close()
                await self.connection_pool.disconnect()
                self._is_connected = False
                logger.info("Disconnected from Redis")
    
    async def ensure_connected(self) -> None:
        """Ensure Redis is connected"""
        if not self._is_connected:
            await self.connect()
    
    def _generate_key(
        self,
        namespace: CacheNamespace,
        identifier: str,
        params: Optional[Dict] = None
    ) -> str:
        """Generate cache key with namespace and optional parameters"""
        key_parts = [namespace.value, identifier]
        
        if params:
            # Sort params for consistent key generation
            sorted_params = json.dumps(params, sort_keys=True)
            param_hash = hashlib.md5(sorted_params.encode()).hexdigest()[:8]
            key_parts.append(param_hash)
        
        return ":".join(key_parts)
    
    async def get(
        self,
        namespace: CacheNamespace,
        identifier: str,
        params: Optional[Dict] = None
    ) -> Optional[Any]:
        """Get value from cache"""
        await self.ensure_connected()
        
        key = self._generate_key(namespace, identifier, params)
        
        try:
            value = await self.client.get(key)
            
            if value:
                # Try to deserialize as JSON first
                try:
                    return json.loads(value)
                except (json.JSONDecodeError, TypeError):
                    # Fall back to pickle for complex objects
                    try:
                        return pickle.loads(value)
                    except:
                        # Return as string if all else fails
                        return value.decode() if isinstance(value, bytes) else value
            
            return None
            
        except RedisError as e:
            logger.error(f"Error getting cache key {key}: {e}")
            return None
    
    async def set(
        self,
        namespace: CacheNamespace,
        identifier: str,
        value: Any,
        ttl: Union[CacheTTL, int] = CacheTTL.MEDIUM,
        params: Optional[Dict] = None
    ) -> bool:
        """Set value in cache with TTL"""
        await self.ensure_connected()
        
        key = self._generate_key(namespace, identifier, params)
        
        # Determine TTL
        if isinstance(ttl, CacheTTL):
            ttl_seconds = ttl.value if ttl != CacheTTL.CUSTOM else None
        else:
            ttl_seconds = ttl
        
        try:
            # Serialize value
            try:
                serialized = json.dumps(value)
            except (TypeError, ValueError):
                # Use pickle for complex objects
                serialized = pickle.dumps(value)
            
            # Set with optional TTL
            if ttl_seconds:
                result = await self.client.setex(key, ttl_seconds, serialized)
            else:
                result = await self.client.set(key, serialized)
            
            return bool(result)
            
        except RedisError as e:
            logger.error(f"Error setting cache key {key}: {e}")
            return False
    
    async def delete(
        self,
        namespace: CacheNamespace,
        identifier: str,
        params: Optional[Dict] = None
    ) -> bool:
        """Delete value from cache"""
        await self.ensure_connected()
        
        key = self._generate_key(namespace, identifier, params)
        
        try:
            result = await self.client.delete(key)
            return bool(result)
        except RedisError as e:
            logger.error(f"Error deleting cache key {key}: {e}")
            return False
    
    async def invalidate_namespace(self, namespace: CacheNamespace) -> int:
        """Invalidate all keys in a namespace"""
        await self.ensure_connected()
        
        pattern = f"{namespace.value}:*"
        
        try:
            # Use SCAN to avoid blocking on large keyspaces
            cursor = 0
            deleted_count = 0
            
            while True:
                cursor, keys = await self.client.scan(
                    cursor, match=pattern, count=100
                )
                
                if keys:
                    deleted_count += await self.client.delete(*keys)
                
                if cursor == 0:
                    break
            
            logger.info(f"Invalidated {deleted_count} keys in namespace {namespace.value}")
            return deleted_count
            
        except RedisError as e:
            logger.error(f"Error invalidating namespace {namespace.value}: {e}")
            return 0
    
    async def exists(
        self,
        namespace: CacheNamespace,
        identifier: str,
        params: Optional[Dict] = None
    ) -> bool:
        """Check if key exists in cache"""
        await self.ensure_connected()
        
        key = self._generate_key(namespace, identifier, params)
        
        try:
            return bool(await self.client.exists(key))
        except RedisError as e:
            logger.error(f"Error checking existence of key {key}: {e}")
            return False
    
    async def get_ttl(
        self,
        namespace: CacheNamespace,
        identifier: str,
        params: Optional[Dict] = None
    ) -> Optional[int]:
        """Get remaining TTL for a key"""
        await self.ensure_connected()
        
        key = self._generate_key(namespace, identifier, params)
        
        try:
            ttl = await self.client.ttl(key)
            return ttl if ttl >= 0 else None
        except RedisError as e:
            logger.error(f"Error getting TTL for key {key}: {e}")
            return None
    
    async def acquire_lock(
        self,
        lock_name: str,
        timeout: int = 10,
        blocking: bool = True,
        blocking_timeout: Optional[int] = None
    ) -> Optional[Lock]:
        """Acquire distributed lock"""
        await self.ensure_connected()
        
        lock = Lock(
            self.client,
            name=f"lock:{lock_name}",
            timeout=timeout
        )
        
        try:
            acquired = await lock.acquire(
                blocking=blocking,
                blocking_timeout=blocking_timeout
            )
            
            if acquired:
                return lock
            return None
            
        except RedisError as e:
            logger.error(f"Error acquiring lock {lock_name}: {e}")
            return None
    
    async def release_lock(self, lock: Lock) -> bool:
        """Release distributed lock"""
        try:
            await lock.release()
            return True
        except RedisError as e:
            logger.error(f"Error releasing lock: {e}")
            return False
    
    async def increment(
        self,
        namespace: CacheNamespace,
        identifier: str,
        amount: int = 1,
        ttl: Optional[int] = None
    ) -> Optional[int]:
        """Atomic increment operation"""
        await self.ensure_connected()
        
        key = self._generate_key(namespace, identifier)
        
        try:
            value = await self.client.incrby(key, amount)
            
            if ttl:
                await self.client.expire(key, ttl)
            
            return value
            
        except RedisError as e:
            logger.error(f"Error incrementing key {key}: {e}")
            return None
    
    async def add_to_set(
        self,
        namespace: CacheNamespace,
        identifier: str,
        *values: Any
    ) -> int:
        """Add values to a set"""
        await self.ensure_connected()
        
        key = self._generate_key(namespace, identifier)
        
        try:
            # Serialize values
            serialized_values = [
                json.dumps(v) if not isinstance(v, (str, bytes, int, float)) else v
                for v in values
            ]
            
            return await self.client.sadd(key, *serialized_values)
            
        except RedisError as e:
            logger.error(f"Error adding to set {key}: {e}")
            return 0
    
    async def get_set_members(
        self,
        namespace: CacheNamespace,
        identifier: str
    ) -> List[Any]:
        """Get all members of a set"""
        await self.ensure_connected()
        
        key = self._generate_key(namespace, identifier)
        
        try:
            members = await self.client.smembers(key)
            
            # Deserialize members
            result = []
            for member in members:
                try:
                    # Try JSON deserialization
                    result.append(json.loads(member))
                except (json.JSONDecodeError, TypeError):
                    # Return as-is if not JSON
                    result.append(
                        member.decode() if isinstance(member, bytes) else member
                    )
            
            return result
            
        except RedisError as e:
            logger.error(f"Error getting set members for {key}: {e}")
            return []
    
    async def publish(self, channel: str, message: Any) -> int:
        """Publish message to channel"""
        await self.ensure_connected()
        
        try:
            # Serialize message
            if isinstance(message, (dict, list)):
                serialized = json.dumps(message)
            else:
                serialized = str(message)
            
            return await self.client.publish(channel, serialized)
            
        except RedisError as e:
            logger.error(f"Error publishing to channel {channel}: {e}")
            return 0
    
    async def get_info(self) -> Optional[Dict]:
        """Get Redis server information"""
        await self.ensure_connected()
        
        try:
            info = await self.client.info()
            return info
        except RedisError as e:
            logger.error(f"Error getting Redis info: {e}")
            return None
    
    async def flush_db(self) -> bool:
        """Flush current database (use with caution)"""
        await self.ensure_connected()
        
        try:
            await self.client.flushdb()
            logger.warning("Flushed Redis database")
            return True
        except RedisError as e:
            logger.error(f"Error flushing database: {e}")
            return False


class CacheDecorator:
    """Decorator for caching function results"""
    
    def __init__(self, redis_manager: RedisManager):
        self.redis = redis_manager
    
    def cache(
        self,
        namespace: CacheNamespace,
        ttl: Union[CacheTTL, int] = CacheTTL.MEDIUM,
        key_builder: Optional[Callable] = None
    ):
        """Cache decorator for async functions"""
        def decorator(func):
            @wraps(func)
            async def wrapper(*args, **kwargs):
                # Build cache key
                if key_builder:
                    cache_key = key_builder(*args, **kwargs)
                else:
                    # Default key builder using function name and args
                    cache_key = f"{func.__name__}:{str(args)}:{str(kwargs)}"
                
                # Try to get from cache
                cached_value = await self.redis.get(namespace, cache_key)
                
                if cached_value is not None:
                    logger.debug(f"Cache hit for {namespace.value}:{cache_key}")
                    return cached_value
                
                # Call function and cache result
                logger.debug(f"Cache miss for {namespace.value}:{cache_key}")
                result = await func(*args, **kwargs)
                
                if result is not None:
                    await self.redis.set(namespace, cache_key, result, ttl)
                
                return result
            
            return wrapper
        return decorator
    
    def invalidate_on_update(
        self,
        namespace: CacheNamespace,
        pattern: Optional[str] = None
    ):
        """Invalidate cache after function execution"""
        def decorator(func):
            @wraps(func)
            async def wrapper(*args, **kwargs):
                # Execute function
                result = await func(*args, **kwargs)
                
                # Invalidate cache
                if pattern:
                    # Invalidate specific pattern
                    await self.redis.delete(namespace, pattern)
                else:
                    # Invalidate entire namespace
                    await self.redis.invalidate_namespace(namespace)
                
                return result
            
            return wrapper
        return decorator


# Singleton instance
redis_manager = RedisManager()
cache_decorator = CacheDecorator(redis_manager)