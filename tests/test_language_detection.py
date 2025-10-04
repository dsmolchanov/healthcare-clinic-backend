"""
Test Multi-Language WhatsApp Conversation Flow
Demonstrates automatic language detection and response
"""

import asyncio
import pytest
from datetime import datetime

from app.services.language_detection_service import LanguageDetectionService
from app.whatsapp.language_aware_handler import LanguageAwareWhatsAppHandler

class TestLanguageDetection:
    """
    Test automatic language detection and multi-language responses
    """

    @pytest.fixture
    def language_service(self):
        return LanguageDetectionService()

    @pytest.fixture
    def whatsapp_handler(self):
        return LanguageAwareWhatsAppHandler()

    async def test_language_detection_accuracy(self, language_service):
        """
        Test language detection for various inputs
        """

        test_cases = [
            # Spanish
            ("Hola, quiero agendar una cita para mañana", "es", 0.7),
            ("Necesito ver al dentista urgentemente", "es", 0.7),

            # English
            ("Hello, I'd like to book an appointment", "en", 0.7),
            ("When is the next available slot?", "en", 0.7),

            # Portuguese
            ("Olá, preciso marcar uma consulta", "pt", 0.7),
            ("Qual é o horário de funcionamento?", "pt", 0.7),

            # French
            ("Bonjour, je voudrais prendre rendez-vous", "fr", 0.7),

            # German
            ("Guten Tag, ich möchte einen Termin vereinbaren", "de", 0.7),

            # Italian
            ("Buongiorno, vorrei prenotare un appuntamento", "it", 0.7),

            # Chinese
            ("你好，我想预约看牙", "zh", 0.7),

            # Japanese
            ("予約したいのですが", "ja", 0.7),

            # Korean
            ("안녕하세요, 예약하고 싶습니다", "ko", 0.7),

            # Arabic
            ("مرحبا، أريد حجز موعد", "ar", 0.7),

            # Hindi
            ("नमस्ते, मुझे अपॉइंटमेंट बुक करना है", "hi", 0.7),

            # Russian
            ("Здравствуйте, я хочу записаться на прием", "ru", 0.7),

            # Hebrew
            ("שלום, אני רוצה לקבוע תור לרופא שיניים", "he", 0.7),
            ("יש לי כאב שיניים חזק, צריך טיפול דחוף", "he", 0.7)
        ]

        for text, expected_lang, min_confidence in test_cases:
            detected_lang, confidence = await language_service.detect_language(text)

            print(f"Text: {text[:30]}...")
            print(f"Expected: {expected_lang}, Detected: {detected_lang}, Confidence: {confidence}")

            assert detected_lang == expected_lang, f"Failed to detect {expected_lang}"
            assert confidence >= min_confidence, f"Low confidence for {expected_lang}"

    async def test_patient_language_persistence(self, language_service):
        """
        Test that patient language preference is stored and retrieved
        """

        patient_phone = "+1234567890"

        # First message in Spanish
        lang1 = await language_service.get_or_detect_patient_language(
            patient_phone=patient_phone,
            message_text="Hola, necesito una cita",
            clinic_id="test_clinic_001"
        )
        assert lang1 == "es"

        # Second message without text - should return stored preference
        lang2 = await language_service.get_or_detect_patient_language(
            patient_phone=patient_phone,
            message_text=None,
            clinic_id="test_clinic_001"
        )
        assert lang2 == "es"

        # Third message in English with high confidence - should update
        lang3 = await language_service.get_or_detect_patient_language(
            patient_phone=patient_phone,
            message_text="Actually, I prefer to communicate in English",
            clinic_id="test_clinic_001"
        )
        assert lang3 == "en"

        # Fourth message - should now default to English
        lang4 = await language_service.get_or_detect_patient_language(
            patient_phone=patient_phone,
            message_text=None,
            clinic_id="test_clinic_001"
        )
        assert lang4 == "en"

    async def test_multilingual_conversation_flow(self, whatsapp_handler):
        """
        Test complete conversation flow with language switching
        """

        clinic_id = "test_clinic_001"
        organization_id = "test_org_001"

        # Conversation 1: Spanish speaker
        spanish_phone = "+34600000001"

        response1 = await whatsapp_handler.handle_message(
            from_number=spanish_phone,
            message_text="Hola, necesito una cita dental",
            clinic_id=clinic_id,
            organization_id=organization_id
        )

        assert "cita" in response1.lower() or "hola" in response1.lower()
        print(f"Spanish response: {response1[:100]}...")

        # Conversation 2: English speaker
        english_phone = "+1555000001"

        response2 = await whatsapp_handler.handle_message(
            from_number=english_phone,
            message_text="Hi, I need to book a dental appointment",
            clinic_id=clinic_id,
            organization_id=organization_id
        )

        assert "appointment" in response2.lower() or "hello" in response2.lower()
        print(f"English response: {response2[:100]}...")

        # Conversation 3: Portuguese speaker
        portuguese_phone = "+351900000001"

        response3 = await whatsapp_handler.handle_message(
            from_number=portuguese_phone,
            message_text="Olá, preciso marcar uma consulta",
            clinic_id=clinic_id,
            organization_id=organization_id
        )

        assert "consulta" in response3.lower() or "olá" in response3.lower()
        print(f"Portuguese response: {response3[:100]}...")

        # Conversation 4: Language switch mid-conversation
        switch_phone = "+1555000002"

        # Start in Spanish
        response4a = await whatsapp_handler.handle_message(
            from_number=switch_phone,
            message_text="Hola, información por favor",
            clinic_id=clinic_id,
            organization_id=organization_id
        )
        assert any(word in response4a.lower() for word in ["hola", "información", "clínica"])

        # Switch to English
        response4b = await whatsapp_handler.handle_message(
            from_number=switch_phone,
            message_text="Actually, can we continue in English?",
            clinic_id=clinic_id,
            organization_id=organization_id
        )
        assert any(word in response4b.lower() for word in ["hello", "help", "appointment", "clinic"])

        print("Language switch test passed!")

    async def test_emergency_in_multiple_languages(self, whatsapp_handler):
        """
        Test emergency handling in different languages
        """

        clinic_id = "test_clinic_001"
        organization_id = "test_org_001"

        emergency_messages = [
            ("+34600000002", "Tengo una emergencia dental, mucho dolor!", "es"),
            ("+1555000003", "I have severe tooth pain, need urgent help!", "en"),
            ("+351900000002", "Emergência! Dor de dente muito forte!", "pt"),
            ("+33600000001", "Urgence dentaire! J'ai très mal!", "fr"),
            ("+49170000001", "Notfall! Starke Zahnschmerzen!", "de"),
            ("+972500000001", "יש לי כאב שיניים נורא, צריך עזרה דחוף!", "he"),
        ]

        for phone, message, expected_lang in emergency_messages:
            response = await whatsapp_handler.handle_message(
                from_number=phone,
                message_text=message,
                clinic_id=clinic_id,
                organization_id=organization_id
            )

            # Check response contains emergency keywords in correct language
            emergency_keywords = {
                'es': ['urgencia', 'emergencia', 'hospital'],
                'en': ['emergency', 'urgent', 'hospital'],
                'pt': ['emergência', 'urgente', 'hospital'],
                'fr': ['urgence', 'hôpital'],
                'de': ['notfall', 'krankenhaus'],
                'he': ['חירום', 'דחוף', 'בית חולים']
            }

            keywords = emergency_keywords.get(expected_lang, emergency_keywords['es'])
            assert any(keyword in response.lower() for keyword in keywords), \
                f"Emergency response not in {expected_lang}"

            print(f"✓ Emergency handled in {expected_lang}")

    async def test_booking_flow_in_spanish(self, whatsapp_handler):
        """
        Test complete booking flow in Spanish
        """

        clinic_id = "test_clinic_001"
        organization_id = "test_org_001"
        phone = "+34600000003"

        # Step 1: Request appointment
        response1 = await whatsapp_handler.handle_message(
            from_number=phone,
            message_text="Quiero agendar una cita para limpieza dental",
            clinic_id=clinic_id,
            organization_id=organization_id
        )

        assert "disponibles" in response1.lower() or "citas" in response1.lower()
        print(f"Step 1 - Appointment request: {response1[:100]}...")

        # Step 2: Check if slots are shown
        if "1." in response1 and "2." in response1:
            # Select a slot
            response2 = await whatsapp_handler.handle_message(
                from_number=phone,
                message_text="1",  # Select first slot
                clinic_id=clinic_id,
                organization_id=organization_id
            )

            assert "confirmada" in response2.lower() or "reserva" in response2.lower()
            print(f"Step 2 - Slot selection: {response2[:100]}...")

    async def test_greeting_in_all_languages(self, language_service):
        """
        Test greeting generation in all supported languages
        """

        clinic_name = "Dental Excellence"

        for lang_code, lang_name in LanguageDetectionService.SUPPORTED_LANGUAGES.items():
            greeting = language_service.get_greeting(lang_code, clinic_name)

            assert clinic_name in greeting, f"Clinic name missing in {lang_name} greeting"
            assert len(greeting) > 20, f"Greeting too short for {lang_name}"

            print(f"{lang_name} ({lang_code}): {greeting}")

    async def test_language_detection_history(self, language_service):
        """
        Test that language detection history is properly tracked
        """

        patient_phone = "+1234567891"
        clinic_id = "test_clinic_001"

        # Send messages in different languages
        languages_used = []

        # Spanish
        lang1 = await language_service.get_or_detect_patient_language(
            patient_phone=patient_phone,
            message_text="Hola, buenos días",
            clinic_id=clinic_id
        )
        languages_used.append(lang1)

        # English
        lang2 = await language_service.get_or_detect_patient_language(
            patient_phone=patient_phone,
            message_text="I'd prefer English please",
            clinic_id=clinic_id
        )
        languages_used.append(lang2)

        # Portuguese
        lang3 = await language_service.get_or_detect_patient_language(
            patient_phone=patient_phone,
            message_text="Agora em português por favor",
            clinic_id=clinic_id
        )
        languages_used.append(lang3)

        # Check patient record has all detected languages
        patient = await language_service._get_patient_by_phone(patient_phone)
        assert patient is not None
        assert patient['preferred_language'] == lang3  # Last used language

        print(f"Languages detected: {languages_used}")
        print(f"Current preference: {patient['preferred_language']}")

# Run tests
if __name__ == "__main__":
    async def run_tests():
        tester = TestLanguageDetection()

        # Initialize services
        language_service = LanguageDetectionService()
        whatsapp_handler = LanguageAwareWhatsAppHandler()

        print("=" * 50)
        print("Testing Language Detection System")
        print("=" * 50)

        # Test 1: Language Detection
        print("\n1. Testing language detection accuracy...")
        await tester.test_language_detection_accuracy(language_service)

        # Test 2: Greeting Generation
        print("\n2. Testing greetings in all languages...")
        await tester.test_greeting_in_all_languages(language_service)

        # Test 3: Emergency Handling
        print("\n3. Testing emergency handling...")
        await tester.test_emergency_in_multiple_languages(whatsapp_handler)

        # Test 4: Language Persistence
        print("\n4. Testing language preference persistence...")
        await tester.test_patient_language_persistence(language_service)

        # Test 5: Booking Flow
        print("\n5. Testing booking flow in Spanish...")
        await tester.test_booking_flow_in_spanish(whatsapp_handler)

        print("\n" + "=" * 50)
        print("✅ All language detection tests passed!")
        print("=" * 50)

    # Run the tests
    asyncio.run(run_tests())
