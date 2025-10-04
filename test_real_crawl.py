#!/usr/bin/env python3
"""Test production API with a real dental website"""

import asyncio
import aiohttp
import json
import sys

PRODUCTION_URL = "https://healthcare-clinic-backend.fly.dev"
CLINIC_ID = "2b8f1c5a-92e1-473e-98f6-e3a13e92b7f5"  # Shtern Dental

async def test_crawl_real_site():
    """Test knowledge crawl with a real dental website"""
    async with aiohttp.ClientSession() as session:
        payload = {
            "url": "https://www.webmd.com/oral-health/default.htm",  # A real health website
            "max_pages": 5,
            "depth": 1,
            "category": "dental_health",
            "clinic_id": CLINIC_ID
        }
        
        print(f"Starting crawl of {payload['url']}...")
        async with session.post(
            f"{PRODUCTION_URL}/api/knowledge/crawl",
            json=payload,
            headers={"Content-Type": "application/json"}
        ) as resp:
            print(f"Crawl request status: {resp.status}")
            data = await resp.json()
            print(f"Response: {json.dumps(data, indent=2)}")
            
            if resp.status == 200 and "job_id" in data:
                return data["job_id"]
            return None

async def check_job_status(job_id):
    """Check job status"""
    async with aiohttp.ClientSession() as session:
        for i in range(20):  # Check for up to 100 seconds
            await asyncio.sleep(5)
            async with session.get(f"{PRODUCTION_URL}/api/knowledge/status/{job_id}") as resp:
                data = await resp.json()
                status = data.get('status')
                progress = data.get('progress', 0)
                print(f"Status check {i+1}: {status} (Progress: {progress}%)")
                
                if status in ['completed', 'failed', 'completed_with_errors']:
                    print(f"\nFinal result:")
                    print(json.dumps(data, indent=2))
                    
                    # Parse the result field if it exists
                    if data.get('result'):
                        try:
                            result_data = json.loads(data['result'])
                            print("\nParsed result:")
                            print(f"  Pages crawled: {result_data.get('pages_crawled', 0)}")
                            print(f"  Documents created: {result_data.get('documents_created', 0)}")
                            print(f"  Failed pages: {result_data.get('documents_failed', 0)}")
                            if result_data.get('message'):
                                print(f"  Message: {result_data['message']}")
                        except:
                            pass
                    
                    return data
    return None

async def main():
    print("Testing production API with real website...")
    print(f"URL: {PRODUCTION_URL}")
    print(f"Clinic ID: {CLINIC_ID}")
    print()
    
    # Test knowledge crawl with real site
    job_id = await test_crawl_real_site()
    if job_id:
        print(f"\nJob created: {job_id}")
        print("Waiting for job to complete (this may take a while)...")
        result = await check_job_status(job_id)
        
        if result:
            status = result.get('status')
            if status == 'completed':
                print("\n✅ Job completed successfully!")
                print("Knowledge has been ingested into Pinecone!")
            elif status == 'completed_with_errors':
                print("\n⚠️ Job completed with some errors")
                print("Some pages were processed successfully!")
            else:
                print(f"\n❌ Job failed")
                if result.get('error'):
                    print(f"Error: {result['error']}")
    else:
        print("\n❌ Failed to create crawl job")

if __name__ == "__main__":
    asyncio.run(main())