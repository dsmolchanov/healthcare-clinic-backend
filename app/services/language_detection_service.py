"""
Language Detection Service for the dental clinic system.
Detects language from text messages to provide appropriate responses.
"""

import re
from typing import Optional, Dict, Any
from collections import Counter

class LanguageDetectionService:
    """Service for detecting language from text messages"""

    def __init__(self):
        # Common Spanish words and patterns
        self.spanish_indicators = {
            'articles': ['el', 'la', 'los', 'las', 'un', 'una', 'unos', 'unas'],
            'prepositions': ['de', 'en', 'por', 'para', 'con', 'sin', 'sobre'],
            'common_words': ['que', 'es', 'y', 'a', 'no', 'si', 'sí', 'hola',
                           'gracias', 'por favor', 'buenos', 'buenas'],
            'question_words': ['qué', 'cómo', 'cuándo', 'dónde', 'por qué', 'quién'],
            'verbs': ['quiero', 'necesito', 'tengo', 'hacer', 'puede', 'puedo']
        }

        # Common English words and patterns
        self.english_indicators = {
            'articles': ['the', 'a', 'an'],
            'prepositions': ['in', 'on', 'at', 'by', 'for', 'with', 'from'],
            'common_words': ['is', 'are', 'was', 'were', 'and', 'or', 'but',
                           'hello', 'thanks', 'thank you', 'please'],
            'question_words': ['what', 'when', 'where', 'why', 'how', 'who'],
            'verbs': ['want', 'need', 'have', 'can', 'could', 'would']
        }

        # Patterns specific to Spanish
        self.spanish_patterns = [
            r'\b[a-zA-Z]+ción\b',  # Words ending in -ción
            r'\b[a-zA-Z]+mente\b',  # Adverbs ending in -mente
            r'\b[a-zA-Z]+ar\b',     # Infinitive verbs ending in -ar
            r'\b[a-zA-Z]+er\b',     # Infinitive verbs ending in -er
            r'\b[a-zA-Z]+ir\b',     # Infinitive verbs ending in -ir
            r'[áéíóúñ]',            # Spanish special characters
        ]

        # Default language for Mexican deployment
        self.default_language = 'es'

    async def detect_language(self, text: str) -> str:
        """
        Detect the language of the given text.

        Args:
            text: The text to analyze

        Returns:
            Language code ('es' for Spanish, 'en' for English)
        """
        if not text:
            return self.default_language

        text_lower = text.lower()
        words = text_lower.split()

        spanish_score = 0
        english_score = 0

        # Check for Spanish indicators
        for category, word_list in self.spanish_indicators.items():
            for word in word_list:
                if word in words:
                    spanish_score += 2 if category in ['common_words', 'verbs'] else 1

        # Check for English indicators
        for category, word_list in self.english_indicators.items():
            for word in word_list:
                if word in words:
                    english_score += 2 if category in ['common_words', 'verbs'] else 1

        # Check Spanish patterns
        for pattern in self.spanish_patterns:
            if re.search(pattern, text):
                spanish_score += 1

        # Check for special characters
        if any(char in text for char in 'áéíóúñÑ¿¡'):
            spanish_score += 3

        # Determine language based on scores
        if spanish_score > english_score:
            return 'es'
        elif english_score > spanish_score:
            return 'en'
        else:
            # Default to Spanish for Mexican deployment
            return self.default_language

    async def get_language_confidence(self, text: str) -> Dict[str, float]:
        """
        Get confidence scores for language detection.

        Args:
            text: The text to analyze

        Returns:
            Dictionary with language codes and confidence scores
        """
        if not text:
            return {'es': 0.5, 'en': 0.5}

        text_lower = text.lower()
        words = text_lower.split()

        spanish_score = 0
        english_score = 0

        # Calculate scores as before
        for category, word_list in self.spanish_indicators.items():
            for word in word_list:
                if word in words:
                    spanish_score += 2 if category in ['common_words', 'verbs'] else 1

        for category, word_list in self.english_indicators.items():
            for word in word_list:
                if word in words:
                    english_score += 2 if category in ['common_words', 'verbs'] else 1

        for pattern in self.spanish_patterns:
            if re.search(pattern, text):
                spanish_score += 1

        if any(char in text for char in 'áéíóúñÑ¿¡'):
            spanish_score += 3

        # Calculate confidence scores
        total_score = spanish_score + english_score
        if total_score == 0:
            return {'es': 0.5, 'en': 0.5}

        return {
            'es': spanish_score / total_score,
            'en': english_score / total_score
        }

    async def is_greeting(self, text: str) -> bool:
        """Check if the message is a greeting"""
        greetings = [
            # Spanish greetings
            'hola', 'buenos días', 'buenas tardes', 'buenas noches',
            'buen día', 'qué tal', 'saludos',
            # English greetings
            'hello', 'hi', 'good morning', 'good afternoon',
            'good evening', 'hey', 'greetings'
        ]

        text_lower = text.lower().strip()
        return any(greeting in text_lower for greeting in greetings)

    async def extract_intent(self, text: str) -> Optional[str]:
        """
        Extract the intent from the message.

        Returns:
            Intent string or None if no clear intent
        """
        text_lower = text.lower()

        # Appointment-related intents
        appointment_keywords = {
            'es': ['cita', 'agendar', 'reservar', 'consulta', 'turno'],
            'en': ['appointment', 'book', 'schedule', 'consultation', 'visit']
        }

        # Check for appointment intent
        for lang_keywords in appointment_keywords.values():
            if any(keyword in text_lower for keyword in lang_keywords):
                return 'appointment'

        # Emergency keywords
        emergency_keywords = {
            'es': ['urgencia', 'emergencia', 'dolor', 'duele', 'urgente'],
            'en': ['emergency', 'urgent', 'pain', 'hurts', 'immediately']
        }

        for lang_keywords in emergency_keywords.values():
            if any(keyword in text_lower for keyword in lang_keywords):
                return 'emergency'

        # Information request
        info_keywords = {
            'es': ['información', 'horario', 'precio', 'costo', 'ubicación', 'dirección'],
            'en': ['information', 'hours', 'price', 'cost', 'location', 'address']
        }

        for lang_keywords in info_keywords.values():
            if any(keyword in text_lower for keyword in lang_keywords):
                return 'information'

        # Cancellation
        cancel_keywords = {
            'es': ['cancelar', 'cancelación', 'cambiar'],
            'en': ['cancel', 'cancellation', 'change', 'reschedule']
        }

        for lang_keywords in cancel_keywords.values():
            if any(keyword in text_lower for keyword in lang_keywords):
                return 'cancellation'

        return None
