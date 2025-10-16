"""
Enhanced Text Normalization for Multilingual Intent Routing

Robust normalization pipeline that handles:
- Unicode normalization (NFKC)
- Typo correction
- Stopword removal with word boundaries
- Language-specific processing
"""

import re
import unicodedata
import logging
from typing import Dict, List, Tuple, Optional

logger = logging.getLogger(__name__)

# Shared flags for Cyrillic-aware regex
RU_FLAGS = re.IGNORECASE | re.UNICODE


class TextNormalizer:
    """
    Production-grade text normalization for service query matching

    Key improvements over basic stopword removal:
    1. Word-boundary regex (no substring nuking)
    2. Typo dictionary for common mistakes
    3. Language-specific normalization
    4. Token length filtering (≥3 chars)
    5. Start-filler removal only
    """

    def __init__(self):
        # Common typo corrections (extendable per clinic)
        self.typo_fixes = {
            'russian': {
                'зоч': 'хочу',              # Common typo for "хочу" (want)
                'виниров': 'виниры',         # Normalize genitive → nominative
                'имплонт': 'имплант',        # Common typo
                'отбеливания': 'отбеливание', # Normalize genitive → nominative
            },
            'english': {
                'veneers': 'veneers',  # Already normalized
                'implants': 'implants',
            },
            'spanish': {
                'carillas': 'carillas',
            }
        }

        # Compile regex patterns for efficiency
        self._compile_patterns()

    def _compile_patterns(self):
        """Pre-compile all regex patterns for performance"""

        # Start fillers (only remove at beginning of message)
        self.start_filler_ru = re.compile(r'^\s*(?:нет|да)[,!\.\s]+', RU_FLAGS)
        self.start_filler_en = re.compile(r'^\s*(?:no|yes|well)[,!\.\s]+', re.IGNORECASE)

        # Non-word characters (keep letters/digits/hyphen/space)
        self.non_words = re.compile(r'[^\w\s-]+', re.UNICODE)

        # Multiple spaces
        self.spaces = re.compile(r'\s+', re.UNICODE)

        # Russian stopwords with word boundaries
        ru_stopwords = [
            # Action verbs (asking/seeking info)
            r'установк\w*', r'постав\w*', r'сдела\w*', r'провед\w*',
            r'нужн\w*', r'хочу', r'хотел\w*', r'хоч\w*',
            r'узна\w*', r'интерес\w*', r'подскаж\w*', r'скаж\w*',
            r'можн\w*', r'расскаж\w*',
            # Price-related words
            r'сколько', r'стоит', r'стоимост\w*', r'цена', r'цен[аы]',
            r'какая', r'какова', r'какой',
            # Fillers/pronouns (conservative)
            r'я', r'мне', r'меня', r'мой', r'моя', r'мои', r'мне',
            r'у\s+вас', r'вам', r'вами',
            # Prepositions that create FTS noise
            r'на', r'за', r'про', r'для', r'без', r'под',
            # Politeness
            r'пожалуйста', r'спасибо', r'благодарю',
            # Question words
            r'что', r'где', r'когда', r'как', r'почему', r'зачем',
        ]

        # English stopwords
        en_stopwords = [
            r'how', r'much', r'does', r'cost', r'price\w*',
            r'what\w*', r'tell', r'me', r'about', r'is', r'the',
            r'for', r'of', r'a', r'an', r'need\w*', r'want\w*',
            r'install\w*', r'get\w*', r'make\w*', r'do\w*',
            r'can', r'you', r'please', r'thanks', r'thank',
        ]

        # Spanish stopwords
        es_stopwords = [
            r'cuánto', r'cuesta', r'cuestan', r'precio\w*',
            r'qué', r'de', r'el', r'la', r'los', r'las',
            r'un\w*', r'necesit\w*', r'quier\w*', r'hacer\w*',
            r'para', r'por', r'favor', r'gracias',
        ]

        # Compile with word boundaries
        self.stopwords_ru = re.compile(
            r'\b(?:' + '|'.join(ru_stopwords) + r')\b',
            RU_FLAGS
        )
        self.stopwords_en = re.compile(
            r'\b(?:' + '|'.join(en_stopwords) + r')\b',
            re.IGNORECASE
        )
        self.stopwords_es = re.compile(
            r'\b(?:' + '|'.join(es_stopwords) + r')\b',
            re.IGNORECASE
        )

    def detect_language(self, text: str) -> str:
        """Simple language detection based on character sets"""
        if re.search(r'[а-яА-Я]', text):
            return 'russian'
        elif re.search(r'[א-ת]', text):
            return 'hebrew'
        elif re.search(r'[áéíóúñ¿¡]', text):
            return 'spanish'
        else:
            return 'english'

    def fix_typos(self, text: str, language: str) -> str:
        """
        Fix common typos using dictionary lookup

        Uses word-boundary replacement to avoid substring issues
        """
        if language not in self.typo_fixes:
            return text

        typo_dict = self.typo_fixes[language]
        if not typo_dict:
            return text

        def replace_func(match):
            word = match.group(0)
            return typo_dict.get(word.lower(), word)

        # Build pattern from typo dictionary keys
        pattern = r'\b(?:' + '|'.join(map(re.escape, typo_dict.keys())) + r')\b'
        flags = RU_FLAGS if language == 'russian' else re.IGNORECASE

        return re.sub(pattern, replace_func, text, flags=flags)

    def normalize_russian(self, text: str) -> str:
        """
        Normalize Russian text for service matching

        Pipeline:
        1. NFKC normalization + ё→е
        2. Remove start fillers (leading 'нет,', 'да,')
        3. Fix common typos
        4. Remove stopwords (word-boundary)
        5. Strip punctuation & collapse whitespace
        6. Filter tokens ≥3 chars
        """
        # Unicode normalization
        t = unicodedata.normalize('NFKC', text)
        t = t.replace('ё', 'е')  # Normalize ё to е

        # Remove start fillers only
        t = self.start_filler_ru.sub(' ', t)

        # Fix typos
        t = self.fix_typos(t, 'russian')

        # Remove stopwords
        t = self.stopwords_ru.sub(' ', t)

        # Strip punctuation
        t = self.non_words.sub(' ', t)

        # Collapse whitespace
        t = self.spaces.sub(' ', t).strip()

        # Filter short tokens (keep ≥3 chars)
        tokens = [tok for tok in t.split() if len(tok) >= 3]

        return ' '.join(tokens)

    def normalize_english(self, text: str) -> str:
        """Normalize English text"""
        t = unicodedata.normalize('NFKC', text)
        t = self.start_filler_en.sub(' ', t)
        t = self.fix_typos(t, 'english')
        t = self.stopwords_en.sub(' ', t)
        t = self.non_words.sub(' ', t)
        t = self.spaces.sub(' ', t).strip()
        tokens = [tok for tok in t.split() if len(tok) >= 3]
        return ' '.join(tokens)

    def normalize_spanish(self, text: str) -> str:
        """Normalize Spanish text"""
        t = unicodedata.normalize('NFKC', text)
        t = self.fix_typos(t, 'spanish')
        t = self.stopwords_es.sub(' ', t)
        t = self.non_words.sub(' ', t)
        t = self.spaces.sub(' ', t).strip()
        tokens = [tok for tok in t.split() if len(tok) >= 3]
        return ' '.join(tokens)

    def normalize(self, text: str, language: Optional[str] = None) -> str:
        """
        Main normalization entry point

        Args:
            text: Input text to normalize
            language: Language hint ('russian', 'english', 'spanish', or None for auto-detect)

        Returns:
            Normalized text suitable for service matching
        """
        if not text or not text.strip():
            return ""

        # Auto-detect if not specified
        if language is None:
            language = self.detect_language(text)

        # Route to language-specific normalizer
        if language == 'russian':
            return self.normalize_russian(text)
        elif language == 'spanish':
            return self.normalize_spanish(text)
        elif language == 'english':
            return self.normalize_english(text)
        else:
            # Fallback: basic normalization
            t = unicodedata.normalize('NFKC', text)
            t = self.non_words.sub(' ', t)
            t = self.spaces.sub(' ', t).strip()
            return t


# Singleton instance
_normalizer: Optional[TextNormalizer] = None


def get_normalizer() -> TextNormalizer:
    """Get or create singleton normalizer instance"""
    global _normalizer
    if _normalizer is None:
        _normalizer = TextNormalizer()
    return _normalizer


def normalize_query(text: str, language: Optional[str] = None) -> str:
    """
    Convenience function for query normalization

    Example:
        >>> normalize_query("Нет, я зоч узнать стоимость виниров")
        'виниры'
    """
    normalizer = get_normalizer()
    return normalizer.normalize(text, language)


# Unit tests (can be run with pytest)
def test_russian_normalization_veneers_case():
    """Test the exact failing case from logs"""
    text = "Нет, я зоч узнать стоимость виниров"
    result = normalize_query(text)
    assert result == "виниры", f"Expected 'виниры', got '{result}'"
    logger.info(f"✅ Veneers case: '{text}' → '{result}'")


def test_russian_normalization_variants():
    """Test various Russian query variants"""
    cases = [
        ("Подскажите, пожалуйста, цена на виниры", "виниры"),
        ("сколько стоит керамические виниры?", "керамические виниры"),
        ("хочу узнать стоимость имплантов", "имплантов"),  # Note: needs alias normalization
        ("виниров сколько", "виниры"),  # Typo fix
    ]

    for text, expected in cases:
        result = normalize_query(text)
        logger.info(f"Test: '{text}' → '{result}' (expected: '{expected}')")
        assert expected in result or result in expected, \
            f"Failed: '{text}' → '{result}' (expected '{expected}')"


def test_english_normalization():
    """Test English query normalization"""
    cases = [
        ("how much for veneers?", "veneers"),
        ("what is the price of dental implants", "dental implants"),
        ("can you tell me about whitening", "whitening"),
    ]

    for text, expected in cases:
        result = normalize_query(text, language='english')
        logger.info(f"EN Test: '{text}' → '{result}'")
        assert expected in result or result in expected


if __name__ == "__main__":
    # Run tests
    logging.basicConfig(level=logging.INFO)
    test_russian_normalization_veneers_case()
    test_russian_normalization_variants()
    test_english_normalization()
    print("\n✅ All normalization tests passed!")
