"""
Enhanced Cache Service with Generation Tokens, Compression, and Distributed Locks

This service implements:
- Generation-based cache invalidation (using healthcare.cache_invalidation table)
- Zstandard compression for large objects (>10KB)
- Distributed locks to prevent cache stampede
- Integration with get_clinic_bundle() RPC
- Redis cluster-friendly hash-tag keys: {clinic_id}
"""

import asyncio
import hashlib
import json
import logging
import time
import zstandard as zstd
from typing import Dict, Any, Optional, Tuple
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class CacheConfig:
    """Cache configuration"""
    default_ttl: int = 3600  # 1 hour
    lock_timeout: int = 10  # 10 seconds
    lock_retry_delay: float = 0.1  # 100ms
    compression_threshold: int = 10240  # 10KB
    max_lock_wait: int = 30  # 30 seconds max wait for lock


class CacheService:
    """
    Enhanced Redis cache service with generation tokens, compression, and distributed locks.

    Features:
    - Generation-based invalidation: Checks healthcare.cache_invalidation table
    - Compression: Uses zstandard for objects >10KB
    - Distributed locks: Prevents cache stampede using Redis SETNX
    - Hash-tag keys: {clinic_id} for Redis Cluster compatibility
    """

    def __init__(self, redis_client, supabase_client, config: Optional[CacheConfig] = None):
        """
        Initialize cache service

        Args:
            redis_client: Redis client instance
            supabase_client: Supabase client for database access
            config: Cache configuration (uses defaults if not provided)
        """
        self.redis = redis_client
        self.supabase = supabase_client
        self.config = config or CacheConfig()
        self.compressor = zstd.ZstdCompressor(level=3)
        self.decompressor = zstd.ZstdDecompressor()

    def _make_key(self, clinic_id: str, data_type: str) -> str:
        """
        Generate cache key with hash-tag for Redis Cluster

        Hash-tag pattern: {clinic_id} ensures all clinic data goes to same slot
        """
        return f"clinic:{{{clinic_id}}}:{data_type}"

    def _make_lock_key(self, cache_key: str) -> str:
        """Generate lock key for distributed locking"""
        return f"{cache_key}:lock"

    def _make_generation_key(self, clinic_id: str, table_name: str) -> str:
        """Generate generation tracking key"""
        return f"gen:{{{clinic_id}}}:{table_name}"

    async def _acquire_lock(self, lock_key: str, timeout: int) -> bool:
        """
        Acquire distributed lock using Redis SETNX

        Args:
            lock_key: Lock key
            timeout: Lock timeout in seconds

        Returns:
            True if lock acquired, False otherwise
        """
        # Try to set lock with NX (only if not exists) and EX (expiration)
        acquired = self.redis.set(lock_key, "1", nx=True, ex=timeout)
        return bool(acquired)

    async def _release_lock(self, lock_key: str):
        """Release distributed lock"""
        self.redis.delete(lock_key)

    async def _wait_for_lock(self, lock_key: str) -> bool:
        """
        Wait for lock to be released

        Returns:
            True if lock was released, False if timeout
        """
        start_time = time.time()
        while time.time() - start_time < self.config.max_lock_wait:
            if not self.redis.exists(lock_key):
                return True
            await asyncio.sleep(self.config.lock_retry_delay)
        return False

    def _compress(self, data: bytes) -> Tuple[bytes, bool]:
        """
        Compress data if it exceeds threshold

        Returns:
            (compressed_data, was_compressed)
        """
        if len(data) > self.config.compression_threshold:
            compressed = self.compressor.compress(data)
            logger.debug(f"Compressed {len(data)} bytes -> {len(compressed)} bytes ({100 * len(compressed) / len(data):.1f}%)")
            return compressed, True
        return data, False

    def _decompress(self, data: bytes, compressed: bool) -> bytes:
        """Decompress data if it was compressed"""
        if compressed:
            return self.decompressor.decompress(data)
        return data

    async def _get_current_generation(self, clinic_id: str, table_name: str) -> Optional[int]:
        """
        Get current generation number from healthcare.cache_invalidation table

        Returns:
            Generation number or None if not found
        """
        try:
            result = await self.supabase.schema('healthcare').table('cache_invalidation').select(
                'generation'
            ).eq('clinic_id', clinic_id).eq('table_name', table_name).maybe_single().execute()

            if result.data:
                return result.data.get('generation')
            return None
        except Exception as e:
            logger.warning(f"Could not fetch generation for {table_name}: {e}")
            return None

    async def _is_cache_valid(self, clinic_id: str, table_name: str, cached_generation: Optional[int]) -> bool:
        """
        Check if cached data is still valid by comparing generations

        Returns:
            True if cache is valid, False if stale
        """
        if cached_generation is None:
            return False

        current_gen = await self._get_current_generation(clinic_id, table_name)
        if current_gen is None:
            # No generation tracking yet, assume valid
            return True

        is_valid = cached_generation == current_gen
        if not is_valid:
            logger.debug(f"Cache stale for {table_name}: gen {cached_generation} != {current_gen}")
        return is_valid

    async def get_clinic_bundle(self, clinic_id: str) -> Optional[Dict[str, Any]]:
        """
        Get complete clinic bundle using RPC from Task #1

        This method:
        1. Checks generation tokens for invalidation
        2. Tries cache with compression support
        3. Acquires distributed lock on miss
        4. Loads from get_clinic_bundle() RPC
        5. Compresses and caches result

        Returns:
            Dictionary with clinic, doctors, services, faqs or None
        """
        cache_key = self._make_key(clinic_id, "bundle")
        gen_key = self._make_generation_key(clinic_id, "bundle")
        lock_key = self._make_lock_key(cache_key)

        # Try cache first
        try:
            cached_data = self.redis.get(cache_key)
            cached_gen_str = self.redis.get(gen_key)

            if cached_data:
                try:
                    # Parse metadata (first byte indicates if compressed)
                    is_compressed = cached_data[0] == 1
                    data_bytes = cached_data[1:]

                    # Decompress if needed
                    decompressed = self._decompress(data_bytes, is_compressed)
                    bundle = json.loads(decompressed.decode('utf-8'))

                    # Validate generation
                    cached_gen = int(cached_gen_str) if cached_gen_str else None
                    if await self._is_cache_valid(clinic_id, 'clinics', cached_gen):
                        logger.debug(f"✅ Cache HIT: bundle for clinic {clinic_id}")
                        return bundle
                    else:
                        logger.debug(f"🔄 Cache STALE: invalidating bundle for clinic {clinic_id}")
                        self.redis.delete(cache_key, gen_key)
                except (UnicodeDecodeError, json.JSONDecodeError, zstd.ZstdError) as decode_error:
                    logger.warning(f"Cache data corrupted, deleting: {decode_error}")
                    self.redis.delete(cache_key, gen_key)
        except Exception as e:
            logger.warning(f"Cache read error: {e}")

        # Cache miss - acquire lock to prevent stampede
        logger.debug(f"❌ Cache MISS: fetching bundle for clinic {clinic_id}")

        if not await self._acquire_lock(lock_key, self.config.lock_timeout):
            # Another process is loading, wait for it
            logger.debug(f"⏳ Waiting for lock on {cache_key}")
            if await self._wait_for_lock(lock_key):
                # Lock released, try cache again
                cached_data = self.redis.get(cache_key)
                if cached_data:
                    try:
                        is_compressed = cached_data[0] == 1
                        data_bytes = cached_data[1:]
                        decompressed = self._decompress(data_bytes, is_compressed)
                        return json.loads(decompressed.decode('utf-8'))
                    except (UnicodeDecodeError, json.JSONDecodeError, zstd.ZstdError) as decode_error:
                        logger.warning(f"Cache data corrupted after lock wait: {decode_error}")
                        self.redis.delete(cache_key, gen_key)
            else:
                logger.warning(f"Lock wait timeout for {cache_key}")

        try:
            # Load from RPC (execute() is synchronous, not async)
            start_time = time.time()
            result = self.supabase.rpc('get_clinic_bundle', {'p_clinic_id': clinic_id}).execute()
            rpc_time_ms = (time.time() - start_time) * 1000
            logger.info(f"📊 RPC get_clinic_bundle took {rpc_time_ms:.2f}ms for clinic {clinic_id}")

            if not result.data:
                logger.warning(f"RPC returned no data for clinic {clinic_id}")
                return None

            bundle = result.data if isinstance(result.data, dict) else json.loads(result.data)

            # Get current generation for caching
            current_gen = await self._get_current_generation(clinic_id, 'clinics')

            # Serialize and compress
            json_bytes = json.dumps(bundle).encode('utf-8')
            compressed_bytes, was_compressed = self._compress(json_bytes)

            # Prepend compression flag (1 byte)
            cache_value = bytes([1 if was_compressed else 0]) + compressed_bytes

            # Cache with TTL
            self.redis.setex(cache_key, self.config.default_ttl, cache_value)
            if current_gen is not None:
                self.redis.setex(gen_key, self.config.default_ttl, str(current_gen))

            logger.info(f"✅ Cached bundle for clinic {clinic_id} (compressed: {was_compressed}, size: {len(cache_value)} bytes)")

            return bundle

        except Exception as e:
            logger.error(f"Error loading clinic bundle: {e}")
            return None
        finally:
            await self._release_lock(lock_key)

    async def get_patient_profile(self, phone_number: str, clinic_id: str) -> Optional[Dict[str, Any]]:
        """
        Get patient profile with phone number hashing (SHA-256)

        Args:
            phone_number: Patient phone number
            clinic_id: Clinic ID for namespacing

        Returns:
            Patient profile dict or None
        """
        # Hash phone number for privacy
        phone_hash = hashlib.sha256(phone_number.encode()).hexdigest()
        cache_key = f"patient:{{{clinic_id}}}:{phone_hash}"

        try:
            cached_data = self.redis.get(cache_key)
            if cached_data:
                logger.debug(f"✅ Cache HIT: patient profile (hashed)")
                return json.loads(cached_data)

            # Cache miss - load from database (execute() is synchronous, not async)
            result = self.supabase.schema('healthcare').table('patients').select(
                'id,first_name,last_name,phone,email,date_of_birth,preferred_language'
            ).eq('phone', phone_number).eq('clinic_id', clinic_id).maybe_single().execute()

            if result.data:
                profile = result.data
                # Cache for default TTL
                self.redis.setex(cache_key, self.config.default_ttl, json.dumps(profile))
                logger.info(f"✅ Cached patient profile (hashed)")
                return profile

            return None

        except Exception as e:
            logger.error(f"Error getting patient profile: {e}")
            return None

    async def hydrate_context(
        self,
        clinic_id: str,
        phone: str,
        session_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Hydrate complete context for message processing in a single optimized call

        Combines:
        - Clinic bundle (clinic, doctors, services, FAQs)
        - Patient profile
        - Session state

        Performance target: <100ms (replaces 7-8 serial queries taking ~2.5s)

        Args:
            clinic_id: Clinic ID
            phone: Patient phone number
            session_id: Optional session ID for state lookup

        Returns:
            Dictionary with complete hydrated context
        """
        start_time = time.time()

        # Run clinic bundle and patient profile in parallel
        clinic_bundle_task = self.get_clinic_bundle(clinic_id)
        patient_profile_task = self.get_patient_profile(phone, clinic_id)

        # Get session state if session_id provided
        session_state = None
        if session_id:
            try:
                # execute() is synchronous, not async
                result = self.supabase.table('conversation_sessions').select(
                    'turn_status,last_agent_action,pending_since'
                ).eq('id', session_id).maybe_single().execute()
                session_state = result.data if result.data else {}
            except Exception as e:
                logger.warning(f"Could not fetch session state: {e}")
                session_state = {}

        # Wait for parallel tasks
        clinic_bundle, patient_profile = await asyncio.gather(
            clinic_bundle_task,
            patient_profile_task,
            return_exceptions=True
        )

        # Handle errors
        if isinstance(clinic_bundle, Exception):
            logger.error(f"Error loading clinic bundle: {clinic_bundle}")
            clinic_bundle = None
        if isinstance(patient_profile, Exception):
            logger.error(f"Error loading patient profile: {patient_profile}")
            patient_profile = None

        # Build hydrated context
        context = {
            'clinic': clinic_bundle.get('clinic', {}) if clinic_bundle else {},
            'doctors': clinic_bundle.get('doctors', []) if clinic_bundle else [],
            'services': clinic_bundle.get('services', []) if clinic_bundle else [],
            'faqs': clinic_bundle.get('faqs', []) if clinic_bundle else [],
            'patient': patient_profile or {},
            'session_state': session_state or {}
        }

        latency_ms = (time.time() - start_time) * 1000
        logger.info(f"✅ Hydrated context in {latency_ms:.2f}ms (target: <100ms)")

        return context

    def invalidate_clinic_bundle(self, clinic_id: str):
        """Invalidate cached clinic bundle"""
        cache_key = self._make_key(clinic_id, "bundle")
        gen_key = self._make_generation_key(clinic_id, "bundle")
        self.redis.delete(cache_key, gen_key)
        logger.info(f"🗑️ Invalidated bundle cache for clinic {clinic_id}")

    def get_stats(self) -> Dict[str, Any]:
        """
        Get cache statistics

        Returns:
            Dictionary with cache stats
        """
        try:
            info = self.redis.info('stats')
            return {
                'total_commands_processed': info.get('total_commands_processed', 0),
                'keyspace_hits': info.get('keyspace_hits', 0),
                'keyspace_misses': info.get('keyspace_misses', 0),
                'hit_rate': (
                    info.get('keyspace_hits', 0) /
                    max(info.get('keyspace_hits', 0) + info.get('keyspace_misses', 0), 1)
                    * 100
                )
            }
        except Exception as e:
            logger.error(f"Error getting cache stats: {e}")
            return {}
