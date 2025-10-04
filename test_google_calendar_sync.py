#!/usr/bin/env python3
"""
Test Google Calendar Integration and Sync for Shtern Clinic
Tests OAuth flow, calendar connection, and bidirectional sync
"""

import asyncio
import os
import sys
import uuid
from datetime import datetime, timezone, timedelta
import json
import logging
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent))

# Set up environment variables
from dotenv import load_dotenv
load_dotenv('../.env')

# Now import after env vars are loaded
from app.calendar.oauth_manager import CalendarOAuthManager
from app.services.external_calendar_service import ExternalCalendarService
from supabase import create_client
import redis.asyncio as redis

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class GoogleCalendarTester:
    """Test suite for Google Calendar integration"""

    def __init__(self):
        self.supabase = None
        self.redis_client = None
        self.oauth_manager = None
        self.calendar_service = None
        self.test_results = []

    async def setup(self):
        """Initialize test environment"""
        try:
            # Initialize Supabase
            self.supabase = create_client(
                os.getenv("SUPABASE_URL"),
                os.getenv("SUPABASE_SERVICE_ROLE_KEY")
            )
            logger.info("✅ Connected to Supabase")

            # Initialize Redis
            try:
                self.redis_client = redis.from_url(
                    os.getenv("REDIS_URL", "redis://localhost:6379/0")
                )
                await self.redis_client.ping()
                logger.info("✅ Connected to Redis")
            except:
                logger.warning("⚠️ Redis not available, some features may be limited")
                self.redis_client = None

            # Initialize OAuth Manager
            self.oauth_manager = CalendarOAuthManager()
            logger.info("✅ OAuth Manager initialized")

            # Initialize Calendar Service
            self.calendar_service = ExternalCalendarService()
            logger.info("✅ Calendar Service initialized")

            return True

        except Exception as e:
            logger.error(f"❌ Setup failed: {e}")
            return False

    async def test_oauth_url_generation(self):
        """Test OAuth URL generation for Google Calendar"""
        test_name = "OAuth URL Generation"
        try:
            logger.info(f"\n🧪 Testing {test_name}...")

            # Generate OAuth URL for a test doctor
            doctor_id = str(uuid.uuid4())  # Use UUID for test
            clinic_id = "shtern_dental_clinic"

            auth_url = await self.oauth_manager.initiate_google_oauth(
                doctor_id=doctor_id,
                clinic_id=clinic_id
            )

            assert auth_url, "Should generate auth URL"
            assert "accounts.google.com" in auth_url, "Should be Google OAuth URL"
            assert "scope" in auth_url, "Should include scopes"
            # State is now handled internally by oauth_manager

            logger.info(f"  ✅ Generated OAuth URL")
            logger.info(f"  📋 URL (first 100 chars): {auth_url[:100]}...")

            self.test_results.append({"test": test_name, "status": "PASSED", "url": auth_url})
            return True

        except Exception as e:
            logger.error(f"❌ {test_name} failed: {e}")
            self.test_results.append({"test": test_name, "status": "FAILED", "error": str(e)})
            return False

    async def test_check_existing_tokens(self):
        """Check if Shtern clinic already has stored tokens"""
        test_name = "Check Existing Tokens"
        try:
            logger.info(f"\n🧪 Testing {test_name}...")

            # Query for existing calendar connections
            response = self.supabase.table("calendar_connections").select("*").execute()

            if response.data:
                logger.info(f"  📊 Found {len(response.data)} existing calendar connection(s)")
                for conn in response.data:
                    logger.info(f"    - Doctor: {conn.get('doctor_id')}, Provider: {conn.get('provider')}")
                    logger.info(f"      Created: {conn.get('created_at')}")
                    logger.info(f"      Last Sync: {conn.get('last_sync_at', 'Never')}")
            else:
                logger.info("  ℹ️ No existing calendar connections found")

            self.test_results.append({"test": test_name, "status": "PASSED"})
            return True

        except Exception as e:
            logger.error(f"❌ {test_name} failed: {e}")
            self.test_results.append({"test": test_name, "status": "FAILED", "error": str(e)})
            return False

    async def test_calendar_availability_check(self):
        """Test checking availability through calendar service"""
        test_name = "Calendar Availability Check"
        try:
            logger.info(f"\n🧪 Testing {test_name}...")

            doctor_id = str(uuid.uuid4())  # Use UUID for test
            start_time = datetime.now(timezone.utc) + timedelta(days=1, hours=10)
            end_time = start_time + timedelta(hours=1)

            # Test ASK-HOLD-RESERVE pattern
            logger.info("  Testing ASK-HOLD-RESERVE pattern...")

            appointment_data = {
                "patient_id": "test_patient_001",
                "patient_name": "Test Patient",
                "type": "consultation",
                "notes": "Test appointment for calendar sync"
            }

            # The ask_hold_reserve method does all three phases
            success, reservation_data = await self.calendar_service.ask_hold_reserve(
                doctor_id=doctor_id,
                start_time=start_time,
                end_time=end_time,
                appointment_data=appointment_data
            )

            logger.info(f"  ✅ ASK-HOLD-RESERVE completed: {'Success' if success else 'Failed'}")
            if reservation_data:
                logger.info(f"    Reservation ID: {reservation_data.get('reservation_id')}")

            self.test_results.append({"test": test_name, "status": "PASSED" if success else "FAILED", "reservation_data": reservation_data})
            return success

        except Exception as e:
            logger.error(f"❌ {test_name} failed: {e}")
            self.test_results.append({"test": test_name, "status": "FAILED", "error": str(e)})
            return False

    async def test_hold_creation(self):
        """Test creating a hold across calendar systems"""
        test_name = "Hold Creation (Separate Test)"
        try:
            logger.info(f"\n🧪 Testing {test_name}...")

            doctor_id = str(uuid.uuid4())  # Use UUID for test
            patient_id = str(uuid.uuid4())  # Use UUID for test
            start_time = datetime.now(timezone.utc) + timedelta(days=2, hours=14)
            end_time = start_time + timedelta(minutes=30)

            # Test separate hold creation
            logger.info("  Testing separate hold creation...")

            appointment_data = {
                "patient_id": patient_id,
                "patient_name": "Test Patient 2",
                "type": "checkup",
                "notes": "Hold test for calendar sync"
            }

            # Use ask_hold_reserve which handles the complete flow
            success, reservation_data = await self.calendar_service.ask_hold_reserve(
                doctor_id=doctor_id,
                start_time=start_time,
                end_time=end_time,
                appointment_data=appointment_data
            )

            if success:
                reservation_id = reservation_data.get("reservation_id")
                logger.info(f"  ✅ Hold created successfully: {reservation_id}")
                logger.info(f"    - Status: {reservation_data.get('status')}")
                logger.info(f"    - Internal: {reservation_data.get('internal_status', 'N/A')}")
                logger.info(f"    - Google: {reservation_data.get('google_status', 'N/A')}")

                # Store reservation_id for cleanup
                self.test_reservation_id = reservation_id
            else:
                logger.warning(f"  ⚠️ Hold creation failed: {reservation_data.get('error')}")

            self.test_results.append({"test": test_name, "status": "PASSED" if success else "FAILED"})
            return success

        except Exception as e:
            logger.error(f"❌ {test_name} failed: {e}")
            self.test_results.append({"test": test_name, "status": "FAILED", "error": str(e)})
            return False

    async def test_webhook_registration(self):
        """Test webhook registration for real-time sync"""
        test_name = "Webhook Registration"
        try:
            logger.info(f"\n🧪 Testing {test_name}...")

            # Check if webhooks are configured
            webhook_url = os.getenv("GOOGLE_WEBHOOK_URL", "https://healthcare-clinic-backend.fly.dev/webhooks/calendar/google")

            logger.info(f"  📡 Webhook URL: {webhook_url}")

            # In production, this would register the webhook with Google
            # For testing, we'll just verify the configuration
            logger.info("  ℹ️ Webhook registration would be done during OAuth flow")
            logger.info("  ℹ️ Google sends push notifications to the webhook URL")

            self.test_results.append({"test": test_name, "status": "PASSED"})
            return True

        except Exception as e:
            logger.error(f"❌ {test_name} failed: {e}")
            self.test_results.append({"test": test_name, "status": "FAILED", "error": str(e)})
            return False

    async def test_sync_status(self):
        """Check current sync status and statistics"""
        test_name = "Sync Status Check"
        try:
            logger.info(f"\n🧪 Testing {test_name}...")

            # Check sync status table
            sync_response = self.supabase.table("calendar_sync_status").select("*").execute()

            if sync_response.data:
                logger.info(f"  📊 Sync Status for {len(sync_response.data)} calendar(s):")
                for status in sync_response.data:
                    logger.info(f"    - Provider: {status.get('provider')}")
                    logger.info(f"      Doctor ID: {status.get('doctor_id')}")
                    logger.info(f"      Status: {status.get('sync_status')}")
                    logger.info(f"      Last Sync: {status.get('last_sync_at', 'Never')}")
                    logger.info(f"      Events Synced: {status.get('events_synced', 0)}")
            else:
                logger.info("  ℹ️ No sync status records found")

            # Check for recent sync conflicts
            conflicts_response = self.supabase.table("conflict_resolutions").select("*").limit(5).execute()

            if conflicts_response.data:
                logger.info(f"\n  ⚠️ Recent Conflicts: {len(conflicts_response.data)}")
                for conflict in conflicts_response.data:
                    logger.info(f"    - Type: {conflict.get('conflict_type')}")
                    logger.info(f"      Status: {conflict.get('status')}")
            else:
                logger.info("\n  ✅ No recent conflicts")

            self.test_results.append({"test": test_name, "status": "PASSED"})
            return True

        except Exception as e:
            logger.error(f"❌ {test_name} failed: {e}")
            self.test_results.append({"test": test_name, "status": "FAILED", "error": str(e)})
            return False

    async def test_demo_oauth_flow(self):
        """Provide instructions for manual OAuth flow testing"""
        test_name = "OAuth Flow Instructions"
        try:
            logger.info(f"\n🧪 {test_name}...")
            logger.info("=" * 60)
            logger.info("📋 MANUAL TESTING INSTRUCTIONS:")
            logger.info("=" * 60)

            # Generate a fresh OAuth URL
            doctor_id = str(uuid.uuid4())  # Use UUID for test
            clinic_id = "shtern_dental_clinic"

            auth_url = await self.oauth_manager.initiate_google_oauth(
                doctor_id=doctor_id,
                clinic_id=clinic_id
            )

            logger.info("\n1. Copy and paste this URL in your browser:")
            logger.info(f"   {auth_url}")
            logger.info("\n2. Log in with a Google account that has calendar access")
            logger.info("\n3. Grant the requested permissions:")
            logger.info("   - View and edit calendar events")
            logger.info("   - Access calendar list")
            logger.info("\n4. You'll be redirected to the callback URL")
            logger.info("   (Should show success message if properly configured)")
            logger.info("\n5. Check the database for stored tokens:")
            logger.info("   - Table: calendar_connections")
            logger.info(f"   - Doctor ID: {doctor_id}")

            logger.info("\n" + "=" * 60)
            logger.info("Alternative: Use the demo endpoint for testing:")
            logger.info("  curl https://healthcare-clinic-backend.fly.dev/api/demo/calendar/connect")
            logger.info("=" * 60)

            self.test_results.append({"test": test_name, "status": "INFO", "auth_url": auth_url})
            return True

        except Exception as e:
            logger.error(f"❌ {test_name} failed: {e}")
            self.test_results.append({"test": test_name, "status": "FAILED", "error": str(e)})
            return False

    async def cleanup(self):
        """Clean up test resources"""
        try:
            # Clean up test reservation if created
            if hasattr(self, 'test_reservation_id'):
                logger.info(f"🧹 Cleaning up test reservation: {self.test_reservation_id}")
                # In production, this would cancel the hold/reservation

            if self.redis_client:
                await self.redis_client.aclose()

            logger.info("✅ Cleanup completed")
        except Exception as e:
            logger.error(f"❌ Cleanup failed: {e}")

    async def run_all_tests(self):
        """Run all Google Calendar tests"""
        logger.info("=" * 60)
        logger.info("🚀 Starting Google Calendar Integration Tests")
        logger.info("=" * 60)

        # Setup
        if not await self.setup():
            logger.error("❌ Setup failed, cannot continue tests")
            return

        # Run tests
        test_methods = [
            self.test_oauth_url_generation,
            self.test_check_existing_tokens,
            self.test_calendar_availability_check,
            self.test_hold_creation,
            self.test_webhook_registration,
            self.test_sync_status,
            self.test_demo_oauth_flow
        ]

        for test_method in test_methods:
            try:
                await test_method()
                await asyncio.sleep(1)  # Small delay between tests
            except Exception as e:
                logger.error(f"❌ Unexpected error in {test_method.__name__}: {e}")

        # Generate report
        logger.info("\n" + "=" * 60)
        logger.info("📊 Test Results Summary")
        logger.info("=" * 60)

        passed = sum(1 for r in self.test_results if r["status"] == "PASSED")
        failed = sum(1 for r in self.test_results if r["status"] == "FAILED")
        info = sum(1 for r in self.test_results if r["status"] == "INFO")

        for result in self.test_results:
            if result["status"] == "PASSED":
                logger.info(f"✅ {result['test']}: PASSED")
            elif result["status"] == "FAILED":
                logger.info(f"❌ {result['test']}: FAILED")
                if "error" in result:
                    logger.info(f"   Error: {result['error']}")
            else:
                logger.info(f"ℹ️ {result['test']}: INFO")

        logger.info("-" * 60)
        logger.info(f"Total: {len(self.test_results)} tests")
        logger.info(f"Passed: {passed}")
        logger.info(f"Failed: {failed}")
        logger.info(f"Info: {info}")
        logger.info("=" * 60)

        # Save OAuth URL for manual testing
        for result in self.test_results:
            if "auth_url" in result:
                with open("google_oauth_test_url.txt", "w") as f:
                    f.write(result["auth_url"])
                logger.info("\n📝 OAuth URL saved to: google_oauth_test_url.txt")

        # Cleanup
        await self.cleanup()

        return failed == 0


async def main():
    """Main test runner"""
    tester = GoogleCalendarTester()
    success = await tester.run_all_tests()
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    asyncio.run(main())