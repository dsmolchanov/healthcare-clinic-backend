#!/usr/bin/env python3
"""
Test Document Upload Fix
========================
Verify that document uploads are working after fixes
"""

import asyncio
import httpx
from pathlib import Path
import json

PRODUCTION_URL = "https://healthcare-clinic-backend.fly.dev/api/knowledge/upload"
CLINIC_ID = "3e411ecb-3411-4add-91e2-8fa897310cb0"

async def test_text_upload():
    """Test uploading a text document"""
    
    text_content = """
    Emergency Procedures at Shtern Dental Clinic
    
    If you experience a dental emergency:
    1. Call our emergency hotline: 555-URGENT (555-874368)
    2. Available 24/7 for existing patients
    3. Common emergencies we handle:
       - Severe tooth pain
       - Broken or knocked-out tooth
       - Dental abscess
       - Lost filling or crown
    
    After-hours emergency fee: $150 (waived for severe cases)
    """
    
    # Create a temporary text file
    temp_file = Path("test_emergency.txt")
    temp_file.write_text(text_content)
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            with open(temp_file, 'rb') as f:
                files = {'file': ('emergency_procedures.txt', f, 'text/plain')}
                data = {
                    'category': 'emergency',
                    'clinic_id': CLINIC_ID
                }
                
                response = await client.post(PRODUCTION_URL, files=files, data=data)
                
                if response.status_code == 200:
                    result = response.json()
                    print("✅ Text upload successful!")
                    print(f"   Job ID: {result.get('job_id')}")
                    return result.get('job_id')
                else:
                    print(f"❌ Text upload failed: {response.status_code}")
                    print(f"   Response: {response.text}")
                    return None
    finally:
        # Clean up temp file
        if temp_file.exists():
            temp_file.unlink()

async def check_job_status(job_id: str):
    """Check the status of an ingestion job"""
    
    if not job_id:
        return
    
    status_url = f"https://healthcare-clinic-backend.fly.dev/api/knowledge/jobs/{job_id}"
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        # Check status multiple times
        for i in range(10):
            response = await client.get(status_url)
            
            if response.status_code == 200:
                data = response.json()
                status = data.get('status')
                progress = data.get('progress', 0)
                
                print(f"   Status: {status} | Progress: {progress}%")
                
                if status == 'completed':
                    print("✅ Job completed successfully!")
                    if data.get('result'):
                        result = json.loads(data['result'])
                        print(f"   Documents: {result.get('documents', 0)}")
                        print(f"   Chunks: {result.get('chunks', 0)}")
                    return True
                elif status == 'failed':
                    print(f"❌ Job failed: {data.get('error')}")
                    return False
            
            # Wait before next check
            await asyncio.sleep(2)
        
        print("⏱️ Job still processing after 20 seconds")
        return False

async def test_retrieval_after_upload():
    """Test if the uploaded content is retrievable"""
    
    test_query = "What is the emergency hotline number?"
    
    payload = {
        "from_phone": "whatsapp:+1234567890",
        "to_phone": "whatsapp:+52999999999",
        "body": test_query,
        "message_sid": f"test_upload_verification",
        "clinic_id": CLINIC_ID,
        "clinic_name": "Shtern Dental Clinic",
        "message_type": "text",
        "channel": "widget",
        "profile_name": "Test User"
    }
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            "https://healthcare-clinic-backend.fly.dev/api/process-message",
            json=payload
        )
        
        if response.status_code == 200:
            data = response.json()
            knowledge_used = data.get("metadata", {}).get("knowledge_used", 0)
            ai_response = data.get("message", "")
            
            print(f"\n🔍 Testing retrieval of uploaded content:")
            print(f"   Query: {test_query}")
            print(f"   Knowledge items used: {knowledge_used}")
            print(f"   Response: {ai_response[:200]}...")
            
            # Check if the response mentions the emergency number
            if "555-URGENT" in ai_response or "555-874368" in ai_response:
                print("   ✅ Uploaded content is being retrieved correctly!")
                return True
            else:
                print("   ⚠️ Response doesn't include uploaded emergency info")
                return False
        else:
            print(f"   ❌ Query failed: {response.status_code}")
            return False

async def main():
    print("🧪 Testing Document Upload Fixes")
    print("=" * 50)
    
    # Test 1: Upload a text document
    print("\n1️⃣ Testing text document upload...")
    job_id = await test_text_upload()
    
    if job_id:
        # Test 2: Check job completion
        print("\n2️⃣ Checking job status...")
        completed = await check_job_status(job_id)
        
        if completed:
            # Wait for indexing
            print("\n⏳ Waiting 5 seconds for Pinecone indexing...")
            await asyncio.sleep(5)
            
            # Test 3: Verify retrieval
            print("\n3️⃣ Testing retrieval of uploaded content...")
            retrieved = await test_retrieval_after_upload()
            
            if retrieved:
                print("\n✅ SUCCESS: Document upload pipeline is fully functional!")
                print("   - Upload works")
                print("   - Processing completes")
                print("   - Content is retrievable")
            else:
                print("\n⚠️ PARTIAL SUCCESS: Upload works but retrieval needs attention")
        else:
            print("\n❌ Upload processing failed")
    else:
        print("\n❌ Upload failed")
    
    print("\n" + "=" * 50)
    print("Test complete!")

if __name__ == "__main__":
    asyncio.run(main())