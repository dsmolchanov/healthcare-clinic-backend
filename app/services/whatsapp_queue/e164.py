"""
Phone Number Normalization to WhatsApp JID Format
"""

def to_jid(number: str) -> str:
    """
    Convert phone number to WhatsApp JID format

    Examples:
        +79857608984 → 79857608984@s.whatsapp.net
        79857608984 → 79857608984@s.whatsapp.net

    Args:
        number: Phone number in various formats

    Returns:
        WhatsApp JID format string
    """
    # Remove existing JID suffix if present
    clean = number.replace("@s.whatsapp.net", "")

    # Remove + and any formatting (spaces, dashes)
    clean = clean.replace("+", "").replace(" ", "").replace("-", "")

    # Add WhatsApp JID suffix
    return f"{clean}@s.whatsapp.net"