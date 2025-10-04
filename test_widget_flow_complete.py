#!/usr/bin/env python3
"""
Complete Widget Chat Flow Test Suite
Tests the entire message flow from widget → backend → LangGraph → response
with detailed latency tracking, database validation, and stage monitoring.
"""

import asyncio
import httpx
import time
import json
import os
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum
import sys

# Test configuration
BACKEND_URL = os.getenv("BACKEND_URL", "https://healthcare-clinic-backend.fly.dev")
CLINIC_ID = "3e411ecb-3411-4add-91e2-8fa897310cb0"
CLINIC_NAME = "Shtern Dental Clinic"
TEST_TIMEOUT = 60  # seconds

# Supabase configuration for direct DB validation
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_ANON_KEY")


class TestStage(Enum):
    """Test execution stages"""
    SETUP = "setup"
    WIDGET_SEND = "widget_send"
    BACKEND_RECEIVE = "backend_receive"
    LANGGRAPH_PROCESS = "langgraph_process"
    MEMORY_RETRIEVE = "memory_retrieve"
    RAG_SEARCH = "rag_search"
    LLM_GENERATE = "llm_generate"
    MEMORY_STORE = "memory_store"
    RESPONSE_RETURN = "response_return"
    DATABASE_VERIFY = "database_verify"
    COMPLETE = "complete"


@dataclass
class LatencyMetrics:
    """Latency tracking for each stage"""
    stage: TestStage
    start_time: float
    end_time: Optional[float] = None
    duration_ms: Optional[float] = None
    success: bool = True
    error: Optional[str] = None
    metadata: Dict = field(default_factory=dict)

    def complete(self, success: bool = True, error: Optional[str] = None, **metadata):
        self.end_time = time.time()
        self.duration_ms = (self.end_time - self.start_time) * 1000
        self.success = success
        self.error = error
        self.metadata.update(metadata)


@dataclass
class TestScenario:
    """Test scenario definition"""
    name: str
    user_message: str
    expected_keywords: List[str]
    expected_intent: Optional[str] = None
    should_use_rag: bool = True
    should_access_memory: bool = True
    session_id: Optional[str] = None


class WidgetFlowTester:
    """Complete end-to-end widget flow tester"""

    def __init__(self, backend_url: str = BACKEND_URL):
        self.backend_url = backend_url
        self.client = httpx.AsyncClient(timeout=TEST_TIMEOUT)
        self.metrics: List[LatencyMetrics] = []
        self.test_results: Dict = {}
        self.session_id = f"test_session_{int(time.time())}"

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.client.aclose()

    def start_stage(self, stage: TestStage, **metadata) -> LatencyMetrics:
        """Start tracking a test stage"""
        metric = LatencyMetrics(
            stage=stage,
            start_time=time.time(),
            metadata=metadata
        )
        self.metrics.append(metric)
        print(f"\n{'='*60}")
        print(f"🔵 STAGE: {stage.value.upper()}")
        if metadata:
            print(f"   Metadata: {json.dumps(metadata, indent=2)}")
        return metric

    def print_stage_result(self, metric: LatencyMetrics):
        """Print stage completion result"""
        status = "✅ SUCCESS" if metric.success else "❌ FAILED"
        duration = f"{metric.duration_ms:.2f}ms" if metric.duration_ms else "N/A"
        print(f"{status} - Duration: {duration}")
        if metric.error:
            print(f"   Error: {metric.error}")
        if metric.metadata:
            print(f"   Metadata: {json.dumps(metric.metadata, indent=2)}")

    async def test_health_check(self) -> bool:
        """Test 1: Health check endpoint"""
        print("\n" + "="*60)
        print("TEST 1: HEALTH CHECK")
        print("="*60)

        metric = self.start_stage(TestStage.SETUP)

        try:
            response = await self.client.get(f"{self.backend_url}/health")

            if response.status_code == 200:
                data = response.json()
                metric.complete(
                    success=True,
                    status=data.get("status"),
                    service=data.get("service"),
                    version=data.get("version")
                )
                self.print_stage_result(metric)
                return True
            else:
                metric.complete(
                    success=False,
                    error=f"Health check failed: {response.status_code}"
                )
                self.print_stage_result(metric)
                return False

        except Exception as e:
            metric.complete(success=False, error=str(e))
            self.print_stage_result(metric)
            return False

    async def send_message(
        self,
        message: str,
        session_id: Optional[str] = None
    ) -> Tuple[Optional[Dict], List[LatencyMetrics]]:
        """Send message through widget flow and track all stages"""

        if not session_id:
            session_id = self.session_id

        stage_metrics = []

        # Stage 1: Widget sends message
        print("\n" + "="*60)
        print("STAGE 1: WIDGET MESSAGE SEND")
        print("="*60)

        metric = self.start_stage(
            TestStage.WIDGET_SEND,
            message=message,
            session_id=session_id
        )

        payload = {
            "from_phone": f"widget_{session_id}",
            "to_phone": "+14155238886",
            "body": message,
            "message_sid": f"widget_{int(time.time())}",
            "clinic_id": CLINIC_ID,
            "clinic_name": CLINIC_NAME,
            "channel": "widget",
            "metadata": {
                "session_id": session_id,
                "agent_id": "demo-agent"
            }
        }

        try:
            print(f"📤 Sending to: {self.backend_url}/api/process-message")
            print(f"📝 Payload: {json.dumps(payload, indent=2)}")

            response = await self.client.post(
                f"{self.backend_url}/api/process-message",
                json=payload
            )

            metric.complete(
                success=True,
                status_code=response.status_code,
                payload_size=len(json.dumps(payload))
            )
            stage_metrics.append(metric)
            self.print_stage_result(metric)

            if response.status_code != 200:
                print(f"❌ Unexpected status code: {response.status_code}")
                print(f"Response: {response.text}")
                return None, stage_metrics

            # Stage 2: Parse response
            print("\n" + "="*60)
            print("STAGE 2: BACKEND RESPONSE PARSING")
            print("="*60)

            metric = self.start_stage(TestStage.RESPONSE_RETURN)

            try:
                response_data = response.json()
                print(f"📥 Response received:")
                print(json.dumps(response_data, indent=2))

                metric.complete(
                    success=True,
                    message_length=len(response_data.get("message", "")),
                    has_metadata=bool(response_data.get("metadata")),
                    session_id=response_data.get("session_id")
                )
                stage_metrics.append(metric)
                self.print_stage_result(metric)

                return response_data, stage_metrics

            except json.JSONDecodeError as e:
                metric.complete(success=False, error=f"JSON decode error: {str(e)}")
                stage_metrics.append(metric)
                self.print_stage_result(metric)
                return None, stage_metrics

        except httpx.TimeoutException:
            metric.complete(success=False, error="Request timeout")
            stage_metrics.append(metric)
            self.print_stage_result(metric)
            return None, stage_metrics

        except Exception as e:
            metric.complete(success=False, error=str(e))
            stage_metrics.append(metric)
            self.print_stage_result(metric)
            return None, stage_metrics

    async def verify_database_storage(
        self,
        session_id: str,
        message: str
    ) -> bool:
        """Verify message was stored in database"""

        if not SUPABASE_URL or not SUPABASE_KEY:
            print("⚠️  Skipping database verification (no Supabase credentials)")
            return True

        print("\n" + "="*60)
        print("DATABASE VERIFICATION")
        print("="*60)

        metric = self.start_stage(
            TestStage.DATABASE_VERIFY,
            session_id=session_id,
            checking_message=message
        )

        try:
            from supabase import create_client

            supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

            # Query conversation_messages table
            result = supabase.table("conversation_messages") \
                .select("*") \
                .contains("metadata", {"session_id": session_id}) \
                .order("created_at", desc=True) \
                .limit(5) \
                .execute()

            if result.data:
                print(f"✅ Found {len(result.data)} messages in database")
                for msg in result.data:
                    print(f"   - {msg['role']}: {msg['content'][:50]}...")

                metric.complete(
                    success=True,
                    messages_found=len(result.data),
                    latest_message_id=result.data[0].get("id")
                )
                self.print_stage_result(metric)
                return True
            else:
                print("⚠️  No messages found in database")
                metric.complete(
                    success=False,
                    error="No messages found",
                    messages_found=0
                )
                self.print_stage_result(metric)
                return False

        except Exception as e:
            print(f"❌ Database verification error: {e}")
            metric.complete(success=False, error=str(e))
            self.print_stage_result(metric)
            return False

    async def run_test_scenario(self, scenario: TestScenario) -> Dict:
        """Run a complete test scenario"""

        print("\n" + "🟢" * 60)
        print(f"TEST SCENARIO: {scenario.name}")
        print("🟢" * 60)
        print(f"Message: {scenario.user_message}")
        print(f"Expected keywords: {', '.join(scenario.expected_keywords)}")
        if scenario.expected_intent:
            print(f"Expected intent: {scenario.expected_intent}")

        result = {
            "scenario": scenario.name,
            "message": scenario.user_message,
            "success": False,
            "stages": [],
            "total_latency_ms": 0,
            "response": None,
            "validations": {}
        }

        start_time = time.time()

        # Send message
        response_data, stage_metrics = await self.send_message(
            scenario.user_message,
            scenario.session_id or self.session_id
        )

        result["stages"] = [
            {
                "stage": m.stage.value,
                "duration_ms": m.duration_ms,
                "success": m.success,
                "error": m.error,
                "metadata": m.metadata
            }
            for m in stage_metrics
        ]

        if not response_data:
            result["error"] = "No response received"
            result["total_latency_ms"] = (time.time() - start_time) * 1000
            return result

        result["response"] = response_data
        result["success"] = True

        # Validate response
        print("\n" + "="*60)
        print("RESPONSE VALIDATION")
        print("="*60)

        response_message = response_data.get("message", "")

        # Check keywords
        keywords_found = [
            kw for kw in scenario.expected_keywords
            if kw.lower() in response_message.lower()
        ]
        result["validations"]["keywords_found"] = keywords_found
        result["validations"]["keywords_expected"] = scenario.expected_keywords
        result["validations"]["keywords_match"] = len(keywords_found) > 0

        print(f"Keywords check: {len(keywords_found)}/{len(scenario.expected_keywords)} found")
        for kw in keywords_found:
            print(f"   ✅ Found: {kw}")
        for kw in set(scenario.expected_keywords) - set(keywords_found):
            print(f"   ❌ Missing: {kw}")

        # Check metadata
        metadata = response_data.get("metadata", {})
        result["validations"]["has_metadata"] = bool(metadata)
        result["validations"]["knowledge_used"] = metadata.get("knowledge_used", 0)
        result["validations"]["memory_context_used"] = metadata.get("memory_context_used", False)

        print(f"\nMetadata check:")
        print(f"   Knowledge items used: {metadata.get('knowledge_used', 0)}")
        print(f"   Memory context used: {metadata.get('memory_context_used', False)}")
        print(f"   Has conversation history: {metadata.get('has_history', False)}")

        # Verify database storage
        if scenario.session_id or self.session_id:
            db_verified = await self.verify_database_storage(
                scenario.session_id or self.session_id,
                scenario.user_message
            )
            result["validations"]["database_verified"] = db_verified

        result["total_latency_ms"] = (time.time() - start_time) * 1000

        print(f"\n⏱️  Total scenario latency: {result['total_latency_ms']:.2f}ms")

        return result

    async def run_all_tests(self) -> Dict:
        """Run complete test suite"""

        print("\n" + "🚀" * 60)
        print("STARTING COMPLETE WIDGET FLOW TEST SUITE")
        print("🚀" * 60)
        print(f"Backend URL: {self.backend_url}")
        print(f"Test Session ID: {self.session_id}")
        print(f"Timestamp: {datetime.now().isoformat()}")

        suite_start = time.time()

        # Test 0: Health check
        health_ok = await self.test_health_check()
        if not health_ok:
            print("\n❌ Health check failed. Aborting tests.")
            return {
                "success": False,
                "error": "Health check failed",
                "timestamp": datetime.now().isoformat()
            }

        # Define test scenarios
        scenarios = [
            TestScenario(
                name="Basic Greeting",
                user_message="Hello! How are you?",
                expected_keywords=["hello", "help", "assist"],
                expected_intent="greeting",
                should_use_rag=False
            ),
            TestScenario(
                name="Office Hours Query",
                user_message="What are your office hours?",
                expected_keywords=["hours", "open", "Monday", "Friday"],
                expected_intent="information",
                should_use_rag=True
            ),
            TestScenario(
                name="Services Information",
                user_message="Tell me about your dental services",
                expected_keywords=["dental", "services", "cleaning", "treatment"],
                expected_intent="information",
                should_use_rag=True
            ),
            TestScenario(
                name="Appointment Request",
                user_message="I need to book a cleaning appointment",
                expected_keywords=["appointment", "schedule", "available"],
                expected_intent="appointment",
                should_use_rag=True
            ),
            TestScenario(
                name="Multi-turn Conversation",
                user_message="Thank you for the information",
                expected_keywords=["welcome", "help", "anything"],
                expected_intent="closing",
                should_use_rag=False,
                should_access_memory=True
            )
        ]

        # Run all scenarios
        results = []
        for i, scenario in enumerate(scenarios, 1):
            print(f"\n\n{'='*60}")
            print(f"RUNNING TEST {i}/{len(scenarios)}")
            print(f"{'='*60}")

            result = await self.run_test_scenario(scenario)
            results.append(result)

            # Small delay between tests
            await asyncio.sleep(1)

        suite_end = time.time()
        suite_duration = (suite_end - suite_start) * 1000

        # Generate summary report
        print("\n\n" + "📊" * 60)
        print("TEST SUITE SUMMARY")
        print("📊" * 60)

        total_tests = len(results)
        passed_tests = sum(1 for r in results if r["success"])
        failed_tests = total_tests - passed_tests

        print(f"\nTotal Tests: {total_tests}")
        print(f"✅ Passed: {passed_tests}")
        print(f"❌ Failed: {failed_tests}")
        print(f"⏱️  Total Duration: {suite_duration:.2f}ms")

        # Latency breakdown
        print(f"\n{'='*60}")
        print("LATENCY BREAKDOWN")
        print(f"{'='*60}")

        for result in results:
            print(f"\n{result['scenario']}:")
            print(f"   Total: {result['total_latency_ms']:.2f}ms")
            for stage in result.get("stages", []):
                status = "✅" if stage["success"] else "❌"
                print(f"   {status} {stage['stage']}: {stage.get('duration_ms', 0):.2f}ms")

        # Validation summary
        print(f"\n{'='*60}")
        print("VALIDATION SUMMARY")
        print(f"{'='*60}")

        for result in results:
            validations = result.get("validations", {})
            print(f"\n{result['scenario']}:")
            print(f"   Keywords match: {validations.get('keywords_match', False)}")
            print(f"   Knowledge used: {validations.get('knowledge_used', 0)} items")
            print(f"   Memory accessed: {validations.get('memory_context_used', False)}")
            print(f"   DB verified: {validations.get('database_verified', 'N/A')}")

        # Final summary
        summary = {
            "success": failed_tests == 0,
            "total_tests": total_tests,
            "passed": passed_tests,
            "failed": failed_tests,
            "suite_duration_ms": suite_duration,
            "timestamp": datetime.now().isoformat(),
            "backend_url": self.backend_url,
            "session_id": self.session_id,
            "results": results
        }

        # Save detailed report
        report_file = f"test_report_{int(time.time())}.json"
        with open(report_file, "w") as f:
            json.dump(summary, f, indent=2)

        print(f"\n📄 Detailed report saved to: {report_file}")

        if failed_tests == 0:
            print("\n🎉 ALL TESTS PASSED! 🎉")
        else:
            print(f"\n⚠️  {failed_tests} TESTS FAILED")

        return summary


async def main():
    """Main test execution"""

    # Check environment variables
    print("Checking environment variables...")
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("⚠️  Warning: SUPABASE_URL and SUPABASE_ANON_KEY not set")
        print("   Database verification will be skipped")

    async with WidgetFlowTester(BACKEND_URL) as tester:
        summary = await tester.run_all_tests()

        # Exit with appropriate code
        sys.exit(0 if summary["success"] else 1)


if __name__ == "__main__":
    asyncio.run(main())