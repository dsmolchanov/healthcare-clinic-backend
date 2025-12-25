"""
Timeout configuration for external service calls.

Centralized timeout settings for all external APIs.
Separate from DB timeouts (configured in app.database).

Usage:
    from app.services.external_timeouts import EVOLUTION_TIMEOUT

    async with httpx.AsyncClient(timeout=EVOLUTION_TIMEOUT) as client:
        response = await client.post(url, json=data)
"""
import httpx

# Evolution API (WhatsApp) - responsive, but can queue messages
EVOLUTION_TIMEOUT = httpx.Timeout(15.0, connect=5.0)

# NocoDB sync - can be slow for large syncs
NOCODB_TIMEOUT = httpx.Timeout(30.0, connect=10.0)

# LLM providers - inherently slow, especially for complex prompts
LLM_TIMEOUT = httpx.Timeout(60.0, connect=10.0)

# Calendar providers (Google, Microsoft) - generally responsive
CALENDAR_TIMEOUT = httpx.Timeout(20.0, connect=5.0)

# Stripe payments - needs to be reliable
STRIPE_TIMEOUT = httpx.Timeout(30.0, connect=10.0)

# Default for unknown services
DEFAULT_TIMEOUT = httpx.Timeout(30.0, connect=10.0)


def get_timeout_for_service(service_name: str) -> httpx.Timeout:
    """Get appropriate timeout for a service by name."""
    timeouts = {
        "evolution": EVOLUTION_TIMEOUT,
        "whatsapp": EVOLUTION_TIMEOUT,
        "nocodb": NOCODB_TIMEOUT,
        "llm": LLM_TIMEOUT,
        "openai": LLM_TIMEOUT,
        "gemini": LLM_TIMEOUT,
        "calendar": CALENDAR_TIMEOUT,
        "google_calendar": CALENDAR_TIMEOUT,
        "outlook": CALENDAR_TIMEOUT,
        "stripe": STRIPE_TIMEOUT,
    }
    return timeouts.get(service_name.lower(), DEFAULT_TIMEOUT)
