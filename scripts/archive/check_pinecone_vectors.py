#!/usr/bin/env python3
"""
Check vectors in Pinecone index for Shtern Dental
"""

import os
from dotenv import load_dotenv
from pinecone import Pinecone
import json

# Load environment variables
load_dotenv()

def check_vectors():
    """Check vectors in the Shtern Dental index"""
    
    api_key = os.environ.get('PINECONE_API_KEY')
    if not api_key:
        print("❌ PINECONE_API_KEY not found")
        return
    
    pc = Pinecone(api_key=api_key)
    
    # Connect to Shtern index
    index_name = "clinic-3e411ecb-kb"
    index = pc.Index(index_name)
    
    print(f"📦 Checking index: {index_name}")
    print("=" * 50)
    
    # Get index stats
    stats = index.describe_index_stats()
    print(f"Total vectors: {stats.total_vector_count}")
    print()
    
    # Query to get some vectors (without filter first)
    try:
        # Create a dummy query vector
        dummy_vector = [0.0] * 1536
        
        # Query without filter to see what's there
        results = index.query(
            vector=dummy_vector,
            top_k=5,
            include_metadata=True
        )
        
        print("Sample vectors (no filter):")
        for i, match in enumerate(results.matches, 1):
            print(f"\n{i}. ID: {match.id}")
            print(f"   Score: {match.score}")
            if match.metadata:
                print(f"   Metadata:")
                for key, value in match.metadata.items():
                    if key == 'text':
                        print(f"     {key}: {value[:100]}...")
                    else:
                        print(f"     {key}: {value}")
        
        # Now try with clinic_id filter
        print("\n" + "=" * 50)
        print("Trying with clinic_id filter:")
        
        results_filtered = index.query(
            vector=dummy_vector,
            top_k=5,
            include_metadata=True,
            filter={"clinic_id": "3e411ecb-3411-4add-91e2-8fa897310cb0"}
        )
        
        print(f"Found {len(results_filtered.matches)} matches with clinic_id filter")
        
        if not results_filtered.matches:
            # Try with different filter variations
            print("\nTrying other filter variations:")
            
            # Try with short clinic_id
            results_short = index.query(
                vector=dummy_vector,
                top_k=5,
                include_metadata=True,
                filter={"clinic_id": "3e411ecb"}
            )
            print(f"  With short clinic_id '3e411ecb': {len(results_short.matches)} matches")
            
    except Exception as e:
        print(f"Error querying index: {e}")

if __name__ == "__main__":
    check_vectors()