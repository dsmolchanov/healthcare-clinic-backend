#!/usr/bin/env python3
"""
Test script for Phase 1 Disaster Recovery Implementation
Tests backup, offline operations, and graceful degradation
"""

import asyncio
import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
import redis.asyncio as redis
from supabase import create_client, Client
from unittest.mock import MagicMock, AsyncMock
import logging

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent))

from app.services.external_backup_service import ExternalBackupService
from app.services.offline_cache_manager import OfflineCacheManager
from app.services.graceful_degradation_handler import GracefulDegradationHandler, ServiceStatus
from app.services.disaster_recovery_orchestrator import (
    DisasterRecoveryOrchestrator,
    DisasterType,
    DisasterEvent
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class TestDisasterRecovery:
    """Test suite for disaster recovery implementation"""

    def __init__(self):
        self.redis_client = None
        self.supabase = None
        self.backup_service = None
        self.cache_manager = None
        self.degradation_handler = None
        self.recovery_orchestrator = None
        self.test_results = []

    async def setup(self):
        """Initialize test environment"""
        try:
            # Initialize Redis (use mock if not available)
            try:
                self.redis_client = redis.from_url(
                    os.getenv("REDIS_URL", "redis://localhost:6379/0")
                )
                await self.redis_client.ping()
                logger.info("✅ Connected to Redis")
            except:
                logger.warning("⚠️ Redis not available, using mock")
                self.redis_client = AsyncMock()
                self.redis_client.get = AsyncMock(return_value=None)
                self.redis_client.set = AsyncMock(return_value=True)
                self.redis_client.setex = AsyncMock(return_value=True)
                self.redis_client.delete = AsyncMock(return_value=1)
                self.redis_client.scan_iter = AsyncMock(return_value=iter([]))
                self.redis_client.pipeline = MagicMock()
                self.redis_client.pipeline().execute = AsyncMock(return_value=[])
                self.redis_client.ping = AsyncMock(return_value=True)
                self.redis_client.publish = AsyncMock(return_value=1)

            # Initialize Supabase (use mock if not configured)
            supabase_url = os.getenv("SUPABASE_URL")
            supabase_key = os.getenv("SUPABASE_SERVICE_KEY")

            if supabase_url and supabase_key:
                self.supabase = create_client(supabase_url, supabase_key)
                logger.info("✅ Connected to Supabase")
            else:
                logger.warning("⚠️ Supabase not configured, using mock")
                self.supabase = MagicMock()
                self.supabase.table = MagicMock(return_value=MagicMock())

            # Initialize services
            self.backup_service = ExternalBackupService(self.redis_client, "/tmp/test_backups")
            self.cache_manager = OfflineCacheManager(self.redis_client, self.supabase)
            self.degradation_handler = GracefulDegradationHandler(self.redis_client, self.supabase)
            self.recovery_orchestrator = DisasterRecoveryOrchestrator(
                self.redis_client,
                self.supabase,
                self.backup_service,
                self.cache_manager,
                self.degradation_handler
            )

            logger.info("✅ All services initialized")
            return True

        except Exception as e:
            logger.error(f"❌ Setup failed: {e}")
            return False

    async def test_external_backup_service(self):
        """Test external data backup functionality"""
        test_name = "External Backup Service"
        try:
            logger.info(f"\n🧪 Testing {test_name}...")

            # Test Redis backup
            logger.info("  Testing Redis data backup...")
            redis_backup = await self.backup_service.backup_redis_data()
            assert redis_backup is not None, "Redis backup should return data"
            assert "timestamp" in redis_backup, "Backup should have timestamp"
            logger.info("  ✅ Redis backup successful")

            # Test WhatsApp auth backup
            logger.info("  Testing WhatsApp auth backup...")
            whatsapp_backup = await self.backup_service.backup_whatsapp_auth()
            assert whatsapp_backup is not None, "WhatsApp backup should return data"
            assert "instances" in whatsapp_backup, "Should have instances field"
            logger.info("  ✅ WhatsApp auth backup successful")

            # Test configuration backup
            logger.info("  Testing configuration backup...")
            config_backup = await self.backup_service.backup_configuration()
            assert config_backup is not None, "Config backup should return data"
            assert "files" in config_backup, "Should have files field"
            logger.info("  ✅ Configuration backup successful")

            # Test complete backup
            logger.info("  Testing complete backup creation...")
            backup_path = await self.backup_service.create_complete_backup()
            assert backup_path, "Should return backup path"
            assert Path(backup_path).exists(), "Backup file should exist"
            logger.info(f"  ✅ Complete backup created: {backup_path}")

            # Test backup listing
            logger.info("  Testing backup listing...")
            backups = await self.backup_service.list_backups()
            assert len(backups) > 0, "Should have at least one backup"
            logger.info(f"  ✅ Found {len(backups)} backup(s)")

            # Test restore (with mock data)
            logger.info("  Testing backup restoration...")
            restore_success = await self.backup_service.restore_from_backup(backup_path)
            assert restore_success, "Restore should succeed"
            logger.info("  ✅ Backup restoration successful")

            self.test_results.append({"test": test_name, "status": "PASSED"})
            logger.info(f"✅ {test_name} test completed successfully")
            return True

        except Exception as e:
            self.test_results.append({"test": test_name, "status": "FAILED", "error": str(e)})
            logger.error(f"❌ {test_name} test failed: {e}")
            return False

    async def test_offline_cache_manager(self):
        """Test offline cache functionality"""
        test_name = "Offline Cache Manager"
        try:
            logger.info(f"\n🧪 Testing {test_name}...")

            # Mock Supabase responses for testing
            if isinstance(self.supabase, MagicMock):
                mock_table = MagicMock()
                mock_table.select.return_value.execute.return_value.data = [
                    {"id": "test-1", "appointment_date": datetime.now(timezone.utc).isoformat()}
                ]
                self.supabase.table.return_value = mock_table

            # Initialize cache
            logger.info("  Testing cache initialization...")
            await self.cache_manager.initialize()
            logger.info("  ✅ Cache initialized")

            # Test caching appointments
            logger.info("  Testing appointment caching...")
            apt_stats = await self.cache_manager.cache_appointments()
            assert apt_stats is not None, "Should return cache statistics"
            logger.info(f"  ✅ Cached appointments: {apt_stats}")

            # Test cache retrieval
            logger.info("  Testing cache retrieval...")
            cached_data = await self.cache_manager.get_cached_data("appointments", "summary")
            logger.info(f"  ✅ Retrieved cached data: {cached_data is not None}")

            # Test cache statistics
            logger.info("  Testing cache statistics...")
            stats = await self.cache_manager.get_cache_statistics()
            assert "total_keys" in stats, "Should have statistics"
            logger.info(f"  ✅ Cache stats: {stats}")

            # Test cache invalidation
            logger.info("  Testing cache invalidation...")
            invalidated = await self.cache_manager.invalidate_cache("test_category")
            logger.info(f"  ✅ Invalidated {invalidated} entries")

            self.test_results.append({"test": test_name, "status": "PASSED"})
            logger.info(f"✅ {test_name} test completed successfully")
            return True

        except Exception as e:
            self.test_results.append({"test": test_name, "status": "FAILED", "error": str(e)})
            logger.error(f"❌ {test_name} test failed: {e}")
            return False

    async def test_graceful_degradation(self):
        """Test graceful degradation handler"""
        test_name = "Graceful Degradation Handler"
        try:
            logger.info(f"\n🧪 Testing {test_name}...")

            # Start monitoring
            logger.info("  Testing service monitoring...")
            await self.degradation_handler.start_monitoring()
            logger.info("  ✅ Service monitoring started")

            # Simulate service degradation
            logger.info("  Testing service health check...")

            async def test_health_check():
                return (True, None)

            health = await self.degradation_handler.check_service(
                "test_service",
                test_health_check
            )
            assert health.status == ServiceStatus.HEALTHY, "Service should be healthy"
            logger.info(f"  ✅ Service health: {health.status.value}")

            # Test feature availability
            logger.info("  Testing feature availability...")
            available = await self.degradation_handler.is_feature_available("test_feature")
            assert available, "Feature should be available when healthy"
            logger.info("  ✅ Feature availability check working")

            # Test cache TTL multiplier
            logger.info("  Testing cache TTL multiplier...")
            multiplier = await self.degradation_handler.get_cache_ttl_multiplier("test_service")
            assert multiplier == 1.0, "Should be 1.0 for healthy service"
            logger.info(f"  ✅ Cache TTL multiplier: {multiplier}")

            # Test service status retrieval
            logger.info("  Testing service status retrieval...")
            status = await self.degradation_handler.get_service_status()
            assert "degradation_level" in status, "Should have degradation level"
            logger.info(f"  ✅ System degradation level: {status['degradation_level']}")

            # Stop monitoring
            await self.degradation_handler.stop_monitoring()
            logger.info("  ✅ Service monitoring stopped")

            self.test_results.append({"test": test_name, "status": "PASSED"})
            logger.info(f"✅ {test_name} test completed successfully")
            return True

        except Exception as e:
            self.test_results.append({"test": test_name, "status": "FAILED", "error": str(e)})
            logger.error(f"❌ {test_name} test failed: {e}")
            return False

    async def test_disaster_recovery_orchestrator(self):
        """Test disaster recovery orchestration"""
        test_name = "Disaster Recovery Orchestrator"
        try:
            logger.info(f"\n🧪 Testing {test_name}...")

            # Test disaster detection
            logger.info("  Testing disaster detection...")
            disaster = await self.recovery_orchestrator.detect_disaster()
            if disaster:
                logger.info(f"  ⚠️ Disaster detected: {disaster.type.value}")
            else:
                logger.info("  ✅ No disaster detected (system healthy)")

            # Simulate a disaster event for testing
            logger.info("  Testing recovery plan creation...")
            test_disaster = DisasterEvent(
                event_id="test_disaster_001",
                type=DisasterType.SERVICE_DEGRADATION,
                severity=2,
                detected_at=datetime.now(timezone.utc),
                affected_services=["test_service"],
                data_loss_risk=False
            )

            recovery_plan = await self.recovery_orchestrator.create_recovery_plan(test_disaster)
            assert recovery_plan is not None, "Should create recovery plan"
            assert len(recovery_plan.phases) > 0, "Plan should have phases"
            logger.info(f"  ✅ Recovery plan created with {len(recovery_plan.phases)} phases")

            # Test recovery status
            logger.info("  Testing recovery status...")
            recovery_status = await self.recovery_orchestrator.get_recovery_status()
            assert "active_disasters" in recovery_status, "Should have status structure"
            logger.info(f"  ✅ Recovery status retrieved: {len(recovery_status['active_disasters'])} active disasters")

            self.test_results.append({"test": test_name, "status": "PASSED"})
            logger.info(f"✅ {test_name} test completed successfully")
            return True

        except Exception as e:
            self.test_results.append({"test": test_name, "status": "FAILED", "error": str(e)})
            logger.error(f"❌ {test_name} test failed: {e}")
            return False

    async def test_integration(self):
        """Test integration between all components"""
        test_name = "Integration Test"
        try:
            logger.info(f"\n🧪 Testing {test_name}...")

            # Simulate a complete disaster recovery scenario
            logger.info("  Simulating disaster scenario...")

            # 1. Create backup
            logger.info("  Step 1: Creating backup...")
            backup_path = await self.backup_service.create_complete_backup()
            assert backup_path, "Backup should be created"
            logger.info("  ✅ Backup created")

            # 2. Initialize cache
            logger.info("  Step 2: Initializing cache...")
            await self.cache_manager.initialize()
            logger.info("  ✅ Cache initialized")

            # 3. Start degradation monitoring
            logger.info("  Step 3: Starting degradation monitoring...")
            await self.degradation_handler.start_monitoring()
            logger.info("  ✅ Monitoring started")

            # 4. Simulate service failure
            logger.info("  Step 4: Simulating service failure...")
            await self.degradation_handler.report_operation_result("critical_service", False)
            logger.info("  ✅ Service failure reported")

            # 5. Check system state
            logger.info("  Step 5: Checking system state...")
            system_status = await self.degradation_handler.get_service_status()
            logger.info(f"  ✅ System state: {system_status['degradation_level']}")

            # 6. Create and execute recovery plan
            logger.info("  Step 6: Creating recovery plan...")
            test_disaster = DisasterEvent(
                event_id="integration_test",
                type=DisasterType.SERVICE_DEGRADATION,
                severity=2,
                detected_at=datetime.now(timezone.utc),
                affected_services=["critical_service"],
                data_loss_risk=False
            )
            recovery_plan = await self.recovery_orchestrator.create_recovery_plan(test_disaster)
            logger.info(f"  ✅ Recovery plan created: {recovery_plan.plan_id}")

            # Clean up
            await self.degradation_handler.stop_monitoring()

            self.test_results.append({"test": test_name, "status": "PASSED"})
            logger.info(f"✅ {test_name} completed successfully")
            return True

        except Exception as e:
            self.test_results.append({"test": test_name, "status": "FAILED", "error": str(e)})
            logger.error(f"❌ {test_name} failed: {e}")
            return False

    async def cleanup(self):
        """Clean up test resources"""
        try:
            # Clean up test backups
            test_backup_dir = Path("/tmp/test_backups")
            if test_backup_dir.exists():
                for file in test_backup_dir.glob("*"):
                    file.unlink()
                logger.info("✅ Cleaned up test backups")

            # Close connections
            if self.redis_client and not isinstance(self.redis_client, AsyncMock):
                await self.redis_client.close()

            logger.info("✅ Cleanup completed")

        except Exception as e:
            logger.error(f"❌ Cleanup failed: {e}")

    async def run_all_tests(self):
        """Run all tests and generate report"""
        logger.info("=" * 60)
        logger.info("🚀 Starting Phase 1 Disaster Recovery Tests")
        logger.info("=" * 60)

        # Setup test environment
        if not await self.setup():
            logger.error("❌ Setup failed, cannot continue tests")
            return

        # Run tests
        test_methods = [
            self.test_external_backup_service,
            self.test_offline_cache_manager,
            self.test_graceful_degradation,
            self.test_disaster_recovery_orchestrator,
            self.test_integration
        ]

        for test_method in test_methods:
            try:
                await test_method()
            except Exception as e:
                logger.error(f"❌ Unexpected error in {test_method.__name__}: {e}")

        # Generate report
        logger.info("\n" + "=" * 60)
        logger.info("📊 Test Results Summary")
        logger.info("=" * 60)

        passed = sum(1 for r in self.test_results if r["status"] == "PASSED")
        failed = sum(1 for r in self.test_results if r["status"] == "FAILED")

        for result in self.test_results:
            status_icon = "✅" if result["status"] == "PASSED" else "❌"
            logger.info(f"{status_icon} {result['test']}: {result['status']}")
            if "error" in result:
                logger.info(f"   Error: {result['error']}")

        logger.info("-" * 60)
        logger.info(f"Total: {len(self.test_results)} tests")
        logger.info(f"Passed: {passed} ({passed/len(self.test_results)*100:.1f}%)")
        logger.info(f"Failed: {failed} ({failed/len(self.test_results)*100:.1f}%)")
        logger.info("=" * 60)

        # Cleanup
        await self.cleanup()

        return failed == 0


async def main():
    """Main test runner"""
    tester = TestDisasterRecovery()
    success = await tester.run_all_tests()
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    asyncio.run(main())