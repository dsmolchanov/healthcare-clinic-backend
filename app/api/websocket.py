"""
WebSocket API endpoints for real-time communication
"""

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query, Depends, HTTPException
from typing import Optional
import logging
from app.websocket.manager import manager, websocket_endpoint
from app.core.auth import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ws", tags=["websocket"])


@router.websocket("/{clinic_id}")
async def websocket_connection(
    websocket: WebSocket,
    clinic_id: str,
    token: Optional[str] = Query(None)
):
    """
    WebSocket endpoint for real-time updates
    
    Connect using: ws://localhost:8000/ws/{clinic_id}?token={jwt_token}
    
    Message types:
    - ping/pong: Heartbeat
    - subscribe: Subscribe to table updates
    - unsubscribe: Unsubscribe from table updates
    - data_update: Receive data change notifications
    - sync_status: Receive sync status updates
    """
    
    # Validate token if provided
    user_id = None
    if token:
        try:
            # Here you would validate the JWT token
            # For now, we'll just extract a user_id from it
            # user = await get_current_user(token)
            # user_id = user.id
            user_id = "authenticated_user"  # Placeholder
        except Exception as e:
            logger.warning(f"Invalid token for WebSocket connection: {e}")
            await websocket.close(code=1008, reason="Invalid token")
            return
    
    await websocket_endpoint(websocket, clinic_id, user_id)


@router.get("/status")
async def get_websocket_status():
    """
    Get WebSocket connection statistics
    
    Returns current connection stats and subscription information
    """
    return manager.get_connection_stats()


@router.post("/broadcast/{clinic_id}")
async def broadcast_message(
    clinic_id: str,
    message: dict
):
    """
    Broadcast a message to all connections in a clinic
    
    This is mainly for testing and admin purposes
    """
    await manager.broadcast_to_clinic(clinic_id, message)
    return {"status": "broadcast_sent", "clinic_id": clinic_id}