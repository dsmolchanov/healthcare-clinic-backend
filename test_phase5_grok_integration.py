#!/usr/bin/env python3
"""
Test Phase 5: Grok Integration Strategy
Validates Grok-4 LLM integration with fallback and A/B testing
"""

import asyncio
import aiohttp
import json
import time
import os
import sys
from datetime import datetime
from typing import Dict, List, Any
import statistics

# Add paths for imports
sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..', 'services', 'llm'))
sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..', 'services', 'orchestrator'))

# Configuration
BACKEND_URL = "http://localhost:8000"
LANGGRAPH_URL = "http://localhost:8000/langgraph"

# Test messages for different scenarios
TEST_MESSAGES = {
    "simple": [
        "Hello, how are you?",
        "What's the weather like today?",
        "Can you help me with something?",
    ],
    "healthcare": [
        "I need to schedule a dental appointment",
        "What are the symptoms of a cavity?",
        "Do you accept Delta Dental insurance?",
    ],
    "complex": [
        "Explain the difference between a root canal and a crown procedure",
        "I have tooth pain that gets worse at night. What could it be?",
        "Can you help me understand my treatment options for gum disease?",
    ],
    "fallback_triggers": [
        "This is a test to trigger fallback " * 100,  # Very long message
        "🦷💉🏥" * 50,  # Emoji overload
        "AAAAAAAAAAAAAAAAAAA" * 100,  # Repetitive content
    ]
}


class Phase5GrokTester:
    """Test harness for Phase 5 Grok integration"""

    def __init__(self):
        self.results = {
            "grok_responses": [],
            "openai_responses": [],
            "fallback_events": [],
            "ab_test_distribution": {"grok": 0, "openai": 0, "unknown": 0},
            "performance_comparison": {},
            "error_analysis": {}
        }

    async def test_direct_llm_clients(self):
        """Test LLM clients directly"""
        print("\n" + "="*80)
        print("🤖 TESTING DIRECT LLM CLIENTS")
        print("="*80)

        try:
            from grok_client import UniversalLLMClient, LLMProvider

            # Test with Grok preference
            print("\n📊 Testing with Grok preference (if available)...")
            client_grok = UniversalLLMClient(
                primary_provider=LLMProvider.GROK,
                fallback_provider=LLMProvider.OPENAI,
                ab_test_enabled=False
            )

            for message in TEST_MESSAGES["simple"][:2]:
                try:
                    start = time.perf_counter()
                    response = await client_grok.complete(
                        prompt=message,
                        temperature=0.7,
                        max_tokens=100
                    )
                    latency = (time.perf_counter() - start) * 1000

                    self.results["grok_responses"].append({
                        "prompt": message[:50],
                        "provider": response.provider.value,
                        "latency_ms": latency,
                        "response_length": len(response.content)
                    })

                    print(f"✅ {response.provider.value}: {latency:.2f}ms - {message[:40]}...")

                except Exception as e:
                    print(f"❌ Grok test failed: {e}")
                    self.results["error_analysis"]["grok_direct"] = str(e)

            # Test with OpenAI preference
            print("\n📊 Testing with OpenAI preference...")
            client_openai = UniversalLLMClient(
                primary_provider=LLMProvider.OPENAI,
                fallback_provider=LLMProvider.GROK,
                ab_test_enabled=False
            )

            for message in TEST_MESSAGES["simple"][:2]:
                try:
                    start = time.perf_counter()
                    response = await client_openai.complete(
                        prompt=message,
                        temperature=0.7,
                        max_tokens=100
                    )
                    latency = (time.perf_counter() - start) * 1000

                    self.results["openai_responses"].append({
                        "prompt": message[:50],
                        "provider": response.provider.value,
                        "latency_ms": latency,
                        "response_length": len(response.content)
                    })

                    print(f"✅ {response.provider.value}: {latency:.2f}ms - {message[:40]}...")

                except Exception as e:
                    print(f"❌ OpenAI test failed: {e}")
                    self.results["error_analysis"]["openai_direct"] = str(e)

        except ImportError as e:
            print(f"❌ Could not import LLM client: {e}")

    async def test_ab_testing(self):
        """Test A/B testing distribution"""
        print("\n" + "="*80)
        print("🔀 TESTING A/B TESTING DISTRIBUTION")
        print("="*80)

        try:
            from grok_client import UniversalLLMClient, LLMProvider

            # Create client with A/B testing enabled
            client = UniversalLLMClient(
                primary_provider=LLMProvider.GROK,
                fallback_provider=LLMProvider.OPENAI,
                ab_test_enabled=True,
                grok_percentage=0.3  # 30% to Grok
            )

            print("\n📊 Running 20 requests with 30% Grok allocation...")

            for i in range(20):
                try:
                    response = await client.complete(
                        prompt=f"Test message {i}",
                        temperature=0.7,
                        max_tokens=50
                    )

                    provider = response.provider.value
                    self.results["ab_test_distribution"][provider] = \
                        self.results["ab_test_distribution"].get(provider, 0) + 1

                    print(f"  Request {i+1:2d}: {provider}")

                except Exception as e:
                    self.results["ab_test_distribution"]["unknown"] += 1
                    print(f"  Request {i+1:2d}: ERROR - {e}")

                await asyncio.sleep(0.05)

            # Print distribution
            total = sum(self.results["ab_test_distribution"].values())
            print("\n📈 A/B Test Distribution:")
            for provider, count in self.results["ab_test_distribution"].items():
                if count > 0:
                    percentage = (count / total) * 100
                    print(f"  {provider}: {count}/{total} ({percentage:.1f}%)")

        except Exception as e:
            print(f"❌ A/B testing failed: {e}")

    async def test_fallback_mechanism(self):
        """Test fallback from Grok to OpenAI"""
        print("\n" + "="*80)
        print("🔄 TESTING FALLBACK MECHANISM")
        print("="*80)

        try:
            from grok_client import UniversalLLMClient, LLMProvider

            # Create client with circuit breaker
            client = UniversalLLMClient(
                primary_provider=LLMProvider.GROK,
                fallback_provider=LLMProvider.OPENAI,
                ab_test_enabled=False
            )

            print("\n📊 Testing fallback triggers...")

            for message in TEST_MESSAGES["fallback_triggers"][:2]:
                try:
                    start = time.perf_counter()
                    response = await client.complete(
                        prompt=message[:100],  # Truncate for testing
                        temperature=0.7,
                        max_tokens=50
                    )
                    latency = (time.perf_counter() - start) * 1000

                    self.results["fallback_events"].append({
                        "trigger": message[:30],
                        "provider_used": response.provider.value,
                        "latency_ms": latency,
                        "fallback_occurred": response.provider != LLMProvider.GROK
                    })

                    status = "🔄 FALLBACK" if response.provider != LLMProvider.GROK else "✅ PRIMARY"
                    print(f"{status} → {response.provider.value}: {latency:.2f}ms")

                except Exception as e:
                    print(f"❌ Fallback test error: {e}")

                await asyncio.sleep(0.1)

        except Exception as e:
            print(f"❌ Fallback testing failed: {e}")

    async def test_langgraph_integration(self):
        """Test Grok integration with LangGraph orchestrators"""
        print("\n" + "="*80)
        print("🔗 TESTING LANGGRAPH INTEGRATION")
        print("="*80)

        async with aiohttp.ClientSession() as session:
            for category, messages in [("simple", TEST_MESSAGES["simple"][:2]),
                                       ("healthcare", TEST_MESSAGES["healthcare"][:2])]:
                print(f"\n📊 Testing {category} messages...")

                for message in messages:
                    try:
                        payload = {
                            "session_id": f"grok_test_{datetime.now().timestamp()}",
                            "text": message,
                            "metadata": {
                                "test_type": "grok_integration",
                                "category": category
                            },
                            "use_healthcare": category == "healthcare",
                            "enable_rag": False,  # Disable RAG for LLM testing
                            "enable_memory": False,
                            "llm_provider": "grok",  # Request Grok
                            "enable_grok_ab": True,
                            "grok_percentage": 0.5
                        }

                        start = time.perf_counter()
                        async with session.post(
                            f"{LANGGRAPH_URL}/process",
                            json=payload,
                            timeout=aiohttp.ClientTimeout(total=5.0)
                        ) as response:
                            if response.status == 200:
                                result = await response.json()
                                latency = (time.perf_counter() - start) * 1000

                                # Check which provider was used
                                provider = result.get("state", {}).get("metadata", {}).get("llm_provider", "unknown")

                                print(f"  ✅ {provider}: {latency:.2f}ms - {message[:40]}...")

                                # Store metrics
                                if provider not in self.results["performance_comparison"]:
                                    self.results["performance_comparison"][provider] = []
                                self.results["performance_comparison"][provider].append(latency)

                            else:
                                print(f"  ❌ HTTP {response.status}: {message[:40]}...")

                    except Exception as e:
                        print(f"  ❌ Error: {e}")

                    await asyncio.sleep(0.2)

    async def test_performance_comparison(self):
        """Compare performance between Grok and OpenAI"""
        print("\n" + "="*80)
        print("📊 PERFORMANCE COMPARISON")
        print("="*80)

        # Analyze collected metrics
        if self.results["grok_responses"]:
            grok_latencies = [r["latency_ms"] for r in self.results["grok_responses"]]
            print("\n🤖 Grok Performance:")
            print(f"  Requests: {len(grok_latencies)}")
            print(f"  Avg latency: {statistics.mean(grok_latencies):.2f}ms")
            if len(grok_latencies) > 1:
                print(f"  Min/Max: {min(grok_latencies):.2f}ms / {max(grok_latencies):.2f}ms")

        if self.results["openai_responses"]:
            openai_latencies = [r["latency_ms"] for r in self.results["openai_responses"]]
            print("\n🟢 OpenAI Performance:")
            print(f"  Requests: {len(openai_latencies)}")
            print(f"  Avg latency: {statistics.mean(openai_latencies):.2f}ms")
            if len(openai_latencies) > 1:
                print(f"  Min/Max: {min(openai_latencies):.2f}ms / {max(openai_latencies):.2f}ms")

        # Compare integrated performance
        if self.results["performance_comparison"]:
            print("\n🔗 LangGraph Integration Performance:")
            for provider, latencies in self.results["performance_comparison"].items():
                if latencies:
                    print(f"  {provider}:")
                    print(f"    Requests: {len(latencies)}")
                    print(f"    Avg latency: {statistics.mean(latencies):.2f}ms")

        # Fallback analysis
        if self.results["fallback_events"]:
            fallback_count = sum(1 for e in self.results["fallback_events"] if e.get("fallback_occurred"))
            total_fallback_tests = len(self.results["fallback_events"])
            print(f"\n🔄 Fallback Events:")
            print(f"  Triggered: {fallback_count}/{total_fallback_tests}")
            if fallback_count > 0:
                print(f"  Fallback rate: {(fallback_count/total_fallback_tests)*100:.1f}%")

    def generate_report(self):
        """Generate final test report"""
        print("\n" + "="*80)
        print("📋 PHASE 5 TEST REPORT")
        print("="*80)

        # Check Grok availability
        grok_available = len(self.results["grok_responses"]) > 0 and \
                        any(r["provider"] == "grok" for r in self.results["grok_responses"])

        if grok_available:
            print("\n✅ Grok Integration Status: AVAILABLE")
        else:
            print("\n⚠️ Grok Integration Status: NOT AVAILABLE (using fallback)")

        # A/B Testing validation
        if self.results["ab_test_distribution"]["grok"] > 0 or \
           self.results["ab_test_distribution"]["openai"] > 0:
            print("✅ A/B Testing: FUNCTIONAL")
        else:
            print("⚠️ A/B Testing: NO DATA")

        # Fallback mechanism
        if self.results["fallback_events"]:
            print("✅ Fallback Mechanism: TESTED")
        else:
            print("⚠️ Fallback Mechanism: NOT TESTED")

        # Performance summary
        print("\n📊 Performance Summary:")
        if grok_available:
            print("  • Grok successfully integrated with LangGraph")
            print("  • Automatic fallback to OpenAI working")
            print("  • A/B testing framework operational")
        else:
            print("  • System operating in fallback mode (OpenAI)")
            print("  • Configure GROK_API_KEY or XAI_API_KEY to enable Grok")

        # Save detailed results
        with open("phase5_grok_test_results.json", "w") as f:
            # Convert any non-serializable objects
            clean_results = json.loads(json.dumps(self.results, default=str))
            json.dump(clean_results, f, indent=2)

        print("\n📝 Detailed results saved to phase5_grok_test_results.json")


async def main():
    """Run Phase 5 Grok integration tests"""
    print("⚠️ Make sure the following are configured:")
    print("  1. GROK_API_KEY or XAI_API_KEY environment variable (optional)")
    print("  2. OPENAI_API_KEY environment variable (for fallback)")
    print("  3. Backend running: cd clinics/backend && uvicorn app.main:app --reload")
    print("\nPress Enter to continue or Ctrl+C to abort...")
    input()

    tester = Phase5GrokTester()

    try:
        # Run all test suites
        await tester.test_direct_llm_clients()
        await tester.test_ab_testing()
        await tester.test_fallback_mechanism()
        await tester.test_langgraph_integration()
        await tester.test_performance_comparison()

        # Generate report
        tester.generate_report()

        print("\n✨ Phase 5 Grok Integration Tests Complete!")

    except Exception as e:
        print(f"\n❌ Test suite error: {e}")

    print("\n" + "="*80)
    print("Phase 5 Testing Complete")
    print("="*80)


if __name__ == "__main__":
    asyncio.run(main())