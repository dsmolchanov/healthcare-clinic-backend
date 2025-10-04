#!/usr/bin/env python3
"""
Clean up database to keep only Shtern Dental clinic data
"""

import asyncio
from app.database import get_db_connection

# Shtern Dental organization ID (from earlier setup)
SHTERN_ORG_ID = "3e411ecb-3411-4add-91e2-8fa897310cb0"

async def cleanup_database():
    """Remove all data except for Shtern Dental clinic"""
    
    async with get_db_connection() as conn:
        print(f"🧹 Cleaning up database to keep only Shtern Dental clinic")
        print(f"   Organization ID to keep: {SHTERN_ORG_ID}")
        print()
        
        # First, show what we're about to delete
        print("📊 Current data before cleanup:")
        
        # Count documents to be deleted
        docs_to_delete = await conn.fetchval("""
            SELECT COUNT(*) FROM knowledge_documents 
            WHERE clinic_id != $1
        """, SHTERN_ORG_ID)
        
        docs_to_keep = await conn.fetchval("""
            SELECT COUNT(*) FROM knowledge_documents 
            WHERE clinic_id = $1
        """, SHTERN_ORG_ID)
        
        print(f"  Documents to DELETE: {docs_to_delete}")
        print(f"  Documents to KEEP: {docs_to_keep}")
        
        # Count jobs to be deleted
        jobs_to_delete = await conn.fetchval("""
            SELECT COUNT(*) FROM ingestion_jobs 
            WHERE clinic_id != $1
        """, SHTERN_ORG_ID)
        
        jobs_to_keep = await conn.fetchval("""
            SELECT COUNT(*) FROM ingestion_jobs 
            WHERE clinic_id = $1
        """, SHTERN_ORG_ID)
        
        print(f"  Jobs to DELETE: {jobs_to_delete}")
        print(f"  Jobs to KEEP: {jobs_to_keep}")
        
        # Auto-confirm for script execution
        print("\n⚠️  This will permanently delete all data except Shtern Dental!")
        print("🚀 Proceeding with cleanup...")
        
        print("\n🗑️  Starting cleanup...")
        
        # Delete knowledge chunks for other clinics
        chunks_result = await conn.fetch("""
            DELETE FROM knowledge_chunks 
            WHERE document_id IN (
                SELECT id FROM knowledge_documents 
                WHERE clinic_id != $1
            )
            RETURNING id
        """, SHTERN_ORG_ID)
        print(f"  ✅ Deleted {len(chunks_result)} knowledge chunks")
        
        # Delete knowledge facts for other clinics
        facts_result = await conn.fetch("""
            DELETE FROM knowledge_facts 
            WHERE document_id IN (
                SELECT id FROM knowledge_documents 
                WHERE clinic_id != $1
            )
            RETURNING id
        """, SHTERN_ORG_ID)
        print(f"  ✅ Deleted {len(facts_result)} knowledge facts")
        
        # Delete knowledge documents for other clinics
        docs_result = await conn.fetch("""
            DELETE FROM knowledge_documents 
            WHERE clinic_id != $1
            RETURNING id
        """, SHTERN_ORG_ID)
        print(f"  ✅ Deleted {len(docs_result)} knowledge documents")
        
        # Delete ingestion jobs for other clinics
        jobs_result = await conn.fetch("""
            DELETE FROM ingestion_jobs 
            WHERE clinic_id != $1
            RETURNING id
        """, SHTERN_ORG_ID)
        print(f"  ✅ Deleted {len(jobs_result)} ingestion jobs")
        
        print("\n📊 Final data after cleanup:")
        
        # Show remaining data
        remaining_docs = await conn.fetch("""
            SELECT id, title, category 
            FROM knowledge_documents 
            WHERE clinic_id = $1
            ORDER BY id
        """, SHTERN_ORG_ID)
        
        print(f"  Remaining documents ({len(remaining_docs)}):")
        for doc in remaining_docs:
            print(f"    [{doc['id']}] {doc['title']} ({doc['category']})")
        
        remaining_jobs = await conn.fetchval("""
            SELECT COUNT(*) FROM ingestion_jobs 
            WHERE clinic_id = $1
        """, SHTERN_ORG_ID)
        print(f"  Remaining jobs: {remaining_jobs}")
        
        print("\n✨ Cleanup complete! Only Shtern Dental data remains.")

if __name__ == "__main__":
    asyncio.run(cleanup_database())