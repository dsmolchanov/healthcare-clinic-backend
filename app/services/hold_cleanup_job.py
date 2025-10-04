"""
Appointment Hold Cleanup Job

Background job to automatically clean up expired appointment holds
and release the corresponding slots in external calendars.
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Any

from app.database import create_supabase_client
from app.services.external_calendar_service import ExternalCalendarService
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

logger = logging.getLogger(__name__)


class HoldCleanupJob:
    """
    Background job to clean up expired appointment holds.
    Runs periodically to find and release expired holds.
    """

    def __init__(self, run_interval_minutes: int = 5):
        """
        Initialize the hold cleanup job.

        Args:
            run_interval_minutes: How often to run the cleanup (default 5 minutes)
        """
        self.supabase = create_supabase_client()
        self.scheduler = AsyncIOScheduler()
        self.run_interval_minutes = run_interval_minutes
        self.is_running = False

        logger.info(f"Initialized HoldCleanupJob with {run_interval_minutes} minute interval")

    async def cleanup_expired_holds(self) -> Dict[str, Any]:
        """
        Find and clean up all expired appointment holds.

        Returns:
            Dictionary with cleanup statistics
        """
        try:
            logger.info("Starting hold cleanup job")
            current_time = datetime.now()
            stats = {
                "expired_holds": 0,
                "released_holds": 0,
                "errors": 0,
                "start_time": current_time.isoformat()
            }

            # Find all expired holds
            expired_holds = await self._find_expired_holds(current_time)
            stats["expired_holds"] = len(expired_holds)

            if expired_holds:
                logger.info(f"Found {len(expired_holds)} expired holds to clean up")

                # Release each expired hold
                for hold in expired_holds:
                    try:
                        success = await self._release_expired_hold(hold)
                        if success:
                            stats["released_holds"] += 1
                        else:
                            stats["errors"] += 1
                    except Exception as e:
                        logger.error(f"Error releasing hold {hold['id']}: {str(e)}")
                        stats["errors"] += 1

            # Clean up old completed/cancelled holds (older than 7 days)
            await self._cleanup_old_holds()

            stats["end_time"] = datetime.now().isoformat()
            stats["duration_seconds"] = (datetime.now() - current_time).total_seconds()

            logger.info(f"Hold cleanup completed. Released {stats['released_holds']} holds, "
                       f"{stats['errors']} errors")

            return stats

        except Exception as e:
            logger.error(f"Error in hold cleanup job: {str(e)}")
            return {
                "error": str(e),
                "expired_holds": 0,
                "released_holds": 0,
                "errors": 1
            }

    async def _find_expired_holds(self, current_time: datetime) -> List[Dict[str, Any]]:
        """
        Find all active holds that have expired.

        Args:
            current_time: Current datetime for comparison

        Returns:
            List of expired hold records
        """
        try:
            # Query for active holds where expire_at < current_time
            result = self.supabase.table('healthcare.appointment_holds').select('*').eq(
                'status', 'active'
            ).lt('expire_at', current_time.isoformat()).execute()

            return result.data if result.data else []

        except Exception as e:
            logger.error(f"Error finding expired holds: {str(e)}")
            return []

    async def _release_expired_hold(self, hold: Dict[str, Any]) -> bool:
        """
        Release an expired hold and update its status.

        Args:
            hold: Hold record to release

        Returns:
            True if successfully released, False otherwise
        """
        try:
            # Update hold status to expired
            update_result = self.supabase.table('healthcare.appointment_holds').update({
                "status": "expired",
                "released_at": datetime.now().isoformat(),
                "release_reason": "Automatic expiration"
            }).eq('id', hold['id']).execute()

            if update_result.data:
                logger.info(f"Released expired hold {hold['id']}")

                # Release in external calendars if applicable
                if hold.get('doctor_id') and hold.get('clinic_id'):
                    try:
                        calendar_service = ExternalCalendarService(
                            hold['clinic_id'],
                            self.supabase
                        )
                        await calendar_service.release_hold(
                            hold_id=hold['id'],
                            doctor_id=hold['doctor_id']
                        )
                    except Exception as e:
                        logger.warning(f"Failed to release hold in external calendar: {str(e)}")

                # Check if there's an associated appointment that needs cleanup
                if hold.get('appointment_id'):
                    await self._cleanup_orphaned_appointment(hold['appointment_id'])

                return True
            else:
                logger.warning(f"Failed to update hold status for {hold['id']}")
                return False

        except Exception as e:
            logger.error(f"Error releasing hold {hold['id']}: {str(e)}")
            return False

    async def _cleanup_orphaned_appointment(self, appointment_id: str):
        """
        Clean up an appointment that was associated with an expired hold.

        Args:
            appointment_id: ID of the potentially orphaned appointment
        """
        try:
            # Check if appointment is still in pending/unconfirmed state
            result = self.supabase.table('healthcare.appointments').select('status').eq(
                'id', appointment_id
            ).execute()

            if result.data and result.data[0]['status'] == 'pending':
                # Cancel the pending appointment
                self.supabase.table('healthcare.appointments').update({
                    'status': 'cancelled',
                    'cancellation_reason': 'Hold expired without confirmation',
                    'cancelled_at': datetime.now().isoformat()
                }).eq('id', appointment_id).execute()

                logger.info(f"Cancelled orphaned appointment {appointment_id}")

        except Exception as e:
            logger.warning(f"Error cleaning up orphaned appointment: {str(e)}")

    async def _cleanup_old_holds(self, days_to_keep: int = 7):
        """
        Clean up old holds that are no longer needed.

        Args:
            days_to_keep: Number of days to keep completed/cancelled holds
        """
        try:
            cutoff_date = datetime.now() - timedelta(days=days_to_keep)

            # Delete old non-active holds
            result = self.supabase.table('healthcare.appointment_holds').delete().in_(
                'status', ['confirmed', 'released', 'expired', 'cancelled']
            ).lt('created_at', cutoff_date.isoformat()).execute()

            if result.data:
                logger.info(f"Cleaned up {len(result.data)} old holds")

        except Exception as e:
            logger.warning(f"Error cleaning up old holds: {str(e)}")

    def start(self):
        """
        Start the scheduled cleanup job.
        """
        if not self.is_running:
            # Schedule the cleanup job
            self.scheduler.add_job(
                self.cleanup_expired_holds,
                trigger=IntervalTrigger(minutes=self.run_interval_minutes),
                id='hold_cleanup_job',
                name='Appointment Hold Cleanup',
                misfire_grace_time=60,  # Allow 60 seconds grace period
                coalesce=True,  # Combine missed runs
                max_instances=1  # Only one instance running at a time
            )

            # Also run cleanup on startup
            self.scheduler.add_job(
                self.cleanup_expired_holds,
                trigger='date',
                run_date=datetime.now() + timedelta(seconds=10),
                id='hold_cleanup_startup',
                name='Appointment Hold Cleanup (Startup)'
            )

            self.scheduler.start()
            self.is_running = True
            logger.info(f"Hold cleanup job started (runs every {self.run_interval_minutes} minutes)")

    def stop(self):
        """
        Stop the scheduled cleanup job.
        """
        if self.is_running:
            self.scheduler.shutdown(wait=False)
            self.is_running = False
            logger.info("Hold cleanup job stopped")

    async def run_once(self) -> Dict[str, Any]:
        """
        Run the cleanup job once (for testing or manual execution).

        Returns:
            Cleanup statistics
        """
        return await self.cleanup_expired_holds()


class HoldMonitor:
    """
    Monitor for appointment holds to track metrics and alert on issues.
    """

    def __init__(self):
        """Initialize the hold monitor."""
        self.supabase = create_supabase_client()

    async def get_hold_statistics(self) -> Dict[str, Any]:
        """
        Get current statistics about appointment holds.

        Returns:
            Dictionary with hold statistics
        """
        try:
            current_time = datetime.now()
            stats = {}

            # Count holds by status
            for status in ['active', 'confirmed', 'expired', 'released']:
                result = self.supabase.table('healthcare.appointment_holds').select(
                    'count', count='exact'
                ).eq('status', status).execute()
                stats[f"{status}_holds"] = result.count

            # Count holds expiring soon (next 5 minutes)
            expire_soon = current_time + timedelta(minutes=5)
            result = self.supabase.table('healthcare.appointment_holds').select(
                'count', count='exact'
            ).eq('status', 'active').lt('expire_at', expire_soon.isoformat()).execute()
            stats['expiring_soon'] = result.count

            # Get average hold duration
            result = self.supabase.rpc('get_avg_hold_duration').execute()
            if result.data:
                stats['avg_hold_duration_minutes'] = result.data

            # Get hold success rate (confirmed vs expired)
            confirmed = stats.get('confirmed_holds', 0)
            expired = stats.get('expired_holds', 0)
            total = confirmed + expired
            if total > 0:
                stats['success_rate'] = round(confirmed / total * 100, 2)
            else:
                stats['success_rate'] = 0

            stats['timestamp'] = current_time.isoformat()

            return stats

        except Exception as e:
            logger.error(f"Error getting hold statistics: {str(e)}")
            return {
                "error": str(e),
                "timestamp": datetime.now().isoformat()
            }

    async def check_hold_health(self) -> Dict[str, Any]:
        """
        Check the health of the hold system and identify potential issues.

        Returns:
            Dictionary with health check results
        """
        try:
            health = {
                "status": "healthy",
                "issues": [],
                "timestamp": datetime.now().isoformat()
            }

            stats = await self.get_hold_statistics()

            # Check for too many expired holds
            if stats.get('expired_holds', 0) > 50:
                health["issues"].append({
                    "severity": "warning",
                    "message": f"High number of expired holds: {stats['expired_holds']}"
                })

            # Check for holds expiring soon without confirmation
            if stats.get('expiring_soon', 0) > 10:
                health["issues"].append({
                    "severity": "info",
                    "message": f"{stats['expiring_soon']} holds expiring in next 5 minutes"
                })

            # Check success rate
            if stats.get('success_rate', 100) < 50:
                health["issues"].append({
                    "severity": "warning",
                    "message": f"Low hold success rate: {stats['success_rate']}%"
                })

            # Set overall status based on issues
            if any(issue['severity'] == 'error' for issue in health['issues']):
                health['status'] = 'error'
            elif any(issue['severity'] == 'warning' for issue in health['issues']):
                health['status'] = 'warning'

            health['statistics'] = stats

            return health

        except Exception as e:
            logger.error(f"Error checking hold health: {str(e)}")
            return {
                "status": "error",
                "error": str(e),
                "timestamp": datetime.now().isoformat()
            }


# Singleton instance for the cleanup job
_cleanup_job_instance = None


def get_cleanup_job(run_interval_minutes: int = 5) -> HoldCleanupJob:
    """
    Get or create the singleton cleanup job instance.

    Args:
        run_interval_minutes: Interval for running the cleanup

    Returns:
        HoldCleanupJob instance
    """
    global _cleanup_job_instance
    if _cleanup_job_instance is None:
        _cleanup_job_instance = HoldCleanupJob(run_interval_minutes)
    return _cleanup_job_instance


async def main():
    """
    Main function for running the cleanup job standalone.
    """
    logging.basicConfig(level=logging.INFO)

    job = get_cleanup_job(run_interval_minutes=5)
    monitor = HoldMonitor()

    # Run once immediately
    logger.info("Running initial cleanup...")
    stats = await job.run_once()
    logger.info(f"Initial cleanup stats: {stats}")

    # Check health
    health = await monitor.check_hold_health()
    logger.info(f"Hold system health: {health}")

    # Start scheduled job
    job.start()

    try:
        # Keep running
        while True:
            await asyncio.sleep(60)

            # Log statistics every minute
            stats = await monitor.get_hold_statistics()
            logger.info(f"Hold statistics: {stats}")

    except KeyboardInterrupt:
        logger.info("Shutting down hold cleanup job...")
        job.stop()


if __name__ == "__main__":
    asyncio.run(main())