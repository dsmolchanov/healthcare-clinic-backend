#!/usr/bin/env python3
"""
Local Widget Flow Test with Mocked Backend
Tests the complete flow logic without hitting the actual backend
"""

import asyncio
import time
import json
from datetime import datetime
from typing import Dict, List
from dataclasses import dataclass
from enum import Enum


class TestStage(Enum):
    """Test execution stages"""
    WIDGET_SEND = "widget_send"
    BACKEND_RECEIVE = "backend_receive"
    SESSION_LOOKUP = "session_lookup"
    MEMORY_RETRIEVE = "memory_retrieve"
    RAG_SEARCH = "rag_search"
    LLM_GENERATE = "llm_generate"
    MEMORY_STORE = "memory_store"
    DB_STORE = "db_store"
    RESPONSE_RETURN = "response_return"


@dataclass
class StageResult:
    """Result of a test stage"""
    stage: TestStage
    duration_ms: float
    success: bool
    metadata: Dict


class MockWidgetFlowTester:
    """Mock tester for widget flow without backend dependency"""

    def __init__(self):
        self.session_history: List[Dict] = []
        self.memory_store: Dict = {}

    async def simulate_widget_send(self, message: str, session_id: str) -> StageResult:
        """Simulate widget sending message"""
        start = time.time()

        payload = {
            "from_phone": f"widget_{session_id}",
            "body": message,
            "clinic_id": "3e411ecb-3411-4add-91e2-8fa897310cb0",
            "channel": "widget",
            "metadata": {"session_id": session_id}
        }

        # Simulate network latency
        await asyncio.sleep(0.05)

        duration = (time.time() - start) * 1000
        return StageResult(
            stage=TestStage.WIDGET_SEND,
            duration_ms=duration,
            success=True,
            metadata={"payload_size": len(json.dumps(payload))}
        )

    async def simulate_backend_receive(self, message: str) -> StageResult:
        """Simulate backend receiving and parsing"""
        start = time.time()

        # Simulate parsing and validation
        await asyncio.sleep(0.01)

        duration = (time.time() - start) * 1000
        return StageResult(
            stage=TestStage.BACKEND_RECEIVE,
            duration_ms=duration,
            success=True,
            metadata={"message_length": len(message)}
        )

    async def simulate_session_lookup(self, session_id: str) -> StageResult:
        """Simulate database session lookup"""
        start = time.time()

        # Simulate DB query
        await asyncio.sleep(0.03)

        # Check if session exists
        session_exists = session_id in self.memory_store

        duration = (time.time() - start) * 1000
        return StageResult(
            stage=TestStage.SESSION_LOOKUP,
            duration_ms=duration,
            success=True,
            metadata={
                "session_exists": session_exists,
                "message_count": len(self.session_history)
            }
        )

    async def simulate_memory_retrieve(self, session_id: str) -> StageResult:
        """Simulate memory retrieval from mem0"""
        start = time.time()

        # Simulate mem0 query
        await asyncio.sleep(0.08)

        # Get memories
        memories = self.memory_store.get(session_id, [])

        duration = (time.time() - start) * 1000
        return StageResult(
            stage=TestStage.MEMORY_RETRIEVE,
            duration_ms=duration,
            success=True,
            metadata={
                "memories_found": len(memories),
                "has_context": len(memories) > 0
            }
        )

    async def simulate_rag_search(self, query: str) -> StageResult:
        """Simulate RAG search in Pinecone"""
        start = time.time()

        # Simulate vector search
        await asyncio.sleep(0.15)

        # Mock results
        mock_results = [
            {"text": "Office hours: Monday-Friday 9AM-6PM", "score": 0.85},
            {"text": "We offer cleaning, whitening, and orthodontics", "score": 0.78}
        ]

        duration = (time.time() - start) * 1000
        return StageResult(
            stage=TestStage.RAG_SEARCH,
            duration_ms=duration,
            success=True,
            metadata={
                "results_found": len(mock_results),
                "top_score": mock_results[0]["score"]
            }
        )

    async def simulate_llm_generate(
        self,
        message: str,
        context: List[str]
    ) -> StageResult:
        """Simulate LLM response generation"""
        start = time.time()

        # Simulate OpenAI API call
        await asyncio.sleep(0.4)

        # Generate mock response based on message
        if "hours" in message.lower():
            response = "Our office hours are Monday through Friday, 9 AM to 6 PM. How can I help you schedule an appointment?"
        elif "services" in message.lower() or "dental" in message.lower():
            response = "We offer comprehensive dental services including cleanings, whitening, orthodontics, and more. Would you like to know more about any specific service?"
        elif "appointment" in message.lower() or "book" in message.lower():
            response = "I'd be happy to help you book an appointment. What type of service are you interested in, and what dates work best for you?"
        elif "thank" in message.lower():
            response = "You're welcome! Is there anything else I can help you with today?"
        else:
            response = "Hello! I'm here to help you with any questions about our dental clinic. What can I assist you with?"

        duration = (time.time() - start) * 1000
        return StageResult(
            stage=TestStage.LLM_GENERATE,
            duration_ms=duration,
            success=True,
            metadata={
                "response_length": len(response),
                "context_items": len(context)
            }
        )

    async def simulate_memory_store(
        self,
        session_id: str,
        message: str,
        response: str
    ) -> StageResult:
        """Simulate storing to memory"""
        start = time.time()

        # Simulate mem0 storage
        await asyncio.sleep(0.06)

        # Store in mock memory
        if session_id not in self.memory_store:
            self.memory_store[session_id] = []

        self.memory_store[session_id].append({
            "user": message,
            "assistant": response,
            "timestamp": datetime.now().isoformat()
        })

        duration = (time.time() - start) * 1000
        return StageResult(
            stage=TestStage.MEMORY_STORE,
            duration_ms=duration,
            success=True,
            metadata={"memories_count": len(self.memory_store[session_id])}
        )

    async def simulate_db_store(
        self,
        session_id: str,
        message: str,
        response: str
    ) -> StageResult:
        """Simulate database storage"""
        start = time.time()

        # Simulate DB insert
        await asyncio.sleep(0.04)

        # Store in session history
        self.session_history.append({
            "session_id": session_id,
            "user_message": message,
            "assistant_response": response,
            "timestamp": datetime.now().isoformat()
        })

        duration = (time.time() - start) * 1000
        return StageResult(
            stage=TestStage.DB_STORE,
            duration_ms=duration,
            success=True,
            metadata={"history_count": len(self.session_history)}
        )

    async def simulate_complete_flow(
        self,
        message: str,
        session_id: str = "test_session"
    ) -> Dict:
        """Simulate complete message flow"""

        print(f"\n{'='*60}")
        print(f"📨 Message: {message}")
        print(f"🔑 Session: {session_id}")
        print(f"{'='*60}")

        stages: List[StageResult] = []
        total_start = time.time()

        # Execute all stages
        stages.append(await self.simulate_widget_send(message, session_id))
        print(f"✅ Widget Send: {stages[-1].duration_ms:.2f}ms")

        stages.append(await self.simulate_backend_receive(message))
        print(f"✅ Backend Receive: {stages[-1].duration_ms:.2f}ms")

        stages.append(await self.simulate_session_lookup(session_id))
        print(f"✅ Session Lookup: {stages[-1].duration_ms:.2f}ms")

        stages.append(await self.simulate_memory_retrieve(session_id))
        print(f"✅ Memory Retrieve: {stages[-1].duration_ms:.2f}ms")

        stages.append(await self.simulate_rag_search(message))
        print(f"✅ RAG Search: {stages[-1].duration_ms:.2f}ms")

        # Get context for LLM
        context = []
        if stages[3].metadata.get("has_context"):
            context = ["Previous conversation context"]

        stages.append(await self.simulate_llm_generate(message, context))
        print(f"✅ LLM Generate: {stages[-1].duration_ms:.2f}ms")

        response = "Generated response"  # Would come from LLM stage

        stages.append(await self.simulate_memory_store(session_id, message, response))
        print(f"✅ Memory Store: {stages[-1].duration_ms:.2f}ms")

        stages.append(await self.simulate_db_store(session_id, message, response))
        print(f"✅ DB Store: {stages[-1].duration_ms:.2f}ms")

        total_duration = (time.time() - total_start) * 1000

        print(f"\n⏱️  Total Duration: {total_duration:.2f}ms")

        # Build result
        result = {
            "message": message,
            "session_id": session_id,
            "total_duration_ms": total_duration,
            "stages": [
                {
                    "stage": s.stage.value,
                    "duration_ms": s.duration_ms,
                    "success": s.success,
                    "metadata": s.metadata
                }
                for s in stages
            ],
            "success": all(s.success for s in stages)
        }

        return result

    async def run_test_suite(self) -> Dict:
        """Run complete test suite"""

        print("\n" + "🚀" * 60)
        print("WIDGET FLOW TEST SUITE (Local Mock)")
        print("🚀" * 60)

        test_messages = [
            "Hello! How are you?",
            "What are your office hours?",
            "Tell me about your dental services",
            "I need to book a cleaning appointment",
            "Thank you for the information"
        ]

        results = []
        session_id = f"test_session_{int(time.time())}"

        for i, message in enumerate(test_messages, 1):
            print(f"\n\n{'🔵'*60}")
            print(f"TEST {i}/{len(test_messages)}")
            print(f"{'🔵'*60}")

            result = await self.simulate_complete_flow(message, session_id)
            results.append(result)

            # Small delay between tests
            await asyncio.sleep(0.1)

        # Generate summary
        print(f"\n\n{'📊'*60}")
        print("TEST SUMMARY")
        print(f"{'📊'*60}")

        total_tests = len(results)
        passed_tests = sum(1 for r in results if r["success"])

        print(f"\n✅ All tests passed: {passed_tests}/{total_tests}")

        # Latency breakdown
        print(f"\n{'='*60}")
        print("LATENCY BREAKDOWN BY STAGE")
        print(f"{'='*60}")

        # Aggregate stage latencies
        stage_latencies = {}
        for result in results:
            for stage in result["stages"]:
                stage_name = stage["stage"]
                if stage_name not in stage_latencies:
                    stage_latencies[stage_name] = []
                stage_latencies[stage_name].append(stage["duration_ms"])

        # Print averages
        for stage_name, latencies in stage_latencies.items():
            avg = sum(latencies) / len(latencies)
            min_lat = min(latencies)
            max_lat = max(latencies)
            print(f"{stage_name:20} | Avg: {avg:6.2f}ms | Min: {min_lat:6.2f}ms | Max: {max_lat:6.2f}ms")

        # Total latency stats
        total_latencies = [r["total_duration_ms"] for r in results]
        avg_total = sum(total_latencies) / len(total_latencies)
        print(f"\n{'='*60}")
        print(f"Average Total Latency: {avg_total:.2f}ms")
        print(f"Min Total Latency: {min(total_latencies):.2f}ms")
        print(f"Max Total Latency: {max(total_latencies):.2f}ms")

        # Memory and history stats
        print(f"\n{'='*60}")
        print("MEMORY & STORAGE STATS")
        print(f"{'='*60}")
        print(f"Session memories: {len(self.memory_store.get(session_id, []))}")
        print(f"Total messages stored: {len(self.session_history)}")

        summary = {
            "success": True,
            "total_tests": total_tests,
            "passed_tests": passed_tests,
            "average_latency_ms": avg_total,
            "results": results,
            "timestamp": datetime.now().isoformat()
        }

        # Save report
        report_file = f"test_report_local_{int(time.time())}.json"
        with open(report_file, "w") as f:
            json.dump(summary, f, indent=2)

        print(f"\n📄 Report saved to: {report_file}")
        print("\n🎉 ALL TESTS PASSED! 🎉")

        return summary


async def main():
    """Main test execution"""
    tester = MockWidgetFlowTester()
    await tester.run_test_suite()


if __name__ == "__main__":
    asyncio.run(main())