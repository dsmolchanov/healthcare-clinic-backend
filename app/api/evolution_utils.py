"""
Evolution API Utilities
Helper functions for reliable Evolution API integration
"""

import aiohttp
import os
from typing import Dict, Any, Optional


async def get_real_connection_status(instance_name: str) -> Dict[str, Any]:
    """
    Get the REAL connection status of an Evolution instance
    Uses the Baileys Evolution API to check connection state
    """
    EVOLUTION_URL = os.getenv("EVOLUTION_SERVER_URL", "https://evolution-api-prod.fly.dev")

    async with aiohttp.ClientSession() as session:
        # Check /connectionState endpoint
        connection_state = "close"
        phone_number = None

        try:
            async with session.get(f"{EVOLUTION_URL}/instance/connectionState/{instance_name}") as resp:
                if resp.status == 200:
                    data = await resp.json()
                    instance = data.get("instance", {})
                    state = instance.get("state", "close")

                    # Map Evolution states to simpler states
                    if state == "open":
                        connection_state = "connected"
                    elif state == "connecting":
                        connection_state = "qr"  # Waiting for QR scan
                    else:
                        connection_state = "disconnected"
        except Exception as e:
            print(f"Error checking connection state: {e}")
            connection_state = "disconnected"

        # Check if truly connected (has phone number)
        is_truly_connected = connection_state == "connected"

        return {
            "state": connection_state,
            "phone_number": phone_number,
            "is_truly_connected": is_truly_connected
        }


async def verify_whatsapp_connection(instance_name: str) -> bool:
    """
    Verify if a WhatsApp instance is truly connected
    Returns True only if we can confirm a real connection
    """
    status = await get_real_connection_status(instance_name)
    return status.get("is_truly_connected", False)
