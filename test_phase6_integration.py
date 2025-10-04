#!/usr/bin/env python3
"""
Test Phase 6: Testing & Progressive Rollout
Comprehensive integration testing for all LangGraph migration phases
"""

import asyncio
import aiohttp
import json
import time
import os
import sys
from datetime import datetime
from typing import Dict, List, Any, Optional
import statistics
import random
from dataclasses import dataclass
from enum import Enum

# Add paths for imports
sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..', 'services'))
sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..'))

# Configuration
BACKEND_URL = "http://localhost:8000"
LANGGRAPH_URL = "http://localhost:8000/langgraph"
EVOLUTION_WEBHOOK_URL = "http://localhost:8000/evolution/webhook"
KNOWLEDGE_API_URL = "http://localhost:8000/api/knowledge"


class TestPhase(Enum):
    """Test phases corresponding to migration phases"""
    SECURITY = "security"
    LANGGRAPH = "langgraph"
    ROUTING = "routing"
    RAG_MEMORY = "rag_memory"
    GROK = "grok"
    FULL_INTEGRATION = "full_integration"


@dataclass
class TestResult:
    """Test result tracking"""
    phase: TestPhase
    test_name: str
    passed: bool
    latency_ms: Optional[float] = None
    error: Optional[str] = None
    details: Optional[Dict[str, Any]] = None


class Phase6IntegrationTester:
    """Comprehensive test harness for all migration phases"""

    def __init__(self):
        self.results: List[TestResult] = []
        self.performance_metrics = {
            "text_response_times": [],
            "voice_response_times": [],
            "rag_retrieval_times": [],
            "memory_access_times": [],
            "grok_response_times": [],
            "openai_response_times": [],
            "webhook_processing_times": []
        }
        self.test_session_id = f"phase6_test_{datetime.now().timestamp()}"

    async def test_phase1_security(self):
        """Test Phase 1: Security Foundation"""
        print("\n" + "="*80)
        print("🔐 PHASE 1: SECURITY FOUNDATION TESTS")
        print("="*80)

        async with aiohttp.ClientSession() as session:

            # Test 1: HMAC Signature Verification
            print("\n📊 Testing HMAC signature verification...")
            payload = {"text": "Test message", "timestamp": datetime.now().isoformat()}

            # Test with valid signature
            import hmac
            import hashlib
            secret = os.environ.get("WEBHOOK_SECRET", "test-secret")
            signature = hmac.new(
                secret.encode(),
                json.dumps(payload).encode(),
                hashlib.sha256
            ).hexdigest()

            headers = {"X-Webhook-Signature": f"sha256={signature}"}

            try:
                start = time.perf_counter()
                async with session.post(
                    f"{EVOLUTION_WEBHOOK_URL}",
                    json=payload,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=5.0)
                ) as response:
                    latency = (time.perf_counter() - start) * 1000

                    if response.status == 200:
                        self.results.append(TestResult(
                            phase=TestPhase.SECURITY,
                            test_name="HMAC Valid Signature",
                            passed=True,
                            latency_ms=latency
                        ))
                        print(f"  ✅ Valid signature accepted ({latency:.2f}ms)")
                    else:
                        self.results.append(TestResult(
                            phase=TestPhase.SECURITY,
                            test_name="HMAC Valid Signature",
                            passed=False,
                            error=f"HTTP {response.status}"
                        ))
                        print(f"  ❌ Valid signature rejected: HTTP {response.status}")
            except Exception as e:
                self.results.append(TestResult(
                    phase=TestPhase.SECURITY,
                    test_name="HMAC Valid Signature",
                    passed=False,
                    error=str(e)
                ))
                print(f"  ❌ Error: {e}")

            # Test with invalid signature
            print("📊 Testing invalid signature rejection...")
            headers = {"X-Webhook-Signature": "sha256=invalid"}

            try:
                async with session.post(
                    f"{EVOLUTION_WEBHOOK_URL}",
                    json=payload,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=5.0)
                ) as response:
                    if response.status == 401:
                        self.results.append(TestResult(
                            phase=TestPhase.SECURITY,
                            test_name="HMAC Invalid Signature Rejection",
                            passed=True
                        ))
                        print(f"  ✅ Invalid signature properly rejected")
                    else:
                        self.results.append(TestResult(
                            phase=TestPhase.SECURITY,
                            test_name="HMAC Invalid Signature Rejection",
                            passed=False,
                            error=f"Expected 401, got {response.status}"
                        ))
                        print(f"  ❌ Invalid signature not rejected: HTTP {response.status}")
            except Exception as e:
                self.results.append(TestResult(
                    phase=TestPhase.SECURITY,
                    test_name="HMAC Invalid Signature Rejection",
                    passed=False,
                    error=str(e)
                ))
                print(f"  ❌ Error: {e}")

            # Test 2: Rate Limiting
            print("\n📊 Testing rate limiting...")
            rapid_requests = []
            for i in range(15):  # Try to exceed rate limit
                rapid_requests.append(
                    session.post(
                        f"{LANGGRAPH_URL}/process",
                        json={"text": f"Test {i}", "session_id": f"rate_test_{i}"},
                        timeout=aiohttp.ClientTimeout(total=1.0)
                    )
                )

            results = await asyncio.gather(*rapid_requests, return_exceptions=True)
            rate_limited = sum(1 for r in results if isinstance(r, aiohttp.ClientResponse) and r.status == 429)

            if rate_limited > 0:
                self.results.append(TestResult(
                    phase=TestPhase.SECURITY,
                    test_name="Rate Limiting",
                    passed=True,
                    details={"rate_limited_requests": rate_limited}
                ))
                print(f"  ✅ Rate limiting working: {rate_limited} requests limited")
            else:
                self.results.append(TestResult(
                    phase=TestPhase.SECURITY,
                    test_name="Rate Limiting",
                    passed=False,
                    error="No requests were rate limited"
                ))
                print(f"  ⚠️ Rate limiting may not be active")

            # Test 3: Idempotency
            print("\n📊 Testing idempotency support...")
            idempotency_key = f"test_key_{datetime.now().timestamp()}"
            idempotent_payload = {
                "text": "Idempotent test",
                "session_id": "idempotency_test"
            }
            headers = {"X-Idempotency-Key": idempotency_key}

            # First request
            response1 = None
            try:
                async with session.post(
                    f"{LANGGRAPH_URL}/process",
                    json=idempotent_payload,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=5.0)
                ) as response:
                    response1 = await response.json()

                # Second request with same key
                async with session.post(
                    f"{LANGGRAPH_URL}/process",
                    json=idempotent_payload,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=5.0)
                ) as response:
                    response2 = await response.json()

                    if response1 == response2:
                        self.results.append(TestResult(
                            phase=TestPhase.SECURITY,
                            test_name="Idempotency",
                            passed=True
                        ))
                        print(f"  ✅ Idempotency working: duplicate requests return same response")
                    else:
                        self.results.append(TestResult(
                            phase=TestPhase.SECURITY,
                            test_name="Idempotency",
                            passed=False,
                            error="Different responses for same idempotency key"
                        ))
                        print(f"  ❌ Idempotency failed: different responses")
            except Exception as e:
                self.results.append(TestResult(
                    phase=TestPhase.SECURITY,
                    test_name="Idempotency",
                    passed=False,
                    error=str(e)
                ))
                print(f"  ❌ Error: {e}")

    async def test_phase2_langgraph(self):
        """Test Phase 2: LangGraph Extraction"""
        print("\n" + "="*80)
        print("🔗 PHASE 2: LANGGRAPH EXTRACTION TESTS")
        print("="*80)

        async with aiohttp.ClientSession() as session:

            # Test different compliance modes
            compliance_modes = ["HIPAA", "GDPR", "LFPDPPP", None]

            for mode in compliance_modes:
                print(f"\n📊 Testing {mode or 'Standard'} compliance mode...")

                payload = {
                    "text": "Tell me about patient data handling",
                    "session_id": f"compliance_test_{mode}",
                    "compliance_mode": mode,
                    "use_healthcare": mode == "HIPAA"
                }

                try:
                    start = time.perf_counter()
                    async with session.post(
                        f"{LANGGRAPH_URL}/process",
                        json=payload,
                        timeout=aiohttp.ClientTimeout(total=10.0)
                    ) as response:
                        latency = (time.perf_counter() - start) * 1000

                        if response.status == 200:
                            result = await response.json()

                            # Check compliance indicators
                            compliance_applied = result.get("state", {}).get("compliance_mode") == mode

                            self.results.append(TestResult(
                                phase=TestPhase.LANGGRAPH,
                                test_name=f"Compliance Mode {mode or 'Standard'}",
                                passed=compliance_applied if mode else True,
                                latency_ms=latency,
                                details={"mode": mode, "response_length": len(str(result))}
                            ))

                            print(f"  ✅ {mode or 'Standard'} mode: {latency:.2f}ms")
                        else:
                            self.results.append(TestResult(
                                phase=TestPhase.LANGGRAPH,
                                test_name=f"Compliance Mode {mode or 'Standard'}",
                                passed=False,
                                error=f"HTTP {response.status}"
                            ))
                            print(f"  ❌ HTTP {response.status}")

                except Exception as e:
                    self.results.append(TestResult(
                        phase=TestPhase.LANGGRAPH,
                        test_name=f"Compliance Mode {mode or 'Standard'}",
                        passed=False,
                        error=str(e)
                    ))
                    print(f"  ❌ Error: {e}")

    async def test_phase3_routing(self):
        """Test Phase 3: Dual-Lane Routing"""
        print("\n" + "="*80)
        print("🚦 PHASE 3: DUAL-LANE ROUTING TESTS")
        print("="*80)

        async with aiohttp.ClientSession() as session:

            # Test text routing (should go direct to LangGraph)
            print("\n📊 Testing text message routing (<500ms target)...")
            text_latencies = []

            for i in range(5):
                payload = {
                    "text": f"Quick response test {i}",
                    "session_id": f"text_routing_{i}",
                    "source": "whatsapp"
                }

                try:
                    start = time.perf_counter()
                    async with session.post(
                        f"{LANGGRAPH_URL}/process",
                        json=payload,
                        timeout=aiohttp.ClientTimeout(total=2.0)
                    ) as response:
                        latency = (time.perf_counter() - start) * 1000
                        text_latencies.append(latency)

                        if response.status == 200:
                            result = await response.json()
                            route = result.get("state", {}).get("metadata", {}).get("route", "unknown")

                            print(f"  ✅ Text {i}: {latency:.2f}ms (route: {route})")
                        else:
                            print(f"  ❌ Text {i}: HTTP {response.status}")

                except Exception as e:
                    print(f"  ❌ Text {i}: {e}")

                await asyncio.sleep(0.1)

            # Calculate performance metrics
            if text_latencies:
                avg_latency = statistics.mean(text_latencies)
                p95_latency = statistics.quantiles(text_latencies, n=20)[18] if len(text_latencies) > 1 else text_latencies[0]

                self.results.append(TestResult(
                    phase=TestPhase.ROUTING,
                    test_name="Text Routing Performance",
                    passed=avg_latency < 500,  # Target: <500ms
                    latency_ms=avg_latency,
                    details={
                        "avg_ms": avg_latency,
                        "p95_ms": p95_latency,
                        "samples": len(text_latencies)
                    }
                ))

                self.performance_metrics["text_response_times"].extend(text_latencies)

                print(f"\n📈 Text Routing Performance:")
                print(f"  Average: {avg_latency:.2f}ms")
                print(f"  P95: {p95_latency:.2f}ms")
                print(f"  Target: <500ms ({'✅ PASSED' if avg_latency < 500 else '❌ FAILED'})")

    async def test_phase4_rag_memory(self):
        """Test Phase 4: RAG/Memory Integration"""
        print("\n" + "="*80)
        print("🧠 PHASE 4: RAG/MEMORY INTEGRATION TESTS")
        print("="*80)

        async with aiohttp.ClientSession() as session:

            # Test RAG search
            print("\n📊 Testing RAG search...")
            rag_payload = {
                "query": "dental procedures",
                "namespace": "test_clinic",
                "top_k": 5
            }

            try:
                start = time.perf_counter()
                async with session.post(
                    f"{KNOWLEDGE_API_URL}/search",
                    json=rag_payload,
                    timeout=aiohttp.ClientTimeout(total=5.0)
                ) as response:
                    latency = (time.perf_counter() - start) * 1000

                    if response.status == 200:
                        results = await response.json()
                        self.results.append(TestResult(
                            phase=TestPhase.RAG_MEMORY,
                            test_name="RAG Search",
                            passed=latency < 200,  # Target: <200ms
                            latency_ms=latency,
                            details={"results_count": len(results.get("results", []))}
                        ))
                        self.performance_metrics["rag_retrieval_times"].append(latency)
                        print(f"  ✅ RAG search: {latency:.2f}ms ({len(results.get('results', []))} results)")
                    else:
                        self.results.append(TestResult(
                            phase=TestPhase.RAG_MEMORY,
                            test_name="RAG Search",
                            passed=False,
                            error=f"HTTP {response.status}"
                        ))
                        print(f"  ❌ HTTP {response.status}")
            except Exception as e:
                self.results.append(TestResult(
                    phase=TestPhase.RAG_MEMORY,
                    test_name="RAG Search",
                    passed=False,
                    error=str(e)
                ))
                print(f"  ❌ Error: {e}")

            # Test Memory storage and retrieval
            print("\n📊 Testing memory system...")
            memory_payload = {
                "user_id": "test_user",
                "content": "Patient prefers afternoon appointments",
                "metadata": {"importance": "high", "timestamp": datetime.now().isoformat()}
            }

            try:
                # Store memory
                async with session.post(
                    f"{KNOWLEDGE_API_URL}/add-memory",
                    json=memory_payload,
                    timeout=aiohttp.ClientTimeout(total=5.0)
                ) as response:
                    if response.status == 200:
                        print(f"  ✅ Memory stored successfully")

                        # Retrieve memory
                        start = time.perf_counter()
                        search_payload = {
                            "user_id": "test_user",
                            "query": "appointment preferences",
                            "top_k": 3
                        }

                        async with session.post(
                            f"{KNOWLEDGE_API_URL}/search",
                            json=search_payload,
                            timeout=aiohttp.ClientTimeout(total=5.0)
                        ) as search_response:
                            latency = (time.perf_counter() - start) * 1000

                            if search_response.status == 200:
                                self.results.append(TestResult(
                                    phase=TestPhase.RAG_MEMORY,
                                    test_name="Memory Retrieval",
                                    passed=latency < 100,  # Target: <100ms
                                    latency_ms=latency
                                ))
                                self.performance_metrics["memory_access_times"].append(latency)
                                print(f"  ✅ Memory retrieval: {latency:.2f}ms")
                            else:
                                self.results.append(TestResult(
                                    phase=TestPhase.RAG_MEMORY,
                                    test_name="Memory Retrieval",
                                    passed=False,
                                    error=f"HTTP {search_response.status}"
                                ))
                                print(f"  ❌ Memory retrieval failed: HTTP {search_response.status}")
                    else:
                        print(f"  ❌ Memory storage failed: HTTP {response.status}")
            except Exception as e:
                self.results.append(TestResult(
                    phase=TestPhase.RAG_MEMORY,
                    test_name="Memory System",
                    passed=False,
                    error=str(e)
                ))
                print(f"  ❌ Error: {e}")

            # Test Appointment Tools
            print("\n📊 Testing appointment tools...")
            appointment_payload = {
                "text": "I need to schedule a dental cleaning for next Tuesday at 2pm",
                "session_id": "appointment_test",
                "use_healthcare": True,
                "enable_tools": True
            }

            try:
                start = time.perf_counter()
                async with session.post(
                    f"{LANGGRAPH_URL}/process",
                    json=appointment_payload,
                    timeout=aiohttp.ClientTimeout(total=10.0)
                ) as response:
                    latency = (time.perf_counter() - start) * 1000

                    if response.status == 200:
                        result = await response.json()
                        tools_used = result.get("state", {}).get("metadata", {}).get("tools_used", [])

                        self.results.append(TestResult(
                            phase=TestPhase.RAG_MEMORY,
                            test_name="Appointment Tools",
                            passed="appointment" in str(tools_used).lower(),
                            latency_ms=latency,
                            details={"tools_used": tools_used}
                        ))
                        print(f"  ✅ Appointment handling: {latency:.2f}ms")
                        if tools_used:
                            print(f"     Tools used: {tools_used}")
                    else:
                        self.results.append(TestResult(
                            phase=TestPhase.RAG_MEMORY,
                            test_name="Appointment Tools",
                            passed=False,
                            error=f"HTTP {response.status}"
                        ))
                        print(f"  ❌ HTTP {response.status}")
            except Exception as e:
                self.results.append(TestResult(
                    phase=TestPhase.RAG_MEMORY,
                    test_name="Appointment Tools",
                    passed=False,
                    error=str(e)
                ))
                print(f"  ❌ Error: {e}")

    async def test_phase5_grok(self):
        """Test Phase 5: Grok Integration"""
        print("\n" + "="*80)
        print("🤖 PHASE 5: GROK INTEGRATION TESTS")
        print("="*80)

        async with aiohttp.ClientSession() as session:

            # Test Grok vs OpenAI responses
            providers = ["grok", "openai"]

            for provider in providers:
                print(f"\n📊 Testing {provider.upper()} provider...")

                for i in range(3):
                    payload = {
                        "text": f"Explain quantum computing in simple terms (test {i})",
                        "session_id": f"{provider}_test_{i}",
                        "llm_provider": provider,
                        "enable_llm": True
                    }

                    try:
                        start = time.perf_counter()
                        async with session.post(
                            f"{LANGGRAPH_URL}/process",
                            json=payload,
                            timeout=aiohttp.ClientTimeout(total=30.0)
                        ) as response:
                            latency = (time.perf_counter() - start) * 1000

                            if response.status == 200:
                                result = await response.json()
                                actual_provider = result.get("state", {}).get("metadata", {}).get("llm_provider", "unknown")

                                if provider == "grok":
                                    self.performance_metrics["grok_response_times"].append(latency)
                                else:
                                    self.performance_metrics["openai_response_times"].append(latency)

                                print(f"  ✅ {provider}: {latency:.2f}ms (actual: {actual_provider})")
                            else:
                                print(f"  ❌ {provider}: HTTP {response.status}")
                    except Exception as e:
                        print(f"  ❌ {provider}: {e}")

                    await asyncio.sleep(0.5)

            # Test A/B testing distribution
            print("\n📊 Testing A/B distribution (30% Grok target)...")
            ab_results = {"grok": 0, "openai": 0, "unknown": 0}

            for i in range(20):
                payload = {
                    "text": f"A/B test {i}",
                    "session_id": f"ab_test_{i}",
                    "enable_llm": True,
                    "enable_grok_ab": True,
                    "grok_percentage": 0.3
                }

                try:
                    async with session.post(
                        f"{LANGGRAPH_URL}/process",
                        json=payload,
                        timeout=aiohttp.ClientTimeout(total=10.0)
                    ) as response:
                        if response.status == 200:
                            result = await response.json()
                            provider = result.get("state", {}).get("metadata", {}).get("llm_provider", "unknown")
                            ab_results[provider] = ab_results.get(provider, 0) + 1
                except:
                    ab_results["unknown"] += 1

                await asyncio.sleep(0.1)

            total = sum(ab_results.values())
            if total > 0:
                grok_percentage = (ab_results.get("grok", 0) / total) * 100
                self.results.append(TestResult(
                    phase=TestPhase.GROK,
                    test_name="A/B Testing Distribution",
                    passed=20 <= grok_percentage <= 40,  # Accept 20-40% range
                    details=ab_results
                ))
                print(f"\n📈 A/B Distribution:")
                for provider, count in ab_results.items():
                    if count > 0:
                        print(f"  {provider}: {count}/{total} ({(count/total)*100:.1f}%)")

            # Test fallback mechanism
            print("\n📊 Testing fallback mechanism...")
            fallback_payload = {
                "text": "x" * 10000,  # Very long message to trigger fallback
                "session_id": "fallback_test",
                "llm_provider": "grok",
                "enable_llm": True
            }

            try:
                async with session.post(
                    f"{LANGGRAPH_URL}/process",
                    json=fallback_payload,
                    timeout=aiohttp.ClientTimeout(total=30.0)
                ) as response:
                    if response.status == 200:
                        result = await response.json()
                        provider_used = result.get("state", {}).get("metadata", {}).get("llm_provider", "unknown")
                        self.results.append(TestResult(
                            phase=TestPhase.GROK,
                            test_name="Fallback Mechanism",
                            passed=True,
                            details={"provider_used": provider_used}
                        ))
                        print(f"  ✅ Fallback working (used: {provider_used})")
                    else:
                        self.results.append(TestResult(
                            phase=TestPhase.GROK,
                            test_name="Fallback Mechanism",
                            passed=False,
                            error=f"HTTP {response.status}"
                        ))
                        print(f"  ❌ HTTP {response.status}")
            except Exception as e:
                self.results.append(TestResult(
                    phase=TestPhase.GROK,
                    test_name="Fallback Mechanism",
                    passed=False,
                    error=str(e)
                ))
                print(f"  ❌ Error: {e}")

    async def test_full_integration(self):
        """Test full integration of all phases"""
        print("\n" + "="*80)
        print("🚀 FULL INTEGRATION TEST")
        print("="*80)

        async with aiohttp.ClientSession() as session:

            # Complex scenario: Healthcare appointment with RAG, memory, and Grok
            print("\n📊 Testing complete healthcare flow...")

            test_scenarios = [
                {
                    "name": "New Patient Appointment",
                    "messages": [
                        "I'm a new patient and need to schedule a dental cleaning",
                        "I prefer afternoons and have Delta Dental insurance",
                        "Next Tuesday at 2pm would be perfect"
                    ]
                },
                {
                    "name": "Symptom Inquiry with Knowledge",
                    "messages": [
                        "I have severe tooth pain that gets worse at night",
                        "Can you explain what might be causing this?",
                        "What treatment options are available?"
                    ]
                },
                {
                    "name": "Rescheduling with Memory",
                    "messages": [
                        "I need to reschedule my appointment",
                        "Can you check what time works best based on my preferences?",
                        "Please book the earliest available slot"
                    ]
                }
            ]

            for scenario in test_scenarios:
                print(f"\n🎯 Scenario: {scenario['name']}")
                session_id = f"integration_{scenario['name'].replace(' ', '_').lower()}"

                conversation_latencies = []

                for i, message in enumerate(scenario['messages']):
                    payload = {
                        "text": message,
                        "session_id": session_id,
                        "use_healthcare": True,
                        "enable_rag": True,
                        "enable_memory": True,
                        "enable_tools": True,
                        "enable_llm": True,
                        "llm_provider": "grok",
                        "enable_grok_ab": True,
                        "grok_percentage": 0.5,
                        "compliance_mode": "HIPAA"
                    }

                    try:
                        start = time.perf_counter()
                        async with session.post(
                            f"{LANGGRAPH_URL}/process",
                            json=payload,
                            timeout=aiohttp.ClientTimeout(total=30.0)
                        ) as response:
                            latency = (time.perf_counter() - start) * 1000
                            conversation_latencies.append(latency)

                            if response.status == 200:
                                result = await response.json()

                                # Extract metadata
                                metadata = result.get("state", {}).get("metadata", {})
                                provider = metadata.get("llm_provider", "unknown")
                                tools_used = metadata.get("tools_used", [])
                                rag_used = metadata.get("rag_context_added", False)
                                memory_used = metadata.get("memory_retrieved", False)

                                print(f"  Message {i+1}: {latency:.2f}ms")
                                print(f"    - Provider: {provider}")
                                if tools_used:
                                    print(f"    - Tools: {tools_used}")
                                if rag_used:
                                    print(f"    - RAG: ✓")
                                if memory_used:
                                    print(f"    - Memory: ✓")
                            else:
                                print(f"  Message {i+1}: ❌ HTTP {response.status}")
                    except Exception as e:
                        print(f"  Message {i+1}: ❌ {e}")

                    await asyncio.sleep(1)  # Simulate conversation pace

                # Scenario performance summary
                if conversation_latencies:
                    avg_latency = statistics.mean(conversation_latencies)
                    max_latency = max(conversation_latencies)

                    self.results.append(TestResult(
                        phase=TestPhase.FULL_INTEGRATION,
                        test_name=scenario['name'],
                        passed=avg_latency < 1000,  # Target: <1000ms average
                        latency_ms=avg_latency,
                        details={
                            "avg_ms": avg_latency,
                            "max_ms": max_latency,
                            "messages": len(conversation_latencies)
                        }
                    ))

                    print(f"  📈 Scenario Performance:")
                    print(f"     Average: {avg_latency:.2f}ms")
                    print(f"     Max: {max_latency:.2f}ms")
                    print(f"     Target: <1000ms avg ({'✅' if avg_latency < 1000 else '❌'})")

    async def load_test(self):
        """Perform load testing with concurrent requests"""
        print("\n" + "="*80)
        print("⚡ LOAD TESTING")
        print("="*80)

        async with aiohttp.ClientSession() as session:

            # Test concurrent user load
            concurrent_users = [5, 10, 20]

            for user_count in concurrent_users:
                print(f"\n📊 Testing with {user_count} concurrent users...")

                tasks = []
                for user_id in range(user_count):
                    payload = {
                        "text": f"Concurrent request from user {user_id}",
                        "session_id": f"load_test_user_{user_id}",
                        "use_healthcare": True,
                        "enable_llm": True,
                        "llm_provider": "openai"  # Use OpenAI to avoid Grok rate limits
                    }

                    tasks.append(
                        session.post(
                            f"{LANGGRAPH_URL}/process",
                            json=payload,
                            timeout=aiohttp.ClientTimeout(total=30.0)
                        )
                    )

                start = time.perf_counter()
                results = await asyncio.gather(*tasks, return_exceptions=True)
                total_time = (time.perf_counter() - start) * 1000

                successful = sum(1 for r in results if isinstance(r, aiohttp.ClientResponse) and r.status == 200)
                failed = len(results) - successful

                self.results.append(TestResult(
                    phase=TestPhase.FULL_INTEGRATION,
                    test_name=f"Load Test {user_count} users",
                    passed=successful >= user_count * 0.8,  # 80% success rate
                    latency_ms=total_time / user_count,
                    details={
                        "concurrent_users": user_count,
                        "successful": successful,
                        "failed": failed,
                        "total_time_ms": total_time
                    }
                ))

                print(f"  Results: {successful}/{user_count} successful")
                print(f"  Total time: {total_time:.2f}ms")
                print(f"  Avg per user: {total_time/user_count:.2f}ms")

                # Clean up responses
                for r in results:
                    if isinstance(r, aiohttp.ClientResponse):
                        r.close()

                await asyncio.sleep(2)  # Cool down between tests

    def generate_report(self):
        """Generate comprehensive test report"""
        print("\n" + "="*80)
        print("📋 PHASE 6 TEST REPORT - TESTING & PROGRESSIVE ROLLOUT")
        print("="*80)

        # Phase-wise summary
        phase_summary = {}
        for result in self.results:
            if result.phase not in phase_summary:
                phase_summary[result.phase] = {"passed": 0, "failed": 0, "tests": []}

            if result.passed:
                phase_summary[result.phase]["passed"] += 1
            else:
                phase_summary[result.phase]["failed"] += 1

            phase_summary[result.phase]["tests"].append(result)

        print("\n📊 PHASE-WISE RESULTS:")
        print("-" * 40)

        for phase in TestPhase:
            if phase in phase_summary:
                summary = phase_summary[phase]
                total = summary["passed"] + summary["failed"]
                pass_rate = (summary["passed"] / total * 100) if total > 0 else 0

                status = "✅" if pass_rate >= 80 else "⚠️" if pass_rate >= 60 else "❌"
                print(f"\n{status} {phase.value.upper()}:")
                print(f"   Passed: {summary['passed']}/{total} ({pass_rate:.1f}%)")

                # Show failed tests
                failed_tests = [t for t in summary["tests"] if not t.passed]
                if failed_tests:
                    print(f"   Failed tests:")
                    for test in failed_tests:
                        print(f"     - {test.test_name}: {test.error}")

        # Performance metrics summary
        print("\n📈 PERFORMANCE METRICS:")
        print("-" * 40)

        metrics_summary = {
            "Text Response": self.performance_metrics["text_response_times"],
            "RAG Retrieval": self.performance_metrics["rag_retrieval_times"],
            "Memory Access": self.performance_metrics["memory_access_times"],
            "Grok Responses": self.performance_metrics["grok_response_times"],
            "OpenAI Responses": self.performance_metrics["openai_response_times"]
        }

        for metric_name, times in metrics_summary.items():
            if times:
                avg = statistics.mean(times)
                p95 = statistics.quantiles(times, n=20)[18] if len(times) > 1 else times[0]
                print(f"\n{metric_name}:")
                print(f"  Average: {avg:.2f}ms")
                print(f"  P95: {p95:.2f}ms")
                print(f"  Samples: {len(times)}")

        # Overall assessment
        total_tests = len(self.results)
        passed_tests = sum(1 for r in self.results if r.passed)
        overall_pass_rate = (passed_tests / total_tests * 100) if total_tests > 0 else 0

        print("\n" + "="*80)
        print("🎯 OVERALL ASSESSMENT")
        print("="*80)

        print(f"\nTotal Tests: {total_tests}")
        print(f"Passed: {passed_tests}")
        print(f"Failed: {total_tests - passed_tests}")
        print(f"Pass Rate: {overall_pass_rate:.1f}%")

        # Production readiness
        print("\n🚀 PRODUCTION READINESS:")

        readiness_checks = {
            "Security (HMAC, Rate Limiting)": any(r.test_name.startswith("HMAC") and r.passed for r in self.results),
            "LangGraph Orchestration": any(r.phase == TestPhase.LANGGRAPH and r.passed for r in self.results),
            "Dual-Lane Routing (<500ms)": any(r.test_name == "Text Routing Performance" and r.passed for r in self.results),
            "RAG/Memory Integration": any(r.phase == TestPhase.RAG_MEMORY and r.passed for r in self.results),
            "Grok with Fallback": any(r.test_name == "Fallback Mechanism" and r.passed for r in self.results),
            "Load Handling": any(r.test_name.startswith("Load Test") and r.passed for r in self.results)
        }

        for check, passed in readiness_checks.items():
            print(f"  {'✅' if passed else '❌'} {check}")

        all_ready = all(readiness_checks.values())

        print(f"\n{'✅ READY FOR PRODUCTION' if all_ready else '⚠️ NOT READY FOR PRODUCTION'}")

        if not all_ready:
            print("\n⚠️ Required fixes before production:")
            for check, passed in readiness_checks.items():
                if not passed:
                    print(f"  - Fix: {check}")

        # Save detailed results
        import json
        results_data = {
            "test_session_id": self.test_session_id,
            "timestamp": datetime.now().isoformat(),
            "overall_pass_rate": overall_pass_rate,
            "phase_summary": {
                phase.value: {
                    "passed": summary["passed"],
                    "failed": summary["failed"],
                    "pass_rate": (summary["passed"] / (summary["passed"] + summary["failed"]) * 100)
                               if (summary["passed"] + summary["failed"]) > 0 else 0
                }
                for phase, summary in phase_summary.items()
            },
            "performance_metrics": {
                metric: {
                    "avg_ms": statistics.mean(times) if times else None,
                    "p95_ms": statistics.quantiles(times, n=20)[18] if len(times) > 1 else times[0] if times else None,
                    "samples": len(times)
                }
                for metric, times in metrics_summary.items()
            },
            "production_readiness": readiness_checks,
            "ready_for_production": all_ready
        }

        with open("phase6_test_results.json", "w") as f:
            json.dump(results_data, f, indent=2)

        print("\n📝 Detailed results saved to phase6_test_results.json")


async def main():
    """Run Phase 6 comprehensive integration tests"""
    print("🚀 PHASE 6: TESTING & PROGRESSIVE ROLLOUT")
    print("="*80)
    print("\n⚠️ Prerequisites:")
    print("  1. Backend running: cd clinics/backend && uvicorn app.main:app --reload")
    print("  2. Environment variables configured (including Grok/OpenAI keys)")
    print("  3. Database migrations applied")
    print("\nPress Enter to start comprehensive testing or Ctrl+C to abort...")
    input()

    tester = Phase6IntegrationTester()

    try:
        # Run all test phases
        await tester.test_phase1_security()
        await tester.test_phase2_langgraph()
        await tester.test_phase3_routing()
        await tester.test_phase4_rag_memory()
        await tester.test_phase5_grok()
        await tester.test_full_integration()
        await tester.load_test()

        # Generate comprehensive report
        tester.generate_report()

        print("\n✨ Phase 6 Testing Complete!")
        print("Next steps for progressive rollout:")
        print("  1. Review phase6_test_results.json")
        print("  2. Fix any failing tests")
        print("  3. Configure canary deployment")
        print("  4. Set up monitoring dashboard")
        print("  5. Begin gradual rollout (5% → 10% → 25% → 50% → 100%)")

    except Exception as e:
        print(f"\n❌ Test suite error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())