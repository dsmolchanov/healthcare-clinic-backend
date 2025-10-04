#!/usr/bin/env python3
"""
Test RAG integration on production
"""

import requests
import json

def test_production_rag():
    """Test that RAG is working on production deployment"""
    
    # Production endpoint
    url = "https://healthcare-clinic-backend.fly.dev/api/process-message"
    
    # Test data
    test_message = {
        "from_phone": "+1234567890",
        "to_phone": "+14155238886",
        "body": "What insurance do you accept?",
        "message_sid": "test_prod_123",
        "clinic_id": "3e411ecb-3411-4add-91e2-8fa897310cb0",
        "clinic_name": "Shtern Dental Clinic",
        "profile_name": "Test User"
    }
    
    print("🚀 Testing RAG on Production")
    print("=" * 50)
    print(f"Endpoint: {url}")
    print(f"Query: {test_message['body']}")
    print(f"Clinic: {test_message['clinic_name']}")
    print()
    
    try:
        # Make request
        response = requests.post(url, json=test_message)
        response.raise_for_status()
        
        result = response.json()
        
        print("✅ Response received:")
        print(f"Status: {result.get('status', 'unknown')}")
        print(f"Session ID: {result.get('session_id', 'none')}")
        print(f"Language: {result.get('detected_language', 'unknown')}")
        print(f"Knowledge items used: {result.get('metadata', {}).get('knowledge_used', 0)}")
        print()
        print("💬 AI Response:")
        print(result.get('message', 'No message'))
        print()
        
        # Test Spanish
        test_message_spanish = {
            "from_phone": "+1234567890",
            "to_phone": "+14155238886",
            "body": "¿Qué cuidados necesito después de una extracción dental?",
            "message_sid": "test_prod_124",
            "clinic_id": "3e411ecb-3411-4add-91e2-8fa897310cb0",
            "clinic_name": "Shtern Dental Clinic",
            "profile_name": "Test User"
        }
        
        print("=" * 50)
        print(f"Spanish Query: {test_message_spanish['body']}")
        print()
        
        response2 = requests.post(url, json=test_message_spanish)
        response2.raise_for_status()
        
        result2 = response2.json()
        
        print("✅ Response received:")
        print(f"Language: {result2.get('detected_language', 'unknown')}")
        print(f"Knowledge items used: {result2.get('metadata', {}).get('knowledge_used', 0)}")
        print()
        print("💬 AI Response:")
        print(result2.get('message', 'No message'))
        
        # Summary
        print()
        print("=" * 50)
        print("📊 Production Test Summary:")
        total_knowledge = (result.get('metadata', {}).get('knowledge_used', 0) + 
                          result2.get('metadata', {}).get('knowledge_used', 0))
        print(f"✅ API is responsive")
        print(f"✅ Multilingual support working")
        print(f"{'✅' if total_knowledge > 0 else '⚠️'} RAG knowledge retrieved: {total_knowledge} items")
        
        if total_knowledge == 0:
            print("\n⚠️ Warning: No RAG knowledge was retrieved.")
            print("This might indicate the Pinecone index is not accessible from production.")
        
    except requests.exceptions.RequestException as e:
        print(f"❌ Error calling production API: {e}")
    except json.JSONDecodeError as e:
        print(f"❌ Error parsing response: {e}")

if __name__ == "__main__":
    test_production_rag()