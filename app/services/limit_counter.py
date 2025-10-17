"""
Limit occurrence counter storage using Redis with graceful fallbacks.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional, Tuple
from uuid import uuid4

from redis.exceptions import RedisError

try:
    from app.config import get_redis_client
except Exception:  # pragma: no cover - configuration import failure
    get_redis_client = None  # type: ignore


@dataclass(frozen=True)
class LimitReservationToken:
    key: str
    member: str


class LimitCounterStore:
    """
    Provides atomic reservation counters backed by Redis sorted sets.

    Counts occurrences within a rolling time window. If Redis is unavailable,
    the store fails open (returns allowed=True).
    """

    _RESERVE_SCRIPT = """
    redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', ARGV[1] - ARGV[2])
    local count = redis.call('ZCARD', KEYS[1])
    if count >= tonumber(ARGV[3]) then
        return {0, count}
    end
    redis.call('ZADD', KEYS[1], ARGV[1], ARGV[4])
    redis.call('EXPIRE', KEYS[1], math.floor(ARGV[2] * 2))
    return {1, count + 1}
    """

    _RELEASE_SCRIPT = """
    local removed = redis.call('ZREM', KEYS[1], ARGV[1])
    return removed
    """

    def __init__(self, redis_client=None):
        self.redis = redis_client
        self._reserve_script = None
        self._release_script = None

        if self.redis is None and get_redis_client:
            try:
                self.redis = get_redis_client()
            except Exception:
                self.redis = None

        if self.redis is not None:
            try:
                self._reserve_script = self.redis.register_script(self._RESERVE_SCRIPT)
                self._release_script = self.redis.register_script(self._RELEASE_SCRIPT)
            except RedisError:
                self.redis = None
                self._reserve_script = None
                self._release_script = None

    def reserve(
        self,
        key: str,
        window_seconds: int,
        max_occurrences: int
    ) -> Tuple[bool, Optional[LimitReservationToken], Optional[int]]:
        """
        Attempt to reserve within the limit.

        Returns:
            allowed: Whether reservation is permitted.
            token: Token to release in case of rollback.
            count: Current count after reservation attempt.
        """
        if self.redis is None or self._reserve_script is None:
            return True, None, None

        now = time.time()
        member = f"{now}:{uuid4()}"
        try:
            allowed, count = self._reserve_script(
                keys=[key],
                args=[now, window_seconds, max_occurrences, member]
            )
            allowed = bool(allowed)
            if not allowed:
                return False, None, int(count)
            token = LimitReservationToken(key=key, member=member)
            return True, token, int(count)
        except RedisError:
            return True, None, None

    def release(self, token: Optional[LimitReservationToken]) -> None:
        """Release a previously reserved occurrence."""
        if token is None or self.redis is None or self._release_script is None:
            return
        try:
            self._release_script(keys=[token.key], args=[token.member])
        except RedisError:
            pass

