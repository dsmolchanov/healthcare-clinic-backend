# File: clinics/backend/app/services/direct_lane/circuit_breaker.py

from datetime import datetime
from enum import Enum
import threading
import logging

logger = logging.getLogger(__name__)

class CircuitState(Enum):
    CLOSED = "closed"  # Normal operation
    OPEN = "open"      # Failing, reject calls
    HALF_OPEN = "half_open"  # Testing if service has recovered

class CircuitBreaker:
    """
    Circuit breaker pattern for direct lane tool execution.

    - Failure threshold: 5 consecutive failures
    - Recovery timeout: 60 seconds
    - Thread-safe implementation
    """

    def __init__(self, failure_threshold=5, recovery_timeout=60):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout

        # Thread-safe implementation
        self.lock = threading.Lock()

        # Track failures per tool
        self.tool_failures = {}

    def is_open(self, tool_name: str) -> bool:
        """Check if circuit breaker is open for a tool"""
        with self.lock:
            # Get or initialize tool-specific state
            if tool_name not in self.tool_failures:
                self.tool_failures[tool_name] = {
                    "failure_count": 0,
                    "last_failure_time": None,
                    "state": CircuitState.CLOSED
                }

            tool_state = self.tool_failures[tool_name]

            if tool_state["state"] == CircuitState.OPEN:
                # Check if recovery timeout has elapsed
                if (datetime.now() - tool_state["last_failure_time"]).seconds >= self.recovery_timeout:
                    tool_state["state"] = CircuitState.HALF_OPEN
                    logger.info(f"Circuit breaker HALF-OPEN for {tool_name}")
                    return False
                return True

            return False

    def record_success(self, tool_name: str):
        """Record a successful execution"""
        with self.lock:
            if tool_name in self.tool_failures:
                old_state = self.tool_failures[tool_name]["state"]
                self.tool_failures[tool_name]["failure_count"] = 0
                self.tool_failures[tool_name]["state"] = CircuitState.CLOSED

                if old_state != CircuitState.CLOSED:
                    logger.info(f"Circuit breaker CLOSED for {tool_name}")

    def record_failure(self, tool_name: str):
        """Record a failed execution"""
        with self.lock:
            if tool_name not in self.tool_failures:
                self.tool_failures[tool_name] = {
                    "failure_count": 0,
                    "last_failure_time": None,
                    "state": CircuitState.CLOSED
                }

            tool_state = self.tool_failures[tool_name]
            tool_state["failure_count"] += 1
            tool_state["last_failure_time"] = datetime.now()

            if tool_state["failure_count"] >= self.failure_threshold:
                tool_state["state"] = CircuitState.OPEN
                logger.warning(
                    f"Circuit breaker OPEN for {tool_name} "
                    f"({tool_state['failure_count']} failures)"
                )
