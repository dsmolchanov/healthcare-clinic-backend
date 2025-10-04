#!/usr/bin/env python3
"""Test job cancellation and deletion functionality"""

import asyncio
import aiohttp
import json
import sys

PRODUCTION_URL = "https://healthcare-clinic-backend.fly.dev"
CLINIC_ID = "2b8f1c5a-92e1-473e-98f6-e3a13e92b7f5"  # Shtern Dental

async def test_cancel_job():
    """Test cancelling a job"""
    async with aiohttp.ClientSession() as session:
        # First, get the list of active jobs - try without clinic_id to see all jobs
        async with session.get(f"{PRODUCTION_URL}/api/knowledge/jobs/active") as resp:
            if resp.status != 200:
                print(f"Failed to get jobs: {resp.status}")
                return
            
            data = await resp.json()
            jobs = data.get('jobs', [])
            
            if not jobs:
                print("No jobs found to cancel")
                return
            
            # Find a job to cancel (preferably stuck in processing/pending)
            job_to_cancel = None
            for job in jobs:
                if job['status'] in ['pending', 'processing', 'failed']:
                    job_to_cancel = job
                    break
            
            if not job_to_cancel:
                job_to_cancel = jobs[0]  # Just take the first one
            
            print(f"Found job to cancel: {job_to_cancel['id']}")
            print(f"  Type: {job_to_cancel['type']}")
            print(f"  Status: {job_to_cancel['status']}")
            print(f"  Progress: {job_to_cancel.get('progress', 0)}%")
            
            # Now cancel the job
            print(f"\nCancelling job {job_to_cancel['id']}...")
            async with session.delete(
                f"{PRODUCTION_URL}/api/knowledge/jobs/{job_to_cancel['id']}"
            ) as cancel_resp:
                if cancel_resp.status == 200:
                    result = await cancel_resp.json()
                    print(f"✅ Job cancelled successfully!")
                    print(f"  Message: {result.get('message')}")
                    if result.get('details'):
                        print(f"  Deleted: {json.dumps(result['details'], indent=2)}")
                elif cancel_resp.status == 404:
                    print(f"❌ Job not found or unauthorized")
                else:
                    error = await cancel_resp.text()
                    print(f"❌ Failed to cancel job: {cancel_resp.status}")
                    print(f"  Error: {error}")

async def test_cleanup_all():
    """Clean up all stuck jobs"""
    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{PRODUCTION_URL}/api/knowledge/jobs/cleanup",
            params={"clinic_id": CLINIC_ID}
        ) as resp:
            if resp.status == 200:
                result = await resp.json()
                print(f"✅ Cleanup completed: {result.get('message')}")
            else:
                print(f"❌ Cleanup failed: {resp.status}")

async def main():
    print("Testing job cancellation API...")
    print(f"URL: {PRODUCTION_URL}")
    print(f"Clinic ID: {CLINIC_ID}")
    print()
    
    # Test cancellation
    await test_cancel_job()
    
    # Optionally cleanup all stuck jobs
    # print("\nCleaning up all stuck jobs...")
    # await test_cleanup_all()

if __name__ == "__main__":
    asyncio.run(main())