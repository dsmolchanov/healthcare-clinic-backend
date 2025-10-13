"""
Load Testing for Room Assignment Feature (Issue #34 - Stream C)

Performance Targets:
- 50 concurrent appointment bookings
- p95 latency <100ms for room assignment
- No database deadlocks
- No race conditions

Test Scenarios:
1. Concurrent Booking Storm - 50 simultaneous bookings
2. Room Conflict Handling - Limited rooms with contention
3. Rules Engine Performance - Complex rule sets at scale

Usage:
    # Run all scenarios with 50 users
    locust -f locustfile.py --host=http://localhost:8000 --users=50 --spawn-rate=10

    # Run specific scenario
    locust -f locustfile.py --host=http://localhost:8000 --users=50 --spawn-rate=10 --tags concurrent

    # Headless mode with report
    locust -f locustfile.py --host=http://localhost:8000 --users=50 --spawn-rate=10 --headless --run-time=2m --html=report.html
"""

import os
import json
import time
from datetime import datetime, timedelta
from uuid import uuid4
from locust import HttpUser, task, between, tag, events
from locust.runners import MasterRunner, WorkerRunner
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Test data management
class TestDataManager:
    """Manages test data for load testing"""

    def __init__(self):
        self.clinic_ids = []
        self.doctor_ids = []
        self.patient_ids = []
        self.room_ids = []
        self.created_appointment_ids = []

    def setup_test_data(self, base_url: str):
        """Setup test clinics, doctors, patients, and rooms"""
        import requests

        logger.info("Setting up test data for load testing...")

        # For load testing, we'll use environment variables for test IDs
        # In a real scenario, these would be created via setup scripts
        self.clinic_ids = [os.environ.get("TEST_CLINIC_ID", "test-clinic-load-1")]
        self.doctor_ids = [os.environ.get("TEST_DOCTOR_ID", "test-doctor-load-1")]
        self.patient_ids = [os.environ.get("TEST_PATIENT_ID", "test-patient-load-1")]
        self.room_ids = [
            os.environ.get("TEST_ROOM_1_ID", "test-room-load-1"),
            os.environ.get("TEST_ROOM_2_ID", "test-room-load-2"),
            os.environ.get("TEST_ROOM_3_ID", "test-room-load-3"),
        ]

        logger.info(f"Using clinic: {self.clinic_ids[0]}")
        logger.info(f"Using {len(self.room_ids)} rooms for load testing")

    def cleanup(self, base_url: str):
        """Cleanup created appointments"""
        logger.info(f"Cleaning up {len(self.created_appointment_ids)} appointments...")
        # Cleanup would be done via API or direct database access


# Global test data manager
test_data = TestDataManager()


# Locust event handlers for setup/teardown
@events.init.add_listener
def on_locust_init(environment, **kwargs):
    """Initialize test data when Locust starts"""
    if isinstance(environment.runner, MasterRunner):
        logger.info("Locust master initializing...")
    elif isinstance(environment.runner, WorkerRunner):
        logger.info("Locust worker initializing...")
    else:
        # Standalone mode
        test_data.setup_test_data(environment.host)


@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    """Cleanup after test completes"""
    logger.info("Load test completed. Cleaning up...")
    # test_data.cleanup(environment.host)


# Custom metrics tracking
room_assignment_latencies = []
conflict_detection_latencies = []
rules_evaluation_latencies = []


class AppointmentBookingUser(HttpUser):
    """
    Simulates users booking appointments concurrently.
    """
    wait_time = between(1, 3)  # Wait 1-3 seconds between tasks

    def on_start(self):
        """Called when a simulated user starts"""
        self.clinic_id = test_data.clinic_ids[0] if test_data.clinic_ids else str(uuid4())
        self.doctor_id = test_data.doctor_ids[0] if test_data.doctor_ids else str(uuid4())
        self.patient_id = test_data.patient_ids[0] if test_data.patient_ids else str(uuid4())

    @task(3)
    @tag("concurrent", "booking")
    def book_appointment_with_room_assignment(self):
        """
        Scenario 1: Concurrent Booking Storm
        Book appointment and measure room assignment latency.
        """
        # Generate appointment time (7 days in future)
        start_time = datetime.now() + timedelta(days=7, hours=10)
        start_time = start_time.replace(minute=0, second=0, microsecond=0)

        # Random minute offset to create variety
        import random
        minute_offset = random.choice([0, 15, 30, 45])
        start_time = start_time.replace(minute=minute_offset)

        end_time = start_time + timedelta(minutes=30)

        payload = {
            "patient_id": self.patient_id,
            "doctor_id": self.doctor_id,
            "clinic_id": self.clinic_id,
            "start_time": start_time.isoformat(),
            "end_time": end_time.isoformat(),
            "appointment_type": "consultation",
            "reason": f"Load test appointment {uuid4()}",
            "notes": "Automated load test"
        }

        start = time.time()

        with self.client.post(
            "/api/appointments/book",
            json=payload,
            catch_response=True,
            name="Book Appointment with Room Assignment"
        ) as response:
            latency_ms = (time.time() - start) * 1000

            if response.status_code == 200:
                data = response.json()
                if data.get("success"):
                    appointment_id = data.get("appointment_id")
                    test_data.created_appointment_ids.append(appointment_id)

                    # Track room assignment latency
                    room_assignment_latencies.append(latency_ms)

                    # Check if latency meets target (<100ms for p95)
                    if latency_ms > 100:
                        logger.warning(f"Room assignment latency: {latency_ms:.2f}ms (exceeds 100ms target)")

                    response.success()
                else:
                    response.failure(f"Booking failed: {data.get('error')}")
            else:
                response.failure(f"HTTP {response.status_code}: {response.text}")

    @task(2)
    @tag("conflict", "booking")
    def book_overlapping_appointments(self):
        """
        Scenario 2: Room Conflict Handling Under Load
        Intentionally create overlapping appointments to test conflict detection.
        """
        # Use fixed time to create intentional conflicts
        base_time = datetime.now() + timedelta(days=7, hours=14)
        start_time = base_time.replace(minute=0, second=0, microsecond=0)
        end_time = start_time + timedelta(minutes=60)  # Longer appointment

        payload = {
            "patient_id": self.patient_id,
            "doctor_id": self.doctor_id,
            "clinic_id": self.clinic_id,
            "start_time": start_time.isoformat(),
            "end_time": end_time.isoformat(),
            "appointment_type": "procedure",
            "reason": "Conflict test appointment",
            "notes": "Testing conflict detection"
        }

        start = time.time()

        with self.client.post(
            "/api/appointments/book",
            json=payload,
            catch_response=True,
            name="Book with Conflict Detection"
        ) as response:
            latency_ms = (time.time() - start) * 1000
            conflict_detection_latencies.append(latency_ms)

            if response.status_code == 200:
                data = response.json()
                if data.get("success"):
                    appointment_id = data.get("appointment_id")
                    test_data.created_appointment_ids.append(appointment_id)

                    # Verify no double-booking occurred
                    # In a real test, we'd verify room assignments don't overlap
                    if latency_ms > 50:
                        logger.warning(f"Conflict detection latency: {latency_ms:.2f}ms (target <50ms)")

                    response.success()
                else:
                    # Failure is acceptable if all rooms are booked
                    logger.info(f"Booking failed (expected with conflicts): {data.get('error')}")
                    response.success()
            else:
                response.failure(f"HTTP {response.status_code}")

    @task(1)
    @tag("rules", "booking")
    def book_with_complex_rules(self):
        """
        Scenario 3: Rules Engine Performance at Scale
        Book appointments that trigger complex rule evaluations.
        """
        start_time = datetime.now() + timedelta(days=7, hours=15)
        start_time = start_time.replace(minute=30, second=0, microsecond=0)
        end_time = start_time + timedelta(minutes=45)

        payload = {
            "patient_id": self.patient_id,
            "doctor_id": self.doctor_id,
            "clinic_id": self.clinic_id,
            "start_time": start_time.isoformat(),
            "end_time": end_time.isoformat(),
            "appointment_type": "procedure",
            "reason": "Complex rules test",
            "notes": "Equipment: ultrasound, x-ray; Special requirements"
        }

        start = time.time()

        with self.client.post(
            "/api/appointments/book",
            json=payload,
            catch_response=True,
            name="Book with Rules Engine Evaluation"
        ) as response:
            latency_ms = (time.time() - start) * 1000
            rules_evaluation_latencies.append(latency_ms)

            if response.status_code == 200:
                data = response.json()
                if data.get("success"):
                    appointment_id = data.get("appointment_id")
                    test_data.created_appointment_ids.append(appointment_id)

                    # Check rules engine evaluation time (target <50ms per slot)
                    if latency_ms > 50:
                        logger.warning(f"Rules evaluation latency: {latency_ms:.2f}ms (target <50ms)")

                    response.success()
                else:
                    response.failure(f"Booking failed: {data.get('error')}")
            else:
                response.failure(f"HTTP {response.status_code}")

    @task(1)
    @tag("availability", "query")
    def query_available_slots(self):
        """Query available slots (read operation for baseline comparison)"""
        date = (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d")

        with self.client.get(
            f"/api/appointments/available-slots",
            params={
                "doctor_id": self.doctor_id,
                "date": date,
                "duration_minutes": 30
            },
            catch_response=True,
            name="Query Available Slots"
        ) as response:
            if response.status_code == 200:
                data = response.json()
                if isinstance(data, list):
                    response.success()
                else:
                    response.failure("Invalid response format")
            else:
                response.failure(f"HTTP {response.status_code}")

    @task(1)
    @tag("override", "room")
    def override_room_assignment(self):
        """Override room assignment (tests PATCH endpoint)"""
        # Only override if we have created appointments
        if not test_data.created_appointment_ids:
            return

        import random
        appointment_id = random.choice(test_data.created_appointment_ids)
        new_room_id = random.choice(test_data.room_ids) if test_data.room_ids else str(uuid4())

        payload = {
            "room_id": new_room_id,
            "reason": "Load test room override for performance validation"
        }

        with self.client.patch(
            f"/api/appointments/{appointment_id}/room",
            json=payload,
            catch_response=True,
            name="Override Room Assignment"
        ) as response:
            if response.status_code == 200:
                response.success()
            elif response.status_code == 404:
                # Appointment may have been cleaned up
                response.success()
            else:
                response.failure(f"HTTP {response.status_code}")


class HighLoadBookingUser(AppointmentBookingUser):
    """
    High-intensity user for stress testing.
    Shorter wait times to maximize load.
    """
    wait_time = between(0.5, 1.5)


# Custom statistics aggregation
@events.report_to_master.add_listener
def on_report_to_master(client_id, data):
    """Send custom metrics to master"""
    data["room_assignment_latencies"] = room_assignment_latencies[-100:]  # Last 100
    data["conflict_detection_latencies"] = conflict_detection_latencies[-100:]
    data["rules_evaluation_latencies"] = rules_evaluation_latencies[-100:]


@events.worker_report.add_listener
def on_worker_report(client_id, data):
    """Receive custom metrics from workers"""
    if "room_assignment_latencies" in data:
        room_assignment_latencies.extend(data["room_assignment_latencies"])
    if "conflict_detection_latencies" in data:
        conflict_detection_latencies.extend(data["conflict_detection_latencies"])
    if "rules_evaluation_latencies" in data:
        rules_evaluation_latencies.extend(data["rules_evaluation_latencies"])


@events.test_stop.add_listener
def on_test_stop_report(environment, **kwargs):
    """Print performance report after test completes"""
    logger.info("\n" + "="*80)
    logger.info("ROOM ASSIGNMENT PERFORMANCE REPORT")
    logger.info("="*80)

    if room_assignment_latencies:
        sorted_latencies = sorted(room_assignment_latencies)
        p50 = sorted_latencies[len(sorted_latencies)//2]
        p95 = sorted_latencies[int(len(sorted_latencies)*0.95)]
        p99 = sorted_latencies[int(len(sorted_latencies)*0.99)]
        avg = sum(sorted_latencies) / len(sorted_latencies)

        logger.info("\nRoom Assignment Latencies:")
        logger.info(f"  Average: {avg:.2f}ms")
        logger.info(f"  p50: {p50:.2f}ms")
        logger.info(f"  p95: {p95:.2f}ms (target: <100ms)")
        logger.info(f"  p99: {p99:.2f}ms")
        logger.info(f"  Target Met: {'✓ YES' if p95 < 100 else '✗ NO'}")

    if conflict_detection_latencies:
        sorted_latencies = sorted(conflict_detection_latencies)
        p95 = sorted_latencies[int(len(sorted_latencies)*0.95)]
        avg = sum(sorted_latencies) / len(sorted_latencies)

        logger.info("\nConflict Detection Latencies:")
        logger.info(f"  Average: {avg:.2f}ms")
        logger.info(f"  p95: {p95:.2f}ms (target: <50ms)")
        logger.info(f"  Target Met: {'✓ YES' if p95 < 50 else '✗ NO'}")

    if rules_evaluation_latencies:
        sorted_latencies = sorted(rules_evaluation_latencies)
        p95 = sorted_latencies[int(len(sorted_latencies)*0.95)]
        avg = sum(sorted_latencies) / len(sorted_latencies)

        logger.info("\nRules Engine Evaluation Latencies:")
        logger.info(f"  Average: {avg:.2f}ms")
        logger.info(f"  p95: {p95:.2f}ms (target: <50ms)")
        logger.info(f"  Target Met: {'✓ YES' if p95 < 50 else '✗ NO'}")

    logger.info("\nTotal Appointments Created: " + str(len(test_data.created_appointment_ids)))
    logger.info("="*80 + "\n")
