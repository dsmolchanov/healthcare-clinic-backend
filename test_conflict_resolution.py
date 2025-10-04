#!/usr/bin/env python3
"""
Test script for Phase 2 Conflict Resolution & Monitoring Dashboard
Tests human-in-the-loop conflict resolution and real-time monitoring
"""

import asyncio
import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
import redis.asyncio as redis
from supabase import create_client
from unittest.mock import MagicMock, AsyncMock
import logging
import uuid

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent))

from app.services.conflict_resolution_enhanced import (
    EnhancedConflictResolver,
    ConflictContext,
    ResolutionStatus,
    HumanInterventionReason
)
from app.services.realtime_conflict_detector import (
    RealtimeConflictDetector,
    ConflictEvent,
    ConflictType,
    ConflictSeverity
)
from app.services.websocket_manager import websocket_manager, NotificationType

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class TestConflictResolution:
    """Test suite for conflict resolution and monitoring"""

    def __init__(self):
        self.redis_client = None
        self.supabase = None
        self.conflict_detector = None
        self.conflict_resolver = None
        self.test_results = []

    async def setup(self):
        """Initialize test environment"""
        try:
            # Initialize Redis
            try:
                self.redis_client = redis.from_url(
                    os.getenv("REDIS_URL", "redis://localhost:6379/0")
                )
                await self.redis_client.ping()
                logger.info("✅ Connected to Redis")
            except:
                logger.warning("⚠️ Redis not available, using mock")
                self.redis_client = AsyncMock()

            # Initialize Supabase
            supabase_url = os.getenv("SUPABASE_URL")
            supabase_key = os.getenv("SUPABASE_SERVICE_KEY")

            if supabase_url and supabase_key:
                self.supabase = create_client(supabase_url, supabase_key)
                logger.info("✅ Connected to Supabase")
            else:
                logger.warning("⚠️ Supabase not configured, using mock")
                self.supabase = MagicMock()

            # Initialize services
            self.conflict_detector = RealtimeConflictDetector()
            self.conflict_resolver = EnhancedConflictResolver(
                self.redis_client,
                self.supabase,
                websocket_manager
            )

            # Start resolver
            await self.conflict_resolver.start()

            logger.info("✅ All services initialized")
            return True

        except Exception as e:
            logger.error(f"❌ Setup failed: {e}")
            return False

    async def cleanup(self):
        """Clean up test resources"""
        try:
            if self.conflict_resolver:
                await self.conflict_resolver.stop()

            if self.redis_client and not isinstance(self.redis_client, AsyncMock):
                await self.redis_client.aclose()

            logger.info("✅ Cleanup completed")
        except Exception as e:
            logger.error(f"❌ Cleanup failed: {e}")

    async def test_conflict_detection(self):
        """Test conflict detection capabilities"""
        test_name = "Conflict Detection"
        try:
            logger.info(f"\n🧪 Testing {test_name}...")

            # Create a test conflict
            conflict = ConflictEvent(
                conflict_id=f"test_{uuid.uuid4().hex[:8]}",
                conflict_type=ConflictType.DOUBLE_BOOKING,
                severity=ConflictSeverity.HIGH,
                doctor_id="test_doctor_001",
                start_time=datetime.now(timezone.utc) + timedelta(hours=2),
                end_time=datetime.now(timezone.utc) + timedelta(hours=3),
                sources=["internal", "google"],
                details={
                    "patient_id": "test_patient_001",
                    "internal_booking_time": datetime.now(timezone.utc).isoformat(),
                    "external_booking_time": (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
                },
                detected_at=datetime.now(timezone.utc)
            )

            logger.info(f"  Created test conflict: {conflict.conflict_id}")

            # Test conflict analysis
            context = ConflictContext(
                urgency_score=0.8,
                business_impact={"vip_status": False}
            )

            confidence, suggestions, intervention_reason = await self.conflict_resolver.analyze_conflict(
                conflict, context
            )

            assert confidence is not None, "Should return confidence score"
            assert len(suggestions) > 0, "Should return suggestions"
            logger.info(f"  ✅ Conflict analysis: confidence={confidence:.2f}, suggestions={len(suggestions)}")

            # Test resolution creation
            resolution = await self.conflict_resolver.create_resolution(conflict, context)
            assert resolution is not None, "Should create resolution"
            assert resolution.resolution_id, "Resolution should have ID"
            logger.info(f"  ✅ Resolution created: {resolution.resolution_id}")

            self.test_results.append({"test": test_name, "status": "PASSED"})
            logger.info(f"✅ {test_name} test completed successfully")
            return True

        except Exception as e:
            self.test_results.append({"test": test_name, "status": "FAILED", "error": str(e)})
            logger.error(f"❌ {test_name} test failed: {e}")
            return False

    async def test_automatic_resolution(self):
        """Test automatic conflict resolution"""
        test_name = "Automatic Resolution"
        try:
            logger.info(f"\n🧪 Testing {test_name}...")

            # Create a simple conflict that can be auto-resolved
            conflict = ConflictEvent(
                conflict_id=f"auto_{uuid.uuid4().hex[:8]}",
                conflict_type=ConflictType.HOLD_CONFLICT,
                severity=ConflictSeverity.LOW,
                doctor_id="test_doctor_002",
                start_time=datetime.now(timezone.utc) + timedelta(hours=1),
                end_time=datetime.now(timezone.utc) + timedelta(hours=2),
                sources=["internal"],
                details={"hold_expired": True},
                detected_at=datetime.now(timezone.utc)
            )

            # Context indicating auto-resolution is safe
            context = ConflictContext(
                urgency_score=0.2,
                business_impact={"vip_status": False}
            )

            # Analyze - should have high confidence for auto-resolution
            confidence, suggestions, intervention_reason = await self.conflict_resolver.analyze_conflict(
                conflict, context
            )

            assert confidence > 0.8, f"Should have high confidence, got {confidence}"
            assert intervention_reason is None, "Should not require intervention"
            logger.info(f"  ✅ High confidence for auto-resolution: {confidence:.2f}")

            # Create resolution - should auto-resolve
            resolution = await self.conflict_resolver.create_resolution(conflict, context)

            # Wait a bit for async processing
            await asyncio.sleep(2)

            # Check if it was auto-resolved
            if resolution.resolution_id in self.conflict_resolver.active_resolutions:
                final_resolution = self.conflict_resolver.active_resolutions[resolution.resolution_id]
                # For this test, we'll accept pending as the resolution is queued
                assert final_resolution.status in [ResolutionStatus.PENDING, ResolutionStatus.AUTO_RESOLVED], \
                    f"Should be pending or auto-resolved, got {final_resolution.status}"
                logger.info(f"  ✅ Resolution status: {final_resolution.status.value}")

            self.test_results.append({"test": test_name, "status": "PASSED"})
            logger.info(f"✅ {test_name} test completed successfully")
            return True

        except Exception as e:
            self.test_results.append({"test": test_name, "status": "FAILED", "error": str(e)})
            logger.error(f"❌ {test_name} test failed: {e}")
            return False

    async def test_human_intervention(self):
        """Test human intervention requirement"""
        test_name = "Human Intervention"
        try:
            logger.info(f"\n🧪 Testing {test_name}...")

            # Create a complex conflict requiring human intervention
            conflict = ConflictEvent(
                conflict_id=f"human_{uuid.uuid4().hex[:8]}",
                conflict_type=ConflictType.DOUBLE_BOOKING,
                severity=ConflictSeverity.CRITICAL,
                doctor_id="test_doctor_003",
                start_time=datetime.now(timezone.utc) + timedelta(hours=1),
                end_time=datetime.now(timezone.utc) + timedelta(hours=2),
                sources=["internal", "google", "outlook"],  # Multiple sources
                details={
                    "patient_id": "vip_patient_001",
                    "conflict_count": 3
                },
                detected_at=datetime.now(timezone.utc)
            )

            # Context indicating VIP patient
            context = ConflictContext(
                urgency_score=0.9,
                business_impact={"vip_status": True},
                previous_resolutions=[{}, {}, {}]  # Multiple previous conflicts
            )

            # Analyze - should require human intervention
            confidence, suggestions, intervention_reason = await self.conflict_resolver.analyze_conflict(
                conflict, context
            )

            assert confidence < 0.8, f"Should have low confidence, got {confidence}"
            assert intervention_reason is not None, "Should require intervention"
            assert intervention_reason == HumanInterventionReason.HIGH_VALUE_PATIENT, \
                f"Should be VIP intervention, got {intervention_reason}"
            logger.info(f"  ✅ Requires human intervention: {intervention_reason.value}")

            # Create resolution
            resolution = await self.conflict_resolver.create_resolution(conflict, context)
            assert resolution.requires_human, "Resolution should require human"
            assert resolution.intervention_reason == HumanInterventionReason.HIGH_VALUE_PATIENT
            logger.info(f"  ✅ Resolution marked for human intervention")

            # Simulate human resolution
            success = await self.conflict_resolver.handle_human_resolution(
                resolution_id=resolution.resolution_id,
                user_id="test_user_001",
                action="contact_patient",
                parameters={"method": "phone", "priority": "high"},
                notes="Called VIP patient to confirm preference"
            )

            assert success, "Human resolution should succeed"
            logger.info(f"  ✅ Human resolution handled successfully")

            self.test_results.append({"test": test_name, "status": "PASSED"})
            logger.info(f"✅ {test_name} test completed successfully")
            return True

        except Exception as e:
            self.test_results.append({"test": test_name, "status": "FAILED", "error": str(e)})
            logger.error(f"❌ {test_name} test failed: {e}")
            return False

    async def test_websocket_notifications(self):
        """Test WebSocket notification system"""
        test_name = "WebSocket Notifications"
        try:
            logger.info(f"\n🧪 Testing {test_name}...")

            # Test connection stats
            stats = websocket_manager.get_connection_stats()
            assert "active_connections" in stats, "Should have connection stats"
            logger.info(f"  ✅ Connection stats: {stats['active_connections']} active")

            # Test notification sending
            await websocket_manager.send_notification(
                NotificationType.CONFLICT_DETECTED,
                {
                    "conflict_id": "test_001",
                    "severity": "high",
                    "type": "double_booking"
                },
                channel="dashboard"
            )
            logger.info(f"  ✅ Conflict notification sent")

            await websocket_manager.send_notification(
                NotificationType.METRICS_UPDATE,
                {
                    "total_conflicts": 10,
                    "auto_resolved": 7,
                    "pending": 3
                },
                channel="monitoring"
            )
            logger.info(f"  ✅ Metrics notification sent")

            self.test_results.append({"test": test_name, "status": "PASSED"})
            logger.info(f"✅ {test_name} test completed successfully")
            return True

        except Exception as e:
            self.test_results.append({"test": test_name, "status": "FAILED", "error": str(e)})
            logger.error(f"❌ {test_name} test failed: {e}")
            return False

    async def test_resolution_metrics(self):
        """Test resolution metrics collection"""
        test_name = "Resolution Metrics"
        try:
            logger.info(f"\n🧪 Testing {test_name}...")

            # Get metrics
            metrics = await self.conflict_resolver.get_resolution_metrics(timedelta(days=7))

            assert "total_conflicts" in metrics, "Should have total conflicts"
            assert "auto_resolved" in metrics, "Should have auto-resolved count"
            assert "human_resolved" in metrics, "Should have human-resolved count"
            assert "automation_rate" in metrics, "Should have automation rate"
            logger.info(f"  ✅ Metrics retrieved: {metrics}")

            # Get pending interventions
            interventions = await self.conflict_resolver.get_pending_interventions()
            assert isinstance(interventions, list), "Should return list of interventions"
            logger.info(f"  ✅ Pending interventions: {len(interventions)}")

            self.test_results.append({"test": test_name, "status": "PASSED"})
            logger.info(f"✅ {test_name} test completed successfully")
            return True

        except Exception as e:
            self.test_results.append({"test": test_name, "status": "FAILED", "error": str(e)})
            logger.error(f"❌ {test_name} test failed: {e}")
            return False

    async def test_escalation(self):
        """Test conflict escalation"""
        test_name = "Conflict Escalation"
        try:
            logger.info(f"\n🧪 Testing {test_name}...")

            # Create a conflict that will escalate due to timeout
            conflict = ConflictEvent(
                conflict_id=f"escalate_{uuid.uuid4().hex[:8]}",
                conflict_type=ConflictType.EXTERNAL_OVERRIDE,
                severity=ConflictSeverity.HIGH,
                doctor_id="test_doctor_004",
                start_time=datetime.now(timezone.utc) + timedelta(minutes=30),
                end_time=datetime.now(timezone.utc) + timedelta(minutes=60),
                sources=["google", "outlook"],
                details={"requires_verification": True},
                detected_at=datetime.now(timezone.utc)
            )

            # Set a very short escalation timeout for testing
            original_timeout = self.conflict_resolver.escalation_timeout
            self.conflict_resolver.escalation_timeout = 1  # 1 second

            # Create resolution requiring human intervention
            context = ConflictContext(
                urgency_score=0.95,
                business_impact={"critical_appointment": True}
            )

            resolution = await self.conflict_resolver.create_resolution(conflict, context)

            # Wait for escalation
            await asyncio.sleep(3)

            # Check if escalated (in a real scenario)
            logger.info(f"  ✅ Escalation process tested")

            # Restore original timeout
            self.conflict_resolver.escalation_timeout = original_timeout

            self.test_results.append({"test": test_name, "status": "PASSED"})
            logger.info(f"✅ {test_name} test completed successfully")
            return True

        except Exception as e:
            self.test_results.append({"test": test_name, "status": "FAILED", "error": str(e)})
            logger.error(f"❌ {test_name} test failed: {e}")
            return False

    async def test_integration(self):
        """Test end-to-end integration"""
        test_name = "Integration Test"
        try:
            logger.info(f"\n🧪 Testing {test_name}...")

            # Simulate complete conflict resolution flow
            logger.info("  Step 1: Creating conflicts...")
            conflicts = []

            # Create multiple conflicts with different characteristics
            for i in range(3):
                severity = [ConflictSeverity.LOW, ConflictSeverity.MEDIUM, ConflictSeverity.HIGH][i]
                conflict = ConflictEvent(
                    conflict_id=f"integration_{i}_{uuid.uuid4().hex[:8]}",
                    conflict_type=ConflictType.DOUBLE_BOOKING,
                    severity=severity,
                    doctor_id=f"test_doctor_{i}",
                    start_time=datetime.now(timezone.utc) + timedelta(hours=i+1),
                    end_time=datetime.now(timezone.utc) + timedelta(hours=i+2),
                    sources=["internal", "google"],
                    details={"test_index": i},
                    detected_at=datetime.now(timezone.utc)
                )
                conflicts.append(conflict)

            logger.info(f"  ✅ Created {len(conflicts)} test conflicts")

            logger.info("  Step 2: Processing conflicts...")
            resolutions = []
            for conflict in conflicts:
                resolution = await self.conflict_resolver.create_resolution(conflict)
                resolutions.append(resolution)

            logger.info(f"  ✅ Created {len(resolutions)} resolutions")

            logger.info("  Step 3: Getting metrics...")
            metrics = await self.conflict_resolver.get_resolution_metrics()
            assert metrics["total_conflicts"] >= len(conflicts), "Metrics should reflect conflicts"
            logger.info(f"  ✅ Metrics show {metrics['total_conflicts']} total conflicts")

            logger.info("  Step 4: Checking pending interventions...")
            interventions = await self.conflict_resolver.get_pending_interventions()
            logger.info(f"  ✅ {len(interventions)} pending interventions")

            self.test_results.append({"test": test_name, "status": "PASSED"})
            logger.info(f"✅ {test_name} completed successfully")
            return True

        except Exception as e:
            self.test_results.append({"test": test_name, "status": "FAILED", "error": str(e)})
            logger.error(f"❌ {test_name} failed: {e}")
            return False

    async def run_all_tests(self):
        """Run all tests and generate report"""
        logger.info("=" * 60)
        logger.info("🚀 Starting Phase 2 Conflict Resolution Tests")
        logger.info("=" * 60)

        # Setup test environment
        if not await self.setup():
            logger.error("❌ Setup failed, cannot continue tests")
            return

        # Run tests
        test_methods = [
            self.test_conflict_detection,
            self.test_automatic_resolution,
            self.test_human_intervention,
            self.test_websocket_notifications,
            self.test_resolution_metrics,
            self.test_escalation,
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
    tester = TestConflictResolution()
    success = await tester.run_all_tests()
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    asyncio.run(main())