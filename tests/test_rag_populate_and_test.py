#!/usr/bin/env python3
"""
RAG System Test with Data Population

This script:
1. Populates Pinecone with structured data from doctors and services
2. Tests the retrieval system
"""

import os
import sys
import asyncio
import json
from datetime import datetime

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import create_supabase_client
from app.api.structured_data_embedder import StructuredDataEmbedder
from app.api.hybrid_search_engine import HybridSearchEngine
from app.api.entity_extractor import MedicalEntityExtractor


async def populate_pinecone_data():
    """Populate Pinecone with structured data"""
    
    print("\n" + "="*60)
    print("POPULATING PINECONE WITH STRUCTURED DATA")
    print("="*60)
    
    clinic_id = "e0c84f56-235d-49f2-9a44-37c1be579afc"
    supabase = create_supabase_client()
    
    try:
        # Initialize embedder
        embedder = StructuredDataEmbedder(clinic_id, supabase)
        
        # Embed doctors
        print("\n1. Embedding doctors...")
        doctors_result = await embedder.embed_doctors()
        print(f"   Indexed {doctors_result['indexed_count']} doctors")
        
        # Embed services
        print("\n2. Embedding services...")
        services_result = await embedder.embed_services()
        print(f"   Indexed {services_result['indexed_count']} services")
        
        print("\nData population complete!")
        return True
        
    except Exception as e:
        print(f"\nERROR during data population: {e}")
        import traceback
        traceback.print_exc()
        return False


async def test_retrieval_after_population():
    """Test retrieval after data is populated"""
    
    print("\n" + "="*60)
    print("TESTING RETRIEVAL WITH POPULATED DATA")
    print("="*60)
    
    clinic_id = "e0c84f56-235d-49f2-9a44-37c1be579afc"
    
    # Initialize components
    search_engine = HybridSearchEngine(clinic_id)
    entity_extractor = MedicalEntityExtractor()
    
    # Test queries focused on what we know exists
    test_queries = [
        "Show me oral surgeons",
        "I need a dental implant",
        "What endodontic services do you have?",
        "Dr. Mark Shtern",
        "Root canal treatment",
        "How much does a tooth extraction cost?",
    ]
    
    print(f"\nTesting {len(test_queries)} queries...\n")
    
    success_count = 0
    for i, query in enumerate(test_queries, 1):
        print(f"\nQuery {i}: '{query}'")
        print("-" * 50)
        
        try:
            # Extract entities
            entities = await entity_extractor.extract_medical_context(query)
            print(f"Entities: {json.dumps({k: v for k, v in entities.items() if v}, indent=2)}")
            
            # Perform search
            import time
            start_time = time.time()
            
            results = await search_engine.hybrid_search(
                query=query,
                top_k=3,
                use_patient_context=False
            )
            
            elapsed_ms = (time.time() - start_time) * 1000
            
            print(f"\nSearch completed in {elapsed_ms:.1f}ms")
            print(f"Found {len(results)} results")
            
            if results:
                success_count += 1
                # Show first 2 results
                for j in range(min(2, len(results))):
                    result = results[j]
                    print(f"\n  Result {j+1}:")

                    # Handle both dict and string results
                    if isinstance(result, dict):
                        print(f"    Type: {result.get('type', 'unknown')}")
                        print(f"    Source: {result.get('source', 'unknown')}")
                        text = result.get('text', '')
                    else:
                        print(f"    Type: text")
                        print(f"    Source: vector_search")
                        text = str(result)

                    # Show text preview
                    preview = text[:100] + "..." if len(text) > 100 else text
                    print(f"    Preview: {preview}")
            else:
                print("  No results found")
            
        except Exception as e:
            print(f"  ERROR: {e}")
    
    print("\n" + "="*60)
    print(f"RESULTS: {success_count}/{len(test_queries)} queries returned results")
    print("="*60)
    
    return success_count > 0


async def test_specific_searches():
    """Test specific search capabilities"""
    
    print("\n" + "="*60)
    print("TESTING SPECIFIC SEARCH CAPABILITIES")
    print("="*60)
    
    clinic_id = "e0c84f56-235d-49f2-9a44-37c1be579afc"
    supabase = create_supabase_client()
    search_engine = HybridSearchEngine(clinic_id)
    
    # Test structured search directly
    print("\n1. Testing Structured Search for Doctors...")
    entities = {'doctor_name': 'Shtern'}
    results = await search_engine._structured_search("Dr. Shtern", entities)
    print(f"   Found {len(results)} doctors")
    if results:
        print(f"   First result: {results[0].get('text', 'No text')}")
    
    # Test structured search for services
    print("\n2. Testing Structured Search for Services...")
    entities = {'service_category': 'endodontics'}
    results = await search_engine._structured_search("root canal", entities)
    print(f"   Found {len(results)} services")
    if results:
        print(f"   First result: {results[0].get('text', 'No text')}")
    
    # Test vector search (if Pinecone is populated)
    print("\n3. Testing Vector Search...")
    try:
        results = await search_engine._vector_search("dental implants", {})
        print(f"   Found {len(results)} vector results")
        if results:
            print(f"   First result type: {results[0].get('type', 'unknown')}")
    except Exception as e:
        print(f"   Vector search failed: {e}")


async def main():
    """Main test function"""
    
    print("\nRAG System Test with Data Population")
    print("Current time:", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    
    # Check environment
    required_vars = ['OPENAI_API_KEY', 'SUPABASE_URL', 'SUPABASE_ANON_KEY', 'PINECONE_API_KEY']
    missing = [v for v in required_vars if not os.environ.get(v)]
    
    if missing:
        print(f"\nWARNING: Missing environment variables: {', '.join(missing)}")
        if 'PINECONE_API_KEY' in missing:
            print("PINECONE_API_KEY is required for this test. Please set it and try again.")
            return
    
    try:
        # First, populate data
        success = await populate_pinecone_data()
        
        if success:
            # Wait a moment for indexing
            print("\nWaiting 5 seconds for Pinecone indexing...")
            await asyncio.sleep(5)
            
            # Test specific searches
            await test_specific_searches()
            
            # Then test retrieval
            await test_retrieval_after_population()
        else:
            print("\nSkipping retrieval tests due to population failure.")
        
        print("\n" + "="*60)
        print("TEST COMPLETED")
        print("="*60)
        
    except Exception as e:
        print(f"\nERROR: Test failed with exception: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())