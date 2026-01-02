"""
Phone Number Normalization to WhatsApp JID Format

Phase 1B: Added normalize_phone for consistent database lookups.
"""


def normalize_phone(phone: str) -> str:
    """
    Normalize phone number for consistent database lookups.

    Phase 1B: Handles various WhatsApp JID formats and international formats
    to ensure consistent patient lookup across different input sources.

    Handles:
    - WhatsApp JIDs: 79857608984@s.whatsapp.net → 79857608984
    - International format: +79857608984 → 79857608984
    - Spaces/dashes: +7 985 760-89-84 → 79857608984
    - Mexico quirk: 521998... → 521998... (preserve country code)

    Args:
        phone: Raw phone number in any format

    Returns:
        Normalized digits-only string

    Examples:
        >>> normalize_phone("79857608984@s.whatsapp.net")
        '79857608984'
        >>> normalize_phone("+52 1 998 123 4567")
        '5219981234567'
        >>> normalize_phone("+7 985 760-89-84")
        '79857608984'
        >>> normalize_phone("")
        ''
    """
    if not phone:
        return ""

    # Strip WhatsApp JID suffix
    if "@" in phone:
        phone = phone.split("@")[0]

    # Remove all non-digit characters
    normalized = "".join(c for c in phone if c.isdigit())

    return normalized


def to_jid(number: str) -> str:
    """
    Convert phone number to WhatsApp JID format

    Examples:
        +79857608984 → 79857608984@s.whatsapp.net
        79857608984 → 79857608984@s.whatsapp.net
        20886862172386@lid → 20886862172386@lid (preserve LID format)

    Args:
        number: Phone number in various formats

    Returns:
        WhatsApp JID format string
    """
    # If already has a JID suffix (@lid, @s.whatsapp.net, etc), return as-is
    if "@" in number:
        return number

    # Remove + and any formatting (spaces, dashes)
    clean = number.replace("+", "").replace(" ", "").replace("-", "")

    # Add WhatsApp JID suffix
    return f"{clean}@s.whatsapp.net"