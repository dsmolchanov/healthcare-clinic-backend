"""Distributed locking utilities for session management."""

import asyncio
import os
import uuid
import logging
from contextlib import asynccontextmanager

logger = logging.getLogger(__name__)

LOCK_TTL_MS = int(os.getenv("BOUNDARY_LOCK_TTL_MS", "5000"))

# Lua script for atomic compare-and-delete
COMPARE_AND_DELETE = """
if redis.call("get", KEYS[1]) == ARGV[1] then
  return redis.call("del", KEYS[1])
else
  return 0
end
"""

class BoundaryLock:
    """Token-based distributed lock for session boundary critical section."""

    def __init__(self, redis_client):
        """
        Args:
            redis_client: Synchronous Redis client (redis-py)
        """
        self.redis = redis_client

    @asynccontextmanager
    async def acquire(self, phone: str, clinic_id: str, timeout_ms: int = LOCK_TTL_MS):
        """
        Acquire distributed lock with automatic release.

        Args:
            phone: User phone number
            clinic_id: Clinic identifier
            timeout_ms: Lock TTL in milliseconds

        Raises:
            RuntimeError: If lock cannot be acquired after retries
        """
        lock_key = f"boundary_lock:{clinic_id}:{phone}"
        token = str(uuid.uuid4())  # Unique token for this lock acquisition
        acquired = False

        try:
            # Try to acquire lock (NX = only if not exists, PX = TTL in ms)
            # Using synchronous Redis client
            acquired = self.redis.set(lock_key, token, nx=True, px=timeout_ms)

            if not acquired:
                # Another request is processing boundary - wait with jittered backoff
                for i in range(8):  # Max 8 retries
                    await asyncio.sleep(0.05 * (i + 1))  # Jittered backoff (50ms, 100ms, 150ms...)
                    acquired = self.redis.set(lock_key, token, nx=True, px=timeout_ms)
                    if acquired:
                        break

                if not acquired:
                    raise RuntimeError(
                        f"Boundary lock busy for {phone[:3]}***:{clinic_id[:8]} "
                        f"after {timeout_ms}ms"
                    )

            logger.debug(f"🔒 Acquired boundary lock: {lock_key} (token: {token[:8]})")
            yield

        finally:
            if acquired:
                try:
                    # Only delete if we still own the lock (compare-and-delete)
                    self.redis.eval(
                        COMPARE_AND_DELETE,
                        1,  # number of keys
                        lock_key,
                        token
                    )
                    logger.debug(f"🔓 Released boundary lock: {lock_key}")
                except Exception as e:
                    logger.warning(f"Failed to release lock {lock_key}: {e}")
