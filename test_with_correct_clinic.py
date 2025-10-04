#!/usr/bin/env python3
"""
Test document ingestion with the correct clinic_id that matches frontend
"""

import asyncio
import os
from dotenv import load_dotenv
from app.api.knowledge_ingestion import KnowledgeIngestionPipeline

load_dotenv()

async def test_with_correct_clinic():
    """Test ingesting a document with the frontend's organization_id"""
    
    # Use the organization_id from frontend
    clinic_id = "3e411ecb-3411-4add-91e2-8fa897310cb0"
    
    # Initialize pipeline
    print(f"Initializing pipeline for clinic_id: {clinic_id}")
    pipeline = KnowledgeIngestionPipeline(clinic_id)
    
    # Test content
    content = """
    Welcome to Dr. Mark Shtern's Dental Clinic
    
    Our clinic offers comprehensive dental care including:
    - General dentistry and preventive care
    - Cosmetic dentistry and teeth whitening
    - Dental implants and restorations
    - Emergency dental services
    
    Office Hours:
    Monday-Friday: 9:00 AM - 6:00 PM
    Saturday: 10:00 AM - 3:00 PM
    Sunday: Closed
    
    Contact us at (555) 123-4567 to schedule an appointment.
    """
    
    metadata = {
        'url': 'https://drmarkshtern.com/about',
        'title': 'Dr. Mark Shtern Dental Clinic - About Us',
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
        print(f"✅ Ingestion successful!")
        print(f"Result: {result}")
        
        if result and isinstance(result, dict):
            print(f"\nDocument Details:")
            print(f"  Status: {result.get('status')}")
            print(f"  Doc ID: {result.get('doc_id')}")
            print(f"  DB ID: {result.get('db_id')}")
            print(f"  Chunks: {result.get('chunks')}")
            print(f"  Category: {result.get('category')}")
            
    except Exception as e:
        print(f"❌ Error during ingestion: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_with_correct_clinic())