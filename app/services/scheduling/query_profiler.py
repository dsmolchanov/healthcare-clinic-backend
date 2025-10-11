"""
Query Performance Profiling Module.

Provides decorators and utilities for profiling database query performance
to ensure scheduling operations meet performance targets:
- suggest_slots() p50 < 400ms, p95 < 800ms
- hold_slot() p95 < 150ms
- confirm_hold() p95 < 200ms
"""

import time
import logging
from functools import wraps
from typing import Callable, Any, Dict, List

logger = logging.getLogger(__name__)


def profile_query(query_name: str):
    """
    Decorator to profile database query performance.

    Logs query duration and warns if execution exceeds 500ms threshold.

    Args:
        query_name: Name of the query for logging purposes

    Returns:
        Decorated async function with performance profiling

    Example:
        @profile_query("suggest_slots")
        async def suggest_slots(self, ...):
            # Implementation
            pass
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args, **kwargs) -> Any:
            start_time = time.perf_counter()
            try:
                result = await func(*args, **kwargs)
                duration_ms = (time.perf_counter() - start_time) * 1000

                logger.info(f"query.{query_name}", extra={
                    "duration_ms": duration_ms,
                    "query": query_name
                })

                if duration_ms > 500:
                    logger.warning(f"query.slow", extra={
                        "duration_ms": duration_ms,
                        "query": query_name,
                        "threshold_ms": 500
                    })

                return result
            except Exception as e:
                duration_ms = (time.perf_counter() - start_time) * 1000
                logger.error(f"query.error", extra={
                    "duration_ms": duration_ms,
                    "query": query_name,
                    "error": str(e)
                })
                raise
        return wrapper
    return decorator


class PerformanceMonitor:
    """
    Track performance metrics for scheduling operations.

    Records operation durations and calculates percentiles (p50, p95, p99)
    to verify performance targets are met.
    """

    def __init__(self):
        """Initialize performance monitor with empty metrics."""
        self.metrics: Dict[str, List[float]] = {
            "suggest_slots": [],
            "hold_slot": [],
            "confirm_hold": [],
            "filter_constraints": [],
            "score_preferences": []
        }

    def record(self, operation: str, duration_ms: float):
        """
        Record operation duration.

        Args:
            operation: Operation name (must exist in metrics dict)
            duration_ms: Duration in milliseconds
        """
        if operation in self.metrics:
            self.metrics[operation].append(duration_ms)
            logger.debug(f"perf.record", extra={
                "operation": operation,
                "duration_ms": duration_ms,
                "total_samples": len(self.metrics[operation])
            })

    def get_stats(self, operation: str) -> Dict[str, float]:
        """
        Get statistics for an operation.

        Calculates p50, p95, p99, min, max, and average duration.

        Args:
            operation: Operation name

        Returns:
            Dict with statistics (count, p50, p95, p99, min, max, avg)
        """
        if operation not in self.metrics or not self.metrics[operation]:
            return {}

        durations = sorted(self.metrics[operation])
        count = len(durations)

        return {
            "count": count,
            "p50": durations[int(count * 0.50)] if count > 0 else 0,
            "p95": durations[int(count * 0.95)] if count > 0 else 0,
            "p99": durations[int(count * 0.99)] if count > 0 else 0,
            "min": min(durations),
            "max": max(durations),
            "avg": sum(durations) / count
        }

    def report(self) -> Dict[str, Dict[str, float]]:
        """
        Generate performance report for all operations.

        Returns:
            Dict mapping operation names to their statistics
        """
        return {
            operation: self.get_stats(operation)
            for operation in self.metrics.keys()
        }

    def reset(self):
        """Clear all recorded metrics."""
        for operation in self.metrics:
            self.metrics[operation] = []
        logger.info("perf.reset", extra={"message": "Performance metrics reset"})

    def check_targets(self) -> Dict[str, bool]:
        """
        Check if performance targets are met.

        Targets:
        - suggest_slots: p50 < 400ms, p95 < 800ms
        - hold_slot: p95 < 150ms
        - confirm_hold: p95 < 200ms

        Returns:
            Dict mapping check names to pass/fail status
        """
        results = {}

        # suggest_slots targets
        suggest_stats = self.get_stats("suggest_slots")
        if suggest_stats:
            results["suggest_slots_p50"] = suggest_stats.get("p50", 0) < 400
            results["suggest_slots_p95"] = suggest_stats.get("p95", 0) < 800

        # hold_slot targets
        hold_stats = self.get_stats("hold_slot")
        if hold_stats:
            results["hold_slot_p95"] = hold_stats.get("p95", 0) < 150

        # confirm_hold targets
        confirm_stats = self.get_stats("confirm_hold")
        if confirm_stats:
            results["confirm_hold_p95"] = confirm_stats.get("p95", 0) < 200

        return results
