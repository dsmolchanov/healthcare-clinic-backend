#!/usr/bin/env python3
"""Test production API to verify API keys are working"""

import asyncio
import aiohttp
import json
import sys

PRODUCTION_URL = "https://healthcare-clinic-backend.fly.dev"
CLINIC_ID = "2b8f1c5a-92e1-473e-98f6-e3a13e92b7f5"  # Shtern Dental

async def test_health():
    """Test health endpoint"""
    async with aiohttp.ClientSession() as session:
        async with session.get(f"{PRODUCTION_URL}/health") as resp:
            print(f"Health check: {resp.status}")
            data = await resp.json()
            print(f"Response: {json.dumps(data, indent=2)}")
            return resp.status == 200

async def test_knowledge_crawl():
    """Test knowledge crawl with API keys"""
    async with aiohttp.ClientSession() as session:
        payload = {
            "url": "https://example.com",
            "max_pages": 1,
            "depth": 1,
            "category": "test",
            "clinic_id": CLINIC_ID
        }
        
        async with session.post(
            f"{PRODUCTION_URL}/api/knowledge/crawl",
            json=payload,
            headers={"Content-Type": "application/json"}
        ) as resp:
            print(f"\nKnowledge crawl test: {resp.status}")
            data = await resp.json()
            print(f"Response: {json.dumps(data, indent=2)}")
            
            if resp.status == 200 and "job_id" in data:
                return data["job_id"]
            return None

async def check_job_status(job_id):
    """Check job status"""
    async with aiohttp.ClientSession() as session:
        for i in range(10):  # Check for up to 50 seconds
            await asyncio.sleep(5)
            async with session.get(f"{PRODUCTION_URL}/api/knowledge/status/{job_id}") as resp:
                data = await resp.json()
                print(f"\nJob status check {i+1}: {data.get('status')} (Progress: {data.get('progress')}%)")
                
                if data.get('status') in ['completed', 'failed', 'completed_with_errors']:
                    print(f"Final result: {json.dumps(data, indent=2)}")
                    return data
    return None

async def main():
    print("Testing production API...")
    print(f"URL: {PRODUCTION_URL}")
    print(f"Clinic ID: {CLINIC_ID}")
    
    # Test health
    if not await test_health():
        print("Health check failed!")
        sys.exit(1)
    
    # Test knowledge crawl (tests API keys)
    job_id = await test_knowledge_crawl()
    if job_id:
        print(f"\nJob created: {job_id}")
        print("Waiting for job to complete...")
        result = await check_job_status(job_id)
        
        if result:
            if result.get('status') == 'failed':
                print(f"\n❌ Job failed: {result.get('error')}")
                if "API key" in str(result.get('error', '')):
                    print("API keys may not be properly configured!")
            else:
                print(f"\n✅ Job completed successfully!")
                print("API keys are working correctly!")
        else:
            print("\n⚠️ Job status check timed out")
    else:
        print("\n❌ Failed to create crawl job")

if __name__ == "__main__":
    asyncio.run(main())