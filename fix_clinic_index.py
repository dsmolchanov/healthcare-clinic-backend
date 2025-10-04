#!/usr/bin/env python3
"""
Fix Clinic Index Issue
Re-index documents to the correct Pinecone index
"""

import asyncio
import os
import sys
from pathlib import Path
import logging

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent))

from app.database import get_db_connection
from app.api.knowledge_ingestion import KnowledgeIngestionPipeline
from pinecone import Pinecone

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def check_current_state():
    """Check the current state of documents and indexes"""
    
    print("\n" + "="*80)
    print("CHECKING CURRENT STATE")
    print("="*80 + "\n")
    
    # Check Pinecone indexes
    pc = Pinecone(api_key=os.environ.get('PINECONE_API_KEY'))
    
    # Check Shtern Dental Clinic index
    shtern_index_name = "clinic-3e411ecb-kb"
    try:
        shtern_index = pc.Index(shtern_index_name)
        stats = shtern_index.describe_index_stats()
        print(f"✓ Shtern Dental Index ({shtern_index_name}):")
        print(f"  Vectors: {stats.total_vector_count}")
    except:
        print(f"✗ Shtern Dental Index ({shtern_index_name}) not found")
    
    # Check database documents
    async with get_db_connection() as conn:
        docs = await conn.fetch("""
            SELECT clinic_id, COUNT(*) as count, 
                   STRING_AGG(title, ', ' ORDER BY title) as titles
            FROM knowledge_documents
            GROUP BY clinic_id
        """)
        
        print("\nDatabase Documents:")
        for doc in docs:
            print(f"  Clinic {doc['clinic_id']}:")
            print(f"    Document count: {doc['count']}")
            print(f"    Titles: {doc['titles'][:100]}...")


async def reindex_documents():
    """Re-index documents to correct Pinecone index"""
    
    print("\n" + "="*80)
    print("RE-INDEXING DOCUMENTS")
    print("="*80 + "\n")
    
    clinic_id = "3e411ecb-3411-4add-91e2-8fa897310cb0"
    
    async with get_db_connection() as conn:
        # Get all documents for Shtern Dental
        docs = await conn.fetch("""
            SELECT id, title, raw_content, processed_content, 
                   source_filename, category, metadata
            FROM knowledge_documents
            WHERE clinic_id = $1
            ORDER BY processed_at DESC
        """, clinic_id)
        
        print(f"Found {len(docs)} documents to index")
        
        # Initialize pipeline
        pipeline = KnowledgeIngestionPipeline(clinic_id)
        
        for doc in docs:
            content = doc['raw_content'] or doc['processed_content'] or ''
            if not content:
                print(f"  ⚠️ Skipping {doc['title']} - no content")
                continue
            
            print(f"  Indexing: {doc['title']}")
            
            try:
                # Re-index the document
                result = await pipeline.ingest_document(
                    content=content,
                    metadata={
                        'title': doc['title'],
                        'filename': doc['source_filename'],
                        'category': doc['category'],
                        'doc_id': str(doc['id'])
                    },
                    category=doc['category']
                )
                
                if result['status'] == 'indexed':
                    print(f"    ✓ Indexed with {result['chunks']} chunks")
                elif result['status'] == 'already_indexed':
                    print(f"    ⚠️ Already indexed")
                else:
                    print(f"    ✗ Failed: {result}")
                    
            except Exception as e:
                print(f"    ✗ Error: {e}")
    
    print("\nRe-indexing complete!")


async def test_retrieval():
    """Test retrieval after re-indexing"""
    
    print("\n" + "="*80)
    print("TESTING RETRIEVAL")
    print("="*80 + "\n")
    
    from app.api.improved_knowledge_base import ImprovedPineconeKnowledgeBase
    
    clinic_id = "3e411ecb-3411-4add-91e2-8fa897310cb0"
    kb = ImprovedPineconeKnowledgeBase(clinic_id)
    
    test_queries = [
        "root canal price",
        "root canal cost",
        "how much does root canal cost",
        "endodontics price"
    ]
    
    for query in test_queries:
        print(f"\nQuery: '{query}'")
        
        results = await kb.search(
            query=query,
            metadata_filter=None
        )
        
        if results:
            print(f"  ✓ Found {len(results)} results")
            best = results[0]
            print(f"    Score: {best['score']:.3f}")
            print(f"    Text: {best['text'][:150]}...")
            
            # Check if price is in the result
            if '$400' in best['text'] or 'root canal' in best['text'].lower():
                print("    💰 Contains root canal price!")
        else:
            print(f"  ✗ No results")


async def main():
    """Main function"""
    
    print("\n" + "="*80)
    print("FIXING CLINIC INDEX FOR PRICE RETRIEVAL")
    print("="*80)
    
    # Check current state
    await check_current_state()
    
    # Ask for confirmation
    print("\nThis will re-index documents to Pinecone.")
    response = input("Continue? (y/n): ")
    
    if response.lower() == 'y':
        await reindex_documents()
        await test_retrieval()
    else:
        print("Cancelled.")
    
    print("\n✅ Complete!")


if __name__ == "__main__":
    if not os.environ.get('PINECONE_API_KEY'):
        print("Error: PINECONE_API_KEY not set")
        sys.exit(1)
    
    asyncio.run(main())