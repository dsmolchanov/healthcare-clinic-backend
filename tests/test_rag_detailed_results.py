#!/usr/bin/env python3
"""
Detailed RAG Results Test

Shows complete results for each query
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


async def test_detailed_results():
    """Test and display detailed results for each query"""
    
    print("\n" + "="*80)
    print("DETAILED RAG RETRIEVAL RESULTS")
    print("="*80)
    
    clinic_id = "e0c84f56-235d-49f2-9a44-37c1be579afc"
    
    # Initialize components
    search_engine = HybridSearchEngine(clinic_id)
    entity_extractor = MedicalEntityExtractor()
    
    # Test queries
    test_queries = [
        "I need to see a dentist",
        "Show me oral surgeons", 
        "Dr. Mark Shtern",
        "Root canal treatment",
        "Dental implant services",
        "How much does a tooth extraction cost?",
        "What endodontic services do you have?",
        "Do you accept insurance?",
        "Emergency dental care",
        "Teeth cleaning appointment"
    ]
    
    for i, query in enumerate(test_queries, 1):
        print(f"\n{'='*80}")
        print(f"QUERY {i}: '{query}'")
        print("="*80)
        
        try:
            # Extract entities
            entities = await entity_extractor.extract_medical_context(query)
            
            # Only show non-empty entities
            relevant_entities = {k: v for k, v in entities.items() if v}
            if relevant_entities:
                print("\n📊 Extracted Entities:")
                for key, value in relevant_entities.items():
                    print(f"  • {key}: {value}")
            
            # Perform search
            import time
            start_time = time.time()
            
            results = await search_engine.hybrid_search(
                query=query,
                top_k=5,
                use_patient_context=False
            )
            
            elapsed_ms = (time.time() - start_time) * 1000
            
            print(f"\n⏱️  Search Time: {elapsed_ms:.1f}ms")
            print(f"📋 Results Found: {len(results)}")
            
            if results:
                print("\n🔍 SEARCH RESULTS:")
                print("-" * 80)
                
                for j, result in enumerate(results, 1):
                    print(f"\n[Result {j}]")
                    
                    # Result metadata
                    if isinstance(result, dict):
                        result_type = result.get('type', 'unknown')
                        source = result.get('source', 'unknown')
                        score = result.get('final_score', result.get('retrieval_score', 0))
                        
                        print(f"  Type: {result_type}")
                        print(f"  Source: {source}")
                        print(f"  Score: {score:.3f}")
                        
                        # Show metadata if available
                        metadata = result.get('metadata', {})
                        if metadata:
                            print(f"  Metadata:")
                            # Show important metadata fields
                            if 'doctor_id' in metadata:
                                print(f"    - Doctor: {metadata.get('name', 'Unknown')}")
                                if 'specialization' in metadata:
                                    print(f"    - Specialization: {metadata['specialization']}")
                            elif 'service_id' in metadata:
                                print(f"    - Service: {metadata.get('name', 'Unknown')}")
                                if 'category' in metadata:
                                    print(f"    - Category: {metadata['category']}")
                                if 'base_price' in metadata:
                                    print(f"    - Price: ${metadata['base_price']}")
                        
                        # Show content
                        text = result.get('text', '')
                        if text:
                            print(f"  Content:")
                            # Show up to 200 chars
                            if len(text) > 200:
                                preview = text[:200] + "..."
                            else:
                                preview = text
                            # Indent the content
                            for line in preview.split('\n'):
                                print(f"    {line}")
                    else:
                        # Handle string results
                        print(f"  Type: text")
                        print(f"  Content: {str(result)[:200]}...")
            else:
                print("\n❌ No results found for this query")
                
        except Exception as e:
            print(f"\n❌ ERROR processing query: {e}")
            import traceback
            traceback.print_exc()
    
    print("\n" + "="*80)
    print("TEST COMPLETED")
    print("="*80)


async def test_source_distribution():
    """Analyze the distribution of result sources"""
    
    print("\n" + "="*80)
    print("SOURCE DISTRIBUTION ANALYSIS")
    print("="*80)
    
    clinic_id = "e0c84f56-235d-49f2-9a44-37c1be579afc"
    search_engine = HybridSearchEngine(clinic_id)
    
    # Test different query types
    queries = [
        "doctors",
        "services",
        "appointments",
        "insurance",
        "emergency care"
    ]
    
    source_stats = {}
    type_stats = {}
    
    for query in queries:
        results = await search_engine.hybrid_search(query, top_k=10)
        
        for result in results:
            if isinstance(result, dict):
                # Track sources
                sources = result.get('sources', [result.get('source', 'unknown')])
                for source in sources:
                    source_stats[source] = source_stats.get(source, 0) + 1
                
                # Track types
                result_type = result.get('type', 'unknown')
                type_stats[result_type] = type_stats.get(result_type, 0) + 1
    
    print("\n📊 Result Sources:")
    for source, count in sorted(source_stats.items(), key=lambda x: x[1], reverse=True):
        print(f"  • {source}: {count} results")
    
    print("\n📊 Result Types:")
    for rtype, count in sorted(type_stats.items(), key=lambda x: x[1], reverse=True):
        print(f"  • {rtype}: {count} results")


async def main():
    """Run all tests"""
    
    print("\nDetailed RAG Retrieval Test")
    print("Time:", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    
    # Check environment
    required_vars = ['OPENAI_API_KEY', 'SUPABASE_URL', 'SUPABASE_ANON_KEY']
    missing = [v for v in required_vars if not os.environ.get(v)]
    
    if missing:
        print(f"\nWARNING: Missing environment variables: {', '.join(missing)}")
    
    try:
        # Run detailed results test
        await test_detailed_results()
        
        # Run source distribution analysis
        await test_source_distribution()
        
    except Exception as e:
        print(f"\nERROR: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())