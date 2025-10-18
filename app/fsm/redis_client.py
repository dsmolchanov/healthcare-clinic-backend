"""
Redis client for FSM state management with atomic CAS operations.

This module provides:
- Async Redis connection pooling for high performance
- Lua-based Compare-And-Set (CAS) for atomic state transitions
- Version-based optimistic locking to prevent race conditions

Redis Key Schema:
    fsm:state:{conversation_id} - FSM state JSON (TTL: 24h)
    fsm:idempotency:{message_sid} - Cached webhook responses (TTL: 24h)

Environment Variables:
    REDIS_URL - Redis connection URL (default: redis://localhost:6379)
"""

import logging
import os
from typing import Optional

import redis.asyncio as redis

logger = logging.getLogger(__name__)

# Lua script for atomic Compare-And-Set (CAS) operation
#
# This script ensures atomic state transitions with version checking:
# 1. If key doesn't exist and expected_version=0, create it (initial state)
# 2. If key exists, check version matches expected_version before updating
# 3. Return 1 on success, 0 on version conflict
#
# Args:
#   KEYS[1] - Redis key to update
#   ARGV[1] - expected_version (current version before update)
#   ARGV[2] - new_value (JSON serialized state with incremented version)
#   ARGV[3] - ttl (expiration in seconds, default 86400 = 24h)
#
# Returns:
#   1 - Success (version matched, value updated)
#   0 - Conflict (version mismatch or unexpected state)
CAS_LUA_SCRIPT = """
local current = redis.call('GET', KEYS[1])
if current == false then
    -- Key doesn't exist - only allow if expected_version is 0 (initial state)
    if tonumber(ARGV[1]) == 0 then
        redis.call('SET', KEYS[1], ARGV[2], 'EX', ARGV[3])
        return 1
    else
        -- Expected version != 0 but key missing - conflict
        return 0
    end
else
    -- Key exists - verify version matches
    local current_obj = cjson.decode(current)
    if current_obj.version == tonumber(ARGV[1]) then
        redis.call('SET', KEYS[1], ARGV[2], 'EX', ARGV[3])
        return 1
    else
        -- Version mismatch - conflict
        return 0
    end
end
"""


class RedisClient:
    """
    Redis client with connection pooling and atomic CAS operations.

    Features:
    - Async connection pooling (max 50 connections)
    - Lua-based CAS for race-condition-free state updates
    - Automatic UTF-8 encoding/decoding
    - Connection lifecycle management

    Usage:
        client = RedisClient()
        await client.connect()

        # Atomic state update
        success = await client.cas_set(
            key="fsm:state:conv123",
            expected_version=5,
            new_value='{"version": 6, "state": "completed"}',
            ttl=86400
        )

        await client.close()
    """

    def __init__(self):
        """Initialize Redis client (connection created on connect())."""
        self.redis: Optional[redis.Redis] = None
        self.cas_script = None
        self._connected = False

    async def connect(self) -> None:
        """
        Initialize Redis connection pool and register Lua script.

        Raises:
            redis.RedisError: If connection fails
            Exception: If Lua script registration fails
        """
        if self._connected:
            logger.warning("Redis client already connected, skipping reconnect")
            return

        redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
        logger.info(f"Connecting to Redis at {redis_url}")

        try:
            self.redis = await redis.from_url(
                redis_url,
                encoding="utf-8",
                decode_responses=True,
                max_connections=50,
                socket_connect_timeout=5,
                socket_keepalive=True,
            )

            # Test connection
            await self.redis.ping()
            logger.info("Redis connection established successfully")

            # Register Lua CAS script
            self.cas_script = self.redis.register_script(CAS_LUA_SCRIPT)
            logger.info("Lua CAS script registered successfully")

            # Verify Lua support (log warning if not available)
            try:
                # Test script execution with dummy values
                test_result = await self.cas_script(
                    keys=["__test_lua_support"],
                    args=[0, '{"version": 0}', 60]
                )
                await self.redis.delete("__test_lua_support")
                logger.info("Lua scripting support verified")
            except Exception as e:
                logger.warning(
                    f"Lua scripting may not be fully supported: {e}. "
                    "CAS operations may fail. Consider using Redis 2.6+ with Lua support."
                )

            self._connected = True

        except redis.RedisError as e:
            logger.error(f"Failed to connect to Redis: {e}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error during Redis connection: {e}")
            raise

    async def close(self) -> None:
        """
        Close Redis connection and release resources.

        Safe to call multiple times (idempotent).
        """
        if self.redis:
            try:
                await self.redis.close()
                logger.info("Redis connection closed")
            except Exception as e:
                logger.error(f"Error closing Redis connection: {e}")
            finally:
                self.redis = None
                self.cas_script = None
                self._connected = False

    async def cas_set(
        self,
        key: str,
        expected_version: int,
        new_value: str,
        ttl: int = 86400
    ) -> bool:
        """
        Atomic compare-and-set operation with version checking.

        This method ensures atomic state transitions by:
        1. Checking current version matches expected_version
        2. Only updating if versions match (optimistic locking)
        3. Setting TTL to auto-expire stale state

        Args:
            key: Redis key to update (e.g., "fsm:state:conv123")
            expected_version: Current version before update (for conflict detection)
            new_value: JSON string with incremented version
            ttl: Time-to-live in seconds (default: 86400 = 24 hours)

        Returns:
            True if update succeeded (version matched)
            False if version conflict occurred (concurrent update detected)

        Raises:
            RuntimeError: If Redis client not connected
            redis.RedisError: If Redis operation fails

        Example:
            # Initial state (version 0)
            success = await client.cas_set(
                "fsm:state:conv123",
                expected_version=0,
                new_value='{"version": 1, "state": "greeting"}',
                ttl=86400
            )
            # success = True

            # Concurrent update attempt with stale version
            success = await client.cas_set(
                "fsm:state:conv123",
                expected_version=0,  # Stale!
                new_value='{"version": 1, "state": "collecting_slots"}',
                ttl=86400
            )
            # success = False (version conflict)
        """
        if not self._connected or not self.redis or not self.cas_script:
            raise RuntimeError(
                "Redis client not connected. Call connect() first."
            )

        try:
            result = await self.cas_script(
                keys=[key],
                args=[expected_version, new_value, ttl]
            )
            return result == 1
        except redis.RedisError as e:
            logger.error(f"Redis CAS operation failed for key {key}: {e}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error during CAS operation: {e}")
            raise

    async def get(self, key: str) -> Optional[str]:
        """
        Get value from Redis.

        Args:
            key: Redis key to retrieve

        Returns:
            Value as string, or None if key doesn't exist

        Raises:
            RuntimeError: If Redis client not connected
        """
        if not self._connected or not self.redis:
            raise RuntimeError("Redis client not connected. Call connect() first.")

        return await self.redis.get(key)

    async def set(
        self,
        key: str,
        value: str,
        ttl: Optional[int] = None
    ) -> bool:
        """
        Set value in Redis with optional TTL.

        Args:
            key: Redis key
            value: Value to store
            ttl: Time-to-live in seconds (optional)

        Returns:
            True if successful

        Raises:
            RuntimeError: If Redis client not connected
        """
        if not self._connected or not self.redis:
            raise RuntimeError("Redis client not connected. Call connect() first.")

        if ttl:
            return await self.redis.set(key, value, ex=ttl)
        return await self.redis.set(key, value)

    async def delete(self, key: str) -> int:
        """
        Delete key from Redis.

        Args:
            key: Redis key to delete

        Returns:
            Number of keys deleted (0 or 1)

        Raises:
            RuntimeError: If Redis client not connected
        """
        if not self._connected or not self.redis:
            raise RuntimeError("Redis client not connected. Call connect() first.")

        return await self.redis.delete(key)

    @property
    def is_connected(self) -> bool:
        """Check if Redis client is connected."""
        return self._connected


# Singleton instance for application-wide use
redis_client = RedisClient()
