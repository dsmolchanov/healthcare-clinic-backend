"""
Circuit breaker implementation for external service calls.
Prevents cascade failures by failing fast when services are down.

FIXES APPLIED:
- Specific exceptions per service (not generic Exception)
- HALF_OPEN allows only 1 trial request (prevents thundering herd)
- TODO: Move state to Redis for multi-instance coordination

Usage:
    from app.utils.circuit_breaker import evolution_breaker

    @evolution_breaker
    async def call_evolution_api():
        ...
"""
import asyncio
import time
import logging
from enum import Enum
from typing import Callable, TypeVar, Tuple, Type
from functools import wraps
import httpx

logger = logging.getLogger(__name__)

T = TypeVar('T')


class CircuitState(Enum):
    CLOSED = "closed"       # Normal operation
    OPEN = "open"           # Failing fast
    HALF_OPEN = "half_open" # Testing recovery


# Specific network exceptions (NOT generic Exception)
# Only these trigger the circuit breaker - code bugs should NOT open the circuit
NETWORK_EXCEPTIONS: Tuple[Type[Exception], ...] = (
    httpx.TimeoutException,
    httpx.ConnectError,
    httpx.ReadError,
    httpx.WriteError,
    httpx.PoolTimeout,
    ConnectionError,
    TimeoutError,
)


class CircuitBreakerOpen(Exception):
    """Raised when circuit breaker is open and request is rejected."""
    pass


class CircuitBreaker:
    """
    Circuit breaker for external service calls.

    Usage:
        evolution_breaker = CircuitBreaker(
            "evolution_api",
            failure_threshold=5,
            expected_exceptions=NETWORK_EXCEPTIONS  # Be specific!
        )

        @evolution_breaker
        async def call_evolution_api():
            ...
    """

    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0,
        expected_exceptions: Tuple[Type[Exception], ...] = NETWORK_EXCEPTIONS,
    ):
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.expected_exceptions = expected_exceptions

        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._last_failure_time: float | None = None
        self._lock = asyncio.Lock()

        # Semaphore for HALF_OPEN - only 1 trial request at a time
        self._half_open_semaphore = asyncio.Semaphore(1)
        self._trial_in_flight = False

    @property
    def state(self) -> CircuitState:
        return self._state

    @property
    def failure_count(self) -> int:
        return self._failure_count

    async def _should_allow_request(self) -> bool:
        """Check if request should be allowed based on circuit state."""
        if self._state == CircuitState.CLOSED:
            return True

        if self._state == CircuitState.OPEN:
            # Check if recovery timeout has passed
            if self._last_failure_time:
                elapsed = time.time() - self._last_failure_time
                if elapsed >= self.recovery_timeout:
                    async with self._lock:
                        self._state = CircuitState.HALF_OPEN
                        self._trial_in_flight = False
                        logger.info(f"Circuit {self.name}: OPEN -> HALF_OPEN")
                    # Fall through to HALF_OPEN check
                else:
                    return False
            else:
                return False

        # HALF_OPEN: allow only ONE trial request (prevent thundering herd)
        if self._state == CircuitState.HALF_OPEN:
            async with self._lock:
                if self._trial_in_flight:
                    # Another request is already testing - fail fast
                    return False
                self._trial_in_flight = True
                return True

        return False

    async def _on_success(self):
        """Handle successful request."""
        async with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                self._state = CircuitState.CLOSED
                self._failure_count = 0
                self._trial_in_flight = False
                logger.info(f"Circuit {self.name}: HALF_OPEN -> CLOSED (recovered)")
            elif self._state == CircuitState.CLOSED:
                self._failure_count = 0

    async def _on_failure(self, error: Exception):
        """Handle failed request."""
        async with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.time()
            self._trial_in_flight = False

            if self._state == CircuitState.HALF_OPEN:
                self._state = CircuitState.OPEN
                logger.warning(f"Circuit {self.name}: HALF_OPEN -> OPEN (still failing)")
            elif self._failure_count >= self.failure_threshold:
                self._state = CircuitState.OPEN
                logger.error(
                    f"Circuit {self.name}: CLOSED -> OPEN "
                    f"(threshold {self.failure_threshold} reached)"
                )

    def __call__(self, func: Callable) -> Callable:
        """Decorator to wrap async function with circuit breaker."""
        @wraps(func)
        async def wrapper(*args, **kwargs):
            if not await self._should_allow_request():
                raise CircuitBreakerOpen(
                    f"Circuit {self.name} is OPEN - failing fast"
                )

            try:
                result = await func(*args, **kwargs)
                await self._on_success()
                return result
            except self.expected_exceptions as e:
                await self._on_failure(e)
                raise

        return wrapper

    def reset(self):
        """Reset circuit breaker state (for testing)."""
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._last_failure_time = None
        self._trial_in_flight = False


# Pre-configured breakers for common services
# NOTE: Use specific exceptions for each service

evolution_breaker = CircuitBreaker(
    "evolution_api",
    failure_threshold=5,
    recovery_timeout=60.0,
    expected_exceptions=NETWORK_EXCEPTIONS,
)

nocodb_breaker = CircuitBreaker(
    "nocodb",
    failure_threshold=3,
    recovery_timeout=120.0,
    expected_exceptions=NETWORK_EXCEPTIONS,
)

calendar_breaker = CircuitBreaker(
    "calendar_service",
    failure_threshold=5,
    recovery_timeout=60.0,
    expected_exceptions=NETWORK_EXCEPTIONS,
)

llm_breaker = CircuitBreaker(
    "llm_service",
    failure_threshold=3,
    recovery_timeout=30.0,
    expected_exceptions=NETWORK_EXCEPTIONS,
)


def get_circuit_stats() -> dict:
    """Get stats for all circuit breakers (for monitoring)."""
    return {
        "evolution": {
            "state": evolution_breaker.state.value,
            "failures": evolution_breaker.failure_count,
        },
        "nocodb": {
            "state": nocodb_breaker.state.value,
            "failures": nocodb_breaker.failure_count,
        },
        "calendar": {
            "state": calendar_breaker.state.value,
            "failures": calendar_breaker.failure_count,
        },
        "llm": {
            "state": llm_breaker.state.value,
            "failures": llm_breaker.failure_count,
        },
    }
