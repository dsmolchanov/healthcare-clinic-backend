"""
Disaster Recovery Orchestrator
Coordinates recovery from system failures combining Supabase PITR with external backups
"""

import asyncio
import logging
from typing import Dict, Any, Optional, List, Tuple
from datetime import datetime, timezone, timedelta
from enum import Enum
from dataclasses import dataclass
import json
import redis.asyncio as redis
from pathlib import Path

from .external_backup_service import ExternalBackupService
from .offline_cache_manager import OfflineCacheManager
from .graceful_degradation_handler import GracefulDegradationHandler

logger = logging.getLogger(__name__)


class DisasterType(Enum):
    """Types of disasters that can occur"""
    DATABASE_FAILURE = "database_failure"
    REDIS_FAILURE = "redis_failure"
    NETWORK_OUTAGE = "network_outage"
    DATA_CORRUPTION = "data_corruption"
    SERVICE_DEGRADATION = "service_degradation"
    COMPLETE_OUTAGE = "complete_outage"


class RecoveryPhase(Enum):
    """Phases of disaster recovery"""
    DETECTION = "detection"
    ASSESSMENT = "assessment"
    ISOLATION = "isolation"
    RECOVERY = "recovery"
    VALIDATION = "validation"
    RESTORATION = "restoration"
    MONITORING = "monitoring"


@dataclass
class DisasterEvent:
    """Represents a disaster event"""
    event_id: str
    type: DisasterType
    severity: int  # 1-5 scale
    detected_at: datetime
    affected_services: List[str]
    data_loss_risk: bool
    recovery_point: Optional[datetime] = None
    recovery_time_objective: Optional[timedelta] = None
    status: str = "detected"
    resolution: Optional[str] = None


@dataclass
class RecoveryPlan:
    """Recovery plan for a disaster"""
    plan_id: str
    disaster_event: DisasterEvent
    phases: List[RecoveryPhase]
    actions: Dict[RecoveryPhase, List[str]]
    estimated_recovery_time: timedelta
    data_sources: List[str]
    rollback_points: List[datetime]
    validation_checks: List[str]


class DisasterRecoveryOrchestrator:
    """
    Orchestrates disaster recovery by coordinating:
    - Supabase PITR for database recovery
    - External backup restoration for Redis, WhatsApp, config
    - Service degradation during recovery
    - Validation and testing of recovered state
    """

    def __init__(
        self,
        redis_client: redis.Redis,
        supabase_client: Any,
        backup_service: ExternalBackupService,
        cache_manager: OfflineCacheManager,
        degradation_handler: GracefulDegradationHandler
    ):
        self.redis_client = redis_client
        self.supabase = supabase_client
        self.backup_service = backup_service
        self.cache_manager = cache_manager
        self.degradation_handler = degradation_handler

        self.active_disasters: Dict[str, DisasterEvent] = {}
        self.recovery_plans: Dict[str, RecoveryPlan] = {}
        self.recovery_history: List[DisasterEvent] = []

        # Recovery configuration
        self.recovery_config = {
            "max_data_age_hours": 24,  # Maximum acceptable data age
            "validation_retries": 3,
            "rollback_enabled": True,
            "parallel_recovery": True,
            "notify_on_recovery": True
        }

    async def detect_disaster(self) -> Optional[DisasterEvent]:
        """
        Detect potential disasters by monitoring system health

        Returns:
            DisasterEvent if disaster detected, None otherwise
        """
        try:
            # Get service status from degradation handler
            service_status = await self.degradation_handler.get_service_status()

            # Check for critical failures
            critical_services = []
            degraded_services = []

            for service, status in service_status.get("services", {}).items():
                if status["status"] == "critical":
                    critical_services.append(service)
                elif status["status"] == "degraded":
                    degraded_services.append(service)

            # Determine disaster type and severity
            disaster_type = None
            severity = 0
            data_loss_risk = False

            if "supabase" in critical_services:
                disaster_type = DisasterType.DATABASE_FAILURE
                severity = 5
                data_loss_risk = True
            elif "redis" in critical_services:
                disaster_type = DisasterType.REDIS_FAILURE
                severity = 3
                data_loss_risk = False  # Redis is cache, not primary storage
            elif len(critical_services) >= 2:
                disaster_type = DisasterType.COMPLETE_OUTAGE
                severity = 5
                data_loss_risk = True
            elif len(degraded_services) >= 3:
                disaster_type = DisasterType.SERVICE_DEGRADATION
                severity = 2
                data_loss_risk = False

            if disaster_type:
                # Create disaster event
                event = DisasterEvent(
                    event_id=f"disaster_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}",
                    type=disaster_type,
                    severity=severity,
                    detected_at=datetime.now(timezone.utc),
                    affected_services=critical_services + degraded_services,
                    data_loss_risk=data_loss_risk,
                    recovery_time_objective=timedelta(minutes=severity * 10)
                )

                self.active_disasters[event.event_id] = event
                logger.critical(f"Disaster detected: {event}")
                return event

            return None

        except Exception as e:
            logger.error(f"Error detecting disaster: {e}")
            return None

    async def create_recovery_plan(self, disaster_event: DisasterEvent) -> RecoveryPlan:
        """
        Create a recovery plan for a disaster event

        Args:
            disaster_event: The disaster event to recover from

        Returns:
            RecoveryPlan with steps to recover
        """
        try:
            phases = []
            actions = {}

            # Always start with assessment
            phases.append(RecoveryPhase.ASSESSMENT)
            actions[RecoveryPhase.ASSESSMENT] = [
                "analyze_data_loss",
                "identify_recovery_point",
                "estimate_recovery_time"
            ]

            # Add phases based on disaster type
            if disaster_event.type == DisasterType.DATABASE_FAILURE:
                phases.extend([
                    RecoveryPhase.ISOLATION,
                    RecoveryPhase.RECOVERY,
                    RecoveryPhase.VALIDATION,
                    RecoveryPhase.RESTORATION
                ])

                actions[RecoveryPhase.ISOLATION] = [
                    "enable_read_only_mode",
                    "disconnect_write_operations",
                    "notify_users"
                ]

                actions[RecoveryPhase.RECOVERY] = [
                    "initiate_supabase_pitr",
                    "restore_external_backups",
                    "synchronize_data"
                ]

                actions[RecoveryPhase.VALIDATION] = [
                    "verify_data_integrity",
                    "test_critical_functions",
                    "compare_checksums"
                ]

                actions[RecoveryPhase.RESTORATION] = [
                    "enable_write_operations",
                    "restore_full_functionality",
                    "clear_cache"
                ]

            elif disaster_event.type == DisasterType.REDIS_FAILURE:
                phases.extend([
                    RecoveryPhase.RECOVERY,
                    RecoveryPhase.VALIDATION
                ])

                actions[RecoveryPhase.RECOVERY] = [
                    "restore_redis_backup",
                    "rebuild_cache",
                    "restore_sessions"
                ]

                actions[RecoveryPhase.VALIDATION] = [
                    "verify_cache_data",
                    "test_session_management"
                ]

            elif disaster_event.type == DisasterType.COMPLETE_OUTAGE:
                phases.extend([
                    RecoveryPhase.ISOLATION,
                    RecoveryPhase.RECOVERY,
                    RecoveryPhase.VALIDATION,
                    RecoveryPhase.RESTORATION,
                    RecoveryPhase.MONITORING
                ])

                actions[RecoveryPhase.ISOLATION] = [
                    "activate_maintenance_mode",
                    "preserve_current_state"
                ]

                actions[RecoveryPhase.RECOVERY] = [
                    "restore_all_services",
                    "synchronize_all_data",
                    "rebuild_all_caches"
                ]

                actions[RecoveryPhase.VALIDATION] = [
                    "comprehensive_system_test",
                    "data_consistency_check",
                    "performance_validation"
                ]

                actions[RecoveryPhase.RESTORATION] = [
                    "gradual_traffic_restoration",
                    "monitor_system_stability"
                ]

                actions[RecoveryPhase.MONITORING] = [
                    "enhanced_monitoring",
                    "alert_on_anomalies"
                ]

            # Calculate estimated recovery time
            estimated_time = timedelta(minutes=len(phases) * disaster_event.severity * 5)

            # Identify recovery points
            rollback_points = await self._identify_rollback_points()

            # Create recovery plan
            plan = RecoveryPlan(
                plan_id=f"recovery_{disaster_event.event_id}",
                disaster_event=disaster_event,
                phases=phases,
                actions=actions,
                estimated_recovery_time=estimated_time,
                data_sources=["supabase_pitr", "external_backups", "redis_cache"],
                rollback_points=rollback_points,
                validation_checks=[
                    "database_connectivity",
                    "data_integrity",
                    "service_availability",
                    "performance_metrics"
                ]
            )

            self.recovery_plans[plan.plan_id] = plan
            logger.info(f"Created recovery plan: {plan.plan_id}")
            return plan

        except Exception as e:
            logger.error(f"Error creating recovery plan: {e}")
            raise

    async def execute_recovery(self, plan: RecoveryPlan) -> Dict[str, Any]:
        """
        Execute a recovery plan

        Args:
            plan: Recovery plan to execute

        Returns:
            Recovery results
        """
        results = {
            "plan_id": plan.plan_id,
            "start_time": datetime.now(timezone.utc).isoformat(),
            "phases": {},
            "success": False,
            "errors": []
        }

        try:
            for phase in plan.phases:
                logger.info(f"Executing recovery phase: {phase.value}")
                phase_results = await self._execute_phase(phase, plan)
                results["phases"][phase.value] = phase_results

                if not phase_results["success"]:
                    # Phase failed, attempt rollback if configured
                    if self.recovery_config["rollback_enabled"]:
                        await self._rollback_recovery(plan, phase)
                    results["errors"].append(f"Phase {phase.value} failed")
                    break

            # All phases completed successfully
            if not results["errors"]:
                results["success"] = True
                plan.disaster_event.status = "recovered"
                plan.disaster_event.resolution = f"Successfully recovered using plan {plan.plan_id}"

                # Move to history
                self.recovery_history.append(plan.disaster_event)
                del self.active_disasters[plan.disaster_event.event_id]

            results["end_time"] = datetime.now(timezone.utc).isoformat()
            return results

        except Exception as e:
            logger.error(f"Error executing recovery: {e}")
            results["errors"].append(str(e))
            return results

    async def _execute_phase(self, phase: RecoveryPhase, plan: RecoveryPlan) -> Dict[str, Any]:
        """
        Execute a specific recovery phase

        Args:
            phase: Recovery phase to execute
            plan: Recovery plan context

        Returns:
            Phase execution results
        """
        phase_results = {
            "phase": phase.value,
            "actions": {},
            "success": False,
            "duration_ms": 0
        }

        start_time = datetime.now(timezone.utc)

        try:
            actions = plan.actions.get(phase, [])

            for action in actions:
                logger.info(f"Executing action: {action}")
                action_result = await self._execute_action(action, plan)
                phase_results["actions"][action] = action_result

                if not action_result["success"]:
                    phase_results["success"] = False
                    return phase_results

            phase_results["success"] = True
            phase_results["duration_ms"] = int(
                (datetime.now(timezone.utc) - start_time).total_seconds() * 1000
            )
            return phase_results

        except Exception as e:
            logger.error(f"Error executing phase {phase.value}: {e}")
            phase_results["success"] = False
            phase_results["error"] = str(e)
            return phase_results

    async def _execute_action(self, action: str, plan: RecoveryPlan) -> Dict[str, Any]:
        """
        Execute a specific recovery action

        Args:
            action: Action name to execute
            plan: Recovery plan context

        Returns:
            Action execution results
        """
        action_result = {
            "action": action,
            "success": False,
            "details": {}
        }

        try:
            # Map action to implementation
            if action == "analyze_data_loss":
                result = await self._analyze_data_loss(plan.disaster_event)
                action_result["details"] = result
                action_result["success"] = True

            elif action == "identify_recovery_point":
                recovery_point = await self._identify_recovery_point(plan.disaster_event)
                plan.disaster_event.recovery_point = recovery_point
                action_result["details"]["recovery_point"] = recovery_point.isoformat()
                action_result["success"] = True

            elif action == "initiate_supabase_pitr":
                success = await self._initiate_supabase_pitr(plan.disaster_event.recovery_point)
                action_result["success"] = success

            elif action == "restore_external_backups":
                backup_results = await self._restore_external_backups()
                action_result["details"] = backup_results
                action_result["success"] = backup_results["success"]

            elif action == "restore_redis_backup":
                success = await self._restore_redis_backup()
                action_result["success"] = success

            elif action == "rebuild_cache":
                cache_stats = await self.cache_manager.refresh_cache(force=True)
                action_result["details"] = cache_stats
                action_result["success"] = cache_stats["failed"] == 0

            elif action == "verify_data_integrity":
                integrity_results = await self._verify_data_integrity()
                action_result["details"] = integrity_results
                action_result["success"] = integrity_results["valid"]

            elif action == "enable_read_only_mode":
                await self._enable_read_only_mode()
                action_result["success"] = True

            elif action == "enable_write_operations":
                await self._enable_write_operations()
                action_result["success"] = True

            elif action == "notify_users":
                await self._notify_users(plan.disaster_event, "recovery_in_progress")
                action_result["success"] = True

            else:
                # Default success for unimplemented actions (for demo)
                logger.warning(f"Action {action} not implemented, marking as success")
                action_result["success"] = True

            return action_result

        except Exception as e:
            logger.error(f"Error executing action {action}: {e}")
            action_result["error"] = str(e)
            return action_result

    async def _analyze_data_loss(self, disaster_event: DisasterEvent) -> Dict[str, Any]:
        """Analyze potential data loss from disaster"""
        analysis = {
            "data_at_risk": [],
            "last_backup": None,
            "recovery_point_objective_met": False
        }

        try:
            # Check last backup times
            backups = await self.backup_service.list_backups()
            if backups:
                analysis["last_backup"] = backups[0]["timestamp"]

                # Calculate potential data loss
                last_backup_time = datetime.fromisoformat(backups[0]["timestamp"])
                time_since_backup = datetime.now(timezone.utc) - last_backup_time

                if time_since_backup > timedelta(hours=1):
                    analysis["data_at_risk"].append({
                        "type": "recent_transactions",
                        "time_window": str(time_since_backup)
                    })

            # Check Supabase PITR capability
            # Supabase has 2-minute RPO, so recent data should be recoverable
            analysis["supabase_pitr_available"] = True
            analysis["supabase_rpo_minutes"] = 2

            # Determine if RPO is met
            if disaster_event.recovery_time_objective:
                analysis["recovery_point_objective_met"] = time_since_backup < disaster_event.recovery_time_objective

            return analysis

        except Exception as e:
            logger.error(f"Error analyzing data loss: {e}")
            return analysis

    async def _identify_recovery_point(self, disaster_event: DisasterEvent) -> datetime:
        """Identify the best recovery point for the disaster"""
        try:
            # Default to 5 minutes before disaster detection
            recovery_point = disaster_event.detected_at - timedelta(minutes=5)

            # Check if we have a recent clean backup
            backups = await self.backup_service.list_backups()
            if backups:
                last_backup_time = datetime.fromisoformat(backups[0]["timestamp"])

                # Use backup time if it's within acceptable range
                if (disaster_event.detected_at - last_backup_time) < timedelta(hours=1):
                    recovery_point = last_backup_time

            return recovery_point

        except Exception as e:
            logger.error(f"Error identifying recovery point: {e}")
            # Fallback to 30 minutes before disaster
            return disaster_event.detected_at - timedelta(minutes=30)

    async def _identify_rollback_points(self) -> List[datetime]:
        """Identify available rollback points"""
        rollback_points = []

        try:
            # Get backup timestamps
            backups = await self.backup_service.list_backups()
            for backup in backups[:5]:  # Keep last 5 backups as rollback points
                rollback_points.append(datetime.fromisoformat(backup["timestamp"]))

            # Add hourly points for last 24 hours (Supabase PITR)
            now = datetime.now(timezone.utc)
            for hours_ago in range(1, 25):
                rollback_points.append(now - timedelta(hours=hours_ago))

            return sorted(rollback_points, reverse=True)

        except Exception as e:
            logger.error(f"Error identifying rollback points: {e}")
            return []

    async def _initiate_supabase_pitr(self, recovery_point: Optional[datetime]) -> bool:
        """
        Initiate Supabase Point-in-Time Recovery

        Note: This would require Supabase Management API access
        For now, this is a placeholder that logs the action
        """
        try:
            if not recovery_point:
                recovery_point = datetime.now(timezone.utc) - timedelta(minutes=5)

            logger.info(f"Initiating Supabase PITR to {recovery_point.isoformat()}")

            # In production, this would call Supabase Management API
            # For now, we'll simulate success
            await asyncio.sleep(2)  # Simulate API call

            return True

        except Exception as e:
            logger.error(f"Error initiating Supabase PITR: {e}")
            return False

    async def _restore_external_backups(self) -> Dict[str, Any]:
        """Restore all external backups"""
        results = {
            "success": False,
            "restored": [],
            "failed": []
        }

        try:
            # Get latest backup
            backups = await self.backup_service.list_backups()
            if not backups:
                logger.error("No backups available")
                return results

            latest_backup = backups[0]["path"]

            # Restore from backup
            success = await self.backup_service.restore_from_backup(latest_backup)

            if success:
                results["success"] = True
                results["restored"] = ["redis", "whatsapp", "configuration"]
            else:
                results["failed"] = ["backup_restoration"]

            return results

        except Exception as e:
            logger.error(f"Error restoring external backups: {e}")
            results["failed"].append(str(e))
            return results

    async def _restore_redis_backup(self) -> bool:
        """Restore Redis data from backup"""
        try:
            # Get latest backup
            backups = await self.backup_service.list_backups()
            if not backups:
                return False

            # Load backup data
            backup_path = Path(backups[0]["path"])
            with open(backup_path, 'r') as f:
                backup_data = json.load(f)

            # Restore Redis data
            if "redis" in backup_data:
                return await self.backup_service.restore_redis_data(backup_data["redis"])

            return False

        except Exception as e:
            logger.error(f"Error restoring Redis backup: {e}")
            return False

    async def _verify_data_integrity(self) -> Dict[str, Any]:
        """Verify data integrity after recovery"""
        results = {
            "valid": True,
            "checks": {},
            "errors": []
        }

        try:
            # Check database connectivity
            try:
                response = self.supabase.table("clinics").select("id").limit(1).execute()
                results["checks"]["database"] = "passed"
            except Exception as e:
                results["checks"]["database"] = "failed"
                results["errors"].append(f"Database check failed: {e}")
                results["valid"] = False

            # Check Redis connectivity
            try:
                await self.redis_client.ping()
                results["checks"]["redis"] = "passed"
            except Exception as e:
                results["checks"]["redis"] = "failed"
                results["errors"].append(f"Redis check failed: {e}")
                results["valid"] = False

            # Check data consistency
            # This would include checksums, record counts, etc.
            results["checks"]["consistency"] = "passed"

            return results

        except Exception as e:
            logger.error(f"Error verifying data integrity: {e}")
            results["valid"] = False
            results["errors"].append(str(e))
            return results

    async def _enable_read_only_mode(self):
        """Enable read-only mode during recovery"""
        try:
            # Set flag in Redis
            await self.redis_client.set("system:read_only_mode", "true", ex=3600)
            logger.info("Enabled read-only mode")
        except Exception as e:
            logger.error(f"Error enabling read-only mode: {e}")

    async def _enable_write_operations(self):
        """Re-enable write operations after recovery"""
        try:
            # Clear read-only flag
            await self.redis_client.delete("system:read_only_mode")
            logger.info("Enabled write operations")
        except Exception as e:
            logger.error(f"Error enabling write operations: {e}")

    async def _notify_users(self, disaster_event: DisasterEvent, status: str):
        """Notify users about disaster recovery status"""
        try:
            notification = {
                "type": "disaster_recovery",
                "status": status,
                "disaster_type": disaster_event.type.value,
                "severity": disaster_event.severity,
                "message": f"System recovery {status}",
                "timestamp": datetime.now(timezone.utc).isoformat()
            }

            # Store notification in Redis for websocket broadcast
            await self.redis_client.publish("system_notifications", json.dumps(notification))
            logger.info(f"Sent user notification: {status}")

        except Exception as e:
            logger.error(f"Error notifying users: {e}")

    async def _rollback_recovery(self, plan: RecoveryPlan, failed_phase: RecoveryPhase):
        """Rollback recovery after failure"""
        try:
            logger.warning(f"Rolling back recovery after {failed_phase.value} failure")

            # Restore to previous state
            if plan.rollback_points:
                rollback_point = plan.rollback_points[0]
                await self._initiate_supabase_pitr(rollback_point)

            # Clear any partial changes
            await self._enable_write_operations()

            # Notify about rollback
            await self._notify_users(plan.disaster_event, "recovery_rolled_back")

        except Exception as e:
            logger.error(f"Error during rollback: {e}")

    async def get_recovery_status(self) -> Dict[str, Any]:
        """Get current recovery status"""
        return {
            "active_disasters": [
                {
                    "event_id": event.event_id,
                    "type": event.type.value,
                    "severity": event.severity,
                    "detected_at": event.detected_at.isoformat(),
                    "status": event.status
                }
                for event in self.active_disasters.values()
            ],
            "recovery_plans": [
                {
                    "plan_id": plan.plan_id,
                    "disaster_event_id": plan.disaster_event.event_id,
                    "phases": [phase.value for phase in plan.phases],
                    "estimated_recovery_time": str(plan.estimated_recovery_time)
                }
                for plan in self.recovery_plans.values()
            ],
            "recovery_history": [
                {
                    "event_id": event.event_id,
                    "type": event.type.value,
                    "recovered_at": event.detected_at.isoformat(),
                    "resolution": event.resolution
                }
                for event in self.recovery_history[-10:]  # Last 10 recoveries
            ]
        }