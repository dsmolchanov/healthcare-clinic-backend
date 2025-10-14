"""Lightweight metrics tracking for mem0 queue operations."""

from __future__ import annotations

import asyncio
from collections import Counter
from dataclasses import dataclass, field
from time import perf_counter
from typing import Dict, Any


@dataclass
class _Mem0QueueMetrics:
    current_queue_size: int = 0
    max_queue_size: int = 0
    processed_jobs_total: int = 0
    job_type_counts: Counter = field(default_factory=Counter)
    total_latency_ms: float = 0.0
    last_job_latency_ms: float = 0.0
    last_updated_at: float = 0.0
    latency_breach_count: int = 0

    def snapshot(self) -> Dict[str, Any]:
        avg_latency = 0.0
        if self.processed_jobs_total:
            avg_latency = self.total_latency_ms / self.processed_jobs_total

        return {
            "current_queue_size": self.current_queue_size,
            "max_queue_size": self.max_queue_size,
            "processed_jobs_total": self.processed_jobs_total,
            "job_type_counts": dict(self.job_type_counts),
            "average_latency_ms": round(avg_latency, 2),
            "last_job_latency_ms": round(self.last_job_latency_ms, 2),
            "last_updated_at": self.last_updated_at,
            "latency_breach_count": self.latency_breach_count,
        }


class Mem0MetricsRecorder:
    """Thread-safe recorder for mem0 queue statistics."""

    def __init__(self, latency_warn_ms: float = 400.0) -> None:
        self._metrics = _Mem0QueueMetrics()
        self._lock = asyncio.Lock()
        self._latency_warn_ms = latency_warn_ms

    async def record_enqueue(self, queue_size: int) -> None:
        async with self._lock:
            self._metrics.current_queue_size = queue_size
            if queue_size > self._metrics.max_queue_size:
                self._metrics.max_queue_size = queue_size
            self._metrics.last_updated_at = perf_counter()

    async def record_job_complete(self, job_type: str, queue_size: int, latency_ms: float) -> None:
        async with self._lock:
            metrics = self._metrics
            metrics.current_queue_size = queue_size
            metrics.processed_jobs_total += 1
            metrics.job_type_counts[job_type] += 1
            metrics.total_latency_ms += latency_ms
            metrics.last_job_latency_ms = latency_ms
            metrics.last_updated_at = perf_counter()
            if queue_size > metrics.max_queue_size:
                metrics.max_queue_size = queue_size
            if latency_ms > self._latency_warn_ms:
                metrics.latency_breach_count += 1

    async def snapshot(self) -> Dict[str, Any]:
        async with self._lock:
            return self._metrics.snapshot()


_metrics_recorder: Mem0MetricsRecorder | None = None


def get_mem0_metrics_recorder() -> Mem0MetricsRecorder:
    global _metrics_recorder
    if _metrics_recorder is None:
        _metrics_recorder = Mem0MetricsRecorder()
    return _metrics_recorder
