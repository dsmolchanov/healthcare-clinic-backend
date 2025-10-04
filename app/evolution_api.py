"""
Evolution API Client for WhatsApp Integration
Handles communication with Evolution API for WhatsApp instance management
"""

import os
import json
import aiohttp
import asyncio
import hashlib
import secrets
from typing import Dict, Any, Optional, List
from datetime import datetime, timedelta
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

@dataclass
class EvolutionInstance:
    """Represents an Evolution API WhatsApp instance"""
    id: str
    tenant_id: str
    instance_name: str
    instance_key: str
    phone_number: Optional[str] = None
    status: str = "disconnected"
    connection_type: str = "baileys"
    last_connected_at: Optional[datetime] = None
    config: Dict[str, Any] = None


class EvolutionAPIClient:
    """Client for Evolution API WhatsApp integration"""

    def __init__(self, base_url: str = None, api_key: str = None):
        # Use real Evolution API server
        self.base_url = base_url or os.getenv("EVOLUTION_SERVER_URL", "https://evolution-api-prod.fly.dev")
        self.api_key = api_key or os.getenv("EVOLUTION_API_KEY", "evolution_api_key_2024")
        self.session = None
        self.webhook_base_url = os.getenv("WEBHOOK_BASE_URL", "https://healthcare-clinic-backend.fly.dev/webhooks/evolution")

    async def __aenter__(self):
        """Async context manager entry"""
        await self.initialize()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit"""
        await self.close()

    async def initialize(self):
        """Initialize the HTTP session"""
        if not self.session:
            self.session = aiohttp.ClientSession(
                headers={"apikey": self.api_key}
            )

    async def close(self):
        """Close the HTTP session"""
        if self.session:
            await self.session.close()
            self.session = None

    def _generate_token(self) -> str:
        """Generate a secure token for instance authentication"""
        return secrets.token_urlsafe(32)

    async def _make_request(self, method: str, endpoint: str, **kwargs) -> Dict[str, Any]:
        """Make an HTTP request to Evolution API"""
        if not self.session:
            await self.initialize()

        url = f"{self.base_url}{endpoint}"

        try:
            async with self.session.request(method, url, **kwargs) as response:
                response_text = await response.text()

                if response.status >= 400:
                    logger.error(f"Evolution API error: {response.status} - {response_text}")
                    raise Exception(f"Evolution API error: {response.status} - {response_text}")

                try:
                    return json.loads(response_text) if response_text else {}
                except json.JSONDecodeError:
                    return {"response": response_text}

        except aiohttp.ClientError as e:
            logger.error(f"Network error calling Evolution API: {e}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error calling Evolution API: {e}")
            raise

    async def create_instance(self, tenant_id: str, instance_name: str = None) -> Dict[str, Any]:
        """Create new WhatsApp instance for tenant"""
        if not instance_name:
            instance_name = f"clinic-{tenant_id}-{int(datetime.now().timestamp())}"

        instance_token = self._generate_token()
        webhook_url = f"{self.webhook_base_url}/{instance_name}"

        payload = {
            "instanceName": instance_name,
            "token": instance_token,
            "qrcode": True,
            "integration": "WHATSAPP-BAILEYS",
            "webhookUrl": webhook_url,
            "webhookByEvents": True,
            "webhookEvents": [
                "APPLICATION_STARTUP",
                "QRCODE_UPDATED",
                "MESSAGES_SET",
                "MESSAGES_UPSERT",
                "MESSAGES_UPDATE",
                "MESSAGES_DELETE",
                "SEND_MESSAGE",
                "CONNECTION_UPDATE"
            ]
        }

        try:
            result = await self._make_request("POST", "/instance/create", json=payload)

            # Store instance in database (would be done in the actual implementation)
            # await self._store_instance(tenant_id, instance_name, instance_token, result)

            # Extract QR code if present in the response
            qrcode_data = None
            # Evolution API returns QR code in 'qr' field, not 'qrcode'
            if isinstance(result.get("qr"), dict):
                qrcode_data = result["qr"].get("base64")

            return {
                "success": True,
                "instance_name": instance_name,
                "instance_key": instance_token,
                "webhook_url": webhook_url,
                "qrcode": qrcode_data,  # Include QR code directly in response
                "instance": result.get("instance"),
                "state": result.get("state"),
                "createdAt": result.get("createdAt")
            }

        except Exception as e:
            logger.error(f"Failed to create instance: {e}")
            return {
                "success": False,
                "error": str(e)
            }

    async def get_qr_code(self, instance_name: str) -> Optional[str]:
        """Get QR code for WhatsApp Web authentication"""
        try:
            # Use the connect endpoint to get QR code
            result = await self._make_request("GET", f"/instance/connect/{instance_name}")

            # Evolution API returns QR code in 'qr' field
            if result and "qr" in result:
                qr_data = result["qr"]
                if isinstance(qr_data, dict):
                    return qr_data.get("base64") or qr_data.get("qr")
                return qr_data

            return None

        except Exception as e:
            logger.error(f"Failed to get QR code: {e}")
            return None

    async def get_connection_status(self, instance_name: str) -> Dict[str, Any]:
        """Check connection status of an instance"""
        try:
            result = await self._make_request("GET", f"/instance/connectionState/{instance_name}")

            return {
                "instance": instance_name,
                "state": result.get("state", "disconnected"),
                "status": result.get("status"),
                **result
            }

        except Exception as e:
            logger.error(f"Failed to get connection status: {e}")
            return {
                "instance": instance_name,
                "state": "error",
                "error": str(e)
            }

    async def send_text_message(self, instance_name: str, to: str, text: str, delay: int = 1200) -> Dict[str, Any]:
        """Send text message through Evolution API"""
        # Ensure phone number is in correct format
        to = self._format_phone_number(to)

        payload = {
            "number": to,
            "textMessage": {
                "text": text
            },
            "options": {
                "delay": delay,
                "presence": "composing"
            }
        }

        try:
            result = await self._make_request(
                "POST",
                f"/message/sendText/{instance_name}",
                json=payload
            )

            return {
                "success": True,
                "message_id": result.get("messageId"),
                **result
            }

        except Exception as e:
            logger.error(f"Failed to send message: {e}")
            return {
                "success": False,
                "error": str(e)
            }

    async def send_media_message(self, instance_name: str, to: str, media_url: str,
                                caption: str = None, media_type: str = "image") -> Dict[str, Any]:
        """Send media message through Evolution API"""
        to = self._format_phone_number(to)

        endpoints = {
            "image": "/message/sendImage",
            "audio": "/message/sendAudio",
            "video": "/message/sendVideo",
            "document": "/message/sendDocument"
        }

        endpoint = endpoints.get(media_type, "/message/sendImage")

        payload = {
            "number": to,
            "mediaUrl": media_url
        }

        if caption:
            payload["caption"] = caption

        try:
            result = await self._make_request(
                "POST",
                f"{endpoint}/{instance_name}",
                json=payload
            )

            return {
                "success": True,
                "message_id": result.get("messageId"),
                **result
            }

        except Exception as e:
            logger.error(f"Failed to send media message: {e}")
            return {
                "success": False,
                "error": str(e)
            }

    async def disconnect_instance(self, instance_name: str) -> Dict[str, Any]:
        """Disconnect a WhatsApp instance"""
        try:
            result = await self._make_request(
                "DELETE",
                f"/instance/logout/{instance_name}"
            )

            return {
                "success": True,
                "instance": instance_name,
                **result
            }

        except Exception as e:
            logger.error(f"Failed to disconnect instance: {e}")
            return {
                "success": False,
                "error": str(e)
            }

    async def delete_instance(self, instance_name: str) -> Dict[str, Any]:
        """Delete a WhatsApp instance completely"""
        try:
            result = await self._make_request(
                "DELETE",
                f"/instance/delete/{instance_name}"
            )

            return {
                "success": True,
                "instance": instance_name,
                **result
            }

        except Exception as e:
            logger.error(f"Failed to delete instance: {e}")
            return {
                "success": False,
                "error": str(e)
            }

    async def list_instances(self) -> List[Dict[str, Any]]:
        """List all instances"""
        try:
            result = await self._make_request("GET", "/instance/fetchInstances")

            if isinstance(result, list):
                return result
            elif isinstance(result, dict) and "instances" in result:
                return result["instances"]
            else:
                return []

        except Exception as e:
            logger.error(f"Failed to list instances: {e}")
            return []

    async def restart_instance(self, instance_name: str) -> Dict[str, Any]:
        """Restart a WhatsApp instance"""
        try:
            result = await self._make_request(
                "PUT",
                f"/instance/restart/{instance_name}"
            )

            return {
                "success": True,
                "instance": instance_name,
                **result
            }

        except Exception as e:
            logger.error(f"Failed to restart instance: {e}")
            return {
                "success": False,
                "error": str(e)
            }

    def _format_phone_number(self, phone: str) -> str:
        """Format phone number for WhatsApp"""
        # Remove any non-digit characters
        phone = ''.join(filter(str.isdigit, phone))

        # Add country code if not present (assuming US for now)
        if len(phone) == 10:
            phone = f"1{phone}"

        # Add WhatsApp suffix
        if not phone.endswith("@s.whatsapp.net"):
            phone = f"{phone}@s.whatsapp.net"

        return phone

    async def set_webhook(self, instance_name: str, webhook_url: str = None, events: List[str] = None) -> Dict[str, Any]:
        """Set webhook configuration for an instance"""
        if webhook_url is None:
            webhook_url = f"{self.webhook_base_url}/{instance_name}"

        if events is None:
            events = [
                "QRCODE_UPDATED",
                "MESSAGES_UPSERT",
                "MESSAGES_UPDATE",
                "CONNECTION_UPDATE"
            ]

        payload = {
            "url": webhook_url,
            "events": events,
            "webhookByEvents": True
        }

        try:
            result = await self._make_request(
                "POST",
                f"/webhook/set/{instance_name}",
                json=payload
            )

            return {
                "success": True,
                "webhook_url": webhook_url,
                **result
            }

        except Exception as e:
            logger.error(f"Failed to set webhook: {e}")
            return {
                "success": False,
                "error": str(e)
            }


# Utility functions for testing
async def test_evolution_client():
    """Test the Evolution API client"""
    client = EvolutionAPIClient()

    try:
        # Test creating an instance
        result = await client.create_instance("test-tenant-123")
        print(f"Create instance result: {result}")

        if result.get("success"):
            instance_name = result["instance_name"]

            # Test getting QR code
            qr_code = await client.get_qr_code(instance_name)
            print(f"QR Code: {qr_code[:50] if qr_code else 'None'}...")

            # Test connection status
            status = await client.get_connection_status(instance_name)
            print(f"Connection status: {status}")

            # List all instances
            instances = await client.list_instances()
            print(f"All instances: {instances}")

    finally:
        await client.close()


if __name__ == "__main__":
    # Run test if executed directly
    asyncio.run(test_evolution_client())
