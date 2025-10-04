#!/usr/bin/env python3
"""
Test Price List Retrieval
Evaluate why RAG is not finding price information
"""

import asyncio
import os
import sys
from pathlib import Path
import logging
from datetime import datetime

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent))

from app.api.improved_knowledge_base import ImprovedPineconeKnowledgeBase
from app.database import get_db_connection
import json

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


async def check_price_documents():
    """Check what price-related documents are in the database"""
    
    print("\n" + "="*80)
    print("CHECKING PRICE DOCUMENTS IN DATABASE")
    print("="*80 + "\n")
    
    async with get_db_connection() as conn:
        # Find documents that might contain pricing
        price_docs = await conn.fetch("""
            SELECT id, title, source_filename, category, chunk_count, 
                   processed_at, raw_content
            FROM knowledge_documents
            WHERE clinic_id = 'e0c84f56-58c1-4089-a2d9-18af2e58dda7'
            AND (
                LOWER(title) LIKE '%price%' 
                OR LOWER(source_filename) LIKE '%price%'
                OR LOWER(raw_content) LIKE '%root canal%'
                OR LOWER(raw_content) LIKE '%conducto%'
                OR LOWER(raw_content) LIKE '%$%'
            )
            ORDER BY processed_at DESC
        """)
        
        print(f"Found {len(price_docs)} potential price documents:\n")
        
        for doc in price_docs:
            print(f"📄 Document ID: {doc['id']}")
            print(f"   Title: {doc['title']}")
            print(f"   Filename: {doc['source_filename']}")
            print(f"   Category: {doc['category']}")
            print(f"   Chunks: {doc['chunk_count']}")
            print(f"   Processed: {doc['processed_at']}")
            
            # Check content for prices
            content = doc['raw_content'] or ''
            if 'root canal' in content.lower():
                print("   ✓ Contains 'root canal'")
            if 'conducto' in content.lower():
                print("   ✓ Contains 'conducto'")
            if '$' in content:
                # Extract price mentions
                import re
                prices = re.findall(r'\$[\d,]+(?:\.\d{2})?', content)
                if prices:
                    print(f"   💰 Prices found: {', '.join(prices[:5])}")
            print()
        
        return price_docs


async def test_rag_retrieval():
    """Test RAG retrieval with various price queries"""
    
    print("\n" + "="*80)
    print("TESTING RAG RETRIEVAL FOR PRICE QUERIES")
    print("="*80 + "\n")
    
    # Initialize knowledge base with the correct clinic ID
    clinic_id = "e0c84f56-58c1-4089-a2d9-18af2e58dda7"
    kb = ImprovedPineconeKnowledgeBase(clinic_id)
    
    # Test queries
    test_queries = [
        "root canal price",
        "root canal cost",
        "how much does root canal cost",
        "precio conducto",
        "conducto radicular precio",
        "dental prices",
        "price list",
        "treatment costs",
        "$",
        "1200",
        "root canal",
        "endodoncia precio"
    ]
    
    print("Testing various price-related queries:\n")
    
    for query in test_queries:
        print(f"Query: '{query}'")
        
        # Test with different configurations
        results = await kb.search(
            query=query,
            top_k=5,
            metadata_filter=None  # No filter first
        )
        
        if results:
            print(f"  ✓ Found {len(results)} results")
            for i, result in enumerate(results[:2], 1):
                score = result.get('score', 0)
                text_preview = result.get('text', '')[:100]
                metadata = result.get('metadata', {})
                print(f"    [{i}] Score: {score:.3f}")
                print(f"        Category: {metadata.get('category', 'unknown')}")
                print(f"        Title: {metadata.get('title', 'unknown')}")
                print(f"        Preview: {text_preview}...")
        else:
            print(f"  ✗ No results found")
        
        print()
    
    # Test with category filter
    print("\nTesting with category filters:\n")
    
    categories = ['general', 'pricing', 'services', 'policies']
    for category in categories:
        print(f"Category: '{category}'")
        results = await kb.search(
            query="root canal",
            top_k=3,
            metadata_filter={"category": category}
        )
        
        if results:
            print(f"  ✓ Found {len(results)} results in {category}")
        else:
            print(f"  ✗ No results in {category}")


async def check_pinecone_vectors():
    """Check what's actually in Pinecone"""
    
    print("\n" + "="*80)
    print("CHECKING PINECONE VECTORS")
    print("="*80 + "\n")
    
    from pinecone import Pinecone
    
    # Initialize Pinecone
    pc = Pinecone(api_key=os.environ.get('PINECONE_API_KEY'))
    index_name = "clinic-e0c84f56-kb"
    index = pc.Index(index_name)
    
    # Get index stats
    stats = index.describe_index_stats()
    print(f"Index: {index_name}")
    print(f"Total vectors: {stats.total_vector_count}")
    print(f"Dimension: {stats.dimension}")
    
    # Query with a dummy vector to see metadata
    print("\nSampling vectors to check metadata:\n")
    
    # Create a query vector (zeros)
    query_vector = [0.0] * 1536
    
    # Query to get sample vectors
    results = index.query(
        vector=query_vector,
        top_k=10,
        include_metadata=True,
        filter={
            "clinic_id": "e0c84f56-58c1-4089-a2d9-18af2e58dda7"
        }
    )
    
    # Analyze metadata
    categories = {}
    titles = {}
    has_price_content = 0
    
    for match in results.matches:
        metadata = match.metadata
        category = metadata.get('category', 'unknown')
        title = metadata.get('title', 'unknown')
        text = metadata.get('text', '')
        
        categories[category] = categories.get(category, 0) + 1
        titles[title] = titles.get(title, 0) + 1
        
        # Check for price-related content
        if any(word in text.lower() for word in ['price', 'cost', '$', 'root canal', 'conducto']):
            has_price_content += 1
            print(f"Found price-related content in: {title[:50]}")
            print(f"  Preview: {text[:100]}...")
    
    print(f"\nCategories found: {categories}")
    print(f"Titles found: {list(titles.keys())[:5]}")
    print(f"Vectors with price content: {has_price_content}/{len(results.matches)}")


async def test_direct_vector_search():
    """Test searching for specific document vectors"""
    
    print("\n" + "="*80)
    print("TESTING DIRECT VECTOR SEARCH")
    print("="*80 + "\n")
    
    from pinecone import Pinecone
    from openai import OpenAI
    
    # Initialize clients
    pc = Pinecone(api_key=os.environ.get('PINECONE_API_KEY'))
    openai_client = OpenAI()
    index = pc.Index("clinic-e0c84f56-kb")
    
    # Create embeddings for specific price-related queries
    test_texts = [
        "Root Canal $1200",
        "Endodoncia conducto radicular mil doscientos dolares",
        "Dental treatment price list costs",
        "How much does a root canal cost? The price is $1200"
    ]
    
    print("Creating embeddings and searching for price-specific content:\n")
    
    for text in test_texts:
        # Create embedding
        response = openai_client.embeddings.create(
            model="text-embedding-3-small",
            input=text
        )
        embedding = response.data[0].embedding
        
        # Search with this embedding
        results = index.query(
            vector=embedding,
            top_k=5,
            include_metadata=True,
            filter={
                "clinic_id": "e0c84f56-58c1-4089-a2d9-18af2e58dda7"
            }
        )
        
        print(f"Query text: '{text}'")
        print(f"Results found: {len(results.matches)}")
        
        if results.matches:
            best_match = results.matches[0]
            print(f"  Best match score: {best_match.score:.3f}")
            print(f"  Title: {best_match.metadata.get('title', 'unknown')}")
            print(f"  Text: {best_match.metadata.get('text', '')[:150]}...")
        print()


async def diagnose_issue():
    """Diagnose why price retrieval is failing"""
    
    print("\n" + "="*80)
    print("DIAGNOSIS SUMMARY")
    print("="*80 + "\n")
    
    issues = []
    
    # Check if price documents exist
    price_docs = await check_price_documents()
    if not price_docs:
        issues.append("❌ No price documents found in database")
    else:
        issues.append(f"✓ Found {len(price_docs)} price-related documents")
    
    # Check Pinecone vectors
    from pinecone import Pinecone
    pc = Pinecone(api_key=os.environ.get('PINECONE_API_KEY'))
    index = pc.Index("clinic-e0c84f56-kb")
    stats = index.describe_index_stats()
    
    if stats.total_vector_count == 0:
        issues.append("❌ No vectors in Pinecone index")
    else:
        issues.append(f"✓ {stats.total_vector_count} vectors in Pinecone")
    
    # Check similarity threshold
    kb = ImprovedPineconeKnowledgeBase("e0c84f56-58c1-4089-a2d9-18af2e58dda7")
    threshold = kb.similarity_threshold
    if threshold > 0.5:
        issues.append(f"⚠️ Similarity threshold might be too high: {threshold}")
    else:
        issues.append(f"✓ Similarity threshold is reasonable: {threshold}")
    
    print("Issues found:")
    for issue in issues:
        print(f"  {issue}")
    
    print("\nRecommendations:")
    print("1. Re-index the price list document with better chunking")
    print("2. Use category='pricing' for price documents")
    print("3. Lower similarity threshold if needed")
    print("4. Ensure price content is properly embedded")


async def main():
    """Run all tests"""
    
    print("\n" + "="*80)
    print("PRICE LIST RETRIEVAL EVALUATION")
    print("="*80)
    print(f"Timestamp: {datetime.now().isoformat()}")
    print(f"Clinic ID: e0c84f56-58c1-4089-a2d9-18af2e58dda7")
    
    # Run tests
    await check_price_documents()
    await check_pinecone_vectors()
    await test_rag_retrieval()
    await test_direct_vector_search()
    await diagnose_issue()
    
    print("\n" + "="*80)
    print("EVALUATION COMPLETE")
    print("="*80)


if __name__ == "__main__":
    # Check environment
    if not os.environ.get('PINECONE_API_KEY'):
        print("Error: PINECONE_API_KEY not set")
        sys.exit(1)
    
    if not os.environ.get('OPENAI_API_KEY'):
        print("Error: OPENAI_API_KEY not set")
        sys.exit(1)
    
    asyncio.run(main())