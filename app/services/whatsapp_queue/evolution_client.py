"""
Evolution API Client
Handles communication with Evolution WhatsApp API
"""
import httpx
from typing import Optional, List, Dict, Any
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


async def send_text(instance: str, to_number: str, text: str) -> dict:
    """
    Send text message via Evolution API

    Args:
        instance: WhatsApp instance name
        to_number: Recipient phone number
        text: Message text content

    Returns:
        Dict with 'success' bool and 'provider_message_id' if available.
        For backwards compatibility, the dict can be used in boolean context.
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
                # Extract provider message ID from response
                # Evolution API returns message key in response
                provider_message_id = None
                response_data = None
                try:
                    response_data = response.json()
                    if isinstance(response_data, dict):
                        key = response_data.get('key', {})
                        provider_message_id = key.get('id')
                except Exception:
                    pass

                logger.info(
                    f"✅ Message sent successfully "
                    f"(status {response.status_code}, provider_id: {provider_message_id})"
                )

                return {
                    'success': True,
                    'provider_message_id': provider_message_id,
                    'response': response_data
                }
            else:
                logger.error(f"❌ Failed to send message: HTTP {response.status_code}")
                logger.error(f"Response: {response.text}")
                return {'success': False, 'error': f"HTTP {response.status_code}"}

    except httpx.TimeoutException:
        logger.error(f"❌ Send message timed out after {EVOLUTION_HTTP_TIMEOUT}s")
        return {'success': False, 'error': 'timeout'}
    except Exception as e:
        logger.error(f"❌ Send error: {e}")
        return {'success': False, 'error': str(e)}


async def send_typing_indicator(instance: str, to_number: str) -> bool:
    """
    Send typing indicator (three dots) to show agent is processing

    Args:
        instance: WhatsApp instance name
        to_number: Recipient phone number

    Returns:
        True if typing indicator sent successfully, False otherwise
    """
    url = f"{EVOLUTION_API_URL}/chat/sendPresence/{instance}"
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
        logger.debug(f"[Typing] Sending to {url} with number={jid_number}")
        async with httpx.AsyncClient(timeout=EVOLUTION_HTTP_TIMEOUT) as client:
            response = await client.post(url, headers=headers, json=payload)

            if response.status_code < 400:
                logger.debug(f"[Typing] ✅ Typing indicator sent successfully")
                return True
            else:
                logger.warning(
                    f"[Typing] Response: {response.status_code} - {response.text[:200]}"
                )
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


async def mark_chat_unread(instance: str, to_number: str) -> bool:
    """
    Mark a chat as unread in WhatsApp (HITL Phase 5 - best effort).

    This function asks Evolution API to mark a chat as unread, so the human
    operator sees an unread badge on their WhatsApp app. This provides a
    visual cue that a patient message needs attention.

    Note: This is a best-effort operation. If it fails, the system continues
    normally - the human can still see messages in the dashboard.

    Args:
        instance: WhatsApp instance name
        to_number: Phone number of the chat to mark unread

    Returns:
        True if chat was marked unread successfully, False otherwise
    """
    # Convert to WhatsApp JID format
    jid_number = to_jid(to_number)

    # Evolution API endpoint for marking chat unread
    # Note: This endpoint may not exist in all Evolution API versions
    url = f"{EVOLUTION_API_URL}/chat/markChatUnread/{instance}"
    headers = {
        "apikey": EVOLUTION_API_KEY,
        "Content-Type": "application/json"
    }

    payload = {
        "number": jid_number,
        "lastMessage": {
            "key": {
                "fromMe": False  # Mark as if we received a message
            }
        }
    }

    try:
        logger.debug(f"Marking chat as unread: {jid_number}")
        async with httpx.AsyncClient(timeout=EVOLUTION_HTTP_TIMEOUT) as client:
            response = await client.post(url, headers=headers, json=payload)

            if response.status_code < 400:
                logger.info(f"✅ Chat marked as unread for {jid_number}")
                return True
            else:
                # This is best-effort - don't log as error if endpoint doesn't exist
                logger.debug(
                    f"⚠️ Could not mark chat unread: HTTP {response.status_code} "
                    f"(best-effort operation)"
                )
                return False

    except Exception as e:
        # Best-effort - log at debug level only
        logger.debug(f"⚠️ Mark unread failed (best-effort): {e}")
        return False


async def send_presence_unavailable(instance: str, to_number: str) -> bool:
    """
    Set presence to unavailable (shows the agent is not active).

    This can be used when a session is under human control to indicate
    that the automated agent is not responding.

    Args:
        instance: WhatsApp instance name
        to_number: Recipient phone number

    Returns:
        True if presence was set successfully, False otherwise
    """
    url = f"{EVOLUTION_API_URL}/chat/sendPresence/{instance}"
    headers = {
        "apikey": EVOLUTION_API_KEY,
        "Content-Type": "application/json"
    }

    # Convert to WhatsApp JID format
    jid_number = to_jid(to_number)

    payload = {
        "number": jid_number,
        "presence": "unavailable",
        "delay": 0
    }

    try:
        logger.debug(f"Setting presence to unavailable for {jid_number}")
        async with httpx.AsyncClient(timeout=EVOLUTION_HTTP_TIMEOUT) as client:
            response = await client.post(url, headers=headers, json=payload)

            if response.status_code < 400:
                logger.debug(f"✅ Presence set to unavailable")
                return True
            else:
                logger.debug(f"⚠️ Failed to set presence: HTTP {response.status_code}")
                return False

    except Exception as e:
        logger.debug(f"⚠️ Set presence error (non-critical): {e}")
        return False


async def send_location(
    instance: str,
    to_number: str,
    lat: float,
    lng: float,
    name: Optional[str] = None,
    address: Optional[str] = None
) -> dict:
    """
    Send location pin via Evolution API.

    Evolution API Docs: https://doc.evolution-api.com/v2/pt/messages/send-location

    Args:
        instance: WhatsApp instance name
        to_number: Recipient phone number
        lat: Latitude coordinate
        lng: Longitude coordinate
        name: Location name (e.g., "Plaintalk Dental Clinic")
        address: Full address string

    Returns:
        Dict with 'success' bool and 'provider_message_id' if available.
    """
    url = f"{EVOLUTION_API_URL}/message/sendLocation/{instance}"
    headers = {
        "apikey": EVOLUTION_API_KEY,
        "Content-Type": "application/json"
    }

    jid_number = to_jid(to_number)

    payload = {
        "number": jid_number,
        "name": name or "Clinic Location",
        "address": address or "",
        "latitude": lat,
        "longitude": lng,
        "delay": 1000
    }

    try:
        logger.info(f"Sending location to {jid_number} via {instance}")
        async with httpx.AsyncClient(timeout=EVOLUTION_HTTP_TIMEOUT) as client:
            response = await client.post(url, headers=headers, json=payload)

            if response.status_code < 400:
                provider_message_id = None
                try:
                    data = response.json()
                    if isinstance(data, dict):
                        provider_message_id = data.get('key', {}).get('id')
                except Exception:
                    pass

                logger.info(f"✅ Location sent (provider_id: {provider_message_id})")
                return {
                    'success': True,
                    'provider_message_id': provider_message_id
                }
            else:
                logger.error(f"❌ Failed to send location: HTTP {response.status_code}")
                return {'success': False, 'error': f"HTTP {response.status_code}"}

    except httpx.TimeoutException:
        logger.error(f"❌ Send location timed out")
        return {'success': False, 'error': 'timeout'}
    except Exception as e:
        logger.error(f"❌ Send location error: {e}")
        return {'success': False, 'error': str(e)}


async def send_buttons(
    instance: str,
    to_number: str,
    text: str,
    buttons: List[Dict[str, str]],
    title: Optional[str] = None,
    footer: Optional[str] = None
) -> dict:
    """
    Send interactive button message via Evolution API.

    Evolution API Docs: https://doc.evolution-api.com/v2/pt/messages/send-buttons

    Args:
        instance: WhatsApp instance name
        to_number: Recipient phone number
        text: Message body text
        buttons: List of button dicts, each with 'buttonId' and 'buttonText'
                 Example: [{"buttonId": "confirm", "buttonText": {"displayText": "Confirm"}}]
        title: Optional message title
        footer: Optional footer text

    Returns:
        Dict with 'success' bool and 'provider_message_id' if available.
    """
    url = f"{EVOLUTION_API_URL}/message/sendButtons/{instance}"
    headers = {
        "apikey": EVOLUTION_API_KEY,
        "Content-Type": "application/json"
    }

    jid_number = to_jid(to_number)

    # Build buttons in Evolution API format
    formatted_buttons = []
    for btn in buttons:
        if isinstance(btn.get('buttonText'), dict):
            formatted_buttons.append(btn)
        else:
            # Simple format: {"id": "confirm", "text": "Confirm"}
            formatted_buttons.append({
                "buttonId": btn.get('id') or btn.get('buttonId'),
                "buttonText": {"displayText": btn.get('text') or btn.get('displayText')}
            })

    payload = {
        "number": jid_number,
        "title": title or "",
        "description": text,
        "footer": footer or "",
        "buttons": formatted_buttons,
        "delay": 1000
    }

    try:
        logger.info(f"Sending buttons to {jid_number} via {instance}")
        async with httpx.AsyncClient(timeout=EVOLUTION_HTTP_TIMEOUT) as client:
            response = await client.post(url, headers=headers, json=payload)

            if response.status_code < 400:
                provider_message_id = None
                try:
                    data = response.json()
                    if isinstance(data, dict):
                        provider_message_id = data.get('key', {}).get('id')
                except Exception:
                    pass

                logger.info(f"✅ Buttons sent (provider_id: {provider_message_id})")
                return {
                    'success': True,
                    'provider_message_id': provider_message_id
                }
            else:
                logger.error(f"❌ Failed to send buttons: HTTP {response.status_code}")
                logger.error(f"Response: {response.text}")
                return {'success': False, 'error': f"HTTP {response.status_code}"}

    except httpx.TimeoutException:
        logger.error(f"❌ Send buttons timed out")
        return {'success': False, 'error': 'timeout'}
    except Exception as e:
        logger.error(f"❌ Send buttons error: {e}")
        return {'success': False, 'error': str(e)}


async def send_template(
    instance: str,
    to_number: str,
    template_name: str,
    language: str = "en",
    components: Optional[List[Dict[str, Any]]] = None
) -> dict:
    """
    Send WhatsApp template message via Evolution API.

    Templates must be pre-approved by Meta. This is for Business API compliance.

    Evolution API Docs: https://doc.evolution-api.com/v2/pt/messages/send-template

    Args:
        instance: WhatsApp instance name
        to_number: Recipient phone number
        template_name: Approved template name (e.g., "appointment_reminder")
        language: Language code (e.g., "en", "ru", "es")
        components: Optional template components for variable substitution

    Returns:
        Dict with 'success' bool and 'provider_message_id' if available.
    """
    url = f"{EVOLUTION_API_URL}/message/sendTemplate/{instance}"
    headers = {
        "apikey": EVOLUTION_API_KEY,
        "Content-Type": "application/json"
    }

    jid_number = to_jid(to_number)

    payload = {
        "number": jid_number,
        "name": template_name,
        "language": language,
        "delay": 1000
    }

    if components:
        payload["components"] = components

    try:
        logger.info(f"Sending template '{template_name}' to {jid_number} via {instance}")
        async with httpx.AsyncClient(timeout=EVOLUTION_HTTP_TIMEOUT) as client:
            response = await client.post(url, headers=headers, json=payload)

            if response.status_code < 400:
                provider_message_id = None
                try:
                    data = response.json()
                    if isinstance(data, dict):
                        provider_message_id = data.get('key', {}).get('id')
                except Exception:
                    pass

                logger.info(f"✅ Template sent (provider_id: {provider_message_id})")
                return {
                    'success': True,
                    'provider_message_id': provider_message_id
                }
            else:
                logger.error(f"❌ Failed to send template: HTTP {response.status_code}")
                logger.error(f"Response: {response.text}")
                return {'success': False, 'error': f"HTTP {response.status_code}"}

    except httpx.TimeoutException:
        logger.error(f"❌ Send template timed out")
        return {'success': False, 'error': 'timeout'}
    except Exception as e:
        logger.error(f"❌ Send template error: {e}")
        return {'success': False, 'error': str(e)}