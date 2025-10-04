#!/usr/bin/env python3
"""
Create sample knowledge documents for testing the UI
"""

import asyncio
import os
from dotenv import load_dotenv
from app.api.knowledge_ingestion import KnowledgeIngestionPipeline

load_dotenv()

async def create_sample_documents():
    """Create multiple sample documents for the knowledge base"""
    
    # Use the organization_id from frontend
    clinic_id = "3e411ecb-3411-4add-91e2-8fa897310cb0"
    
    # Initialize pipeline
    print(f"Initializing pipeline for clinic_id: {clinic_id}")
    pipeline = KnowledgeIngestionPipeline(clinic_id)
    
    # Sample documents
    documents = [
        {
            "content": """
            Patient Preparation Instructions for Dental Surgery
            
            Before Your Appointment:
            - Do not eat or drink anything for 8 hours before surgery if general anesthesia will be used
            - For local anesthesia, eat a light meal 2 hours before
            - Arrange for someone to drive you home after the procedure
            - Wear comfortable, loose-fitting clothing
            - Remove all jewelry and contact lenses
            
            Medications:
            - Continue taking prescribed medications unless instructed otherwise
            - Inform us of all medications you're currently taking
            - If you're taking blood thinners, consult with us before the procedure
            """,
            "metadata": {
                'url': 'https://drmarkshtern.com/patient-prep',
                'title': 'Patient Preparation Instructions',
                'source': 'manual'
            },
            "category": "procedures"
        },
        {
            "content": """
            Common Dental Procedures and Treatments
            
            1. Teeth Cleaning (Prophylaxis)
            Regular professional cleaning to remove plaque and tartar buildup.
            Recommended every 6 months for optimal oral health.
            
            2. Fillings
            Treatment for cavities using composite resin or amalgam materials.
            Procedure typically takes 30-60 minutes per tooth.
            
            3. Root Canal Therapy
            Treatment to save infected or damaged teeth by removing infected pulp.
            Usually requires 1-2 visits depending on complexity.
            
            4. Dental Crowns
            Caps placed over damaged teeth to restore shape, size, and strength.
            Requires 2 visits: preparation and placement.
            
            5. Teeth Whitening
            Professional bleaching for brighter, whiter teeth.
            In-office treatment takes about 1 hour with immediate results.
            """,
            "metadata": {
                'url': 'https://drmarkshtern.com/procedures',
                'title': 'Common Dental Procedures Guide',
                'source': 'manual'
            },
            "category": "procedures"
        },
        {
            "content": """
            Insurance and Payment Information
            
            Accepted Insurance Plans:
            - Delta Dental
            - MetLife
            - Cigna
            - Aetna
            - Blue Cross Blue Shield
            - Guardian
            - United Healthcare
            
            Payment Options:
            - Cash
            - Credit/Debit Cards (Visa, MasterCard, Amex, Discover)
            - CareCredit financing
            - Payment plans available for treatments over $500
            
            Insurance Claims:
            We file insurance claims on your behalf and accept assignment of benefits.
            Co-payments are due at the time of service.
            Pre-authorization available for major procedures.
            """,
            "metadata": {
                'url': 'https://drmarkshtern.com/insurance',
                'title': 'Insurance and Payment Options',
                'source': 'manual'
            },
            "category": "policies"
        },
        {
            "content": """
            Post-Operative Care Instructions
            
            After Tooth Extraction:
            - Bite on gauze for 30-45 minutes to control bleeding
            - Apply ice packs to reduce swelling (20 minutes on, 20 minutes off)
            - Avoid hot liquids and alcoholic beverages for 24 hours
            - Do not use straws for 48 hours
            - Eat soft foods and gradually return to normal diet
            - Take prescribed pain medication as directed
            - Rinse gently with warm salt water after 24 hours
            
            Warning Signs - Contact Us Immediately If:
            - Excessive bleeding continues after 4 hours
            - Severe pain not controlled by medication
            - Signs of infection (fever, pus, severe swelling)
            - Numbness lasting more than 6 hours
            """,
            "metadata": {
                'url': 'https://drmarkshtern.com/post-op-care',
                'title': 'Post-Operative Care Instructions',
                'source': 'manual'
            },
            "category": "procedures"
        },
        {
            "content": """
            Frequently Asked Questions
            
            Q: How often should I visit the dentist?
            A: We recommend checkups and cleanings every 6 months for most patients.
            
            Q: What age should children first visit the dentist?
            A: Children should have their first dental visit by age 1 or within 6 months of their first tooth.
            
            Q: Is teeth whitening safe?
            A: Yes, professional teeth whitening under dental supervision is safe and effective.
            
            Q: How long do dental fillings last?
            A: With proper care, composite fillings last 5-10 years, amalgam fillings 10-15 years.
            
            Q: What causes bad breath?
            A: Common causes include poor oral hygiene, gum disease, dry mouth, and certain foods.
            
            Q: Are dental X-rays safe?
            A: Yes, modern digital X-rays use minimal radiation and are very safe.
            """,
            "metadata": {
                'url': 'https://drmarkshtern.com/faq',
                'title': 'Frequently Asked Questions',
                'source': 'manual'
            },
            "category": "general"
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
    
    print("\n✨ Sample documents creation complete!")

if __name__ == "__main__":
    asyncio.run(create_sample_documents())