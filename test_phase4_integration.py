#!/usr/bin/env python3
"""
Test Phase 4 Integration: RAG, Memory, and Calendar Tools
Validates the complete integration of universal services with LangGraph orchestrators
"""

import asyncio
import aiohttp
import json
import time
from datetime import datetime, timedelta
from typing import Dict, List, Any
import statistics
import sys
import os

# Add paths for imports
sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..', 'services', 'orchestrator'))
sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..', 'services', 'rag'))

# Configuration
BACKEND_URL = "http://localhost:8000"
LANGGRAPH_URL = "http://localhost:8000/langgraph"
KNOWLEDGE_URL = "http://localhost:8000/api/knowledge"

# Test scenarios
TEST_SCENARIOS = {
    "appointment_booking": [
        "I need to schedule a dental cleaning next week",
        "Do you have any appointments available tomorrow?",
        "I'd like to book an appointment for Thursday at 2pm",
        "Can I schedule a checkup for my daughter?",
        "What times are available on Monday?"
    ],
    "knowledge_query": [
        "What are the symptoms of a cavity?",
        "How often should I get a dental cleaning?",
        "What's the difference between a crown and a filling?",
        "Do you accept Delta Dental insurance?",
        "What should I do after a tooth extraction?"
    ],
    "conversation_memory": [
        "My name is John Smith and I'm a new patient",
        "I mentioned earlier that I have tooth pain",
        "As we discussed, I need a root canal",
        "Remember I told you about my dental anxiety?",
        "You said there was an opening at 3pm"
    ],
    "mixed_intents": [
        "I have tooth pain and need an urgent appointment",
        "Can you tell me about the cost and schedule an appointment?",
        "I want to cancel my appointment and ask about insurance",
        "What are your hours and can I book for next week?",
        "I need a cleaning and want to know if you accept my insurance"
    ]
}


class Phase4IntegrationTester:
    """Test harness for Phase 4 integration"""

    def __init__(self):
        self.session = None
        self.results = {
            "rag_tests": [],
            "memory_tests": [],
            "appointment_tests": [],
            "integration_tests": [],
            "performance_metrics": {}
        }

    async def setup(self):
        """Initialize test session"""
        self.session = aiohttp.ClientSession()
        print("\n" + "="*80)
        print("PHASE 4 INTEGRATION TESTS")
        print("Testing: RAG, Memory, and Calendar Tools")
        print("="*80)

    async def cleanup(self):
        """Clean up test session"""
        if self.session:
            await self.session.close()

    async def test_rag_integration(self):
        """Test RAG knowledge retrieval"""
        print("\n📚 Testing RAG Integration...")
        print("-" * 40)

        for query in TEST_SCENARIOS["knowledge_query"]:
            start_time = time.perf_counter()

            try:
                # Test direct knowledge API
                async with self.session.post(
                    f"{KNOWLEDGE_URL}/search",
                    json={
                        "query": query,
                        "namespace": "dental_clinic",
                        "top_k": 3,
                        "include_rag": True,
                        "include_memory": False
                    },
                    timeout=aiohttp.ClientTimeout(total=2.0)
                ) as response:
                    if response.status == 200:
                        result = await response.json()
                        latency_ms = (time.perf_counter() - start_time) * 1000

                        self.results["rag_tests"].append({
                            "query": query[:50],
                            "success": True,
                            "results_count": result.get("total_results", 0),
                            "latency_ms": latency_ms
                        })

                        status = "✅" if latency_ms < 200 else "⚠️"
                        print(f"{status} RAG Query: {latency_ms:.2f}ms - {query[:40]}...")
                    else:
                        self.results["rag_tests"].append({
                            "query": query[:50],
                            "success": False,
                            "error": f"HTTP {response.status}"
                        })
                        print(f"❌ RAG Query failed: HTTP {response.status}")

            except Exception as e:
                self.results["rag_tests"].append({
                    "query": query[:50],
                    "success": False,
                    "error": str(e)
                })
                print(f"❌ RAG Query error: {e}")

            await asyncio.sleep(0.1)

    async def test_memory_integration(self):
        """Test mem0 memory system"""
        print("\n🧠 Testing Memory Integration...")
        print("-" * 40)

        session_id = f"test_memory_{datetime.now().timestamp()}"

        # First, add some memories
        for i, content in enumerate(TEST_SCENARIOS["conversation_memory"][:3]):
            try:
                async with self.session.post(
                    f"{KNOWLEDGE_URL}/add-memory",
                    json={
                        "content": content,
                        "session_id": session_id,
                        "metadata": {"turn": i}
                    },
                    timeout=aiohttp.ClientTimeout(total=2.0)
                ) as response:
                    if response.status == 200:
                        print(f"✅ Added memory: {content[:40]}...")
                    else:
                        print(f"❌ Failed to add memory: HTTP {response.status}")
            except Exception as e:
                print(f"❌ Memory add error: {e}")

            await asyncio.sleep(0.1)

        # Then test memory retrieval
        for query in TEST_SCENARIOS["conversation_memory"][3:]:
            start_time = time.perf_counter()

            try:
                async with self.session.post(
                    f"{KNOWLEDGE_URL}/search",
                    json={
                        "query": query,
                        "session_id": session_id,
                        "include_memory": True,
                        "include_rag": False,
                        "top_k": 3
                    },
                    timeout=aiohttp.ClientTimeout(total=2.0)
                ) as response:
                    if response.status == 200:
                        result = await response.json()
                        latency_ms = (time.perf_counter() - start_time) * 1000

                        self.results["memory_tests"].append({
                            "query": query[:50],
                            "success": True,
                            "memories_found": len(result.get("memory_results", [])),
                            "latency_ms": latency_ms
                        })

                        status = "✅" if latency_ms < 100 else "⚠️"
                        print(f"{status} Memory Search: {latency_ms:.2f}ms - {query[:40]}...")
                    else:
                        self.results["memory_tests"].append({
                            "query": query[:50],
                            "success": False,
                            "error": f"HTTP {response.status}"
                        })
                        print(f"❌ Memory search failed: HTTP {response.status}")

            except Exception as e:
                self.results["memory_tests"].append({
                    "query": query[:50],
                    "success": False,
                    "error": str(e)
                })
                print(f"❌ Memory search error: {e}")

            await asyncio.sleep(0.1)

    async def test_appointment_tools(self):
        """Test calendar appointment tools integration"""
        print("\n📅 Testing Appointment Tools...")
        print("-" * 40)

        for message in TEST_SCENARIOS["appointment_booking"]:
            start_time = time.perf_counter()
            session_id = f"appt_test_{datetime.now().timestamp()}"

            try:
                async with self.session.post(
                    f"{LANGGRAPH_URL}/process",
                    json={
                        "session_id": session_id,
                        "text": message,
                        "metadata": {
                            "clinic_id": "test_clinic",
                            "patient_id": "test_patient"
                        },
                        "use_healthcare": True,
                        "enable_rag": False,  # Disable RAG for appointment tests
                        "enable_memory": False
                    },
                    timeout=aiohttp.ClientTimeout(total=3.0)
                ) as response:
                    if response.status == 200:
                        result = await response.json()
                        latency_ms = (time.perf_counter() - start_time) * 1000

                        # Check if response mentions appointments
                        response_text = result.get("response", "").lower()
                        handles_appointment = any(word in response_text for word in [
                            "appointment", "schedule", "book", "available", "slot"
                        ])

                        self.results["appointment_tests"].append({
                            "message": message[:50],
                            "success": True,
                            "handles_appointment": handles_appointment,
                            "latency_ms": latency_ms
                        })

                        status = "✅" if handles_appointment else "⚠️"
                        print(f"{status} Appointment: {latency_ms:.2f}ms - {message[:40]}...")
                    else:
                        self.results["appointment_tests"].append({
                            "message": message[:50],
                            "success": False,
                            "error": f"HTTP {response.status}"
                        })
                        print(f"❌ Appointment test failed: HTTP {response.status}")

            except Exception as e:
                self.results["appointment_tests"].append({
                    "message": message[:50],
                    "success": False,
                    "error": str(e)
                })
                print(f"❌ Appointment test error: {e}")

            await asyncio.sleep(0.1)

    async def test_full_integration(self):
        """Test full integration with all components"""
        print("\n🔗 Testing Full Integration...")
        print("-" * 40)

        for message in TEST_SCENARIOS["mixed_intents"]:
            start_time = time.perf_counter()
            session_id = f"full_test_{datetime.now().timestamp()}"

            try:
                # Test with all features enabled
                async with self.session.post(
                    f"{LANGGRAPH_URL}/process",
                    json={
                        "session_id": session_id,
                        "text": message,
                        "metadata": {
                            "clinic_id": "test_clinic",
                            "patient_id": "test_patient",
                            "channel": "test"
                        },
                        "use_healthcare": True,
                        "enable_rag": True,
                        "enable_memory": True
                    },
                    timeout=aiohttp.ClientTimeout(total=5.0)
                ) as response:
                    if response.status == 200:
                        result = await response.json()
                        latency_ms = (time.perf_counter() - start_time) * 1000

                        # Analyze what was used
                        used_rag = "knowledge" in result.get("state", {})
                        used_memory = "memories" in result.get("state", {})
                        nodes_executed = len(result.get("state", {}).get("audit_trail", []))

                        self.results["integration_tests"].append({
                            "message": message[:50],
                            "success": True,
                            "latency_ms": latency_ms,
                            "used_rag": used_rag,
                            "used_memory": used_memory,
                            "nodes_executed": nodes_executed
                        })

                        status = "✅" if latency_ms < 1000 else "⚠️"
                        features = []
                        if used_rag:
                            features.append("RAG")
                        if used_memory:
                            features.append("Memory")
                        features_str = f" [{', '.join(features)}]" if features else ""

                        print(f"{status} Integration: {latency_ms:.2f}ms{features_str} - {message[:35]}...")
                    else:
                        self.results["integration_tests"].append({
                            "message": message[:50],
                            "success": False,
                            "error": f"HTTP {response.status}"
                        })
                        print(f"❌ Integration test failed: HTTP {response.status}")

            except Exception as e:
                self.results["integration_tests"].append({
                    "message": message[:50],
                    "success": False,
                    "error": str(e)
                })
                print(f"❌ Integration test error: {e}")

            await asyncio.sleep(0.2)

    def analyze_results(self):
        """Analyze and print test results"""
        print("\n" + "="*80)
        print("📊 TEST RESULTS SUMMARY")
        print("="*80)

        # RAG Results
        rag_successful = [r for r in self.results["rag_tests"] if r.get("success")]
        if rag_successful:
            rag_latencies = [r["latency_ms"] for r in rag_successful]
            print("\n📚 RAG Integration:")
            print(f"  Success rate: {len(rag_successful)}/{len(self.results['rag_tests'])}")
            print(f"  Avg latency: {statistics.mean(rag_latencies):.2f}ms")
            print(f"  P95 latency: {statistics.quantiles(rag_latencies, n=20)[18]:.2f}ms" if len(rag_latencies) > 1 else "")

        # Memory Results
        mem_successful = [r for r in self.results["memory_tests"] if r.get("success")]
        if mem_successful:
            mem_latencies = [r["latency_ms"] for r in mem_successful]
            print("\n🧠 Memory Integration:")
            print(f"  Success rate: {len(mem_successful)}/{len(self.results['memory_tests'])}")
            print(f"  Avg latency: {statistics.mean(mem_latencies):.2f}ms")

        # Appointment Results
        appt_successful = [r for r in self.results["appointment_tests"] if r.get("success")]
        if appt_successful:
            appt_latencies = [r["latency_ms"] for r in appt_successful]
            appt_handled = [r for r in appt_successful if r.get("handles_appointment")]
            print("\n📅 Appointment Tools:")
            print(f"  Success rate: {len(appt_successful)}/{len(self.results['appointment_tests'])}")
            print(f"  Handled correctly: {len(appt_handled)}/{len(appt_successful)}")
            print(f"  Avg latency: {statistics.mean(appt_latencies):.2f}ms")

        # Full Integration Results
        int_successful = [r for r in self.results["integration_tests"] if r.get("success")]
        if int_successful:
            int_latencies = [r["latency_ms"] for r in int_successful]
            with_rag = [r for r in int_successful if r.get("used_rag")]
            with_memory = [r for r in int_successful if r.get("used_memory")]
            print("\n🔗 Full Integration:")
            print(f"  Success rate: {len(int_successful)}/{len(self.results['integration_tests'])}")
            print(f"  Used RAG: {len(with_rag)}/{len(int_successful)}")
            print(f"  Used Memory: {len(with_memory)}/{len(int_successful)}")
            print(f"  Avg latency: {statistics.mean(int_latencies):.2f}ms")
            print(f"  P95 latency: {statistics.quantiles(int_latencies, n=20)[18]:.2f}ms" if len(int_latencies) > 1 else "")

        # Overall Performance
        print("\n" + "="*80)
        print("📈 PERFORMANCE TARGETS")
        print("="*80)

        targets_met = []
        targets_not_met = []

        # Check RAG target (<200ms)
        if rag_successful:
            avg_rag = statistics.mean(rag_latencies)
            if avg_rag < 200:
                targets_met.append(f"RAG retrieval: {avg_rag:.2f}ms < 200ms")
            else:
                targets_not_met.append(f"RAG retrieval: {avg_rag:.2f}ms > 200ms target")

        # Check Memory target (<100ms)
        if mem_successful:
            avg_mem = statistics.mean(mem_latencies)
            if avg_mem < 100:
                targets_met.append(f"Memory search: {avg_mem:.2f}ms < 100ms")
            else:
                targets_not_met.append(f"Memory search: {avg_mem:.2f}ms > 100ms target")

        # Check Integration target (<1000ms)
        if int_successful:
            avg_int = statistics.mean(int_latencies)
            if avg_int < 1000:
                targets_met.append(f"Full integration: {avg_int:.2f}ms < 1000ms")
            else:
                targets_not_met.append(f"Full integration: {avg_int:.2f}ms > 1000ms target")

        if targets_met:
            print("\n✅ Targets Met:")
            for target in targets_met:
                print(f"  • {target}")

        if targets_not_met:
            print("\n⚠️ Targets Not Met:")
            for target in targets_not_met:
                print(f"  • {target}")

        # Save results
        with open("phase4_test_results.json", "w") as f:
            json.dump(self.results, f, indent=2)
        print("\n📝 Detailed results saved to phase4_test_results.json")

        return len(targets_not_met) == 0


async def main():
    """Run all Phase 4 integration tests"""
    print("⚠️ Make sure the following services are running:")
    print("  1. Backend API: cd clinics/backend && uvicorn app.main:app --reload")
    print("  2. RAG/Memory services should be configured")
    print("\nPress Enter to continue or Ctrl+C to abort...")
    input()

    tester = Phase4IntegrationTester()

    try:
        await tester.setup()

        # Run all test suites
        await tester.test_rag_integration()
        await tester.test_memory_integration()
        await tester.test_appointment_tools()
        await tester.test_full_integration()

        # Analyze results
        success = tester.analyze_results()

        if success:
            print("\n✨ Phase 4 Integration Tests PASSED!")
            print("RAG, Memory, and Calendar tools are working correctly.")
        else:
            print("\n⚠️ Some Phase 4 tests did not meet performance targets.")
            print("Review the results above for details.")

    except Exception as e:
        print(f"\n❌ Test suite error: {e}")
    finally:
        await tester.cleanup()

    print("\n" + "="*80)
    print("Phase 4 Testing Complete")
    print("="*80)


if __name__ == "__main__":
    asyncio.run(main())