"""
Cache Invalidation Strategy
Implements intelligent cache invalidation patterns
"""

import logging
from typing import List, Dict, Any, Optional, Set
from datetime import datetime
from enum import Enum
import asyncio

from app.cache.redis_manager import (
    RedisManager,
    CacheNamespace,
    redis_manager
)

logger = logging.getLogger(__name__)


class InvalidationStrategy(Enum):
    """Cache invalidation strategies"""
    IMMEDIATE = "immediate"  # Invalidate immediately
    LAZY = "lazy"  # Invalidate on next access
    TIMED = "timed"  # Invalidate after delay
    CASCADE = "cascade"  # Invalidate related caches
    SELECTIVE = "selective"  # Invalidate specific keys only


class DependencyGraph:
    """Manages cache dependencies for cascade invalidation"""
    
    def __init__(self):
        self.dependencies: Dict[str, Set[str]] = {}
        self.reverse_dependencies: Dict[str, Set[str]] = {}
    
    def add_dependency(self, parent: str, child: str) -> None:
        """Add dependency relationship"""
        # Add to forward dependencies
        if parent not in self.dependencies:
            self.dependencies[parent] = set()
        self.dependencies[parent].add(child)
        
        # Add to reverse dependencies
        if child not in self.reverse_dependencies:
            self.reverse_dependencies[child] = set()
        self.reverse_dependencies[child].add(parent)
    
    def get_children(self, parent: str) -> Set[str]:
        """Get all dependent caches"""
        return self.dependencies.get(parent, set())
    
    def get_parents(self, child: str) -> Set[str]:
        """Get all parent caches"""
        return self.reverse_dependencies.get(child, set())
    
    def get_cascade_targets(self, key: str) -> Set[str]:
        """Get all caches that should be invalidated in cascade"""
        targets = {key}
        queue = [key]
        
        while queue:
            current = queue.pop(0)
            children = self.get_children(current)
            
            for child in children:
                if child not in targets:
                    targets.add(child)
                    queue.append(child)
        
        return targets


class CacheInvalidator:
    """Manages cache invalidation logic"""
    
    def __init__(self, redis: Optional[RedisManager] = None):
        self.redis = redis or redis_manager
        self.dependency_graph = DependencyGraph()
        self._setup_dependencies()
        self._invalidation_queue: asyncio.Queue = asyncio.Queue()
        self._processing_task = None
    
    def _setup_dependencies(self):
        """Setup cache dependency relationships"""
        # Appointments depend on availability
        self.dependency_graph.add_dependency(
            f"{CacheNamespace.AVAILABILITY.value}:*",
            f"{CacheNamespace.APPOINTMENTS.value}:*"
        )
        
        # Availability depends on doctor schedules
        self.dependency_graph.add_dependency(
            f"{CacheNamespace.DOCTORS.value}:schedule:*",
            f"{CacheNamespace.AVAILABILITY.value}:*"
        )
        
        # Appointments depend on patient data
        self.dependency_graph.add_dependency(
            f"{CacheNamespace.PATIENTS.value}:*",
            f"{CacheNamespace.APPOINTMENTS.value}:patient:*"
        )
        
        # Rules affect all operational caches
        self.dependency_graph.add_dependency(
            f"{CacheNamespace.RULES.value}:*",
            f"{CacheNamespace.APPOINTMENTS.value}:*"
        )
        self.dependency_graph.add_dependency(
            f"{CacheNamespace.RULES.value}:*",
            f"{CacheNamespace.AVAILABILITY.value}:*"
        )
    
    async def start(self):
        """Start background invalidation processor"""
        if not self._processing_task:
            self._processing_task = asyncio.create_task(
                self._process_invalidation_queue()
            )
            logger.info("Cache invalidation processor started")
    
    async def stop(self):
        """Stop background invalidation processor"""
        if self._processing_task:
            self._processing_task.cancel()
            try:
                await self._processing_task
            except asyncio.CancelledError:
                pass
            self._processing_task = None
            logger.info("Cache invalidation processor stopped")
    
    async def _process_invalidation_queue(self):
        """Process queued invalidation requests"""
        while True:
            try:
                # Get invalidation request from queue
                request = await self._invalidation_queue.get()
                
                # Process based on strategy
                await self._execute_invalidation(request)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error processing invalidation queue: {e}")
                await asyncio.sleep(1)
    
    async def _execute_invalidation(self, request: Dict[str, Any]):
        """Execute invalidation request"""
        strategy = request.get("strategy", InvalidationStrategy.IMMEDIATE)
        namespace = request.get("namespace")
        pattern = request.get("pattern")
        delay = request.get("delay", 0)
        
        # Apply delay for timed invalidation
        if strategy == InvalidationStrategy.TIMED and delay > 0:
            await asyncio.sleep(delay)
        
        # Execute invalidation
        if strategy == InvalidationStrategy.CASCADE:
            await self._cascade_invalidate(namespace, pattern)
        elif strategy == InvalidationStrategy.SELECTIVE:
            await self._selective_invalidate(namespace, pattern)
        else:
            await self._immediate_invalidate(namespace, pattern)
    
    async def invalidate(
        self,
        namespace: CacheNamespace,
        pattern: Optional[str] = None,
        strategy: InvalidationStrategy = InvalidationStrategy.IMMEDIATE,
        delay: int = 0
    ):
        """Invalidate cache with specified strategy"""
        request = {
            "namespace": namespace,
            "pattern": pattern,
            "strategy": strategy,
            "delay": delay,
            "timestamp": datetime.utcnow().isoformat()
        }
        
        if strategy == InvalidationStrategy.IMMEDIATE:
            # Execute immediately
            await self._execute_invalidation(request)
        else:
            # Queue for processing
            await self._invalidation_queue.put(request)
    
    async def _immediate_invalidate(
        self,
        namespace: CacheNamespace,
        pattern: Optional[str] = None
    ):
        """Immediate cache invalidation"""
        if pattern:
            # Delete specific pattern
            deleted = await self.redis.delete(namespace, pattern)
            logger.info(f"Immediately invalidated {namespace.value}:{pattern}")
        else:
            # Delete entire namespace
            deleted = await self.redis.invalidate_namespace(namespace)
            logger.info(f"Immediately invalidated namespace {namespace.value}: {deleted} keys")
    
    async def _cascade_invalidate(
        self,
        namespace: CacheNamespace,
        pattern: Optional[str] = None
    ):
        """Cascade invalidation of dependent caches"""
        # Build key for dependency lookup
        key = f"{namespace.value}:{pattern or '*'}"
        
        # Get all cascade targets
        targets = self.dependency_graph.get_cascade_targets(key)
        
        logger.info(f"Cascade invalidating {len(targets)} cache targets")
        
        # Invalidate each target
        for target in targets:
            parts = target.split(":")
            if len(parts) >= 2:
                target_namespace = CacheNamespace(parts[0])
                target_pattern = ":".join(parts[1:]) if len(parts) > 1 else None
                
                await self._immediate_invalidate(target_namespace, target_pattern)
    
    async def _selective_invalidate(
        self,
        namespace: CacheNamespace,
        pattern: str
    ):
        """Selective invalidation of specific cache keys"""
        # Use pattern matching to find keys
        await self.redis.ensure_connected()
        
        cursor = 0
        invalidated = 0
        
        while True:
            cursor, keys = await self.redis.client.scan(
                cursor,
                match=f"{namespace.value}:{pattern}",
                count=100
            )
            
            if keys:
                # Delete matching keys
                invalidated += await self.redis.client.delete(*keys)
            
            if cursor == 0:
                break
        
        logger.info(f"Selectively invalidated {invalidated} keys matching {namespace.value}:{pattern}")
    
    async def invalidate_on_event(self, event: Dict[str, Any]):
        """Invalidate cache based on event type"""
        event_type = event.get("type")
        data = event.get("data", {})
        
        if event_type == "appointment_created":
            # Invalidate related caches
            doctor_id = data.get("doctor_id")
            patient_id = data.get("patient_id")
            date = data.get("date")
            
            if doctor_id and date:
                await self.invalidate(
                    CacheNamespace.AVAILABILITY,
                    f"{doctor_id}:{date}",
                    InvalidationStrategy.IMMEDIATE
                )
            
            if patient_id:
                await self.invalidate(
                    CacheNamespace.APPOINTMENTS,
                    f"patient:{patient_id}",
                    InvalidationStrategy.IMMEDIATE
                )
        
        elif event_type == "appointment_cancelled":
            # Similar logic for cancellation
            doctor_id = data.get("doctor_id")
            date = data.get("date")
            
            if doctor_id and date:
                await self.invalidate(
                    CacheNamespace.AVAILABILITY,
                    f"{doctor_id}:{date}",
                    InvalidationStrategy.TIMED,
                    delay=2  # Small delay to avoid race conditions
                )
        
        elif event_type == "doctor_schedule_updated":
            # Cascade invalidation for schedule changes
            doctor_id = data.get("doctor_id")
            
            if doctor_id:
                await self.invalidate(
                    CacheNamespace.DOCTORS,
                    f"schedule:{doctor_id}",
                    InvalidationStrategy.CASCADE
                )
        
        elif event_type == "rule_updated":
            # Invalidate all rule-dependent caches
            await self.invalidate(
                CacheNamespace.RULES,
                "*",
                InvalidationStrategy.CASCADE
            )
        
        elif event_type == "sync_completed":
            # Invalidate sync-related caches
            table = data.get("table")
            
            if table == "appointments":
                await self.invalidate(
                    CacheNamespace.APPOINTMENTS,
                    None,
                    InvalidationStrategy.IMMEDIATE
                )
            elif table == "patients":
                await self.invalidate(
                    CacheNamespace.PATIENTS,
                    None,
                    InvalidationStrategy.IMMEDIATE
                )
    
    async def get_invalidation_stats(self) -> Dict[str, Any]:
        """Get statistics about cache invalidation"""
        stats = {
            "queue_size": self._invalidation_queue.qsize(),
            "dependencies": len(self.dependency_graph.dependencies),
            "reverse_dependencies": len(self.dependency_graph.reverse_dependencies),
            "processor_running": self._processing_task is not None
        }
        
        return stats
    
    async def register_dependency(
        self,
        parent_namespace: CacheNamespace,
        parent_pattern: str,
        child_namespace: CacheNamespace,
        child_pattern: str
    ):
        """Register new cache dependency"""
        parent_key = f"{parent_namespace.value}:{parent_pattern}"
        child_key = f"{child_namespace.value}:{child_pattern}"
        
        self.dependency_graph.add_dependency(parent_key, child_key)
        
        logger.info(f"Registered dependency: {parent_key} -> {child_key}")


# Singleton instance
cache_invalidator = CacheInvalidator()