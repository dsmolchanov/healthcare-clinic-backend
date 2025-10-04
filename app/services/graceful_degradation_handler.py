"""
Graceful Degradation Handler
Manages service degradation during partial system failures
"""

import asyncio
import logging
from typing import Dict, Any, Optional, List, Callable
from datetime import datetime, timezone, timedelta
from enum import Enum
from dataclasses import dataclass, field
import json

logger = logging.getLogger(__name__)


class ServiceStatus(Enum):
    """Service health status levels"""
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    CRITICAL = "critical"
    OFFLINE = "offline"


class DegradationLevel(Enum):
    """System degradation levels"""
    NONE = 0
    MINIMAL = 1
    MODERATE = 2
    SEVERE = 3
    CRITICAL = 4


@dataclass
class ServiceHealth:
    """Health status of a service"""
    name: str
    status: ServiceStatus
    last_check: datetime
    error_count: int = 0
    response_time_ms: Optional[int] = None
    error_message: Optional[str] = None
    features_disabled: List[str] = field(default_factory=list)


@dataclass
class DegradationPolicy:
    """Policy for handling degradation"""
    service: str
    threshold_error_count: int = 3
    threshold_response_time_ms: int = 5000
    fallback_actions: List[str] = field(default_factory=list)
    disabled_features: List[str] = field(default_factory=list)
    cache_ttl_multiplier: float = 2.0
    retry_delay_seconds: int = 30


class GracefulDegradationHandler:
    """
    Manages graceful degradation when external services fail or degrade.
    Implements circuit breaker pattern and fallback mechanisms.
    """

    def __init__(self, redis_client: Any, supabase_client: Any):
        self.redis_client = redis_client
        self.supabase = supabase_client
        self.service_health: Dict[str, ServiceHealth] = {}
        self.degradation_policies: Dict[str, DegradationPolicy] = {}
        self.fallback_handlers: Dict[str, Callable] = {}
        self.current_degradation_level = DegradationLevel.NONE
        self.monitoring_task: Optional[asyncio.Task] = None

        # Initialize default policies
        self._initialize_default_policies()

    def _initialize_default_policies(self):
        """Initialize default degradation policies for known services"""

        # Google Calendar policy
        self.degradation_policies["google_calendar"] = DegradationPolicy(
            service="google_calendar",
            threshold_error_count=3,
            threshold_response_time_ms=3000,
            fallback_actions=["use_cache", "queue_for_retry", "notify_user"],
            disabled_features=["real_time_sync", "calendar_creation"],
            cache_ttl_multiplier=3.0,
            retry_delay_seconds=60
        )

        # WhatsApp/Evolution API policy
        self.degradation_policies["whatsapp"] = DegradationPolicy(
            service="whatsapp",
            threshold_error_count=5,
            threshold_response_time_ms=5000,
            fallback_actions=["queue_messages", "use_sms_fallback"],
            disabled_features=["voice_notes", "media_messages"],
            cache_ttl_multiplier=1.0,
            retry_delay_seconds=30
        )

        # Supabase policy
        self.degradation_policies["supabase"] = DegradationPolicy(
            service="supabase",
            threshold_error_count=2,
            threshold_response_time_ms=2000,
            fallback_actions=["use_redis_cache", "read_only_mode"],
            disabled_features=["write_operations", "realtime_updates"],
            cache_ttl_multiplier=5.0,
            retry_delay_seconds=10
        )

        # Redis policy
        self.degradation_policies["redis"] = DegradationPolicy(
            service="redis",
            threshold_error_count=5,
            threshold_response_time_ms=1000,
            fallback_actions=["use_memory_cache", "disable_caching"],
            disabled_features=["session_storage", "rate_limiting"],
            cache_ttl_multiplier=1.0,
            retry_delay_seconds=5
        )

    async def start_monitoring(self):
        """Start monitoring services"""
        if self.monitoring_task:
            return

        self.monitoring_task = asyncio.create_task(self._monitor_services())
        logger.info("Started service health monitoring")

    async def stop_monitoring(self):
        """Stop monitoring services"""
        if self.monitoring_task:
            self.monitoring_task.cancel()
            try:
                await self.monitoring_task
            except asyncio.CancelledError:
                pass
            self.monitoring_task = None
            logger.info("Stopped service health monitoring")

    async def _monitor_services(self):
        """Continuously monitor service health"""
        while True:
            try:
                await self.check_all_services()
                await asyncio.sleep(30)  # Check every 30 seconds
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in service monitoring: {e}")
                await asyncio.sleep(60)

    async def check_all_services(self):
        """Check health of all registered services"""
        services_to_check = [
            ("google_calendar", self._check_google_calendar),
            ("whatsapp", self._check_whatsapp),
            ("supabase", self._check_supabase),
            ("redis", self._check_redis),
        ]

        results = await asyncio.gather(
            *[self.check_service(name, checker) for name, checker in services_to_check],
            return_exceptions=True
        )

        # Update overall degradation level
        self._update_degradation_level()

        # Log health summary
        healthy = sum(1 for r in results if isinstance(r, ServiceHealth) and r.status == ServiceStatus.HEALTHY)
        degraded = sum(1 for r in results if isinstance(r, ServiceHealth) and r.status == ServiceStatus.DEGRADED)
        critical = sum(1 for r in results if isinstance(r, ServiceHealth) and r.status == ServiceStatus.CRITICAL)

        logger.info(f"Service health: {healthy} healthy, {degraded} degraded, {critical} critical")

    async def check_service(self, service_name: str, health_checker: Callable) -> ServiceHealth:
        """
        Check health of a specific service

        Args:
            service_name: Name of the service
            health_checker: Async function to check service health

        Returns:
            ServiceHealth object
        """
        try:
            start_time = datetime.now(timezone.utc)

            # Run health check
            is_healthy, error_message = await health_checker()

            response_time_ms = int((datetime.now(timezone.utc) - start_time).total_seconds() * 1000)

            # Get or create health record
            if service_name not in self.service_health:
                self.service_health[service_name] = ServiceHealth(
                    name=service_name,
                    status=ServiceStatus.HEALTHY,
                    last_check=start_time,
                    error_count=0
                )

            health = self.service_health[service_name]
            health.last_check = start_time
            health.response_time_ms = response_time_ms

            policy = self.degradation_policies.get(service_name)

            if not is_healthy:
                # Service check failed
                health.error_count += 1
                health.error_message = error_message

                if health.error_count >= (policy.threshold_error_count if policy else 3):
                    health.status = ServiceStatus.CRITICAL
                    await self._apply_degradation(service_name)
                else:
                    health.status = ServiceStatus.DEGRADED
            elif policy and response_time_ms > policy.threshold_response_time_ms:
                # Service is slow
                health.status = ServiceStatus.DEGRADED
                health.error_message = f"Slow response: {response_time_ms}ms"
                await self._apply_degradation(service_name, partial=True)
            else:
                # Service is healthy
                if health.status != ServiceStatus.HEALTHY:
                    await self._restore_service(service_name)
                health.status = ServiceStatus.HEALTHY
                health.error_count = 0
                health.error_message = None

            # Store health status in Redis
            await self._store_health_status(health)

            return health

        except Exception as e:
            logger.error(f"Error checking service {service_name}: {e}")
            return ServiceHealth(
                name=service_name,
                status=ServiceStatus.OFFLINE,
                last_check=datetime.now(timezone.utc),
                error_message=str(e)
            )

    async def _check_google_calendar(self) -> tuple[bool, Optional[str]]:
        """Check Google Calendar service health"""
        try:
            # Import here to avoid circular dependencies
            from app.calendar.oauth_manager import CalendarOAuthManager

            manager = CalendarOAuthManager()
            # Try to get calendar list (lightweight operation)
            test_token = await manager._get_test_token()
            if test_token:
                return True, None
            return False, "No valid tokens available"
        except Exception as e:
            return False, str(e)

    async def _check_whatsapp(self) -> tuple[bool, Optional[str]]:
        """Check WhatsApp/Evolution API health"""
        try:
            import aiohttp
            evolution_url = "http://evolution-api-prod.fly.dev/health"

            async with aiohttp.ClientSession() as session:
                async with session.get(evolution_url, timeout=5) as response:
                    if response.status == 200:
                        return True, None
                    return False, f"Status code: {response.status}"
        except Exception as e:
            return False, str(e)

    async def _check_supabase(self) -> tuple[bool, Optional[str]]:
        """Check Supabase health"""
        try:
            # Simple query to check connection
            response = self.supabase.table("clinics").select("id").limit(1).execute()
            if response.data is not None:
                return True, None
            return False, "No data returned"
        except Exception as e:
            return False, str(e)

    async def _check_redis(self) -> tuple[bool, Optional[str]]:
        """Check Redis health"""
        try:
            # Ping Redis
            await self.redis_client.ping()
            return True, None
        except Exception as e:
            return False, str(e)

    async def _apply_degradation(self, service_name: str, partial: bool = False):
        """
        Apply degradation policy for a service

        Args:
            service_name: Name of the degraded service
            partial: Whether this is partial degradation (slow but working)
        """
        policy = self.degradation_policies.get(service_name)
        if not policy:
            logger.warning(f"No degradation policy for {service_name}")
            return

        logger.warning(f"Applying {'partial' if partial else 'full'} degradation for {service_name}")

        # Apply fallback actions
        for action in policy.fallback_actions:
            if handler := self.fallback_handlers.get(f"{service_name}:{action}"):
                try:
                    await handler(partial)
                except Exception as e:
                    logger.error(f"Error applying fallback {action} for {service_name}: {e}")

        # Disable features if full degradation
        if not partial:
            health = self.service_health[service_name]
            health.features_disabled = policy.disabled_features

        # Store degradation state
        await self._store_degradation_state(service_name, policy, partial)

    async def _restore_service(self, service_name: str):
        """
        Restore service after recovery

        Args:
            service_name: Name of the restored service
        """
        logger.info(f"Restoring service {service_name}")

        health = self.service_health[service_name]
        health.features_disabled = []

        # Run restoration handlers
        if handler := self.fallback_handlers.get(f"{service_name}:restore"):
            try:
                await handler()
            except Exception as e:
                logger.error(f"Error restoring {service_name}: {e}")

        # Clear degradation state
        await self._clear_degradation_state(service_name)

    def _update_degradation_level(self):
        """Update overall system degradation level"""
        critical_count = sum(1 for h in self.service_health.values() if h.status == ServiceStatus.CRITICAL)
        degraded_count = sum(1 for h in self.service_health.values() if h.status == ServiceStatus.DEGRADED)
        total_services = len(self.service_health)

        if total_services == 0:
            self.current_degradation_level = DegradationLevel.NONE
        elif critical_count >= total_services * 0.5:
            self.current_degradation_level = DegradationLevel.CRITICAL
        elif critical_count > 0:
            self.current_degradation_level = DegradationLevel.SEVERE
        elif degraded_count >= total_services * 0.5:
            self.current_degradation_level = DegradationLevel.MODERATE
        elif degraded_count > 0:
            self.current_degradation_level = DegradationLevel.MINIMAL
        else:
            self.current_degradation_level = DegradationLevel.NONE

    async def _store_health_status(self, health: ServiceHealth):
        """Store health status in Redis"""
        try:
            key = f"service_health:{health.name}"
            data = {
                "status": health.status.value,
                "last_check": health.last_check.isoformat(),
                "error_count": health.error_count,
                "response_time_ms": health.response_time_ms,
                "error_message": health.error_message,
                "features_disabled": health.features_disabled
            }
            await self.redis_client.setex(key, 300, json.dumps(data))  # 5 minute TTL
        except Exception as e:
            logger.error(f"Failed to store health status: {e}")

    async def _store_degradation_state(self, service_name: str, policy: DegradationPolicy, partial: bool):
        """Store degradation state in Redis"""
        try:
            key = f"degradation:{service_name}"
            data = {
                "service": service_name,
                "partial": partial,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "disabled_features": policy.disabled_features,
                "fallback_actions": policy.fallback_actions,
                "retry_after": (datetime.now(timezone.utc) + timedelta(seconds=policy.retry_delay_seconds)).isoformat()
            }
            await self.redis_client.setex(key, 3600, json.dumps(data))  # 1 hour TTL
        except Exception as e:
            logger.error(f"Failed to store degradation state: {e}")

    async def _clear_degradation_state(self, service_name: str):
        """Clear degradation state from Redis"""
        try:
            key = f"degradation:{service_name}"
            await self.redis_client.delete(key)
        except Exception as e:
            logger.error(f"Failed to clear degradation state: {e}")

    def register_fallback_handler(self, service_action: str, handler: Callable):
        """
        Register a fallback handler for a service action

        Args:
            service_action: Service and action name (e.g., "google_calendar:use_cache")
            handler: Async function to handle the fallback
        """
        self.fallback_handlers[service_action] = handler
        logger.info(f"Registered fallback handler for {service_action}")

    async def get_service_status(self, service_name: Optional[str] = None) -> Dict[str, Any]:
        """
        Get current service status

        Args:
            service_name: Optional specific service name

        Returns:
            Service status information
        """
        if service_name:
            if service_name in self.service_health:
                health = self.service_health[service_name]
                return {
                    "name": health.name,
                    "status": health.status.value,
                    "last_check": health.last_check.isoformat(),
                    "error_count": health.error_count,
                    "response_time_ms": health.response_time_ms,
                    "error_message": health.error_message,
                    "features_disabled": health.features_disabled
                }
            return {"error": f"Service {service_name} not found"}

        # Return all services status
        return {
            "degradation_level": self.current_degradation_level.name,
            "services": {
                name: {
                    "status": health.status.value,
                    "last_check": health.last_check.isoformat(),
                    "error_count": health.error_count,
                    "response_time_ms": health.response_time_ms,
                    "features_disabled": health.features_disabled
                }
                for name, health in self.service_health.items()
            }
        }

    async def is_feature_available(self, feature: str) -> bool:
        """
        Check if a feature is available based on current degradation

        Args:
            feature: Feature name to check

        Returns:
            True if feature is available
        """
        for health in self.service_health.values():
            if feature in health.features_disabled:
                return False
        return True

    async def get_cache_ttl_multiplier(self, service_name: str) -> float:
        """
        Get cache TTL multiplier for a service

        Args:
            service_name: Service name

        Returns:
            TTL multiplier (1.0 = normal, higher = longer cache)
        """
        if service_name in self.service_health:
            health = self.service_health[service_name]
            if health.status != ServiceStatus.HEALTHY:
                policy = self.degradation_policies.get(service_name)
                if policy:
                    return policy.cache_ttl_multiplier
        return 1.0

    async def should_retry_operation(self, service_name: str) -> bool:
        """
        Check if operation should be retried for a service

        Args:
            service_name: Service name

        Returns:
            True if operation should be retried
        """
        try:
            # Check if service has degradation state
            key = f"degradation:{service_name}"
            data = await self.redis_client.get(key)

            if data:
                degradation = json.loads(data)
                retry_after = datetime.fromisoformat(degradation["retry_after"])
                return datetime.now(timezone.utc) >= retry_after

            return True  # No degradation, can retry

        except Exception as e:
            logger.error(f"Error checking retry status: {e}")
            return False

    async def report_operation_result(self, service_name: str, success: bool, response_time_ms: Optional[int] = None):
        """
        Report operation result to update service health

        Args:
            service_name: Service name
            success: Whether operation succeeded
            response_time_ms: Response time in milliseconds
        """
        if service_name not in self.service_health:
            self.service_health[service_name] = ServiceHealth(
                name=service_name,
                status=ServiceStatus.HEALTHY,
                last_check=datetime.now(timezone.utc)
            )

        health = self.service_health[service_name]

        if success:
            # Successful operation
            if health.error_count > 0:
                health.error_count = max(0, health.error_count - 1)  # Gradually reduce error count

            if response_time_ms:
                health.response_time_ms = response_time_ms
                policy = self.degradation_policies.get(service_name)
                if policy and response_time_ms > policy.threshold_response_time_ms:
                    health.status = ServiceStatus.DEGRADED
                elif health.error_count == 0:
                    health.status = ServiceStatus.HEALTHY
        else:
            # Failed operation
            health.error_count += 1
            policy = self.degradation_policies.get(service_name)

            if policy and health.error_count >= policy.threshold_error_count:
                health.status = ServiceStatus.CRITICAL
                await self._apply_degradation(service_name)
            else:
                health.status = ServiceStatus.DEGRADED

        health.last_check = datetime.now(timezone.utc)
        await self._store_health_status(health)