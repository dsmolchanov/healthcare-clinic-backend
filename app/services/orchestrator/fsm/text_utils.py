"""
Unicode-safe text utilities for multilingual WhatsApp input.

Phase 5.1: Robust multilingual tokenization and intent detection.

Key improvements over naive split()/strip():
1. Unicode normalization (NFKC) handles full-width chars, combined accents
2. Punctuation removal by Unicode category (not hardcoded ASCII list)
3. Negation-first checking prevents "не хочу" → True false positives
4. Language-scoped matching with English fallback
"""
import re
import unicodedata
from typing import List, Set


# ==========================================
# Affirmative/Negative word sets by language
# ==========================================

AFFIRMATIVES = {
    "ru": {"да", "ага", "угу", "конечно", "хорошо", "давай", "давайте", "запиши", "хочу", "ладно"},
    "en": {"yes", "yeah", "yep", "sure", "ok", "okay", "please", "absolutely", "confirm"},
    "es": {"sí", "si", "claro", "ok", "bueno", "vale", "confirmo"},
    "he": {"כן", "בטח", "אוקי", "טוב", "בסדר", "נכון", "מעולה", "סבבה", "יופי", "אישור"},
}

NEGATIONS = {
    "ru": {"нет", "не", "неа", "отмена", "отменить"},
    "en": {"no", "nope", "dont", "don't", "not", "never", "cancel", "nevermind"},
    "es": {"no", "nunca", "jamás", "cancelar"},
    "he": {"לא", "אל", "אין", "ביטול", "לבטל", "עזוב"},
}

REJECTIONS = {
    "ru": {"нет", "неа", "отмена", "отменить", "отказ"},
    "en": {"no", "nope", "cancel", "nevermind", "stop"},
    "es": {"no", "cancelar", "nunca"},
    "he": {"לא", "ביטול", "לבטל", "עזוב", "תעזוב"},
}


# ==========================================
# Unicode-safe tokenization
# ==========================================

def normalize_tokens(text: str) -> List[str]:
    """
    Unicode-safe tokenization for multilingual WhatsApp input.

    Handles edge cases:
    - «да» (guillemets)
    - да… (ellipsis)
    - да— (em-dash)
    - (да) (parentheses)
    - "да" (quotes)
    - да!!! (multiple punctuation)

    Returns:
        List of lowercase word tokens with all punctuation/symbols removed
    """
    if not text:
        return []

    # Normalize weird unicode (full-width, combined accents, etc.)
    text = unicodedata.normalize("NFKC", text).lower().strip()

    # Replace punctuation (P) and symbols (S) with spaces
    # This covers «», …, —, emoji modifiers, quotes, etc.
    cleaned = "".join(
        " " if unicodedata.category(ch)[0] in ("P", "S") else ch
        for ch in text
    )

    # Extract word tokens (Unicode letters) and numbers
    # [^\W\d_]+ matches Unicode letters
    # [0-9]+ matches digits
    tokens = re.findall(r"[^\W\d_]+|[0-9]+", cleaned, flags=re.UNICODE)

    return tokens


def get_word_set(text: str) -> Set[str]:
    """Get set of normalized tokens for O(1) membership testing."""
    return set(normalize_tokens(text))


# ==========================================
# Negation detection
# ==========================================

def has_negation_prefix(tokens: List[str], lang: str) -> bool:
    """
    Check first 3 tokens for negation words.

    This catches patterns like:
    - "не хочу" (I don't want)
    - "нет, давай другое" (no, let's do something else)
    - "no thanks"

    Args:
        tokens: List of normalized tokens
        lang: Current language code

    Returns:
        True if negation detected in first 3 tokens
    """
    if not tokens:
        return False

    # Get negations for current language + English fallback
    neg_set = NEGATIONS.get(lang, set()) | NEGATIONS.get("en", set())

    # Check first 3 tokens (most negations appear early)
    return any(t in neg_set for t in tokens[:3])


# ==========================================
# Intent detection functions
# ==========================================

def is_affirmative(text: str, lang: str) -> bool:
    """
    Check if user response is affirmative (yes, да, sí, etc.).

    SOTA Implementation (Phase 5.1):
    - Unicode-safe tokenization handles «да», да…, да— etc.
    - Negation-first check prevents "не хочу" → True
    - Language-scoped matching (current + English fallback)
    - First-token priority for "Да, ..." patterns

    Args:
        text: User's response text
        lang: Language code

    Returns:
        True if this is an affirmative response
    """
    tokens = normalize_tokens(text)
    if not tokens:
        return False

    # CRITICAL: Negation override - check first 3 tokens
    # This prevents "не хочу" (contains хочу) from being True
    if has_negation_prefix(tokens, lang):
        return False

    # Get affirmatives for current language + English fallback
    aff_set = AFFIRMATIVES.get(lang, set()) | AFFIRMATIVES.get("en", set())

    # Strong signal: first token is affirmative (handles "Да, мне нужно...")
    if tokens[0] in aff_set:
        return True

    # Weak signal: short utterance with affirmative anywhere
    # Only for very short messages like "ok!", "да-да", "sure thing"
    if len(tokens) <= 3 and any(t in aff_set for t in tokens):
        return True

    return False


def is_confirmation(text: str, lang: str) -> bool:
    """
    Check if text is a booking confirmation.

    This is a stricter check than is_affirmative() - used when
    we're explicitly asking "confirm this booking?"

    Args:
        text: User's response text
        lang: Language code

    Returns:
        True if this is a confirmation
    """
    tokens = normalize_tokens(text)
    if not tokens:
        return False

    # Negation override
    if has_negation_prefix(tokens, lang):
        return False

    # Confirmation words (subset of affirmatives, more strict)
    confirms = {
        "yes", "yeah", "yep", "sure", "ok", "okay", "confirm",
        "да", "хорошо", "ладно", "подтверждаю", "конечно",
        "sí", "si", "vale", "confirmo", "claro",
        "כן", "בסדר", "טוב", "אישור", "מאשר",  # Hebrew
    }

    # First token match or short utterance
    if tokens[0] in confirms:
        return True

    if len(tokens) <= 3 and any(t in confirms for t in tokens):
        return True

    return False


def is_rejection(text: str, lang: str) -> bool:
    """
    Check if text is a rejection/cancellation.

    Args:
        text: User's response text
        lang: Language code

    Returns:
        True if this is a rejection
    """
    tokens = normalize_tokens(text)
    if not tokens:
        return False

    # Get rejections for current language + English fallback
    rej_set = REJECTIONS.get(lang, set()) | REJECTIONS.get("en", set())

    # First token is rejection
    if tokens[0] in rej_set:
        return True

    # Short utterance with rejection
    if len(tokens) <= 3 and any(t in rej_set for t in tokens):
        return True

    return False


# ==========================================
# Availability intent detection
# ==========================================

AVAILABILITY_KEYWORDS = {
    "ru": {
        "свободен", "свободна", "свободны", "свободно",  # free/available
        "доступен", "доступна", "доступны",  # available
        "принимает", "работает",  # receiving/working
        "есть", "время", "окошко", "запись",  # есть время, окошко, запись
        "когда", "можно", "записаться",  # when can I book
        "слоты", "места",  # slots, spots
    },
    "en": {
        "available", "availability", "free", "open",
        "slot", "slots", "spot", "spots",
        "appointment", "appointments",
        "when", "schedule", "book", "booking",
    },
    "es": {
        "disponible", "disponibles", "libre", "libres",
        "cita", "citas", "horario", "horarios",
        "cuando", "reservar", "agendar",
    },
    "he": {
        "פנוי", "פנויה", "פנויים",  # free/available (m/f/pl)
        "זמין", "זמינה", "זמינים",  # available (m/f/pl)
        "תור", "תורים",  # appointment(s)
        "פגישה", "פגישות",  # meeting(s)
        "מתי", "לקבוע", "להזמין",  # when, to schedule, to book
        "שעות", "זמן",  # hours, time
    },
}


def has_availability_intent(text: str, lang: str) -> bool:
    """
    Check if user is explicitly asking about availability.

    This guards against auto-calling check_availability when user
    just mentioned a doctor but didn't ask about availability.

    Examples that SHOULD return True:
    - "Доктор Штерн свободен завтра?" (Is Dr. Shtern free tomorrow?)
    - "Когда можно записаться к Марку?" (When can I book with Mark?)
    - "Is Dr. Smith available?"
    - "Do you have any slots?"

    Examples that should NOT return True:
    - "Да, мне нужно отбеливание" (Yes, I need whitening)
    - "I want a cleaning" (wants service, not asking about availability)

    Args:
        text: User's message text
        lang: Language code

    Returns:
        True if user is asking about availability
    """
    tokens = normalize_tokens(text)
    if not tokens:
        return False

    # Get keywords for current language + English fallback
    kw_set = AVAILABILITY_KEYWORDS.get(lang, set()) | AVAILABILITY_KEYWORDS.get("en", set())

    # Check for any availability keyword
    return any(t in kw_set for t in tokens)
