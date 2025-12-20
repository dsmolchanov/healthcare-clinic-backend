#!/usr/bin/env python3
"""
List all Pinecone indexes
"""

import os
from dotenv import load_dotenv
from pinecone import Pinecone

# Load environment variables
load_dotenv()

def list_indexes():
    """List all Pinecone indexes"""
    
    api_key = os.environ.get('PINECONE_API_KEY')
    if not api_key:
        print("❌ PINECONE_API_KEY not found")
        return
    
    pc = Pinecone(api_key=api_key)
    
    print("📦 Pinecone Indexes:")
    print("=" * 50)
    
    indexes = pc.list_indexes()
    for index_info in indexes:
        print(f"\n📌 Index: {index_info.name}")
        print(f"   Status: {index_info.status.state}")
        print(f"   Ready: {index_info.status.ready}")
        print(f"   Dimension: {index_info.dimension}")
        print(f"   Metric: {index_info.metric}")
        print(f"   Spec: {index_info.spec}")
        
        # Get stats for the index
        try:
            index = pc.Index(index_info.name)
            stats = index.describe_index_stats()
            print(f"   Total vectors: {stats.total_vector_count}")
            print(f"   Namespaces: {list(stats.namespaces.keys()) if stats.namespaces else 'None'}")
        except Exception as e:
            print(f"   Could not get stats: {e}")
    
    if not indexes:
        print("No indexes found")

if __name__ == "__main__":
    list_indexes()