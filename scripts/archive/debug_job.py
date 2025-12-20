#!/usr/bin/env python3
"""Debug job data in database"""

import asyncio
import os
from dotenv import load_dotenv
from app.database import get_db_connection

load_dotenv()

async def debug_job():
    """Check job details in database"""
    job_id = "bcdde359-4119-40fe-9099-7c9161472e04"
    
    async with get_db_connection() as conn:
        job = await conn.fetchrow("""
            SELECT id, clinic_id, job_type, status, progress, created_at
            FROM ingestion_jobs
            WHERE id = $1::uuid
        """, job_id)
        
        if job:
            print(f"Job found:")
            print(f"  ID: {job['id']}")
            print(f"  Clinic ID: {job['clinic_id']}")
            print(f"  Type: {job['job_type']}")
            print(f"  Status: {job['status']}")
            print(f"  Progress: {job['progress']}")
            print(f"  Created: {job['created_at']}")
        else:
            print(f"Job {job_id} not found")
        
        # Also list all clinic IDs with jobs
        print("\nAll clinic IDs with jobs:")
        clinics = await conn.fetch("""
            SELECT DISTINCT clinic_id, COUNT(*) as job_count
            FROM ingestion_jobs
            GROUP BY clinic_id
        """)
        for clinic in clinics:
            print(f"  {clinic['clinic_id']}: {clinic['job_count']} jobs")

if __name__ == "__main__":
    asyncio.run(debug_job())