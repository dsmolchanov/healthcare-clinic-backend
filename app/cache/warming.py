"""
Cache Warming Strategies
Proactive cache population for optimal performance
"""

import logging
import asyncio
from typing import List, Dict, Any, Optional, Callable
from datetime import datetime, timedelta, date
from enum import Enum
import json

from app.cache.redis_manager import (
    RedisManager,
    CacheNamespace,
    CacheTTL,
    redis_manager
)
from app.core.database import get_db_connection

logger = logging.getLogger(__name__)


class WarmingStrategy(Enum):
    """Cache warming strategies"""
    EAGER = "eager"  # Warm all data immediately
    LAZY = "lazy"  # Warm on first miss
    SCHEDULED = "scheduled"  # Warm at specific times
    PREDICTIVE = "predictive"  # Warm based on usage patterns
    PRIORITY = "priority"  # Warm high-priority data first


class WarmingPriority(Enum):
    """Priority levels for cache warming"""
    CRITICAL = 1
    HIGH = 2
    MEDIUM = 3
    LOW = 4


class CacheWarmer:
    """Manages cache warming operations"""
    
    def __init__(self, redis: Optional[RedisManager] = None):
        self.redis = redis or redis_manager
        self.warming_tasks: List[asyncio.Task] = []
        self.warming_schedule: Dict[str, Dict] = {}
        self.usage_patterns: Dict[str, List] = {}
        self._scheduler_task = None
    
    async def start(self):
        """Start cache warming scheduler"""
        if not self._scheduler_task:
            self._scheduler_task = asyncio.create_task(self._run_scheduler())
            logger.info("Cache warming scheduler started")
    
    async def stop(self):
        """Stop cache warming scheduler"""
        # Cancel scheduler
        if self._scheduler_task:
            self._scheduler_task.cancel()
            try:
                await self._scheduler_task
            except asyncio.CancelledError:
                pass
            self._scheduler_task = None
        
        # Cancel all warming tasks
        for task in self.warming_tasks:
            if not task.done():
                task.cancel()
        
        # Wait for all tasks to complete
        if self.warming_tasks:
            await asyncio.gather(*self.warming_tasks, return_exceptions=True)
        
        self.warming_tasks.clear()
        logger.info("Cache warming stopped")
    
    async def _run_scheduler(self):
        """Run scheduled warming tasks"""
        while True:
            try:
                current_time = datetime.now()
                
                # Check scheduled warming tasks
                for task_id, schedule in list(self.warming_schedule.items()):
                    next_run = schedule.get("next_run")
                    
                    if next_run and current_time >= next_run:
                        # Execute warming task
                        warming_func = schedule.get("func")
                        if warming_func:
                            task = asyncio.create_task(warming_func())
                            self.warming_tasks.append(task)
                        
                        # Update next run time
                        interval = schedule.get("interval")
                        if interval:
                            schedule["next_run"] = current_time + interval
                        else:
                            # One-time task, remove from schedule
                            del self.warming_schedule[task_id]
                
                # Clean up completed tasks
                self.warming_tasks = [
                    task for task in self.warming_tasks
                    if not task.done()
                ]
                
                # Sleep for a minute before next check
                await asyncio.sleep(60)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in warming scheduler: {e}")
                await asyncio.sleep(60)
    
    async def warm_appointments_cache(
        self,
        clinic_id: str,
        days_ahead: int = 7,
        priority: WarmingPriority = WarmingPriority.HIGH
    ):
        """Warm appointment-related caches"""
        try:
            logger.info(f"Warming appointments cache for clinic {clinic_id}")
            
            # Get database connection
            async with get_db_connection() as conn:
                # Warm upcoming appointments
                start_date = date.today()
                end_date = start_date + timedelta(days=days_ahead)
                
                # Query appointments
                query = """
                    SELECT 
                        a.id,
                        a.patient_id,
                        a.doctor_id,
                        a.appointment_datetime,
                        a.status,
                        a.type,
                        p.name as patient_name,
                        d.name as doctor_name
                    FROM healthcare.appointments a
                    JOIN healthcare.patients p ON a.patient_id = p.id
                    JOIN healthcare.doctors d ON a.doctor_id = d.id
                    WHERE a.clinic_id = $1
                    AND DATE(a.appointment_datetime) >= $2
                    AND DATE(a.appointment_datetime) <= $3
                    AND a.status != 'cancelled'
                    ORDER BY a.appointment_datetime
                """
                
                rows = await conn.fetch(query, clinic_id, start_date, end_date)
                
                # Cache appointments by date
                appointments_by_date = {}
                for row in rows:
                    app_date = row["appointment_datetime"].date().isoformat()
                    
                    if app_date not in appointments_by_date:
                        appointments_by_date[app_date] = []
                    
                    appointments_by_date[app_date].append({
                        "id": str(row["id"]),
                        "patient_id": str(row["patient_id"]),
                        "patient_name": row["patient_name"],
                        "doctor_id": str(row["doctor_id"]),
                        "doctor_name": row["doctor_name"],
                        "datetime": row["appointment_datetime"].isoformat(),
                        "status": row["status"],
                        "type": row["type"]
                    })
                
                # Store in cache
                for app_date, appointments in appointments_by_date.items():
                    cache_key = f"{clinic_id}:date:{app_date}"
                    
                    await self.redis.set(
                        CacheNamespace.APPOINTMENTS,
                        cache_key,
                        appointments,
                        ttl=CacheTTL.LONG
                    )
                
                logger.info(f"Warmed {len(rows)} appointments for {len(appointments_by_date)} days")
                
        except Exception as e:
            logger.error(f"Error warming appointments cache: {e}")
    
    async def warm_availability_cache(
        self,
        clinic_id: str,
        days_ahead: int = 7
    ):
        """Warm doctor availability caches"""
        try:
            logger.info(f"Warming availability cache for clinic {clinic_id}")
            
            async with get_db_connection() as conn:
                # Get all doctors in clinic
                doctors = await conn.fetch(
                    "SELECT id, name FROM healthcare.doctors WHERE clinic_id = $1",
                    clinic_id
                )
                
                for doctor in doctors:
                    doctor_id = str(doctor["id"])
                    
                    # Calculate availability for each day
                    for day_offset in range(days_ahead):
                        check_date = date.today() + timedelta(days=day_offset)
                        
                        # Get doctor's schedule for the day
                        schedule_query = """
                            SELECT 
                                start_time,
                                end_time,
                                slot_duration_minutes
                            FROM healthcare.doctor_schedules
                            WHERE doctor_id = $1
                            AND day_of_week = $2
                            AND is_available = true
                        """
                        
                        schedule = await conn.fetchrow(
                            schedule_query,
                            doctor["id"],
                            check_date.weekday()
                        )
                        
                        if not schedule:
                            continue
                        
                        # Get existing appointments
                        appointments_query = """
                            SELECT 
                                appointment_datetime,
                                duration_minutes
                            FROM healthcare.appointments
                            WHERE doctor_id = $1
                            AND DATE(appointment_datetime) = $2
                            AND status != 'cancelled'
                        """
                        
                        appointments = await conn.fetch(
                            appointments_query,
                            doctor["id"],
                            check_date
                        )
                        
                        # Calculate available slots
                        available_slots = self._calculate_available_slots(
                            check_date,
                            schedule,
                            appointments
                        )
                        
                        # Cache availability
                        cache_key = f"{doctor_id}:{check_date.isoformat()}"
                        
                        await self.redis.set(
                            CacheNamespace.AVAILABILITY,
                            cache_key,
                            available_slots,
                            ttl=CacheTTL.LONG
                        )
                
                logger.info(f"Warmed availability for {len(doctors)} doctors")
                
        except Exception as e:
            logger.error(f"Error warming availability cache: {e}")
    
    def _calculate_available_slots(
        self,
        check_date: date,
        schedule: Dict,
        appointments: List[Dict]
    ) -> List[str]:
        """Calculate available time slots"""
        available_slots = []
        
        # Create datetime objects for start and end
        start_datetime = datetime.combine(check_date, schedule["start_time"])
        end_datetime = datetime.combine(check_date, schedule["end_time"])
        slot_duration = timedelta(minutes=schedule["slot_duration_minutes"])
        
        # Create list of booked times
        booked_times = set()
        for apt in appointments:
            apt_time = apt["appointment_datetime"]
            duration = apt.get("duration_minutes", 30)
            
            # Mark all slots during appointment as booked
            current = apt_time
            end = apt_time + timedelta(minutes=duration)
            while current < end:
                booked_times.add(current.time().isoformat()[:5])  # HH:MM format
                current += slot_duration
        
        # Generate available slots
        current_slot = start_datetime
        while current_slot < end_datetime:
            slot_time = current_slot.time().isoformat()[:5]
            
            if slot_time not in booked_times:
                # Check if slot is in the future
                if datetime.combine(check_date, current_slot.time()) > datetime.now():
                    available_slots.append(slot_time)
            
            current_slot += slot_duration
        
        return available_slots
    
    async def warm_patient_cache(self, clinic_id: str):
        """Warm patient-related caches"""
        try:
            logger.info(f"Warming patient cache for clinic {clinic_id}")
            
            async with get_db_connection() as conn:
                # Get active patients
                patients_query = """
                    SELECT 
                        p.id,
                        p.name,
                        p.phone,
                        p.email,
                        COUNT(a.id) as appointment_count,
                        MAX(a.appointment_datetime) as last_appointment
                    FROM healthcare.patients p
                    LEFT JOIN healthcare.appointments a ON p.id = a.patient_id
                    WHERE p.clinic_id = $1
                    GROUP BY p.id
                    HAVING MAX(a.appointment_datetime) > NOW() - INTERVAL '6 months'
                    OR COUNT(a.id) > 0
                """
                
                patients = await conn.fetch(patients_query, clinic_id)
                
                # Cache patient data
                for patient in patients:
                    patient_data = {
                        "id": str(patient["id"]),
                        "name": patient["name"],
                        "phone": patient["phone"],
                        "email": patient["email"],
                        "appointment_count": patient["appointment_count"],
                        "last_appointment": patient["last_appointment"].isoformat() 
                            if patient["last_appointment"] else None
                    }
                    
                    cache_key = str(patient["id"])
                    
                    await self.redis.set(
                        CacheNamespace.PATIENTS,
                        cache_key,
                        patient_data,
                        ttl=CacheTTL.LONG
                    )
                
                logger.info(f"Warmed cache for {len(patients)} active patients")
                
        except Exception as e:
            logger.error(f"Error warming patient cache: {e}")
    
    async def warm_rules_cache(self, clinic_id: str):
        """Warm business rules cache"""
        try:
            logger.info(f"Warming rules cache for clinic {clinic_id}")
            
            async with get_db_connection() as conn:
                # Get active rules
                rules_query = """
                    SELECT 
                        id,
                        name,
                        rule_type,
                        conditions,
                        actions,
                        priority,
                        metadata
                    FROM healthcare.business_rules
                    WHERE clinic_id = $1
                    AND is_active = true
                    ORDER BY priority
                """
                
                rules = await conn.fetch(rules_query, clinic_id)
                
                # Cache rules by type
                rules_by_type = {}
                for rule in rules:
                    rule_type = rule["rule_type"]
                    
                    if rule_type not in rules_by_type:
                        rules_by_type[rule_type] = []
                    
                    rules_by_type[rule_type].append({
                        "id": str(rule["id"]),
                        "name": rule["name"],
                        "conditions": rule["conditions"],
                        "actions": rule["actions"],
                        "priority": rule["priority"],
                        "metadata": rule["metadata"]
                    })
                
                # Store in cache
                for rule_type, type_rules in rules_by_type.items():
                    cache_key = f"{clinic_id}:{rule_type}"
                    
                    await self.redis.set(
                        CacheNamespace.RULES,
                        cache_key,
                        type_rules,
                        ttl=CacheTTL.VERY_LONG
                    )
                
                logger.info(f"Warmed {len(rules)} rules in {len(rules_by_type)} categories")
                
        except Exception as e:
            logger.error(f"Error warming rules cache: {e}")
    
    async def warm_all_caches(self, clinic_id: str):
        """Warm all caches for a clinic"""
        logger.info(f"Starting full cache warming for clinic {clinic_id}")
        
        # Create warming tasks with priority
        tasks = [
            (WarmingPriority.CRITICAL, self.warm_rules_cache(clinic_id)),
            (WarmingPriority.HIGH, self.warm_appointments_cache(clinic_id)),
            (WarmingPriority.HIGH, self.warm_availability_cache(clinic_id)),
            (WarmingPriority.MEDIUM, self.warm_patient_cache(clinic_id))
        ]
        
        # Sort by priority
        tasks.sort(key=lambda x: x[0].value)
        
        # Execute in priority order
        for priority, task in tasks:
            try:
                await task
            except Exception as e:
                logger.error(f"Error in cache warming task: {e}")
        
        logger.info(f"Completed full cache warming for clinic {clinic_id}")
    
    async def schedule_warming(
        self,
        task_id: str,
        warming_func: Callable,
        interval: Optional[timedelta] = None,
        start_time: Optional[datetime] = None
    ):
        """Schedule a cache warming task"""
        schedule_info = {
            "func": warming_func,
            "interval": interval,
            "next_run": start_time or datetime.now()
        }
        
        self.warming_schedule[task_id] = schedule_info
        
        logger.info(f"Scheduled warming task: {task_id}")
    
    async def warm_based_on_usage(
        self,
        namespace: CacheNamespace,
        pattern: str,
        threshold: int = 5
    ):
        """Warm cache based on usage patterns"""
        # Track cache misses
        miss_key = f"misses:{namespace.value}:{pattern}"
        miss_count = await self.redis.increment(
            CacheNamespace.SYNC,
            miss_key,
            ttl=3600
        )
        
        # If misses exceed threshold, trigger warming
        if miss_count and miss_count >= threshold:
            logger.info(f"Usage-based warming triggered for {namespace.value}:{pattern}")
            
            # Reset counter
            await self.redis.delete(CacheNamespace.SYNC, miss_key)
            
            # Trigger appropriate warming based on namespace
            if namespace == CacheNamespace.APPOINTMENTS:
                # Extract clinic_id from pattern if possible
                parts = pattern.split(":")
                if parts:
                    await self.warm_appointments_cache(parts[0])
            elif namespace == CacheNamespace.AVAILABILITY:
                parts = pattern.split(":")
                if parts:
                    # Warm specific doctor availability
                    await self.warm_availability_cache(parts[0])
    
    async def get_warming_stats(self) -> Dict[str, Any]:
        """Get cache warming statistics"""
        stats = {
            "active_tasks": len([t for t in self.warming_tasks if not t.done()]),
            "scheduled_tasks": len(self.warming_schedule),
            "scheduler_running": self._scheduler_task is not None
        }
        
        # Add cache hit/miss ratios if available
        info = await self.redis.get_info()
        if info:
            stats["keyspace_hits"] = info.get("keyspace_hits", 0)
            stats["keyspace_misses"] = info.get("keyspace_misses", 0)
            
            total = stats["keyspace_hits"] + stats["keyspace_misses"]
            if total > 0:
                stats["hit_ratio"] = stats["keyspace_hits"] / total
        
        return stats


# Singleton instance
cache_warmer = CacheWarmer()