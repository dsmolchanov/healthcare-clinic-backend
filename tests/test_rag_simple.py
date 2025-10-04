#!/usr/bin/env python3
"""
Simple RAG Retrieval Test

Quick test to verify the enhanced RAG system is working
"""

import os
import sys
import asyncio
import json
from datetime import datetime

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.api.hybrid_search_engine import HybridSearchEngine
from app.api.entity_extractor import MedicalEntityExtractor
from app.database import create_supabase_client


async def test_basic_retrieval():
    """Test basic retrieval functionality"""
    
    print("\n" + "="*60)
    print("TESTING ENHANCED RAG RETRIEVAL SYSTEM")
    print("="*60)
    
    # Use a test clinic ID (UUID for Shtern Dental Clinic)
    clinic_id = "e0c84f56-235d-49f2-9a44-37c1be579afc"
    
    # Initialize components
    print("\n1. Initializing components...")
    search_engine = HybridSearchEngine(clinic_id)
    entity_extractor = MedicalEntityExtractor()
    
    # Test queries
    test_queries = [
        "I need to see a dentist",
        "What services do you offer?",
        "Do you have appointments available tomorrow?",
        "I have tooth pain and need urgent care",
        "How much does a dental cleaning cost?",
        "Do you accept insurance?",
    ]
    
    print(f"\n2. Testing {len(test_queries)} queries...\n")
    
    for i, query in enumerate(test_queries, 1):
        print(f"\nQuery {i}: '{query}'")
        print("-" * 50)
        
        try:
            # Extract entities
            entities = await entity_extractor.extract_medical_context(query)
            print(f"Extracted Entities: {json.dumps(entities, indent=2)}")
            
            # Perform search
            import time
            start_time = time.time()
            
            results = await search_engine.hybrid_search(
                query=query,
                top_k=3,
                use_patient_context=False  # No patient context for simple test
            )
            
            elapsed_ms = (time.time() - start_time) * 1000
            
            print(f"\nSearch completed in {elapsed_ms:.1f}ms")
            print(f"Found {len(results)} results:")
            
            for j, result in enumerate(results, 1):
                print(f"\n  Result {j}:")
                print(f"    Type: {result.get('type', 'unknown')}")
                print(f"    Source: {result.get('source', 'unknown')}")
                print(f"    Score: {result.get('final_score', 0):.3f}")
                
                # Show preview of text
                text = result.get('text', '')
                preview = text[:150] + "..." if len(text) > 150 else text
                print(f"    Preview: {preview}")
                
                # Show metadata if available
                metadata = result.get('metadata', {})
                if metadata:
                    print(f"    Metadata:")
                    for key, value in list(metadata.items())[:3]:  # Show first 3 metadata items
                        print(f"      - {key}: {value}")
            
        except Exception as e:
            print(f"  ERROR: {e}")
    
    print("\n" + "="*60)
    print("TEST COMPLETED")
    print("="*60)


async def test_entity_extraction():
    """Test entity extraction separately"""
    
    print("\n" + "="*60)
    print("TESTING ENTITY EXTRACTION")
    print("="*60)
    
    extractor = MedicalEntityExtractor()
    
    test_cases = [
        "I need to see Dr. Smith tomorrow morning",
        "My child has a fever and rash",
        "Do you have a Spanish-speaking cardiologist?",
        "I need an urgent appointment for chest pain",
        "What's the cost of an MRI without insurance?",
    ]
    
    for query in test_cases:
        print(f"\nQuery: '{query}'")
        entities = await extractor.extract_medical_context(query)
        print(f"Entities: {json.dumps(entities, indent=2)}")


async def test_cache_performance():
    """Test cache performance"""
    
    print("\n" + "="*60)
    print("TESTING CACHE PERFORMANCE")
    print("="*60)
    
    clinic_id = "e0c84f56-235d-49f2-9a44-37c1be579afc"
    search_engine = HybridSearchEngine(clinic_id)
    
    query = "What are your office hours?"
    
    # First search (no cache)
    import time
    print(f"\nQuery: '{query}'")
    
    start = time.time()
    results1 = await search_engine.hybrid_search(query, top_k=3)
    time1 = (time.time() - start) * 1000
    print(f"First search (no cache): {time1:.1f}ms")
    
    # Second search (should hit cache)
    start = time.time()
    results2 = await search_engine.hybrid_search(query, top_k=3)
    time2 = (time.time() - start) * 1000
    print(f"Second search (with cache): {time2:.1f}ms")
    
    if time2 < time1:
        speedup = time1 / time2
        print(f"\nCache speedup: {speedup:.1f}x faster")
    else:
        print("\nNo cache speedup detected (cache may be disabled)")


async def test_structured_data():
    """Test structured data retrieval"""
    
    print("\n" + "="*60)
    print("TESTING STRUCTURED DATA RETRIEVAL")
    print("="*60)
    
    clinic_id = "e0c84f56-235d-49f2-9a44-37c1be579afc"
    supabase = create_supabase_client()
    
    # Check if we have doctors and services
    print("\nChecking structured data...")
    
    # Count doctors
    doctors = supabase.table('doctors').select('*', count='exact').eq(
        'clinic_id', clinic_id
    ).eq('active', True).execute()
    
    print(f"Active doctors: {doctors.count if doctors.count else 'None found'}")
    
    if doctors.data and len(doctors.data) > 0:
        print("Sample doctor:")
        doc = doctors.data[0]
        print(f"  - {doc.get('title', 'Dr.')} {doc['first_name']} {doc['last_name']}")
        print(f"    Specialization: {doc.get('specialization', 'General')}")
    
    # Count services
    services = supabase.table('services').select('*', count='exact').eq(
        'clinic_id', clinic_id
    ).eq('active', True).execute()
    
    print(f"\nActive services: {services.count if services.count else 'None found'}")
    
    if services.data and len(services.data) > 0:
        print("Sample services:")
        for svc in services.data[:3]:
            print(f"  - {svc['name']} ({svc.get('category', 'General')})")
            if svc.get('base_price'):
                print(f"    Price: ${svc['base_price']}")


async def main():
    """Run all tests"""
    
    print("\nStarting RAG System Tests...")
    print("Current time:", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    
    # Check environment
    required_vars = ['OPENAI_API_KEY', 'SUPABASE_URL', 'SUPABASE_ANON_KEY']
    missing = [v for v in required_vars if not os.environ.get(v)]
    
    if missing:
        print(f"\nWARNING: Missing environment variables: {', '.join(missing)}")
        print("Some tests may fail without proper configuration.")
    
    try:
        # Run tests
        await test_structured_data()
        await test_entity_extraction()
        await test_basic_retrieval()
        await test_cache_performance()
        
        print("\n" + "="*60)
        print("ALL TESTS COMPLETED SUCCESSFULLY")
        print("="*60)
        
    except Exception as e:
        print(f"\nERROR: Test failed with exception: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())