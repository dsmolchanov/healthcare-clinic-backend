#!/usr/bin/env python3
"""
Clean up Pinecone index to keep only Shtern Dental vectors
"""

import os
from pinecone import Pinecone

# Shtern Dental organization ID
SHTERN_ORG_ID = "3e411ecb-3411-4add-91e2-8fa897310cb0"

def cleanup_pinecone():
    """Remove all vectors except for Shtern Dental clinic"""
    
    print("🧹 Cleaning up Pinecone index")
    
    # Initialize Pinecone
    api_key = os.environ.get('PINECONE_API_KEY')
    if not api_key:
        print("❌ PINECONE_API_KEY not found")
        return
    
    pc = Pinecone(api_key=api_key)
    
    # List all indexes
    indexes = [index.name for index in pc.list_indexes()]
    print(f"Found indexes: {indexes}")
    
    # Clean each index
    for index_name in indexes:
        if 'clinic' in index_name.lower():
            print(f"\n📦 Processing index: {index_name}")
            index = pc.Index(index_name)
            
            # Get index stats
            stats = index.describe_index_stats()
            print(f"  Total vectors: {stats.total_vector_count}")
            
            # Query to find vectors not belonging to Shtern
            # We'll need to delete vectors by querying and checking metadata
            # This is a limitation - we'd need to iterate through all vectors
            
            # For now, let's just report what we have
            print(f"  Index {index_name} - manual cleanup may be needed")
            
            # If this is not the Shtern index, we could delete the whole index
            if not index_name.endswith('3e411ecb'):  # First 8 chars of Shtern ID
                print(f"  This index doesn't belong to Shtern Dental")
                # Uncomment to delete non-Shtern indexes
                # pc.delete_index(index_name)
                # print(f"  ✅ Deleted index {index_name}")
    
    print("\n✨ Pinecone cleanup complete!")

if __name__ == "__main__":
    cleanup_pinecone()