"""
Load and performance tests for dental clinic system
Tests system performance under various load conditions
"""

import asyncio
import time
import random
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch, MagicMock, AsyncMock
from .test_base import AsyncTestCase


class TestLoadHandling(AsyncTestCase):
    """Test system behavior under load"""

    async def test_concurrent_appointments(self):
        """Test handling multiple concurrent appointment requests"""
        from clinics.backend.app.appointments import AppointmentManager

        manager = AppointmentManager()

        # Simulate 50 concurrent appointment requests for same slot
        async def book_appointment(user_id: int):
            return await manager.book_appointment(
                clinic_id=self.test_clinic_id,
                phone=f'+52123456{user_id:04d}',
                date='2024-12-20',
                time='14:00',
                max_slot_capacity=10  # Only 10 slots available
            )

        # Run concurrent bookings
        tasks = [book_appointment(i) for i in range(50)]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Count successful bookings
        successful = [r for r in results if isinstance(r, dict) and r.get('success')]
        failed = [r for r in results if isinstance(r, dict) and not r.get('success')]

        # Should have exactly 10 successful bookings
        self.assertEqual(len(successful), 10)
        self.assertEqual(len(failed), 40)

        # No duplicate bookings
        appointment_ids = [r['appointment_id'] for r in successful]
        self.assertEqual(len(appointment_ids), len(set(appointment_ids)))

    async def test_message_processing_throughput(self):
        """Test message processing throughput"""
        from clinics.backend.app.whatsapp import MessageProcessor

        processor = MessageProcessor()

        # Generate test messages
        messages = []
        for i in range(100):
            messages.append({
                'phone': f'+52123456{i:04d}',
                'message': random.choice([
                    'Quiero una cita',
                    '¿Cuáles son sus horarios?',
                    'Necesito cancelar mi cita',
                    '¿Cuánto cuesta una limpieza?'
                ]),
                'timestamp': datetime.utcnow()
            })

        # Process messages and measure time
        start_time = time.time()

        tasks = [processor.process_message(msg) for msg in messages]
        results = await asyncio.gather(*tasks)

        end_time = time.time()
        duration = end_time - start_time

        # Calculate throughput
        throughput = len(messages) / duration

        # Should process at least 20 messages per second
        self.assertGreater(throughput, 20, f"Low throughput: {throughput:.2f} msg/sec")

        # All messages should be processed
        self.assertEqual(len(results), 100)

    @patch('redis.Redis')
    async def test_session_storage_performance(self, mock_redis):
        """Test Redis session storage performance under load"""
        from clinics.backend.app.session_manager import RedisSessionManager

        mock_redis_instance = MagicMock()
        mock_redis.return_value = mock_redis_instance

        # Simulate Redis latency
        async def simulate_redis_operation(delay=0.001):
            await asyncio.sleep(delay)
            return {'session_id': str(uuid.uuid4())}

        mock_redis_instance.get = AsyncMock(side_effect=simulate_redis_operation)
        mock_redis_instance.setex = AsyncMock(side_effect=simulate_redis_operation)

        manager = RedisSessionManager()

        # Create 100 concurrent sessions
        start_time = time.time()

        tasks = []
        for i in range(100):
            phone = f'+52123456{i:04d}'
            tasks.append(manager.get_or_create_session(phone, self.test_clinic_id))

        sessions = await asyncio.gather(*tasks)

        end_time = time.time()
        duration = end_time - start_time

        # Should complete within 2 seconds even with Redis latency
        self.assertLess(duration, 2.0)

        # All sessions should be created
        self.assertEqual(len(sessions), 100)


class TestResponseTime(AsyncTestCase):
    """Test system response times"""

    async def test_appointment_booking_response_time(self):
        """Test appointment booking response time"""
        from clinics.backend.app.appointments import SimpleAppointmentBooking

        booking = SimpleAppointmentBooking()

        # Measure booking time
        start_time = time.perf_counter()

        result = await booking.book_appointment(
            clinic_id=self.test_clinic_id,
            patient_phone=self.test_phone,
            requested_date='2024-12-20',
            requested_time='14:00'
        )

        end_time = time.perf_counter()
        response_time = (end_time - start_time) * 1000  # Convert to ms

        # Should respond within 500ms
        self.assertLess(response_time, 500, f"Slow response: {response_time:.2f}ms")

    async def test_whatsapp_webhook_response_time(self):
        """Test WhatsApp webhook processing time"""
        from clinics.backend.app.whatsapp import handle_whatsapp_webhook

        payload = self.create_whatsapp_webhook_payload()

        # Measure processing time
        start_time = time.perf_counter()

        result = await handle_whatsapp_webhook(
            organization_id=self.test_clinic_id,
            payload=payload
        )

        end_time = time.perf_counter()
        response_time = (end_time - start_time) * 1000

        # Should respond within 200ms (Twilio requirement)
        self.assertLess(response_time, 200, f"Webhook too slow: {response_time:.2f}ms")

    async def test_database_query_performance(self):
        """Test database query performance"""
        from clinics.backend.app.database import DatabaseClient

        db = DatabaseClient()

        # Test various query types
        queries = [
            ('check_availability', {
                'clinic_id': self.test_clinic_id,
                'date': '2024-12-20',
                'time': '14:00'
            }),
            ('get_appointments', {
                'clinic_id': self.test_clinic_id,
                'date': '2024-12-20'
            }),
            ('get_patient_history', {
                'phone': self.test_phone,
                'clinic_id': self.test_clinic_id
            })
        ]

        for query_name, params in queries:
            start_time = time.perf_counter()

            result = await db.execute_query(query_name, **params)

            end_time = time.perf_counter()
            query_time = (end_time - start_time) * 1000

            # Database queries should complete within 50ms
            self.assertLess(
                query_time, 50,
                f"Slow query '{query_name}': {query_time:.2f}ms"
            )


class TestScalability(AsyncTestCase):
    """Test system scalability"""

    async def test_multi_clinic_handling(self):
        """Test handling multiple clinics simultaneously"""
        from clinics.backend.app.main import ClinicManager

        manager = ClinicManager()

        # Create 10 test clinics
        clinics = []
        for i in range(10):
            clinic = self.create_test_clinic(
                name=f'Test Clinic {i}',
                max_appointments_per_slot=5
            )
            clinics.append(clinic)
            await manager.register_clinic(clinic)

        # Simulate messages to different clinics
        async def send_message_to_clinic(clinic_id: str, message_count: int):
            results = []
            for i in range(message_count):
                result = await manager.process_message(
                    clinic_id=clinic_id,
                    phone=f'+52123456{i:04d}',
                    message='Quiero una cita'
                )
                results.append(result)
            return results

        # Send 10 messages to each clinic concurrently
        tasks = [send_message_to_clinic(clinic['id'], 10) for clinic in clinics]

        start_time = time.time()
        all_results = await asyncio.gather(*tasks)
        end_time = time.time()

        # Should handle 100 messages across 10 clinics in under 5 seconds
        duration = end_time - start_time
        self.assertLess(duration, 5.0)

        # All messages should be processed
        total_messages = sum(len(results) for results in all_results)
        self.assertEqual(total_messages, 100)

    async def test_memory_usage_under_load(self):
        """Test memory usage doesn't grow unbounded under load"""
        import psutil
        import gc

        process = psutil.Process()

        # Get initial memory usage
        gc.collect()
        initial_memory = process.memory_info().rss / 1024 / 1024  # MB

        # Process many messages
        from clinics.backend.app.whatsapp import MessageProcessor
        processor = MessageProcessor()

        for batch in range(10):
            messages = []
            for i in range(100):
                messages.append({
                    'phone': f'+52123456{i:04d}',
                    'message': 'Test message ' * 100,  # Large message
                    'timestamp': datetime.utcnow()
                })

            await asyncio.gather(*[processor.process_message(msg) for msg in messages])

            # Force garbage collection
            gc.collect()

        # Get final memory usage
        final_memory = process.memory_info().rss / 1024 / 1024  # MB
        memory_increase = final_memory - initial_memory

        # Memory increase should be less than 100MB
        self.assertLess(
            memory_increase, 100,
            f"Memory leak detected: {memory_increase:.2f}MB increase"
        )


class TestRateLimiting(AsyncTestCase):
    """Test rate limiting effectiveness"""

    async def test_rate_limit_enforcement(self):
        """Test that rate limits are properly enforced"""
        from clinics.backend.app.middleware import RateLimiter

        limiter = RateLimiter(limit=30, window=60)  # 30 requests per minute

        ip_address = '192.168.1.100'
        allowed_count = 0
        blocked_count = 0

        # Try 50 requests
        for i in range(50):
            allowed = await limiter.check_limit(ip_address)
            if allowed:
                allowed_count += 1
            else:
                blocked_count += 1

        # Should allow exactly 30 requests
        self.assertEqual(allowed_count, 30)
        self.assertEqual(blocked_count, 20)

    async def test_rate_limit_reset(self):
        """Test that rate limits reset after window expires"""
        from clinics.backend.app.middleware import RateLimiter

        limiter = RateLimiter(limit=5, window=1)  # 5 requests per second

        ip_address = '192.168.1.100'

        # Use up the limit
        for i in range(5):
            allowed = await limiter.check_limit(ip_address)
            self.assertTrue(allowed)

        # Should be blocked
        allowed = await limiter.check_limit(ip_address)
        self.assertFalse(allowed)

        # Wait for window to expire
        await asyncio.sleep(1.1)

        # Should be allowed again
        allowed = await limiter.check_limit(ip_address)
        self.assertTrue(allowed)


class TestCachePerformance(AsyncTestCase):
    """Test caching effectiveness"""

    @patch('clinics.backend.app.cache.RedisCache')
    async def test_cache_hit_rate(self, mock_cache_class):
        """Test cache hit rate for common queries"""
        from clinics.backend.app.services import ServiceInfoProvider

        mock_cache = MagicMock()
        mock_cache_class.return_value = mock_cache

        # Simulate cache hits and misses
        cache_data = {}
        mock_cache.get = lambda key: cache_data.get(key)
        mock_cache.set = lambda key, value, ttl: cache_data.update({key: value})

        provider = ServiceInfoProvider(cache=mock_cache)

        # Make repeated requests for same data
        queries = ['business_hours'] * 10 + ['services'] * 10 + ['prices'] * 10

        cache_hits = 0
        cache_misses = 0

        for query in queries:
            result = await provider.get_info(self.test_clinic_id, query)

            if query in cache_data:
                cache_hits += 1
            else:
                cache_misses += 1
                cache_data[query] = result

        # Cache hit rate should be high for repeated queries
        hit_rate = cache_hits / (cache_hits + cache_misses)
        self.assertGreater(hit_rate, 0.8, f"Low cache hit rate: {hit_rate:.2%}")

    async def test_cache_performance_benefit(self):
        """Test performance improvement from caching"""
        from clinics.backend.app.services import get_clinic_info

        # First call (cache miss)
        start_time = time.perf_counter()
        result1 = await get_clinic_info(self.test_clinic_id)
        uncached_time = time.perf_counter() - start_time

        # Second call (cache hit)
        start_time = time.perf_counter()
        result2 = await get_clinic_info(self.test_clinic_id)
        cached_time = time.perf_counter() - start_time

        # Cached call should be at least 10x faster
        speedup = uncached_time / cached_time
        self.assertGreater(speedup, 10, f"Insufficient cache speedup: {speedup:.2f}x")


class TestStressTest(AsyncTestCase):
    """Stress test the system to find breaking points"""

    async def test_sustained_load(self):
        """Test system under sustained load for extended period"""
        from clinics.backend.app.main import DentalClinicSystem

        system = DentalClinicSystem()

        # Run for 30 seconds with constant load
        duration = 30  # seconds
        messages_per_second = 10

        start_time = time.time()
        errors = []
        successful = 0

        while time.time() - start_time < duration:
            batch_start = time.time()

            # Send batch of messages
            tasks = []
            for i in range(messages_per_second):
                phone = f'+52123456{random.randint(0, 9999):04d}'
                message = random.choice([
                    'Quiero una cita',
                    'Cancelar cita',
                    '¿Horarios?',
                    '¿Precios?'
                ])

                tasks.append(system.handle_message(phone, message))

            results = await asyncio.gather(*tasks, return_exceptions=True)

            # Count successes and errors
            for result in results:
                if isinstance(result, Exception):
                    errors.append(result)
                else:
                    successful += 1

            # Maintain rate
            batch_duration = time.time() - batch_start
            if batch_duration < 1.0:
                await asyncio.sleep(1.0 - batch_duration)

        # System should handle sustained load with <1% error rate
        total_messages = successful + len(errors)
        error_rate = len(errors) / total_messages if total_messages > 0 else 0

        self.assertLess(error_rate, 0.01, f"High error rate: {error_rate:.2%}")
        self.assertGreater(successful, duration * messages_per_second * 0.99)
