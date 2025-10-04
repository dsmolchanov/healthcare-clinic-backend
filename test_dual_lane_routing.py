#!/usr/bin/env python3
"""
Test Dual-Lane Routing Performance
Tests the Phase 3 implementation of LangGraph migration

Expected Results:
- Text messages route directly to LangGraph (<500ms target)
- Voice messages route through LiveKit
- Performance metrics are tracked and reported
"""

import asyncio
import aiohttp
import time
import json
from typing import Dict, List, Any
import statistics

# Configuration
BACKEND_URL = "http://localhost:8000"
LANGGRAPH_URL = "http://localhost:8000/langgraph"

# Test data
TEST_MESSAGES = [
    # Healthcare appointment requests
    "I need to schedule a dental cleaning",
    "What are your office hours?",
    "I have severe tooth pain, can I get an emergency appointment?",

    # General inquiries
    "Do you accept my insurance?",
    "How much does a root canal cost?",
    "What's the address of your clinic?",

    # Spanish messages (to test multilingual)
    "Necesito una cita para limpieza dental",
    "¿Cuáles son sus horarios?",
    "Tengo dolor de muelas severo",
]


async def test_langgraph_direct(session: aiohttp.ClientSession) -> Dict[str, Any]:
    """Test direct LangGraph endpoint (fast lane)"""
    results = []

    print("\n" + "="*80)
    print("TESTING LANGGRAPH DIRECT ENDPOINT")
    print("="*80)

    for i, message in enumerate(TEST_MESSAGES, 1):
        payload = {
            "session_id": f"test_session_{i}",
            "text": message,
            "metadata": {
                "clinic_id": "3e411ecb-3411-4add-91e2-8fa897310cb0",
                "test_run": True
            },
            "use_healthcare": True,
            "enable_rag": True,
            "enable_memory": True
        }

        start_time = time.perf_counter()

        try:
            async with session.post(
                f"{LANGGRAPH_URL}/process",
                json=payload,
                timeout=aiohttp.ClientTimeout(total=3.0)
            ) as response:
                result = await response.json()
                latency_ms = (time.perf_counter() - start_time) * 1000

                results.append({
                    "message": message[:50],
                    "latency_ms": latency_ms,
                    "status": response.status,
                    "response_length": len(result.get("response", "")),
                    "routing_path": result.get("routing_path", "langgraph_direct")
                })

                # Print result
                status_emoji = "✅" if latency_ms < 500 else "⚠️"
                print(f"{status_emoji} Message {i}: {latency_ms:.2f}ms - {message[:40]}...")

        except asyncio.TimeoutError:
            latency_ms = 3000
            results.append({
                "message": message[:50],
                "latency_ms": latency_ms,
                "status": "timeout",
                "error": "Request timed out"
            })
            print(f"❌ Message {i}: TIMEOUT - {message[:40]}...")

        except Exception as e:
            results.append({
                "message": message[:50],
                "error": str(e)
            })
            print(f"❌ Message {i}: ERROR - {e}")

        # Small delay between requests
        await asyncio.sleep(0.1)

    return analyze_results(results, "LangGraph Direct")


async def test_message_router(session: aiohttp.ClientSession) -> Dict[str, Any]:
    """Test message router endpoint (dual-lane routing)"""
    results = []

    print("\n" + "="*80)
    print("TESTING MESSAGE ROUTER (DUAL-LANE)")
    print("="*80)

    # Simulate Evolution webhook messages
    for i, message in enumerate(TEST_MESSAGES, 1):
        webhook_payload = {
            "message": {
                "key": {
                    "remoteJid": f"521234567{i:03d}@s.whatsapp.net",
                    "fromMe": False
                },
                "pushName": f"Test User {i}",
                "message": {
                    "conversation": message
                }
            }
        }

        start_time = time.perf_counter()

        try:
            # Simulate Evolution webhook call
            async with session.post(
                f"{BACKEND_URL}/webhooks/evolution/test-instance",
                json=webhook_payload,
                timeout=aiohttp.ClientTimeout(total=0.5)  # Webhook should return immediately
            ) as response:
                webhook_latency = (time.perf_counter() - start_time) * 1000
                result = await response.json()

                results.append({
                    "message": message[:50],
                    "webhook_latency_ms": webhook_latency,
                    "status": response.status,
                    "immediate_return": webhook_latency < 100  # Should return in <100ms
                })

                # Print result
                status_emoji = "✅" if webhook_latency < 100 else "⚠️"
                print(f"{status_emoji} Webhook {i}: {webhook_latency:.2f}ms - {message[:40]}...")

        except Exception as e:
            results.append({
                "message": message[:50],
                "error": str(e)
            })
            print(f"❌ Webhook {i}: ERROR - {e}")

        await asyncio.sleep(0.1)

    return analyze_results(results, "Message Router")


async def test_concurrent_load(session: aiohttp.ClientSession) -> Dict[str, Any]:
    """Test concurrent message handling"""
    print("\n" + "="*80)
    print("TESTING CONCURRENT LOAD (10 parallel requests)")
    print("="*80)

    async def send_message(session_id: int, message: str):
        payload = {
            "session_id": f"concurrent_{session_id}",
            "text": message,
            "metadata": {"test": "concurrent"},
            "use_healthcare": True
        }

        start_time = time.perf_counter()
        try:
            async with session.post(
                f"{LANGGRAPH_URL}/process",
                json=payload,
                timeout=aiohttp.ClientTimeout(total=3.0)
            ) as response:
                latency_ms = (time.perf_counter() - start_time) * 1000
                return {"success": True, "latency_ms": latency_ms}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # Send 10 concurrent requests
    tasks = [
        send_message(i, TEST_MESSAGES[i % len(TEST_MESSAGES)])
        for i in range(10)
    ]

    start_time = time.perf_counter()
    results = await asyncio.gather(*tasks)
    total_time = (time.perf_counter() - start_time) * 1000

    successful = [r for r in results if r.get("success")]
    latencies = [r["latency_ms"] for r in successful]

    print(f"\n📊 Concurrent Test Results:")
    print(f"  Total time: {total_time:.2f}ms")
    print(f"  Success rate: {len(successful)}/10")

    if latencies:
        print(f"  Avg latency: {statistics.mean(latencies):.2f}ms")
        print(f"  P50 latency: {statistics.median(latencies):.2f}ms")
        if len(latencies) > 1:
            print(f"  P95 latency: {statistics.quantiles(latencies, n=20)[18]:.2f}ms")

    return {
        "total_time_ms": total_time,
        "success_rate": f"{len(successful)}/10",
        "avg_latency_ms": statistics.mean(latencies) if latencies else 0
    }


def analyze_results(results: List[Dict], test_name: str) -> Dict[str, Any]:
    """Analyze and print test results"""
    print(f"\n📊 {test_name} Results Summary:")
    print("-" * 40)

    # Extract latencies
    latencies = []
    for r in results:
        if "latency_ms" in r:
            latencies.append(r["latency_ms"])
        elif "webhook_latency_ms" in r:
            latencies.append(r["webhook_latency_ms"])

    if not latencies:
        print("❌ No successful requests")
        return {"error": "No successful requests"}

    # Calculate statistics
    stats = {
        "total_requests": len(results),
        "successful_requests": len(latencies),
        "avg_latency_ms": statistics.mean(latencies),
        "min_latency_ms": min(latencies),
        "max_latency_ms": max(latencies),
        "median_latency_ms": statistics.median(latencies),
        "under_500ms": sum(1 for l in latencies if l < 500),
        "under_100ms": sum(1 for l in latencies if l < 100)
    }

    # Print statistics
    print(f"  Total requests: {stats['total_requests']}")
    print(f"  Successful: {stats['successful_requests']}")
    print(f"  Average latency: {stats['avg_latency_ms']:.2f}ms")
    print(f"  Min latency: {stats['min_latency_ms']:.2f}ms")
    print(f"  Max latency: {stats['max_latency_ms']:.2f}ms")
    print(f"  Median latency: {stats['median_latency_ms']:.2f}ms")

    # Performance targets
    if test_name == "LangGraph Direct":
        target_pct = (stats['under_500ms'] / stats['successful_requests']) * 100
        print(f"  📍 Meeting <500ms target: {stats['under_500ms']}/{stats['successful_requests']} ({target_pct:.1f}%)")

        if target_pct >= 90:
            print("  ✅ Performance target MET (>90% under 500ms)")
        else:
            print("  ⚠️ Performance target NOT MET (<90% under 500ms)")

    elif "Router" in test_name:
        target_pct = (stats['under_100ms'] / stats['successful_requests']) * 100
        print(f"  📍 Immediate returns (<100ms): {stats['under_100ms']}/{stats['successful_requests']} ({target_pct:.1f}%)")

    return stats


async def run_all_tests():
    """Run all performance tests"""
    print("\n" + "="*80)
    print("🚀 DUAL-LANE ROUTING PERFORMANCE TESTS")
    print("Testing Phase 3 LangGraph Migration Implementation")
    print("="*80)

    all_results = {}

    async with aiohttp.ClientSession() as session:
        # Test 1: Direct LangGraph endpoint
        try:
            langgraph_results = await test_langgraph_direct(session)
            all_results["langgraph_direct"] = langgraph_results
        except Exception as e:
            print(f"\n❌ LangGraph Direct test failed: {e}")
            all_results["langgraph_direct"] = {"error": str(e)}

        # Test 2: Message router (Evolution webhook simulation)
        try:
            router_results = await test_message_router(session)
            all_results["message_router"] = router_results
        except Exception as e:
            print(f"\n❌ Message Router test failed: {e}")
            all_results["message_router"] = {"error": str(e)}

        # Test 3: Concurrent load
        try:
            concurrent_results = await test_concurrent_load(session)
            all_results["concurrent_load"] = concurrent_results
        except Exception as e:
            print(f"\n❌ Concurrent Load test failed: {e}")
            all_results["concurrent_load"] = {"error": str(e)}

    # Final summary
    print("\n" + "="*80)
    print("📈 FINAL TEST SUMMARY")
    print("="*80)

    # Check if targets are met
    langgraph_stats = all_results.get("langgraph_direct", {})
    if "avg_latency_ms" in langgraph_stats:
        avg_latency = langgraph_stats["avg_latency_ms"]
        if avg_latency < 500:
            print(f"✅ LangGraph average latency: {avg_latency:.2f}ms (TARGET MET)")
        else:
            print(f"⚠️ LangGraph average latency: {avg_latency:.2f}ms (TARGET NOT MET)")

    # Save results to file
    with open("dual_lane_test_results.json", "w") as f:
        json.dump(all_results, f, indent=2)
    print("\n📝 Results saved to dual_lane_test_results.json")

    print("\n✨ Phase 3 Dual-Lane Routing Tests Complete!")

    return all_results


if __name__ == "__main__":
    # Check if services are running
    print("⚠️ Make sure the following services are running:")
    print("  1. Backend API: cd clinics/backend && uvicorn app.main:app --reload")
    print("  2. LangGraph service should be included in the backend")
    print("\nPress Enter to continue or Ctrl+C to abort...")
    input()

    # Run tests
    asyncio.run(run_all_tests())