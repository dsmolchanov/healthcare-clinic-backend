"""
Evolution API Client
Handles communication with Evolution WhatsApp API
"""
import httpx
from typing import Optional
from .config import EVOLUTION_API_URL, EVOLUTION_API_KEY, EVOLUTION_HTTP_TIMEOUT, logger
from .e164 import to_jid


async def is_connected(instance: str) -> bool:
    """
    Check if Evolution instance is connected to WhatsApp

    Args:
        instance: WhatsApp instance name

    Returns:
        True if connected (state == "open"), False otherwise
    """
    url = f"{EVOLUTION_API_URL}/instance/connectionState/{instance}"
    headers = {"apikey": EVOLUTION_API_KEY}

    try:
        async with httpx.AsyncClient(timeout=EVOLUTION_HTTP_TIMEOUT) as client:
            response = await client.get(url, headers=headers)

            if response.status_code != 200:
                logger.warning(f"Evolution connection check failed: HTTP {response.status_code}")
                return False

            data = response.json()

            # Handle different response formats from Evolution API
            state = data.get("instance", {}).get("state") or data.get("state")

            is_open = state == "open"
            logger.debug(f"Evolution instance {instance} state: {state} (connected: {is_open})")
            return is_open

    except httpx.TimeoutException:
        logger.warning(f"Evolution connection check timed out for {instance}")
        return False
    except Exception as e:
        logger.error(f"Evolution connection check failed: {e}")
        return False


async def send_text(instance: str, to_number: str, text: str) -> bool:
    """
    Send text message via Evolution API

    Args:
        instance: WhatsApp instance name
        to_number: Recipient phone number
        text: Message text content

    Returns:
        True if message sent successfully, False otherwise
    """
    url = f"{EVOLUTION_API_URL}/message/sendText/{instance}"
    headers = {
        "apikey": EVOLUTION_API_KEY,
        "Content-Type": "application/json"
    }

    # Convert to WhatsApp JID format
    jid_number = to_jid(to_number)

    payload = {
        "number": jid_number,
        "text": text,
        "delay": 1000  # 1 second natural delay
    }

    try:
        logger.info(f"Sending message to {jid_number} via {instance}")
        async with httpx.AsyncClient(timeout=EVOLUTION_HTTP_TIMEOUT) as client:
            response = await client.post(url, headers=headers, json=payload)

            # Evolution API returns 2xx for success
            if response.status_code < 400:
                logger.info(f"✅ Message sent successfully (status {response.status_code})")
                return True
            else:
                logger.error(f"❌ Failed to send message: HTTP {response.status_code}")
                logger.error(f"Response: {response.text}")
                return False

    except httpx.TimeoutException:
        logger.error(f"❌ Send message timed out after {EVOLUTION_HTTP_TIMEOUT}s")
        return False
    except Exception as e:
        logger.error(f"❌ Send error: {e}")
        return False


async def send_typing_indicator(instance: str, to_number: str) -> bool:
    """
    Send typing indicator (three dots) to show agent is processing

    Args:
        instance: WhatsApp instance name
        to_number: Recipient phone number

    Returns:
        True if typing indicator sent successfully, False otherwise
    """
    url = f"{EVOLUTION_API_URL}/chat/presence/{instance}"
    headers = {
        "apikey": EVOLUTION_API_KEY,
        "Content-Type": "application/json"
    }

    # Convert to WhatsApp JID format
    jid_number = to_jid(to_number)

    payload = {
        "number": jid_number,
        "presence": "composing",  # Shows typing indicator
        "delay": 0
    }

    try:
        logger.debug(f"Sending typing indicator to {jid_number}")
        async with httpx.AsyncClient(timeout=EVOLUTION_HTTP_TIMEOUT) as client:
            response = await client.post(url, headers=headers, json=payload)

            if response.status_code < 400:
                logger.debug(f"✅ Typing indicator sent")
                return True
            else:
                logger.warning(f"⚠️ Failed to send typing indicator: HTTP {response.status_code}")
                return False

    except Exception as e:
        logger.warning(f"⚠️ Typing indicator error (non-critical): {e}")
        return False


async def send_quick_ack(instance: str, to_number: str, message: str = "Секунду, обрабатываю ваш запрос...") -> bool:
    """
    Send immediate acknowledgment message (bypasses queue for instant feedback)

    This is a high-priority message sent directly without queuing to provide
    instant feedback to users during AI processing.

    Args:
        instance: WhatsApp instance name
        to_number: Recipient phone number
        message: Quick ack message (default in Russian)

    Returns:
        True if message sent successfully, False otherwise
    """
    url = f"{EVOLUTION_API_URL}/message/sendText/{instance}"
    headers = {
        "apikey": EVOLUTION_API_KEY,
        "Content-Type": "application/json"
    }

    # Convert to WhatsApp JID format
    jid_number = to_jid(to_number)

    payload = {
        "number": jid_number,
        "text": message,
        "delay": 0  # No delay for quick ack
    }

    try:
        logger.info(f"Sending quick ack to {jid_number}")
        async with httpx.AsyncClient(timeout=EVOLUTION_HTTP_TIMEOUT) as client:
            response = await client.post(url, headers=headers, json=payload)

            if response.status_code < 400:
                logger.info(f"✅ Quick ack sent successfully")
                return True
            else:
                logger.warning(f"⚠️ Quick ack failed: HTTP {response.status_code}")
                return False

    except httpx.TimeoutException:
        logger.warning(f"⚠️ Quick ack timed out (non-critical)")
        return False
    except Exception as e:
        logger.warning(f"⚠️ Quick ack error (non-critical): {e}")
        return False


async def get_instance_info(instance: str) -> Optional[dict]:
    """
    Get Evolution instance information

    Args:
        instance: WhatsApp instance name

    Returns:
        Instance info dict or None if failed
    """
    url = f"{EVOLUTION_API_URL}/instance/connectionState/{instance}"
    headers = {"apikey": EVOLUTION_API_KEY}

    try:
        async with httpx.AsyncClient(timeout=EVOLUTION_HTTP_TIMEOUT) as client:
            response = await client.get(url, headers=headers)

            if response.status_code == 200:
                return response.json()
            else:
                logger.warning(f"Failed to get instance info: HTTP {response.status_code}")
                return None

    except Exception as e:
        logger.error(f"Failed to get instance info: {e}")
        return None