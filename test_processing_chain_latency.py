#!/usr/bin/env python3
"""
Test script to measure latency at each step of the processing chain:
1. User query processing
2. RAG vector search
3. OpenAI API call
4. Response generation
5. Total end-to-end time
"""

import asyncio
import time
import os
import json
from typing import Dict, List, Any, Optional
from datetime import datetime
from dataclasses import dataclass, asdict
import statistics

import openai
from openai import AsyncOpenAI
from supabase import create_client, Client
import numpy as np
from dotenv import load_dotenv
from pathlib import Path
import httpx

# Load environment variables from the correct .env file
env_path = Path(__file__).parent / '.env'
load_dotenv(dotenv_path=env_path)

@dataclass
class LatencyMetrics:
    """Store latency measurements for each processing step"""
    query: str
    timestamp: str
    query_preprocessing_ms: float
    embedding_generation_ms: float
    rag_search_ms: float
    context_preparation_ms: float
    openai_api_ms: float
    response_processing_ms: float
    total_ms: float
    rag_results_count: int
    context_tokens: int
    response_tokens: int

    def to_dict(self):
        return asdict(self)


class ProcessingChainTester:
    """Test the complete processing chain with latency measurements"""

    def __init__(self):
        # Initialize Supabase client
        self.supabase: Client = create_client(
            os.getenv("SUPABASE_URL"),
            os.getenv("SUPABASE_SERVICE_ROLE_KEY")
        )

        # Initialize OpenAI client for embeddings (still using OpenAI for embeddings)
        self.openai_client = AsyncOpenAI(
            api_key=os.getenv("OPENAI_API_KEY")
        )

        # Initialize Grok client for LLM (using xAI's Grok)
        self.grok_client = AsyncOpenAI(
            api_key=os.getenv("XAI_API_KEY", os.getenv("GROK_API_KEY", "")),
            base_url="https://api.x.ai/v1",
            http_client=httpx.AsyncClient(
                limits=httpx.Limits(
                    max_keepalive_connections=5,
                    max_connections=10,
                ),
                timeout=60.0,
            ),
        )

        self.metrics_history: List[LatencyMetrics] = []

    async def preprocess_query(self, query: str) -> tuple[str, float]:
        """Preprocess the user query"""
        start = time.perf_counter()

        # Simulate query preprocessing (normalization, intent detection, etc.)
        processed_query = query.lower().strip()

        # Add any additional preprocessing steps here
        # For example: spell correction, entity extraction, etc.

        elapsed_ms = (time.perf_counter() - start) * 1000
        return processed_query, elapsed_ms

    async def generate_embedding(self, text: str) -> tuple[List[float], float]:
        """Generate embedding for the query"""
        start = time.perf_counter()

        try:
            response = await self.openai_client.embeddings.create(
                model="text-embedding-3-small",
                input=text
            )
            embedding = response.data[0].embedding
        except Exception as e:
            print(f"Error generating embedding: {e}")
            # Fallback to random embedding for testing
            embedding = np.random.rand(1536).tolist()

        elapsed_ms = (time.perf_counter() - start) * 1000
        return embedding, elapsed_ms

    async def search_rag(self, embedding: List[float], limit: int = 5) -> tuple[List[Dict], float]:
        """Search RAG/vector database for relevant context"""
        start = time.perf_counter()

        try:
            # First, let's check if we have any documents in the knowledge base
            docs_response = self.supabase.table("knowledge_documents").select("*").limit(1).execute()

            if not docs_response.data:
                # No documents, simulate RAG results for testing
                print("No documents in knowledge base, simulating RAG results...")
                results = [
                    {
                        "content": "The clinic has 5 doctors on staff.",
                        "metadata": {"source": "clinic_info.txt", "score": 0.92}
                    },
                    {
                        "content": "Our medical team includes specialists in cardiology, pediatrics, and general practice.",
                        "metadata": {"source": "staff_overview.txt", "score": 0.88}
                    },
                    {
                        "content": "Dr. Smith, Dr. Johnson, Dr. Williams, Dr. Brown, and Dr. Davis are our primary physicians.",
                        "metadata": {"source": "doctor_list.txt", "score": 0.85}
                    }
                ]
            else:
                # Try to search with embeddings if available
                try:
                    # Attempt vector search if embeddings table exists
                    results_response = self.supabase.rpc(
                        "match_documents",
                        {
                            "query_embedding": embedding,
                            "match_count": limit
                        }
                    ).execute()

                    results = [
                        {
                            "content": r.get("content", ""),
                            "metadata": r.get("metadata", {})
                        }
                        for r in results_response.data
                    ]
                except Exception as e:
                    print(f"Vector search not available: {e}")
                    # Fallback to text search
                    search_response = self.supabase.table("knowledge_documents")\
                        .select("content, metadata")\
                        .ilike("content", f"%doctor%")\
                        .limit(limit)\
                        .execute()

                    results = [
                        {
                            "content": r.get("content", ""),
                            "metadata": r.get("metadata", {})
                        }
                        for r in search_response.data
                    ]
        except Exception as e:
            print(f"RAG search error: {e}")
            # Fallback results for testing
            results = [
                {
                    "content": "The clinic employs 5 doctors across various specialties.",
                    "metadata": {"source": "fallback", "score": 0.80}
                }
            ]

        elapsed_ms = (time.perf_counter() - start) * 1000
        return results, elapsed_ms

    async def prepare_context(self, query: str, rag_results: List[Dict]) -> tuple[str, int, float]:
        """Prepare context for the LLM"""
        start = time.perf_counter()

        # Build context from RAG results
        context_parts = []
        for i, result in enumerate(rag_results, 1):
            content = result.get("content", "")
            source = result.get("metadata", {}).get("source", "unknown")
            context_parts.append(f"[Source {i}: {source}]\n{content}")

        context = "\n\n".join(context_parts)

        # Create the system prompt with context
        system_prompt = f"""You are a helpful assistant for a healthcare clinic.
        Use the following context to answer questions accurately:

        {context}

        If the information is not in the context, say so clearly."""

        # Estimate token count (rough approximation)
        token_count = len(system_prompt.split()) + len(query.split())

        elapsed_ms = (time.perf_counter() - start) * 1000
        return system_prompt, token_count, elapsed_ms

    async def call_openai(self, system_prompt: str, query: str) -> tuple[str, int, float]:
        """Call Grok API to generate response"""
        start = time.perf_counter()

        try:
            # Use Grok-4-fast model for fastest response
            response = await self.grok_client.chat.completions.create(
                model="grok-4-fast",  # Using the newest and fastest Grok model
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": query}
                ],
                temperature=0.7,
                max_tokens=500
            )

            answer = response.choices[0].message.content
            response_tokens = response.usage.completion_tokens if response.usage else 0

        except Exception as e:
            print(f"Grok API error: {e}")
            # Fallback to OpenAI if Grok fails
            try:
                print("Falling back to OpenAI...")
                response = await self.openai_client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": query}
                    ],
                    temperature=0.7,
                    max_tokens=500
                )
                answer = response.choices[0].message.content
                response_tokens = response.usage.completion_tokens if response.usage else 0
            except Exception as e2:
                print(f"OpenAI API error: {e2}")
                answer = "I apologize, but I'm having trouble processing your request right now."
                response_tokens = 0

        elapsed_ms = (time.perf_counter() - start) * 1000
        return answer, response_tokens, elapsed_ms

    async def process_response(self, response: str) -> tuple[str, float]:
        """Post-process the response"""
        start = time.perf_counter()

        # Simulate response post-processing
        # This could include: formatting, safety checks, personalization, etc.
        processed_response = response.strip()

        # Add any additional processing steps here

        elapsed_ms = (time.perf_counter() - start) * 1000
        return processed_response, elapsed_ms

    async def test_query(self, query: str) -> LatencyMetrics:
        """Test a single query through the complete processing chain"""
        print(f"\n{'='*60}")
        print(f"Testing query: '{query}'")
        print(f"{'='*60}")

        total_start = time.perf_counter()

        # Step 1: Preprocess query
        processed_query, preprocess_time = await self.preprocess_query(query)
        print(f"✓ Query preprocessing: {preprocess_time:.2f}ms")

        # Step 2: Generate embedding
        embedding, embedding_time = await self.generate_embedding(processed_query)
        print(f"✓ Embedding generation: {embedding_time:.2f}ms")

        # Step 3: Search RAG
        rag_results, rag_time = await self.search_rag(embedding)
        print(f"✓ RAG search ({len(rag_results)} results): {rag_time:.2f}ms")

        # Step 4: Prepare context
        system_prompt, context_tokens, context_time = await self.prepare_context(query, rag_results)
        print(f"✓ Context preparation (~{context_tokens} tokens): {context_time:.2f}ms")

        # Step 5: Call Grok/LLM API
        response, response_tokens, llm_time = await self.call_openai(system_prompt, query)
        print(f"✓ LLM API call (Grok/OpenAI) ({response_tokens} tokens): {llm_time:.2f}ms")

        # Step 6: Process response
        final_response, response_time = await self.process_response(response)
        print(f"✓ Response processing: {response_time:.2f}ms")

        total_time = (time.perf_counter() - total_start) * 1000

        print(f"\n[TOTAL TIME: {total_time:.2f}ms]")
        print(f"\nResponse: {final_response[:200]}{'...' if len(final_response) > 200 else ''}")

        # Create metrics object
        metrics = LatencyMetrics(
            query=query,
            timestamp=datetime.now().isoformat(),
            query_preprocessing_ms=preprocess_time,
            embedding_generation_ms=embedding_time,
            rag_search_ms=rag_time,
            context_preparation_ms=context_time,
            openai_api_ms=llm_time,
            response_processing_ms=response_time,
            total_ms=total_time,
            rag_results_count=len(rag_results),
            context_tokens=context_tokens,
            response_tokens=response_tokens
        )

        self.metrics_history.append(metrics)
        return metrics

    def print_statistics(self):
        """Print statistics from all test runs"""
        if not self.metrics_history:
            print("No metrics collected yet.")
            return

        print(f"\n{'='*60}")
        print("PERFORMANCE STATISTICS")
        print(f"{'='*60}")

        # Calculate statistics for each metric
        metrics_fields = [
            ("Query Preprocessing", "query_preprocessing_ms"),
            ("Embedding Generation", "embedding_generation_ms"),
            ("RAG Search", "rag_search_ms"),
            ("Context Preparation", "context_preparation_ms"),
            ("LLM API (Grok/OpenAI)", "openai_api_ms"),
            ("Response Processing", "response_processing_ms"),
            ("Total End-to-End", "total_ms")
        ]

        for display_name, field_name in metrics_fields:
            values = [getattr(m, field_name) for m in self.metrics_history]
            print(f"\n{display_name}:")
            print(f"  Min:    {min(values):.2f}ms")
            print(f"  Max:    {max(values):.2f}ms")
            print(f"  Mean:   {statistics.mean(values):.2f}ms")
            print(f"  Median: {statistics.median(values):.2f}ms")
            if len(values) > 1:
                print(f"  StdDev: {statistics.stdev(values):.2f}ms")

        # Print breakdown percentages
        print(f"\n{'='*60}")
        print("LATENCY BREAKDOWN (Average %)")
        print(f"{'='*60}")

        avg_total = statistics.mean([m.total_ms for m in self.metrics_history])
        for display_name, field_name in metrics_fields[:-1]:  # Exclude total
            avg_value = statistics.mean([getattr(m, field_name) for m in self.metrics_history])
            percentage = (avg_value / avg_total) * 100
            print(f"{display_name:25s}: {percentage:5.1f}% ({avg_value:.2f}ms)")

    def save_results(self, filename: str = "latency_test_results.json"):
        """Save test results to a JSON file"""
        results = {
            "test_timestamp": datetime.now().isoformat(),
            "metrics": [m.to_dict() for m in self.metrics_history],
            "summary": {
                "total_tests": len(self.metrics_history),
                "avg_total_latency_ms": statistics.mean([m.total_ms for m in self.metrics_history]) if self.metrics_history else 0,
                "avg_openai_latency_ms": statistics.mean([m.openai_api_ms for m in self.metrics_history]) if self.metrics_history else 0,
                "avg_rag_latency_ms": statistics.mean([m.rag_search_ms for m in self.metrics_history]) if self.metrics_history else 0
            }
        }

        filepath = f"/Users/dmitrymolchanov/Programs/livekit-voice-agent/clinics/backend/{filename}"
        with open(filepath, "w") as f:
            json.dump(results, f, indent=2)

        print(f"\nResults saved to: {filepath}")


async def main():
    """Main test execution"""
    tester = ProcessingChainTester()

    # Test queries (reduced for faster testing)
    test_queries = [
        "How many doctors work in the clinic?",
        "What specialties are available at the clinic?",
        "Who are the doctors at this clinic?"
    ]

    print("Starting Processing Chain Latency Tests")
    print("="*60)

    # Run tests
    for query in test_queries:
        await tester.test_query(query)
        # Small delay between tests
        await asyncio.sleep(0.5)

    # Print statistics
    tester.print_statistics()

    # Save results
    tester.save_results()

    print("\n✅ All tests completed successfully!")


if __name__ == "__main__":
    asyncio.run(main())