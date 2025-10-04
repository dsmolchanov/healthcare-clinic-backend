#!/usr/bin/env python3
"""
Test script to verify conversation memory persistence
"""

import os
import asyncio
from datetime import datetime
from app.memory.conversation_memory import ConversationMemoryManager
from app.api.multilingual_message_processor import MessageRequest, MultilingualMessageProcessor
import json

async def test_memory_persistence():
    print("=" * 60)
    print("Testing Conversation Memory Persistence")
    print("=" * 60)
    
    # Initialize components
    memory_mgr = ConversationMemoryManager()
    processor = MultilingualMessageProcessor()
    
    # Test parameters
    test_phone = '+5215512345678'  # Test phone number
    clinic_id = '3e411ecb-3411-4add-91e2-8fa897310cb0'
    clinic_name = 'Shtern Dental'
    
    # Test Case 1: Initial message with name introduction
    print("\n📱 Test 1: User introduces themselves")
    print("-" * 40)
    
    req1 = MessageRequest(
        from_phone=test_phone,
        to_phone='+14155238886',
        body='Hola, me llamo María y necesito una cita para limpieza dental',
        message_sid='test_msg_001',
        clinic_id=clinic_id,
        clinic_name=clinic_name,
        profile_name='María González'
    )
    
    resp1 = await processor.process_message(req1)
    print(f"User: {req1.body}")
    print(f"Agent: {resp1.message[:150]}...")
    print(f"Session ID: {resp1.session_id}")
    print(f"Language: {resp1.detected_language}")
    
    # Wait a moment to simulate real conversation
    await asyncio.sleep(2)
    
    # Test Case 2: Follow-up question - should remember name
    print("\n📱 Test 2: Follow-up question (should remember María)")
    print("-" * 40)
    
    req2 = MessageRequest(
        from_phone=test_phone,
        to_phone='+14155238886',
        body='¿Cuánto cuesta?',
        message_sid='test_msg_002',
        clinic_id=clinic_id,
        clinic_name=clinic_name,
        profile_name='María González'
    )
    
    resp2 = await processor.process_message(req2)
    print(f"User: {req2.body}")
    print(f"Agent: {resp2.message[:150]}...")
    print(f"Has History: {resp2.metadata.get('has_history')}")
    print(f"Message Count: {resp2.metadata.get('message_count')}")
    
    # Check if name was remembered
    if 'María' in resp2.message or 'maria' in resp2.message.lower():
        print("✅ SUCCESS: Agent remembered the user's name!")
    else:
        print("⚠️  WARNING: Agent did not reference the user's name")
    
    # Test Case 3: Another follow-up with context
    print("\n📱 Test 3: Appointment scheduling (should have full context)")
    print("-" * 40)
    
    req3 = MessageRequest(
        from_phone=test_phone,
        to_phone='+14155238886',
        body='Perfecto, ¿tienen disponibilidad el martes?',
        message_sid='test_msg_003',
        clinic_id=clinic_id,
        clinic_name=clinic_name,
        profile_name='María González'
    )
    
    resp3 = await processor.process_message(req3)
    print(f"User: {req3.body}")
    print(f"Agent: {resp3.message[:150]}...")
    print(f"Message Count: {resp3.metadata.get('message_count')}")
    
    # Verify conversation history
    print("\n📊 Conversation History Analysis")
    print("-" * 40)
    
    history = await memory_mgr.get_conversation_history(
        phone_number=test_phone,
        clinic_id=clinic_id,
        limit=20,
        include_all_sessions=True
    )
    
    print(f"Total messages in database: {len(history)}")
    
    if history:
        print("\nConversation flow:")
        for i, msg in enumerate(history, 1):
            role = msg.get('role', 'unknown')
            content = msg.get('content', '')[:80]
            print(f"  {i}. [{role:9}] {content}...")
    
    # Get user preferences
    preferences = await memory_mgr.get_user_preferences(test_phone)
    print(f"\nDetected user preferences:")
    print(f"  Language: {preferences.get('language', 'Not detected')}")
    print(f"  Preferred name: {preferences.get('preferred_name', 'Not detected')}")
    
    # Test Case 4: Simulate conversation after a break
    print("\n📱 Test 4: Message after 'long break' (testing session persistence)")
    print("-" * 40)
    
    # Simulate time passing
    print("Simulating user returning after a break...")
    await asyncio.sleep(2)
    
    req4 = MessageRequest(
        from_phone=test_phone,
        to_phone='+14155238886',
        body='Hola, ¿todavía tienen mi cita programada?',
        message_sid='test_msg_004',
        clinic_id=clinic_id,
        clinic_name=clinic_name,
        profile_name='María González'
    )
    
    resp4 = await processor.process_message(req4)
    print(f"User: {req4.body}")
    print(f"Agent: {resp4.message[:150]}...")
    
    # Final check
    if any(word in resp4.message.lower() for word in ['maría', 'maria', 'limpieza', 'martes']):
        print("✅ SUCCESS: Agent maintained context across messages!")
    else:
        print("⚠️  WARNING: Context might not be fully maintained")
    
    print("\n" + "=" * 60)
    print("Memory Persistence Test Complete")
    print("=" * 60)
    
    # Summary
    print("\n📋 Test Summary:")
    print(f"  • Total messages exchanged: {resp4.metadata.get('message_count', 0)}")
    print(f"  • Knowledge items used: {resp4.metadata.get('knowledge_used', 0)}")
    print(f"  • Memory context items: {resp4.metadata.get('memory_context_used', 0)}")
    print(f"  • Session maintained: {resp2.session_id == resp4.session_id}")
    
    return {
        'session_id': resp4.session_id,
        'total_messages': len(history),
        'name_remembered': 'maría' in resp4.message.lower() or 'maria' in resp4.message.lower()
    }

if __name__ == "__main__":
    result = asyncio.run(test_memory_persistence())
    print(f"\n🎯 Final Result: {json.dumps(result, indent=2)}")