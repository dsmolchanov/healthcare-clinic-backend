"""
Load Test for Task #8: Testing Suite

Simulates 1000 concurrent users with realistic message distribution:
- 70% PRICE queries (fast-path)
- 20% FAQ queries (fast-path)
- 10% COMPLEX queries (LLM)

Performance Targets:
- P95 latency: <2s
- Error rate: <1%
- Fast-path coverage: >70%

Run: locust -f locustfile_task8.py --host=https://healthcare-clinic-backend.fly.dev
"""

from locust import HttpUser, task, between, events
import json
import uuid
import time

# Track metrics
metrics = {
    'fast_path_count': 0,
    'complex_count': 0,
    'total_count': 0,
    'errors': 0
}


class HealthcareWebhookUser(HttpUser):
    """Simulates realistic user behavior"""

    wait_time = between(1, 3)
    host = "https://healthcare-clinic-backend.fly.dev"

    def on_start(self):
        self.clinic_id = "test-clinic"
        self.phone = f"+1555{uuid.uuid4().hex[:7]}"

    @task(70)
    def price_query(self):
        """Price query - Fast-path (<600ms target)"""
        message = "How much does teeth cleaning cost?"
        payload = {
            "message": {
                "key": {
                    "remoteJid": f"{self.phone}@s.whatsapp.net",
                    "id": str(uuid.uuid4())
                },
                "message": {"conversation": message}
            }
        }

        with self.client.post("/webhooks/evolution/test", json=payload, catch_response=True) as response:
            if response.status_code == 200:
                metrics['fast_path_count'] += 1
                metrics['total_count'] += 1
                if response.elapsed.total_seconds() < 0.6:
                    response.success()
                else:
                    response.failure(f"Slow: {response.elapsed.total_seconds():.2f}s")
            else:
                metrics['errors'] += 1

    @task(20)
    def faq_query(self):
        """FAQ query - Fast-path (<400ms target)"""
        message = "What are your hours?"
        payload = {
            "message": {
                "key": {
                    "remoteJid": f"{self.phone}@s.whatsapp.net",
                    "id": str(uuid.uuid4())
                },
                "message": {"conversation": message}
            }
        }

        with self.client.post("/webhooks/evolution/test", json=payload, catch_response=True) as response:
            if response.status_code == 200:
                metrics['fast_path_count'] += 1
                metrics['total_count'] += 1
                response.success()
            else:
                metrics['errors'] += 1

    @task(10)
    def complex_query(self):
        """Complex query - LLM lane (<2s target)"""
        message = "I have tooth pain for a week, what should I do?"
        payload = {
            "message": {
                "key": {
                    "remoteJid": f"{self.phone}@s.whatsapp.net",
                    "id": str(uuid.uuid4())
                },
                "message": {"conversation": message}
            }
        }

        with self.client.post("/webhooks/evolution/test", json=payload, catch_response=True) as response:
            if response.status_code == 200:
                metrics['complex_count'] += 1
                metrics['total_count'] += 1
                response.success()
            else:
                metrics['errors'] += 1


@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    """Print test summary"""
    print("\n" + "="*80)
    print("LOAD TEST SUMMARY - Task #8")
    print("="*80)

    total = metrics['total_count']
    if total > 0:
        fast_path_pct = (metrics['fast_path_count'] / total) * 100
        error_rate = (metrics['errors'] / (total + metrics['errors'])) * 100

        print(f"\nFast-Path Coverage: {fast_path_pct:.1f}% (target: >70%) {'✅' if fast_path_pct > 70 else '❌'}")
        print(f"Error Rate: {error_rate:.2f}% (target: <1%) {'✅' if error_rate < 1 else '❌'}")
        print(f"\nCheck Locust UI for P95 latency (target: <2s)")
    print("="*80 + "\n")
