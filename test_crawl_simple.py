#!/usr/bin/env python3
"""
Simple test script to crawl a URL and verify document storage
"""

import asyncio
import aiohttp
import json
import time

BASE_URL = "https://healthcare-clinic-backend.fly.dev"
# Use the organization_id that matches the frontend
CLINIC_ID = "3e411ecb-3411-4add-91e2-8fa897310cb0"

async def test_crawl():
    """Test crawling a simple URL"""
    
    async with aiohttp.ClientSession() as session:
        # 1. Start a crawl
        print("Starting website crawl...")
        crawl_data = {
            "url": "https://example.com",  # Simple test page
            "max_pages": 1,
            "depth": 1,
            "category": "general",
            "clinic_id": CLINIC_ID
        }
        
        job_id = None
        async with session.post(f"{BASE_URL}/api/knowledge/crawl", 
                               json=crawl_data,
                               headers={"Content-Type": "application/json"}) as resp:
            print(f"Crawl response: {resp.status}")
            if resp.status == 200:
                data = await resp.json()
                job_id = data.get('job_id')
                print(f"Job ID: {job_id}")
            else:
                text = await resp.text()
                print(f"Error: {text}")
                return
        
        if not job_id:
            print("No job ID returned")
            return
        
        # 2. Wait for job to complete
        print("\nWaiting for job to complete...")
        for i in range(30):  # Wait up to 30 seconds
            await asyncio.sleep(1)
            
            # Check job status
            async with session.get(f"{BASE_URL}/api/knowledge/jobs/{job_id}") as resp:
                if resp.status == 200:
                    job = await resp.json()
                    status = job.get('status')
                    progress = job.get('progress', 0)
                    
                    print(f"  Status: {status}, Progress: {progress}%")
                    
                    if status == 'completed':
                        print("\nJob completed successfully!")
                        print(f"Result: {json.dumps(job.get('result'), indent=2)}")
                        break
                    elif status == 'failed':
                        print(f"\nJob failed: {job.get('error')}")
                        break
                else:
                    print(f"  Failed to check status: {resp.status}")
        
        # 3. Check if documents were created
        print("\nChecking for documents...")
        params = {"clinic_id": CLINIC_ID}
        async with session.get(f"{BASE_URL}/api/knowledge/documents", params=params) as resp:
            if resp.status == 200:
                docs = await resp.json()
                if isinstance(docs, dict) and 'documents' in docs:
                    docs = docs['documents']
                print(f"Found {len(docs) if isinstance(docs, list) else 0} documents")
                if isinstance(docs, list):
                    for doc in docs[:5]:  # Show first 5
                        print(f"  - {doc.get('title', 'Untitled')} ({doc.get('chunk_count', 0)} chunks)")
            else:
                text = await resp.text()
                print(f"Error fetching documents: {text}")

if __name__ == "__main__":
    print(f"Testing Knowledge Crawl with clinic ID: {CLINIC_ID}")
    print("=" * 60)
    asyncio.run(test_crawl())