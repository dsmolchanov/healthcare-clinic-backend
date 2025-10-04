#!/usr/bin/env python3
"""Check if there are any documents in the knowledge_documents table"""

import asyncio
import os
from dotenv import load_dotenv
from app.database import get_db_connection

load_dotenv()

async def check_documents():
    """Check documents in database"""
    
    async with get_db_connection() as conn:
        # Count all documents
        count = await conn.fetchval("""
            SELECT COUNT(*) FROM knowledge_documents
        """)
        print(f"Total documents in database: {count}")
        
        # Get documents by clinic
        clinics = await conn.fetch("""
            SELECT clinic_id, COUNT(*) as doc_count
            FROM knowledge_documents
            GROUP BY clinic_id
        """)
        
        if clinics:
            print("\nDocuments by clinic:")
            for clinic in clinics:
                print(f"  {clinic['clinic_id']}: {clinic['doc_count']} documents")
        
        # Get recent documents
        recent = await conn.fetch("""
            SELECT id, clinic_id, title, category, source_type, chunk_count, processed_at
            FROM knowledge_documents
            ORDER BY processed_at DESC
            LIMIT 5
        """)
        
        if recent:
            print("\nRecent documents:")
            for doc in recent:
                print(f"  [{doc['id']}] {doc['title']} - {doc['category']} ({doc['chunk_count']} chunks)")
                print(f"    Clinic: {doc['clinic_id']}")
                print(f"    Processed: {doc['processed_at']}")

if __name__ == "__main__":
    asyncio.run(check_documents())