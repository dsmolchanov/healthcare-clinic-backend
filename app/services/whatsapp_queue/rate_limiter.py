"""
Token Bucket Rate Limiter
Redis-based rate limiting to prevent WhatsApp bans
"""
import asyncio
import time
from redis import Redis
from .queue import get_redis_client
from .config import logger


class TokenBucket:
    """Token bucket rate limiter using Redis for distributed rate limiting"""

    def __init__(self, instance: str, tokens_per_second: float, capacity: int):
        """
        Initialize token bucket rate limiter

        Args:
            instance: WhatsApp instance name
            tokens_per_second: Rate of token refill (messages per second)
            capacity: Maximum burst capacity
        """
        self.instance = instance
        self.tokens_per_second = tokens_per_second
        self.capacity = capacity
        self.redis = get_redis_client()

        # Redis keys for this instance
        self.bucket_key = f"wa:{instance}:bucket"
        self.timestamp_key = f"wa:{instance}:bucket:ts"

    def _refill(self):
        """Refill tokens based on elapsed time"""
        now = time.time()
        last_ts = self.redis.get(self.timestamp_key)

        if last_ts is None:
            # Initialize bucket
            self.redis.set(self.timestamp_key, str(now))
            self.redis.set(self.bucket_key, self.capacity)
            logger.debug(f"Initialized token bucket for {self.instance}")
            return

        last = float(last_ts)
        elapsed = max(0.0, now - last)
        tokens_to_add = int(elapsed * self.tokens_per_second)

        if tokens_to_add > 0:
            self.redis.set(self.timestamp_key, str(now))
            current = int(self.redis.get(self.bucket_key) or 0)
            new_count = min(self.capacity, current + tokens_to_add)
            self.redis.set(self.bucket_key, new_count)
            logger.debug(f"Refilled {tokens_to_add} tokens for {self.instance}, now at {new_count}")

    def _take_token(self) -> bool:
        """
        Try to take one token from bucket

        Returns:
            True if token was available and taken, False otherwise
        """
        self._refill()

        # Atomic decrement if available
        with self.redis.pipeline() as pipe:
            try:
                pipe.watch(self.bucket_key)
                current = int(pipe.get(self.bucket_key) or 0)

                if current <= 0:
                    pipe.unwatch()
                    return False

                pipe.multi()
                pipe.decr(self.bucket_key)
                pipe.execute()
                logger.debug(f"Token taken for {self.instance}, {current-1} remaining")
                return True
            except Exception as e:
                logger.warning(f"Failed to take token: {e}")
                return False

    async def wait_for_token(self):
        """
        Wait until a token is available (with exponential backoff)

        This method will block until a token is available, implementing
        exponential backoff to avoid excessive Redis polling.
        """
        attempt = 0
        while not self._take_token():
            # Exponential backoff (up to 1 second)
            delay = min(1.0, 0.1 * (2 ** attempt))
            await asyncio.sleep(delay)
            attempt += 1

            if attempt >= 10:  # Max ~10 seconds wait
                logger.warning(f"Long wait for token on {self.instance}")
                attempt = 5  # Reset to moderate backoff

        logger.debug(f"Token acquired for {self.instance} after {attempt} attempts")