#!/usr/bin/env python3
"""
Test script for the Knowledge Management API
"""

import asyncio
import aiohttp
import json

BASE_URL = "https://healthcare-clinic-backend.fly.dev"
LOCAL_URL = "http://localhost:8000"

async def test_knowledge_api(use_local=False):
    """Test the knowledge management API endpoints"""
    
    url = LOCAL_URL if use_local else BASE_URL
    
    async with aiohttp.ClientSession() as session:
        # Test health endpoint
        print("Testing health endpoint...")
        async with session.get(f"{url}/health") as resp:
            print(f"Health check: {resp.status}")
            data = await resp.json()
            print(f"Response: {json.dumps(data, indent=2)}")
        
        # Test documents endpoint
        print("\nTesting documents endpoint...")
        clinic_id = "3e411ecb-3411-4add-91e2-8fa897310cb0"  # Organization ID that matches frontend
        params = {"clinic_id": clinic_id}
        async with session.get(f"{url}/api/knowledge/documents", params=params) as resp:
            print(f"Documents list: {resp.status}")
            data = await resp.json()
            print(f"Response: {json.dumps(data, indent=2)}")
        
        # Test upload endpoint (text as file)
        print("\nTesting text upload...")
        
        # Create a simple text file-like object
        text_content = 'This is a test knowledge entry for the clinic.'
        form_data = aiohttp.FormData()
        form_data.add_field('file',
                           text_content.encode('utf-8'),
                           filename='test.txt',
                           content_type='text/plain')
        form_data.add_field('category', 'general')
        form_data.add_field('clinic_id', clinic_id)
        form_data.add_field('metadata', json.dumps({}))
        
        try:
            async with session.post(f"{url}/api/knowledge/upload", data=form_data) as resp:
                print(f"Upload response: {resp.status}")
                if resp.status == 200:
                    data = await resp.json()
                    print(f"Response: {json.dumps(data, indent=2)}")
                else:
                    text = await resp.text()
                    print(f"Error response: {text}")
        except Exception as e:
            print(f"Upload error: {e}")
        
        # Test manual text endpoint
        print("\nTesting manual text endpoint...")
        manual_data = {
            "content": "This is a manual knowledge entry.",
            "title": "Test Entry",
            "category": "general",
            "tags": ["test", "demo"],
            "clinic_id": clinic_id
        }
        
        try:
            async with session.post(f"{url}/api/knowledge/manual", 
                                   json=manual_data,
                                   headers={"Content-Type": "application/json"}) as resp:
                print(f"Manual text response: {resp.status}")
                if resp.status == 200:
                    data = await resp.json()
                    print(f"Response: {json.dumps(data, indent=2)}")
                else:
                    text = await resp.text()
                    print(f"Error response: {text}")
        except Exception as e:
            print(f"Manual text error: {e}")
        
        # Test search endpoint
        print("\nTesting search endpoint...")
        params = {
            "query": "test",
            "clinic_id": clinic_id
        }
        try:
            async with session.get(f"{url}/api/knowledge/search", params=params) as resp:
                print(f"Search response: {resp.status}")
                if resp.status == 200:
                    data = await resp.json()
                    print(f"Response: {json.dumps(data, indent=2)}")
                else:
                    text = await resp.text()
                    print(f"Error response: {text}")
        except Exception as e:
            print(f"Search error: {e}")

if __name__ == "__main__":
    print("Testing Knowledge Management API on Fly.io...")
    print("=" * 50)
    asyncio.run(test_knowledge_api(use_local=False))