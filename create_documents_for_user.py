#!/usr/bin/env python3
"""
Create sample documents for the actual logged-in user's organization
"""

import asyncio
import os
from dotenv import load_dotenv
from app.api.knowledge_ingestion import KnowledgeIngestionPipeline

load_dotenv()

async def create_documents_for_user():
    """Create documents for the user's actual organization_id"""
    
    # Use the organization_id from the logged-in user (Dani Castro)
    clinic_id = "4e8ddba1-ad52-4613-9a03-ec64636b3f6c"
    
    print(f"Creating documents for organization_id: {clinic_id}")
    print(f"User: Dani Castro (trener2055@gmail.com)")
    
    # Initialize pipeline
    pipeline = KnowledgeIngestionPipeline(clinic_id)
    
    # Sample documents
    documents = [
        {
            "content": """
            Welcome to Our Dental Practice
            
            We provide comprehensive dental care for the whole family.
            Our services include:
            - Regular checkups and cleanings
            - Cosmetic dentistry
            - Dental implants
            - Emergency dental care
            
            Schedule your appointment today!
            """,
            "metadata": {
                'url': 'https://example.com/welcome',
                'title': 'Welcome to Our Practice',
                'source': 'manual'
            },
            "category": "general"
        },
        {
            "content": """
            Patient Care Instructions
            
            Before Your Visit:
            - Arrive 15 minutes early for paperwork
            - Bring your insurance card and ID
            - List all medications you're taking
            
            After Treatment:
            - Follow all post-operative instructions
            - Take medications as prescribed
            - Contact us if you have concerns
            """,
            "metadata": {
                'url': 'https://example.com/patient-care',
                'title': 'Patient Care Instructions',
                'source': 'manual'
            },
            "category": "procedures"
        },
        {
            "content": """
            Insurance and Payment Information
            
            We accept most major insurance plans.
            Payment options include:
            - Cash
            - Credit cards
            - Payment plans for qualified patients
            
            Please contact our office for insurance verification.
            """,
            "metadata": {
                'url': 'https://example.com/insurance',
                'title': 'Insurance Information',
                'source': 'manual'
            },
            "category": "policies"
        }
    ]
    
    # Ingest each document
    for i, doc in enumerate(documents, 1):
        print(f"\n[{i}/{len(documents)}] Ingesting: {doc['metadata']['title']}")
        try:
            result = await pipeline.ingest_document(
                content=doc["content"],
                metadata=doc["metadata"],
                category=doc["category"]
            )
            if result:
                print(f"  ✅ Success - Doc ID: {result.get('doc_id')}, DB ID: {result.get('db_id')}")
            else:
                print(f"  ❌ Failed - No result returned")
        except Exception as e:
            print(f"  ❌ Error: {e}")
    
    print(f"\n✨ Documents created for organization {clinic_id}!")

if __name__ == "__main__":
    asyncio.run(create_documents_for_user())