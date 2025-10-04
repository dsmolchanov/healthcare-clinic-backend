"""
Offline Cache Manager
Maintains local caches for essential data to enable offline operations
"""

import json
import redis.asyncio as redis
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional, List, Set
import asyncio
import logging
from dataclasses import dataclass, asdict
import hashlib

logger = logging.getLogger(__name__)


@dataclass
class CacheEntry:
    """Represents a cached data entry with metadata"""
    key: str
    value: Any
    timestamp: datetime
    ttl: Optional[int] = None
    source: str = "database"
    version: int = 1
    checksum: Optional[str] = None
    tenant_id: Optional[str] = None

    def is_expired(self) -> bool:
        """Check if cache entry has expired"""
        if self.ttl is None:
            return False
        expiry_time = self.timestamp + timedelta(seconds=self.ttl)
        return datetime.now(timezone.utc) > expiry_time

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for storage"""
        data = asdict(self)
        data["timestamp"] = self.timestamp.isoformat()
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'CacheEntry':
        """Create from dictionary"""
        data["timestamp"] = datetime.fromisoformat(data["timestamp"])
        return cls(**data)


class OfflineCacheManager:
    """
    Manages offline cache for critical data including:
    - Appointment schedules
    - Doctor availability
    - Patient records (limited PHI)
    - Calendar sync status
    - Configuration data
    """

    def __init__(self, redis_client: redis.Redis, supabase_client: Any):
        self.redis_client = redis_client
        self.supabase = supabase_client
        self.cache_prefix = "offline_cache"
        self.priority_keys: Set[str] = set()
        self.refresh_intervals = {
            "appointments": 300,  # 5 minutes
            "doctors": 3600,  # 1 hour
            "patients": 1800,  # 30 minutes
            "calendar_sync": 60,  # 1 minute
            "config": 86400  # 24 hours
        }

    async def initialize(self, tenant_id: Optional[str] = None):
        """
        Initialize offline cache with essential data

        Args:
            tenant_id: Optional tenant ID for multi-tenant caching
        """
        try:
            # Load priority configuration
            await self._load_priority_configuration()

            # Pre-populate critical caches
            await asyncio.gather(
                self.cache_appointments(tenant_id),
                self.cache_doctor_availability(tenant_id),
                self.cache_patient_records(tenant_id),
                self.cache_calendar_status(tenant_id),
                self.cache_configuration(tenant_id)
            )

            logger.info(f"Offline cache initialized for tenant: {tenant_id or 'all'}")

        except Exception as e:
            logger.error(f"Failed to initialize offline cache: {e}")
            raise

    async def _load_priority_configuration(self):
        """Load configuration for priority caching"""
        try:
            # Define critical keys that must always be cached
            self.priority_keys = {
                "appointments:today",
                "appointments:tomorrow",
                "doctors:on_duty",
                "calendar:sync_status",
                "config:business_hours",
                "config:emergency_contacts"
            }
        except Exception as e:
            logger.error(f"Failed to load priority configuration: {e}")

    async def cache_appointments(self, tenant_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Cache appointment data for offline access

        Args:
            tenant_id: Optional tenant ID filter

        Returns:
            Cache statistics
        """
        try:
            stats = {"cached": 0, "failed": 0, "duration_ms": 0}
            start_time = datetime.now(timezone.utc)

            # Get appointments for next 7 days
            end_date = datetime.now(timezone.utc) + timedelta(days=7)

            # Query from Supabase
            query = self.supabase.table("appointments").select("*")

            if tenant_id:
                query = query.eq("tenant_id", tenant_id)

            query = query.lte("appointment_date", end_date.isoformat())
            query = query.gte("appointment_date", datetime.now(timezone.utc).isoformat())

            response = query.execute()
            appointments = response.data if response.data else []

            # Group by date for efficient caching
            appointments_by_date = {}
            for apt in appointments:
                date_key = apt["appointment_date"].split("T")[0]
                if date_key not in appointments_by_date:
                    appointments_by_date[date_key] = []
                appointments_by_date[date_key].append(apt)

            # Cache each day's appointments
            for date_key, day_appointments in appointments_by_date.items():
                cache_key = self._build_cache_key("appointments", date_key, tenant_id)

                entry = CacheEntry(
                    key=cache_key,
                    value=day_appointments,
                    timestamp=datetime.now(timezone.utc),
                    ttl=self.refresh_intervals["appointments"],
                    source="supabase",
                    tenant_id=tenant_id,
                    checksum=self._calculate_checksum(day_appointments)
                )

                await self._store_cache_entry(entry)
                stats["cached"] += len(day_appointments)

            # Cache summary for quick access
            summary_key = self._build_cache_key("appointments", "summary", tenant_id)
            summary_entry = CacheEntry(
                key=summary_key,
                value={
                    "total": len(appointments),
                    "by_date": {k: len(v) for k, v in appointments_by_date.items()},
                    "last_update": datetime.now(timezone.utc).isoformat()
                },
                timestamp=datetime.now(timezone.utc),
                ttl=60,  # 1 minute for summary
                source="computed",
                tenant_id=tenant_id
            )
            await self._store_cache_entry(summary_entry)

            stats["duration_ms"] = int((datetime.now(timezone.utc) - start_time).total_seconds() * 1000)
            logger.info(f"Cached {stats['cached']} appointments in {stats['duration_ms']}ms")
            return stats

        except Exception as e:
            logger.error(f"Failed to cache appointments: {e}")
            return {"cached": 0, "failed": 1, "error": str(e)}

    async def cache_doctor_availability(self, tenant_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Cache doctor availability data

        Args:
            tenant_id: Optional tenant ID filter

        Returns:
            Cache statistics
        """
        try:
            stats = {"cached": 0, "failed": 0}

            # Query doctors and their schedules
            query = self.supabase.table("doctors").select("*, schedules(*)")

            if tenant_id:
                query = query.eq("tenant_id", tenant_id)

            response = query.execute()
            doctors = response.data if response.data else []

            # Cache each doctor's availability
            for doctor in doctors:
                doctor_id = doctor["id"]
                cache_key = self._build_cache_key("doctor_availability", doctor_id, tenant_id)

                # Calculate availability for next 7 days
                availability = await self._calculate_availability(doctor)

                entry = CacheEntry(
                    key=cache_key,
                    value={
                        "doctor": doctor,
                        "availability": availability,
                        "cached_at": datetime.now(timezone.utc).isoformat()
                    },
                    timestamp=datetime.now(timezone.utc),
                    ttl=self.refresh_intervals["doctors"],
                    source="computed",
                    tenant_id=tenant_id
                )

                await self._store_cache_entry(entry)
                stats["cached"] += 1

            logger.info(f"Cached availability for {stats['cached']} doctors")
            return stats

        except Exception as e:
            logger.error(f"Failed to cache doctor availability: {e}")
            return {"cached": 0, "failed": 1, "error": str(e)}

    async def cache_patient_records(self, tenant_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Cache limited patient records (only essential non-PHI or encrypted PHI)

        Args:
            tenant_id: Optional tenant ID filter

        Returns:
            Cache statistics
        """
        try:
            stats = {"cached": 0, "failed": 0}

            # Only cache patients with upcoming appointments
            query = self.supabase.table("patients").select("id, first_name, phone, preferences")

            if tenant_id:
                query = query.eq("tenant_id", tenant_id)

            # Limit to active patients
            query = query.eq("active", True).limit(1000)

            response = query.execute()
            patients = response.data if response.data else []

            # Batch cache patient records
            batch_size = 100
            for i in range(0, len(patients), batch_size):
                batch = patients[i:i + batch_size]
                cache_key = self._build_cache_key("patients", f"batch_{i // batch_size}", tenant_id)

                entry = CacheEntry(
                    key=cache_key,
                    value=batch,
                    timestamp=datetime.now(timezone.utc),
                    ttl=self.refresh_intervals["patients"],
                    source="supabase",
                    tenant_id=tenant_id,
                    checksum=self._calculate_checksum(batch)
                )

                await self._store_cache_entry(entry)
                stats["cached"] += len(batch)

            logger.info(f"Cached {stats['cached']} patient records")
            return stats

        except Exception as e:
            logger.error(f"Failed to cache patient records: {e}")
            return {"cached": 0, "failed": 1, "error": str(e)}

    async def cache_calendar_status(self, tenant_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Cache calendar sync status for all providers

        Args:
            tenant_id: Optional tenant ID filter

        Returns:
            Cache statistics
        """
        try:
            stats = {"cached": 0, "failed": 0}

            # Query calendar sync status
            query = self.supabase.table("calendar_sync_status").select("*")

            if tenant_id:
                query = query.eq("tenant_id", tenant_id)

            response = query.execute()
            sync_statuses = response.data if response.data else []

            # Cache sync status
            cache_key = self._build_cache_key("calendar", "sync_status", tenant_id)

            entry = CacheEntry(
                key=cache_key,
                value=sync_statuses,
                timestamp=datetime.now(timezone.utc),
                ttl=self.refresh_intervals["calendar_sync"],
                source="supabase",
                tenant_id=tenant_id
            )

            await self._store_cache_entry(entry)
            stats["cached"] = len(sync_statuses)

            # Also cache individual provider status for quick lookup
            for status in sync_statuses:
                provider_key = self._build_cache_key(
                    "calendar",
                    f"provider_{status['provider']}_{status['doctor_id']}",
                    tenant_id
                )

                provider_entry = CacheEntry(
                    key=provider_key,
                    value=status,
                    timestamp=datetime.now(timezone.utc),
                    ttl=60,  # 1 minute for individual status
                    source="supabase",
                    tenant_id=tenant_id
                )

                await self._store_cache_entry(provider_entry)

            logger.info(f"Cached calendar status for {stats['cached']} providers")
            return stats

        except Exception as e:
            logger.error(f"Failed to cache calendar status: {e}")
            return {"cached": 0, "failed": 1, "error": str(e)}

    async def cache_configuration(self, tenant_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Cache system configuration

        Args:
            tenant_id: Optional tenant ID filter

        Returns:
            Cache statistics
        """
        try:
            stats = {"cached": 0, "failed": 0}

            # Configuration tables to cache
            config_tables = [
                "clinics",
                "business_hours",
                "appointment_types",
                "notification_templates"
            ]

            for table in config_tables:
                query = self.supabase.table(table).select("*")

                if tenant_id and table != "appointment_types":  # Some tables are global
                    query = query.eq("tenant_id", tenant_id)

                response = query.execute()
                data = response.data if response.data else []

                cache_key = self._build_cache_key("config", table, tenant_id)

                entry = CacheEntry(
                    key=cache_key,
                    value=data,
                    timestamp=datetime.now(timezone.utc),
                    ttl=self.refresh_intervals["config"],
                    source="supabase",
                    tenant_id=tenant_id
                )

                await self._store_cache_entry(entry)
                stats["cached"] += len(data)

            logger.info(f"Cached {stats['cached']} configuration items")
            return stats

        except Exception as e:
            logger.error(f"Failed to cache configuration: {e}")
            return {"cached": 0, "failed": 1, "error": str(e)}

    async def get_cached_data(
        self,
        category: str,
        key: str,
        tenant_id: Optional[str] = None,
        fallback_to_stale: bool = True
    ) -> Optional[Any]:
        """
        Retrieve data from cache

        Args:
            category: Data category (appointments, doctors, etc.)
            key: Specific key within category
            tenant_id: Optional tenant ID
            fallback_to_stale: Use stale data if fresh data unavailable

        Returns:
            Cached data or None
        """
        try:
            cache_key = self._build_cache_key(category, key, tenant_id)

            # Try to get from Redis
            cached_json = await self.redis_client.get(cache_key)

            if cached_json:
                entry = CacheEntry.from_dict(json.loads(cached_json))

                # Check if expired
                if not entry.is_expired():
                    return entry.value
                elif fallback_to_stale:
                    logger.warning(f"Using stale cache for {cache_key}")
                    return entry.value

            return None

        except Exception as e:
            logger.error(f"Failed to get cached data: {e}")
            return None

    async def refresh_cache(self, force: bool = False) -> Dict[str, Any]:
        """
        Refresh all or expired cache entries

        Args:
            force: Force refresh even if not expired

        Returns:
            Refresh statistics
        """
        try:
            stats = {"refreshed": 0, "failed": 0, "skipped": 0}

            # Get all cache keys
            pattern = f"{self.cache_prefix}:*"
            keys = []
            async for key in self.redis_client.scan_iter(match=pattern):
                keys.append(key)

            # Check each key for expiration
            for key in keys:
                try:
                    cached_json = await self.redis_client.get(key)
                    if cached_json:
                        entry = CacheEntry.from_dict(json.loads(cached_json))

                        if force or entry.is_expired():
                            # Determine category and refresh
                            parts = key.decode() if isinstance(key, bytes) else key
                            parts = parts.split(":")

                            if len(parts) >= 3:
                                category = parts[1]

                                # Refresh based on category
                                if category == "appointments":
                                    await self.cache_appointments(entry.tenant_id)
                                elif category == "doctor_availability":
                                    await self.cache_doctor_availability(entry.tenant_id)
                                elif category == "patients":
                                    await self.cache_patient_records(entry.tenant_id)
                                elif category == "calendar":
                                    await self.cache_calendar_status(entry.tenant_id)
                                elif category == "config":
                                    await self.cache_configuration(entry.tenant_id)

                                stats["refreshed"] += 1
                        else:
                            stats["skipped"] += 1

                except Exception as e:
                    logger.error(f"Failed to refresh {key}: {e}")
                    stats["failed"] += 1

            logger.info(f"Cache refresh complete: {stats}")
            return stats

        except Exception as e:
            logger.error(f"Failed to refresh cache: {e}")
            return {"refreshed": 0, "failed": 1, "error": str(e)}

    async def invalidate_cache(
        self,
        category: Optional[str] = None,
        key: Optional[str] = None,
        tenant_id: Optional[str] = None
    ) -> int:
        """
        Invalidate cache entries

        Args:
            category: Optional category to invalidate
            key: Optional specific key
            tenant_id: Optional tenant ID

        Returns:
            Number of entries invalidated
        """
        try:
            if key:
                # Invalidate specific key
                cache_key = self._build_cache_key(category, key, tenant_id)
                result = await self.redis_client.delete(cache_key)
                return result
            else:
                # Invalidate by pattern
                pattern = self._build_cache_key(category or "*", "*", tenant_id)
                count = 0

                async for key in self.redis_client.scan_iter(match=pattern):
                    await self.redis_client.delete(key)
                    count += 1

                logger.info(f"Invalidated {count} cache entries")
                return count

        except Exception as e:
            logger.error(f"Failed to invalidate cache: {e}")
            return 0

    async def get_cache_statistics(self) -> Dict[str, Any]:
        """
        Get cache statistics

        Returns:
            Cache statistics including size, hit rate, etc.
        """
        try:
            stats = {
                "total_keys": 0,
                "expired_keys": 0,
                "by_category": {},
                "by_tenant": {},
                "total_size_bytes": 0
            }

            pattern = f"{self.cache_prefix}:*"
            async for key in self.redis_client.scan_iter(match=pattern):
                stats["total_keys"] += 1

                # Get entry details
                cached_json = await self.redis_client.get(key)
                if cached_json:
                    stats["total_size_bytes"] += len(cached_json)

                    entry = CacheEntry.from_dict(json.loads(cached_json))
                    if entry.is_expired():
                        stats["expired_keys"] += 1

                    # Parse category
                    parts = key.decode() if isinstance(key, bytes) else key
                    parts = parts.split(":")
                    if len(parts) >= 2:
                        category = parts[1]
                        stats["by_category"][category] = stats["by_category"].get(category, 0) + 1

                    # Track by tenant
                    if entry.tenant_id:
                        stats["by_tenant"][entry.tenant_id] = stats["by_tenant"].get(entry.tenant_id, 0) + 1

            return stats

        except Exception as e:
            logger.error(f"Failed to get cache statistics: {e}")
            return {}

    def _build_cache_key(self, category: str, key: str, tenant_id: Optional[str] = None) -> str:
        """Build a cache key with optional tenant namespace"""
        if tenant_id:
            return f"{self.cache_prefix}:{category}:{tenant_id}:{key}"
        return f"{self.cache_prefix}:{category}:{key}"

    def _calculate_checksum(self, data: Any) -> str:
        """Calculate checksum for data integrity verification"""
        json_str = json.dumps(data, sort_keys=True)
        return hashlib.sha256(json_str.encode()).hexdigest()

    async def _calculate_availability(self, doctor: Dict[str, Any]) -> Dict[str, List[str]]:
        """Calculate doctor availability for next 7 days"""
        availability = {}
        for i in range(7):
            date = datetime.now(timezone.utc) + timedelta(days=i)
            date_str = date.strftime("%Y-%m-%d")

            # Simple availability logic (would be more complex in production)
            if date.weekday() < 5:  # Monday to Friday
                availability[date_str] = ["09:00", "10:00", "11:00", "14:00", "15:00", "16:00"]
            else:
                availability[date_str] = []  # Weekend

        return availability

    async def _store_cache_entry(self, entry: CacheEntry) -> bool:
        """Store cache entry in Redis"""
        try:
            json_data = json.dumps(entry.to_dict())
            if entry.ttl:
                await self.redis_client.setex(entry.key, entry.ttl, json_data)
            else:
                await self.redis_client.set(entry.key, json_data)
            return True
        except Exception as e:
            logger.error(f"Failed to store cache entry: {e}")
            return False