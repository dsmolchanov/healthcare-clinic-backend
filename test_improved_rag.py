#!/usr/bin/env python3
"""
Test Improved RAG System
========================
Verify that the improved RAG system is retrieving content properly
"""

import asyncio
import json
from typing import Dict, Any
import httpx
from datetime import datetime

# Production endpoint
PRODUCTION_URL = "https://healthcare-clinic-backend.fly.dev/api/process-message"

# Test queries that were previously failing
TEST_QUERIES = [
    # Previously failing queries
    ("What services do you offer?", "en"),
    ("What are your prices?", "en"),
    ("Do you do root canals?", "en"),
    ("What's your cancellation policy?", "en"),
    
    # Spanish queries
    ("¿Qué servicios ofrecen?", "es"),
    ("Información en español", "es"),
    
    # Russian queries (testing multilingual)
    ("Сколько у вас работает врачей?", "ru"),
    ("у вас работает врач по имени Дан", "ru"),
    
    # Queries that should work well
    ("What insurance do you accept?", "en"),
    ("Post extraction care", "en"),
    ("How do I book an appointment?", "en"),
]

async def test_query(query: str, expected_lang: str) -> Dict[str, Any]:
    """Test a single query against production"""
    
    payload = {
        "from_phone": "whatsapp:+1234567890",
        "to_phone": "whatsapp:+52999999999",
        "body": query,
        "message_sid": f"test_{datetime.now().timestamp()}",
        "clinic_id": "3e411ecb-3411-4add-91e2-8fa897310cb0",
        "clinic_name": "Shtern Dental Clinic",
        "message_type": "text",
        "channel": "widget",
        "profile_name": "Test User"
    }
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            response = await client.post(PRODUCTION_URL, json=payload)
            if response.status_code == 200:
                data = response.json()
                return {
                    "query": query,
                    "success": True,
                    "knowledge_used": data.get("metadata", {}).get("knowledge_used", 0),
                    "response_preview": data.get("message", "")[:100] + "...",
                    "language": data.get("detected_language", "unknown")
                }
            else:
                return {
                    "query": query,
                    "success": False,
                    "error": f"Status {response.status_code}",
                    "knowledge_used": 0
                }
        except Exception as e:
            return {
                "query": query,
                "success": False,
                "error": str(e),
                "knowledge_used": 0
            }

async def main():
    print("🧪 Testing Improved RAG System")
    print("=" * 60)
    print(f"Endpoint: {PRODUCTION_URL}")
    print(f"Testing {len(TEST_QUERIES)} queries\n")
    
    results = []
    total_knowledge = 0
    successful_queries = 0
    
    for query, lang in TEST_QUERIES:
        print(f"Testing: {query[:50]}...")
        result = await test_query(query, lang)
        results.append(result)
        
        if result["success"]:
            successful_queries += 1
            knowledge_count = result["knowledge_used"]
            total_knowledge += knowledge_count
            
            if knowledge_count > 0:
                print(f"  ✅ Retrieved {knowledge_count} knowledge items")
            else:
                print(f"  ⚠️ No knowledge retrieved")
        else:
            print(f"  ❌ Failed: {result.get('error', 'Unknown error')}")
        
        # Small delay between requests
        await asyncio.sleep(1)
    
    # Print summary
    print("\n" + "=" * 60)
    print("📊 RESULTS SUMMARY")
    print("=" * 60)
    
    print(f"\nSuccess Rate: {successful_queries}/{len(TEST_QUERIES)} ({successful_queries/len(TEST_QUERIES)*100:.1f}%)")
    print(f"Total Knowledge Items Retrieved: {total_knowledge}")
    print(f"Average Knowledge per Query: {total_knowledge/len(TEST_QUERIES):.2f}")
    
    # Detailed breakdown
    print("\n📋 Detailed Results:")
    print("-" * 60)
    
    for result in results:
        status = "✅" if result["success"] else "❌"
        knowledge = result.get("knowledge_used", 0)
        query = result["query"][:40]
        
        print(f"{status} {query:40} | Knowledge: {knowledge}")
        if knowledge == 0 and result["success"]:
            print(f"   ⚠️ Warning: Query succeeded but no knowledge retrieved")
    
    # Analysis
    print("\n🔍 Analysis:")
    print("-" * 60)
    
    queries_with_knowledge = sum(1 for r in results if r.get("knowledge_used", 0) > 0)
    queries_without_knowledge = sum(1 for r in results if r["success"] and r.get("knowledge_used", 0) == 0)
    
    print(f"Queries with knowledge: {queries_with_knowledge}/{len(TEST_QUERIES)} ({queries_with_knowledge/len(TEST_QUERIES)*100:.1f}%)")
    print(f"Queries without knowledge: {queries_without_knowledge}/{len(TEST_QUERIES)} ({queries_without_knowledge/len(TEST_QUERIES)*100:.1f}%)")
    
    if queries_with_knowledge < len(TEST_QUERIES) * 0.5:
        print("\n⚠️ WARNING: Less than 50% of queries retrieved knowledge")
        print("The RAG system may still need tuning")
    elif queries_with_knowledge >= len(TEST_QUERIES) * 0.8:
        print("\n✅ SUCCESS: 80%+ of queries retrieved knowledge")
        print("The improved RAG system is working well!")
    else:
        print("\n⚡ MODERATE: 50-80% of queries retrieved knowledge")
        print("The RAG system is improved but could be better")
    
    # Save results
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"rag_test_results_{timestamp}.json"
    with open(filename, 'w') as f:
        json.dump({
            "timestamp": datetime.now().isoformat(),
            "endpoint": PRODUCTION_URL,
            "summary": {
                "total_queries": len(TEST_QUERIES),
                "successful": successful_queries,
                "total_knowledge_retrieved": total_knowledge,
                "average_knowledge_per_query": total_knowledge/len(TEST_QUERIES)
            },
            "results": results
        }, f, indent=2)
    
    print(f"\n💾 Results saved to: {filename}")

if __name__ == "__main__":
    asyncio.run(main())