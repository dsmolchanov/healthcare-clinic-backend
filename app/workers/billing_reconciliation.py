"""
Billing Reconciliation Worker
Nightly job to ensure Stripe subscription quantities match actual doctor counts.
Catches drift from failed syncs, race conditions, or edge cases.
"""

import asyncio
import logging
from datetime import datetime, timezone, timedelta

from app.database import get_core_client, get_healthcare_client
from app.services.billing_sync_service import execute_doctor_sync, get_organization_doctor_count

logger = logging.getLogger(__name__)


class BillingReconciliationWorker:
    """
    Runs nightly to reconcile all per-doctor billing subscriptions.
    """

    def __init__(self, run_interval_hours: int = 24):
        self._running = False
        self._task = None
        self.run_interval = timedelta(hours=run_interval_hours)
        self.last_run: datetime | None = None

    async def start(self):
        """Start the reconciliation worker."""
        if self._running:
            logger.warning("Billing reconciliation worker already running")
            return

        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info("Billing reconciliation worker started")

    async def stop(self):
        """Stop the reconciliation worker."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Billing reconciliation worker stopped")

    async def _run_loop(self):
        """Main loop - runs once per interval."""
        while self._running:
            try:
                # Calculate time until next run (aim for 3 AM UTC)
                now = datetime.now(timezone.utc)
                next_run = now.replace(hour=3, minute=0, second=0, microsecond=0)

                if next_run <= now:
                    next_run += timedelta(days=1)

                wait_seconds = (next_run - now).total_seconds()

                logger.info(f"Billing reconciliation next run at {next_run.isoformat()}")
                await asyncio.sleep(wait_seconds)

                if self._running:
                    await self.run_reconciliation()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in billing reconciliation loop: {e}")
                await asyncio.sleep(3600)  # Wait 1 hour on error

    async def run_reconciliation(self):
        """
        Run reconciliation for all per-doctor subscriptions.
        """
        logger.info("Starting billing reconciliation run...")
        self.last_run = datetime.now(timezone.utc)

        healthcare_client = get_healthcare_client()

        try:
            # Get all organization_subscriptions with per_doctor tier and active/trialing status
            subs_result = healthcare_client.table("organization_subscriptions").select(
                "organization_id, tier, status, doctor_count_cached, stripe_subscription_id"
            ).eq("tier", "per_doctor").in_(
                "status", ["active", "trialing", "trial", "past_due"]
            ).execute()

            if not subs_result.data:
                logger.info("No per-doctor subscriptions to reconcile")
                return {
                    "status": "success",
                    "reconciled": 0,
                    "updated": 0,
                    "errors": 0
                }

            reconciled = 0
            updated = 0
            errors = 0

            for sub in subs_result.data:
                org_id = sub["organization_id"]
                cached_count = sub.get("doctor_count_cached", 0)

                try:
                    # Get actual doctor count
                    actual_count = await get_organization_doctor_count(org_id)

                    reconciled += 1

                    # Only sync if there's a mismatch
                    if actual_count != cached_count:
                        logger.info(
                            f"Reconciliation mismatch for org {org_id}: "
                            f"cached={cached_count}, actual={actual_count}"
                        )

                        result = await execute_doctor_sync(org_id)

                        if result.get("status") == "success":
                            updated += 1
                            logger.info(f"Reconciled org {org_id}: {cached_count} -> {actual_count}")
                        elif result.get("status") in ("skipped", "no_change"):
                            pass  # Expected - already in sync or legacy tier
                        else:
                            errors += 1
                            logger.error(f"Reconciliation failed for org {org_id}: {result}")

                except Exception as e:
                    errors += 1
                    logger.error(f"Error reconciling org {org_id}: {e}")

                # Small delay between orgs to avoid rate limits
                await asyncio.sleep(0.5)

            logger.info(
                f"Billing reconciliation complete: "
                f"reconciled={reconciled}, updated={updated}, errors={errors}"
            )

            return {
                "status": "success",
                "reconciled": reconciled,
                "updated": updated,
                "errors": errors
            }

        except Exception as e:
            logger.error(f"Billing reconciliation failed: {e}")
            return {
                "status": "error",
                "message": str(e)
            }


# Global instance
_reconciliation_worker: BillingReconciliationWorker | None = None


async def start_reconciliation_worker():
    """Start the billing reconciliation worker (call from app startup)."""
    global _reconciliation_worker
    if _reconciliation_worker is None:
        _reconciliation_worker = BillingReconciliationWorker()
        await _reconciliation_worker.start()
        return True
    return False


async def stop_reconciliation_worker():
    """Stop the billing reconciliation worker (call from app shutdown)."""
    global _reconciliation_worker
    if _reconciliation_worker:
        await _reconciliation_worker.stop()
        _reconciliation_worker = None


async def run_reconciliation_now():
    """Manually trigger a reconciliation run (for debugging/admin)."""
    global _reconciliation_worker
    if _reconciliation_worker:
        return await _reconciliation_worker.run_reconciliation()
    else:
        # Create temporary worker
        worker = BillingReconciliationWorker()
        return await worker.run_reconciliation()
