#!/usr/bin/env python3
"""
Fix Knowledge Document Metadata Issues
- Proper title extraction from filenames
- Correct source_type for file uploads
- Better real-time updates
"""

import asyncio
import logging
from pathlib import Path
import sys

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent))

from app.database import get_db_connection

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def fix_existing_documents():
    """Fix metadata for existing documents in the database"""
    
    async with get_db_connection() as conn:
        # Get all documents with bad metadata
        documents = await conn.fetch("""
            SELECT id, source_type, source_filename, title, metadata
            FROM knowledge_documents
            WHERE (title = 'Untitled' OR title IS NULL OR source_type = 'url')
            AND source_filename IS NOT NULL AND source_filename != ''
        """)
        
        logger.info(f"Found {len(documents)} documents to fix")
        
        fixed_count = 0
        for doc in documents:
            updates = {}
            
            # Fix source_type if it's a file
            if doc['source_filename'] and doc['source_type'] == 'url':
                updates['source_type'] = 'file'
            
            # Fix title if it's Untitled
            if doc['title'] == 'Untitled' or not doc['title']:
                filename = doc['source_filename']
                if filename:
                    # Extract title from filename
                    import os
                    title = os.path.splitext(filename)[0]
                    # Clean up title
                    title = title.replace('_', ' ').replace('-', ' ')
                    # Capitalize words
                    title = ' '.join(word.capitalize() for word in title.split())
                    if title:
                        updates['title'] = title
            
            # Apply updates if any
            if updates:
                set_clause = ', '.join([f"{k} = ${i+2}" for i, k in enumerate(updates.keys())])
                values = [doc['id']] + list(updates.values())
                
                await conn.execute(f"""
                    UPDATE knowledge_documents
                    SET {set_clause}, updated_at = NOW()
                    WHERE id = $1
                """, *values)
                
                fixed_count += 1
                logger.info(f"Fixed document {doc['id']}: {updates}")
        
        logger.info(f"Fixed {fixed_count} documents")


async def update_knowledge_routes():
    """Update the knowledge routes to use the fixed pipeline"""
    
    fix_code = '''
# Fix for knowledge_routes.py process_document_task function
# Add this right after creating the pipeline variable (around line 696):

# Ensure proper metadata for file uploads
if 'filename' in metadata and metadata['filename']:
    # It's a file upload, ensure source_type is 'file'
    metadata['source_type'] = 'file'
    
    # Extract title from filename if not provided
    if not metadata.get('title') or metadata.get('title') == 'Untitled':
        import os
        title = os.path.splitext(metadata['filename'])[0]
        title = title.replace('_', ' ').replace('-', ' ')
        title = ' '.join(word.capitalize() for word in title.split())
        metadata['title'] = title or 'Document'
'''
    
    print("\n" + "="*60)
    print("CODE FIX TO APPLY")
    print("="*60)
    print(fix_code)
    print("="*60 + "\n")


async def test_fix():
    """Test the fix with a sample document"""
    
    from app.api.knowledge_ingestion_fixed import FixedKnowledgeIngestionPipeline
    
    # Test with sample metadata
    test_cases = [
        {
            'filename': 'dental_services_price_list.pdf',
            'mime_type': 'application/pdf'
        },
        {
            'filename': 'insurance-policies-2024.doc',
            'mime_type': 'application/msword'
        },
        {
            'url': 'https://example.com/services',
            'source': 'web_crawler'
        }
    ]
    
    pipeline = FixedKnowledgeIngestionPipeline('test_clinic')
    
    for metadata in test_cases:
        source_type = pipeline._determine_source_type(metadata)
        title = pipeline._extract_title_from_content('', metadata)
        
        print(f"Test case: {metadata}")
        print(f"  Source type: {source_type}")
        print(f"  Title: {title}")
        print()


async def main():
    """Run all fixes"""
    
    print("\n" + "="*80)
    print("KNOWLEDGE DOCUMENT METADATA FIX")
    print("="*80 + "\n")
    
    print("This script will:")
    print("1. Fix existing documents with wrong metadata")
    print("2. Show code changes needed in knowledge_routes.py")
    print("3. Test the fixed pipeline")
    print()
    
    # Fix existing documents
    print("1. Fixing existing documents...")
    await fix_existing_documents()
    print()
    
    # Show code updates needed
    print("2. Code updates needed...")
    await update_knowledge_routes()
    print()
    
    # Test the fix
    print("3. Testing the fix...")
    await test_fix()
    print()
    
    print("✅ Fix complete!")
    print()
    print("Next steps:")
    print("1. Apply the code fix shown above to knowledge_routes.py")
    print("2. Deploy the updated backend")
    print("3. The frontend will auto-refresh when documents complete processing")


if __name__ == "__main__":
    asyncio.run(main())