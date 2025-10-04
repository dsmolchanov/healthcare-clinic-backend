#!/usr/bin/env python3
"""
Test script for multilingual message processor
Tests the agent's ability to respond in any language
"""

import asyncio
import os
from dotenv import load_dotenv
from app.api.multilingual_message_processor import MultilingualMessageProcessor, MessageRequest

# Load environment variables
load_dotenv()

async def test_multilingual_responses():
    """Test the processor with messages in different languages"""

    processor = MultilingualMessageProcessor()

    # Test messages in various languages
    test_messages = [
        ("Hello, what are your opening hours?", "English"),
        ("Hola, ¿cuáles son sus horarios de atención?", "Spanish"),
        ("Bonjour, quels sont vos horaires d'ouverture?", "French"),
        ("Olá, quais são os horários de funcionamento?", "Portuguese"),
        ("Guten Tag, was sind Ihre Öffnungszeiten?", "German"),
        ("Ciao, quali sono i vostri orari di apertura?", "Italian"),
        ("こんにちは、営業時間を教えてください", "Japanese"),
        ("你好，请问营业时间是什么？", "Chinese"),
        ("مرحبا، ما هي ساعات العمل؟", "Arabic"),
        ("Здравствуйте, какие у вас часы работы?", "Russian"),
        ("안녕하세요, 영업 시간이 어떻게 되나요?", "Korean"),
        ("Merhaba, çalışma saatleriniz nedir?", "Turkish"),
    ]

    print("=" * 80)
    print("MULTILINGUAL MESSAGE PROCESSOR TEST")
    print("Testing automatic language detection and response")
    print("=" * 80)

    for message, expected_lang in test_messages:
        print(f"\n📝 Testing {expected_lang}:")
        print(f"   User: {message}")

        # Create request
        request = MessageRequest(
            from_phone="+1234567890",
            to_phone="+0987654321",
            body=message,
            message_sid=f"test_{expected_lang}",
            clinic_id="test_clinic",
            clinic_name="Shtern Dental Clinic"
        )

        try:
            # Process message
            response = await processor.process_message(request)

            print(f"   🤖 Assistant: {response.message}")
            print(f"   📊 Detected language: {response.detected_language}")

        except Exception as e:
            print(f"   ❌ Error: {e}")

    print("\n" + "=" * 80)
    print("TEST COMPLETE")
    print("=" * 80)

    # Test conversation continuity
    print("\n📚 Testing conversation continuity in mixed languages:")

    session_test = [
        "Hello, I need a dental appointment",
        "¿Cuánto cuesta una limpieza dental?",
        "Merci, et pour le blanchiment des dents?",
        "返回英语，what about orthodontics?"
    ]

    for i, message in enumerate(session_test, 1):
        print(f"\n   Message {i}: {message}")

        request = MessageRequest(
            from_phone="+1111111111",  # Same phone for session
            to_phone="+0987654321",
            body=message,
            message_sid=f"session_test_{i}",
            clinic_id="test_clinic",
            clinic_name="Shtern Dental Clinic"
        )

        response = await processor.process_message(request)
        print(f"   Response: {response.message}")

    print("\n✅ All tests completed!")

if __name__ == "__main__":
    asyncio.run(test_multilingual_responses())
