#!/usr/bin/env python3
"""
Test direct document ingestion to diagnose issues
"""

import asyncio
import os
from dotenv import load_dotenv
from app.api.knowledge_ingestion import KnowledgeIngestionPipeline

load_dotenv()

async def test_direct_ingestion():
    """Test ingesting a document directly"""
    
    clinic_id = "e0c84f56-235d-49f2-9a44-37c1be579afc"
    
    # Initialize pipeline
    print("Initializing pipeline...")
    pipeline = KnowledgeIngestionPipeline(clinic_id)
    
    # Test content
    content = """
    This is a test document for the knowledge base.
    It contains information about dental procedures and treatments.
    Regular checkups are important for maintaining oral health.
    """
    
    metadata = {
        'url': 'https://test.example.com',
        'title': 'Test Document',
        'source': 'manual'
    }
    
    # Try to ingest
    print("Attempting to ingest document...")
    try:
        result = await pipeline.ingest_document(
            content=content,
            metadata=metadata,
            category='general'
        )
        print(f"Ingestion result: {result}")
        
        # Check what type of result we got
        if result:
            print(f"Result type: {type(result)}")
            print(f"Result keys: {result.keys() if isinstance(result, dict) else 'Not a dict'}")
            if isinstance(result, dict):
                print(f"Status: {result.get('status')}")
                print(f"Doc ID: {result.get('doc_id')}")
                print(f"DB ID: {result.get('db_id')}")
                print(f"Chunks: {result.get('chunks')}")
        else:
            print("Result is None or empty")
            
    except Exception as e:
        print(f"Error during ingestion: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_direct_ingestion())