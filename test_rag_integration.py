#!/usr/bin/env python3
"""
Test RAG integration with WhatsApp message processor
"""

import asyncio
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

async def test_rag_integration():
    """Test that RAG retrieval is working in the message processor"""
    
    # Import the processor
    from app.api.multilingual_message_processor import MultilingualMessageProcessor, MessageRequest
    
    # Create test request
    test_request = MessageRequest(
        from_phone="+1234567890",
        to_phone="+14155238886",
        body="What insurance do you accept?",  # Query that should match stored content
        message_sid="test_123",
        clinic_id="3e411ecb-3411-4add-91e2-8fa897310cb0",  # Shtern Dental
        clinic_name="Shtern Dental Clinic",
        profile_name="Test User"
    )
    
    print("🧪 Testing RAG Integration")
    print("=" * 50)
    print(f"Query: {test_request.body}")
    print(f"Clinic: {test_request.clinic_name} ({test_request.clinic_id})")
    print()
    
    # Process the message
    processor = MultilingualMessageProcessor()
    response = await processor.process_message(test_request)
    
    print("📊 Response Details:")
    print(f"Session ID: {response.session_id}")
    print(f"Language: {response.detected_language}")
    print(f"Knowledge items used: {response.metadata.get('knowledge_used', 0)}")
    print()
    
    print("💬 AI Response:")
    print(response.message)
    print()
    
    # Test another query
    test_request2 = MessageRequest(
        from_phone="+1234567890",
        to_phone="+14155238886",
        body="What services do you offer?",
        message_sid="test_124",
        clinic_id="3e411ecb-3411-4add-91e2-8fa897310cb0",
        clinic_name="Shtern Dental Clinic",
        profile_name="Test User"
    )
    
    print("=" * 50)
    print(f"Query 2: {test_request2.body}")
    
    response2 = await processor.process_message(test_request2)
    
    print(f"Knowledge items used: {response2.metadata.get('knowledge_used', 0)}")
    print()
    print("💬 AI Response:")
    print(response2.message)
    print()
    
    # Test Spanish query
    test_request3 = MessageRequest(
        from_phone="+1234567890",
        to_phone="+14155238886",
        body="¿Qué cuidados necesito después de una extracción?",  # Spanish: What care after extraction?
        message_sid="test_125",
        clinic_id="3e411ecb-3411-4add-91e2-8fa897310cb0",
        clinic_name="Shtern Dental Clinic",
        profile_name="Test User"
    )
    
    print("=" * 50)
    print(f"Query 3 (Spanish): {test_request3.body}")
    
    response3 = await processor.process_message(test_request3)
    
    print(f"Language detected: {response3.detected_language}")
    print(f"Knowledge items used: {response3.metadata.get('knowledge_used', 0)}")
    print()
    print("💬 AI Response:")
    print(response3.message)
    
    print()
    print("✅ RAG integration test complete!")
    
    # Summary
    print()
    print("📋 Summary:")
    print(f"  - Total queries tested: 3")
    print(f"  - Languages tested: English, Spanish")
    print(f"  - RAG knowledge retrieved: {response.metadata.get('knowledge_used', 0) + response2.metadata.get('knowledge_used', 0) + response3.metadata.get('knowledge_used', 0)} total items")

if __name__ == "__main__":
    asyncio.run(test_rag_integration())