"""
Mock Evolution API endpoints for testing WhatsApp integration
This provides a simple mock that returns QR codes and connection status
"""

from fastapi import APIRouter, HTTPException
from typing import Dict, Any
import uuid
import base64
from datetime import datetime

router = APIRouter(prefix="/evolution-mock", tags=["evolution-mock"])

# Store for mock instances
mock_instances = {}

# Sample QR code (a simple test pattern)
MOCK_QR_CODE = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="

@router.post("/instance/create")
async def create_instance(data: Dict[str, Any]):
    """Mock instance creation"""
    instance_name = data.get("instanceName", f"instance-{uuid.uuid4()}")

    mock_instances[instance_name] = {
        "instance": {
            "instanceName": instance_name,
            "status": "created"
        },
        "hash": {
            "apikey": "mock_api_key_" + instance_name
        },
        "qrcode": {
            "code": MOCK_QR_CODE,
            "base64": MOCK_QR_CODE
        },
        "webhook": data.get("webhookUrl", "")
    }

    return mock_instances[instance_name]

@router.get("/instance/fetchInstances/{instance_name}")
async def get_instance(instance_name: str):
    """Get instance with QR code"""
    if instance_name not in mock_instances:
        # Create a new mock instance if it doesn't exist
        mock_instances[instance_name] = {
            "instance": {
                "instanceName": instance_name,
                "status": "created"
            },
            "qrcode": {
                "code": MOCK_QR_CODE,
                "base64": MOCK_QR_CODE
            }
        }

    return mock_instances[instance_name]

@router.get("/instance/connectionState/{instance_name}")
async def get_connection_state(instance_name: str):
    """Get connection state"""
    # Simulate connection states
    if instance_name not in mock_instances:
        return {
            "instance": instance_name,
            "state": "disconnected"
        }

    # For demo, alternate between states
    import random
    states = ["connecting", "qr_pending", "connected"]

    return {
        "instance": instance_name,
        "state": random.choice(states)
    }

@router.put("/instance/restart/{instance_name}")
async def restart_instance(instance_name: str):
    """Restart instance"""
    if instance_name in mock_instances:
        mock_instances[instance_name]["instance"]["status"] = "restarting"

    return {
        "instance": instance_name,
        "message": "Instance restarted"
    }

@router.post("/message/sendText/{instance_name}")
async def send_text_message(instance_name: str, data: Dict[str, Any]):
    """Mock sending text message"""
    return {
        "key": {
            "remoteJid": data.get("number"),
            "fromMe": True,
            "id": str(uuid.uuid4())
        },
        "message": {
            "conversation": data.get("textMessage", {}).get("text", "")
        },
        "messageTimestamp": str(int(datetime.now().timestamp())),
        "status": "PENDING"
    }

@router.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "ok", "service": "evolution-mock"}
