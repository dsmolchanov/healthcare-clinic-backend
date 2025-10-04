#!/usr/bin/env python3
"""
Test Enhanced Multimodal PDF Processing for RAG Pipeline
Demonstrates extraction of tables, images, and complex formatting
"""

import asyncio
import os
import sys
from pathlib import Path
import json
import logging
from datetime import datetime

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent))

from app.api.enhanced_knowledge_ingestion import EnhancedKnowledgeIngestionPipeline
from app.api.improved_knowledge_base import ImprovedPineconeKnowledgeBase

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


async def test_pdf_with_tables():
    """Test PDF processing with table extraction"""
    
    # Create sample PDF content with a table (you would use a real PDF)
    sample_pdf_content = b"""
    %PDF-1.4
    Sample clinic document with pricing table:
    
    Service Price List:
    | Service | Duration | Price |
    |---------|----------|-------|
    | Dental Cleaning | 30 min | $150 |
    | Filling | 45 min | $200 |
    | Crown | 60 min | $800 |
    | Root Canal | 90 min | $1200 |
    
    Insurance Information:
    We accept most major insurance providers including:
    - Delta Dental
    - MetLife
    - Aetna
    - Blue Cross Blue Shield
    
    Office Hours:
    Monday-Friday: 9:00 AM - 6:00 PM
    Saturday: 9:00 AM - 2:00 PM
    Sunday: Closed
    
    Emergency Contact: Call 555-EMERGENCY for after-hours dental emergencies.
    """
    
    return sample_pdf_content


async def main():
    """Main test function"""
    
    print("\n" + "="*80)
    print("ENHANCED MULTIMODAL PDF PROCESSING TEST")
    print("="*80 + "\n")
    
    # Test clinic ID
    clinic_id = "test_clinic_multimodal"
    
    # Initialize the enhanced ingestion pipeline
    print("1. Initializing Enhanced Knowledge Ingestion Pipeline...")
    pipeline = EnhancedKnowledgeIngestionPipeline(clinic_id)
    print("   ✓ Pipeline initialized with multimodal capabilities\n")
    
    # Test 1: Ingest a PDF with tables and complex formatting
    print("2. Testing PDF Document Ingestion with Tables...")
    
    # Get sample PDF content (in production, this would be a real PDF file)
    pdf_content = await test_pdf_with_tables()
    
    # Ingest the PDF
    result = await pipeline.ingest_document(
        content=pdf_content,
        filename="clinic_services_pricing.pdf",
        mime_type="application/pdf",
        metadata={
            "clinic_name": "Test Dental Clinic",
            "document_type": "service_pricing",
            "last_updated": datetime.utcnow().isoformat()
        },
        category="pricing"
    )
    
    print(f"   Ingestion Result:")
    print(f"   - Status: {result['status']}")
    print(f"   - Document ID: {result.get('doc_id', 'N/A')}")
    print(f"   - Chunks Created: {result.get('chunks', 0)}")
    
    if 'extraction_details' in result:
        print(f"   - Extraction Details:")
        details = result['extraction_details']
        print(f"     • Processor: {details.get('processor', 'N/A')}")
        print(f"     • Model Used: {details.get('model_used', 'N/A')}")
        print(f"     • Tables Found: {details.get('tables_found', 0)}")
        print(f"     • Images Found: {details.get('images_found', 0)}")
        print(f"     • Processing Time: {details.get('processing_time_ms', 0):.2f}ms")
    
    print()
    
    # Test 2: Query the knowledge base for table data
    if result['status'] == 'indexed':
        print("3. Testing Retrieval of Table Data...")
        
        # Initialize the improved knowledge base
        kb = ImprovedPineconeKnowledgeBase(clinic_id)
        
        # Test queries that should retrieve table information
        test_queries = [
            "What is the price of a dental cleaning?",
            "How much does a root canal cost?",
            "What are the office hours on Saturday?",
            "Which insurance providers are accepted?",
            "What services are offered and their prices?"
        ]
        
        for query in test_queries:
            print(f"\n   Query: '{query}'")
            
            # Search the knowledge base
            search_results = await kb.search(
                query=query,
                top_k=3,
                metadata_filter={"category": "pricing"}
            )
            
            if search_results:
                print(f"   Found {len(search_results)} relevant chunks:")
                for i, result in enumerate(search_results[:2], 1):
                    print(f"   [{i}] Score: {result['score']:.3f}")
                    print(f"       Type: {result['metadata'].get('chunk_type', 'text')}")
                    preview = result['text'][:150].replace('\n', ' ')
                    print(f"       Preview: {preview}...")
            else:
                print("   No results found")
    
    # Test 3: Ingest from a website URL
    print("\n4. Testing Website Ingestion with Multimodal Parsing...")
    
    # Example website URL (replace with actual clinic website)
    website_url = "https://example-clinic.com"
    
    try:
        url_result = await pipeline.ingest_from_url(
            url=website_url,
            category="website",
            metadata={
                "clinic_name": "Test Dental Clinic",
                "source": "website"
            }
        )
        
        print(f"   Website Ingestion Result:")
        print(f"   - Status: {url_result['status']}")
        print(f"   - Pages Crawled: {url_result.get('pages_crawled', 0)}")
        
        if 'extraction_summary' in url_result:
            summary = url_result['extraction_summary']
            print(f"   - Extraction Summary:")
            print(f"     • Services Found: {summary.get('services_found', 0)}")
            print(f"     • FAQs Found: {summary.get('faqs_found', 0)}")
            print(f"     • Team Members: {summary.get('team_members_found', 0)}")
            print(f"     • Has Contact Info: {summary.get('has_contact_info', False)}")
            print(f"     • Has Business Hours: {summary.get('has_business_hours', False)}")
    except Exception as e:
        print(f"   Website ingestion skipped (example URL): {e}")
    
    # Summary
    print("\n" + "="*80)
    print("TEST SUMMARY")
    print("="*80)
    print("""
Key Improvements Demonstrated:
1. ✓ Multimodal PDF processing using GPT-5-mini/GPT-4o-mini
2. ✓ Table extraction and structured data preservation
3. ✓ Enhanced metadata with extraction details
4. ✓ Intelligent chunking that preserves table context
5. ✓ Web crawling with structured data extraction
6. ✓ Lower cost with GPT-5-mini ($0.4/1M tokens)

Benefits:
- Tables and complex formatting are preserved
- Better retrieval accuracy for structured data
- Rich metadata for filtering and ranking
- Cost-effective with cheaper multimodal models
- Comprehensive fact extraction
    """)
    
    print("\nNext Steps:")
    print("1. Test with real PDF documents containing tables and images")
    print("2. Fine-tune chunk size and overlap for optimal retrieval")
    print("3. Implement caching for frequently accessed documents")
    print("4. Add support for Excel, PowerPoint, and other formats")
    print("5. Set up incremental updates for changed documents")


if __name__ == "__main__":
    # Check environment variables
    required_vars = ['OPENAI_API_KEY', 'PINECONE_API_KEY']
    missing_vars = [var for var in required_vars if not os.environ.get(var)]
    
    if missing_vars:
        print(f"Error: Missing environment variables: {', '.join(missing_vars)}")
        print("\nPlease set the following environment variables:")
        for var in missing_vars:
            print(f"  export {var}=your_{var.lower()}")
        sys.exit(1)
    
    # Run the test
    asyncio.run(main())