"""
Phone Number Normalization to WhatsApp JID Format
"""

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