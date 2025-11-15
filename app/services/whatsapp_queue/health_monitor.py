"""
WhatsApp Instance Health Monitor

This service monitors the health of WhatsApp instances and updates database status:
1. Checks connection state of all instances
2. Updates database with current status
3. Detects disconnections and logs disconnect reasons
4. Notifies workers of status changes

PREVENTION STRATEGY 4: Instance health monitoring
"""

import asyncio
import logging
from typing import List, Dict, Any
from datetime import datetime

logger = logging.getLogger(__name__)


class InstanceHealthMonitor:
    """Service to monitor WhatsApp instance health"""

    def __init__(self, supabase_client, evolution_client):
        """
        Initialize health monitor

        Args:
            supabase_client: Supabase client instance
            evolution_client: EvolutionAPIClient instance
        """
        self.supabase = supabase_client
        self.evolution = evolution_client

    async def check_instance_health(self, instance_name: str, expected_status: str) -> Dict[str, Any]:
        """
        Check health of a single instance

        Args:
            instance_name: Name of instance to check
            expected_status: Current status in database

        Returns:
            Health check result with status and any changes detected
        """
        try:
            # Get actual connection state from Evolution
            connection_state = await self.evolution.get_connection_status(instance_name)

            actual_state = connection_state.get("instance", {}).get("state", "unknown")

            # Map Evolution states to our database states
            if actual_state == "open":
                new_status = "connected"
            elif actual_state == "connecting":
                new_status = "connecting"
            elif actual_state == "close":
                new_status = "disconnected"
            else:
                new_status = "unknown"

            # Detect status change
            status_changed = (expected_status != new_status)

            result = {
                "instance_name": instance_name,
                "expected_status": expected_status,
                "actual_status": new_status,
                "actual_state": actual_state,
                "status_changed": status_changed,
                "healthy": (new_status == "connected")
            }

            if status_changed:
                logger.warning(f"Status change detected for {instance_name}: {expected_status} → {new_status}")
                result["disconnect_reason"] = connection_state.get("error", "unknown")
            else:
                logger.debug(f"Instance {instance_name} status OK: {new_status}")

            return result

        except Exception as e:
            logger.error(f"Error checking health for {instance_name}: {e}")
            return {
                "instance_name": instance_name,
                "expected_status": expected_status,
                "actual_status": "error",
                "status_changed": True,
                "healthy": False,
                "error": str(e)
            }

    async def update_instance_status(self, instance_id: str, new_status: str, disconnect_reason: str = None):
        """
        Update instance status in database

        Args:
            instance_id: Database ID of integration
            new_status: New status to set
            disconnect_reason: Optional reason for disconnection
        """
        try:
            update_data = {
                "status": new_status,
                "updated_at": datetime.utcnow().isoformat()
            }

            if disconnect_reason:
                update_data["disconnect_reason"] = disconnect_reason

            # Clear disconnect reason if reconnected
            if new_status == "connected":
                update_data["connected_at"] = datetime.utcnow().isoformat()
                update_data["disconnect_reason"] = None

            self.supabase.schema("healthcare").table("integrations").update(
                update_data
            ).eq("id", instance_id).execute()

            logger.info(f"Updated instance {instance_id} status to: {new_status}")

        except Exception as e:
            logger.error(f"Error updating instance {instance_id} status: {e}")

    async def monitor_all_instances(self) -> Dict[str, Any]:
        """
        Monitor health of all WhatsApp instances

        Returns:
            Summary of health check results
        """
        start_time = datetime.utcnow()
        logger.info("=" * 80)
        logger.info(f"Starting instance health check at {start_time.isoformat()}")
        logger.info("=" * 80)

        summary = {
            "started_at": start_time.isoformat(),
            "total_instances": 0,
            "healthy": 0,
            "unhealthy": 0,
            "status_changes": 0,
            "errors": 0,
            "instances": []
        }

        try:
            # Get all active WhatsApp instances from database
            result = self.supabase.schema("healthcare").table("integrations").select(
                "id, organization_id, config, status, enabled"
            ).eq("type", "whatsapp").eq("provider", "evolution").eq("enabled", True).execute()

            instances = result.data
            summary["total_instances"] = len(instances)

            logger.info(f"Checking {len(instances)} active WhatsApp instances...")

            # Check each instance
            for instance in instances:
                instance_id = instance["id"]
                instance_name = instance.get("config", {}).get("instance_name")
                current_status = instance.get("status", "unknown")

                if not instance_name:
                    logger.warning(f"Instance {instance_id} has no instance_name in config")
                    continue

                # Check health
                health_result = await self.check_instance_health(instance_name, current_status)

                # Update summary
                if health_result.get("healthy"):
                    summary["healthy"] += 1
                else:
                    summary["unhealthy"] += 1

                if health_result.get("status_changed"):
                    summary["status_changes"] += 1

                    # Update database
                    await self.update_instance_status(
                        instance_id,
                        health_result["actual_status"],
                        health_result.get("disconnect_reason")
                    )

                if health_result.get("error"):
                    summary["errors"] += 1

                # Add to detailed results
                summary["instances"].append({
                    "instance_name": instance_name,
                    "organization_id": instance["organization_id"],
                    "expected_status": health_result["expected_status"],
                    "actual_status": health_result["actual_status"],
                    "healthy": health_result["healthy"],
                    "changed": health_result["status_changed"]
                })

        except Exception as e:
            logger.error(f"Error during health monitoring: {e}")
            summary["error"] = str(e)

        end_time = datetime.utcnow()
        duration = (end_time - start_time).total_seconds()

        summary["completed_at"] = end_time.isoformat()
        summary["duration_seconds"] = duration

        logger.info("=" * 80)
        logger.info(f"Health check completed in {duration:.2f}s")
        logger.info(f"Total: {summary['total_instances']}, Healthy: {summary['healthy']}, Unhealthy: {summary['unhealthy']}")
        logger.info(f"Status changes: {summary['status_changes']}, Errors: {summary['errors']}")
        logger.info("=" * 80)

        return summary


async def run_health_monitor():
    """
    Standalone function to run health monitor

    This can be called from a cron job or background worker
    """
    from ...main import supabase
    from ...evolution_api import EvolutionAPIClient

    logger.info("Initializing health monitor...")

    async with EvolutionAPIClient() as evolution_client:
        monitor = InstanceHealthMonitor(supabase, evolution_client)
        return await monitor.monitor_all_instances()


if __name__ == "__main__":
    # Can be run directly for testing
    asyncio.run(run_health_monitor())
