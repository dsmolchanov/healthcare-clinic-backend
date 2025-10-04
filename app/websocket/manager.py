"""
WebSocket Connection Manager for Real-time Updates
Manages WebSocket connections and broadcasts updates to connected clients
"""

from typing import Dict, Set, List, Optional
from fastapi import WebSocket, WebSocketDisconnect
import json
import logging
import asyncio
from datetime import datetime
from enum import Enum

logger = logging.getLogger(__name__)


class MessageType(Enum):
    """WebSocket message types"""
    DATA_UPDATE = "data_update"
    DATA_DELETE = "data_delete"
    SYNC_STATUS = "sync_status"
    NOTIFICATION = "notification"
    ERROR = "error"
    PING = "ping"
    PONG = "pong"
    SUBSCRIBE = "subscribe"
    UNSUBSCRIBE = "unsubscribe"


class ConnectionManager:
    """
    Manages WebSocket connections for real-time updates
    Groups connections by clinic for efficient broadcasting
    """
    
    def __init__(self):
        # Store active connections grouped by clinic_id
        self.active_connections: Dict[str, Set[WebSocket]] = {}
        # Store connection metadata
        self.connection_metadata: Dict[WebSocket, Dict] = {}
        # Store subscriptions per connection
        self.subscriptions: Dict[WebSocket, Set[str]] = {}
        # Lock for thread-safe operations
        self.lock = asyncio.Lock()
        # Heartbeat task
        self.heartbeat_task: Optional[asyncio.Task] = None
        
    async def connect(
        self, 
        websocket: WebSocket, 
        clinic_id: str,
        user_id: Optional[str] = None,
        metadata: Optional[Dict] = None
    ):
        """
        Accept and register a new WebSocket connection
        
        Args:
            websocket: The WebSocket connection
            clinic_id: Clinic ID for grouping connections
            user_id: Optional user ID for tracking
            metadata: Optional additional metadata
        """
        await websocket.accept()
        
        async with self.lock:
            # Add to clinic group
            if clinic_id not in self.active_connections:
                self.active_connections[clinic_id] = set()
            self.active_connections[clinic_id].add(websocket)
            
            # Store metadata
            self.connection_metadata[websocket] = {
                "clinic_id": clinic_id,
                "user_id": user_id,
                "connected_at": datetime.utcnow().isoformat(),
                "metadata": metadata or {}
            }
            
            # Initialize subscriptions
            self.subscriptions[websocket] = set()
            
        logger.info(f"WebSocket connected: clinic={clinic_id}, user={user_id}")
        
        # Send connection confirmation
        await self.send_personal_message(
            websocket,
            {
                "type": MessageType.NOTIFICATION.value,
                "message": "Connected to real-time updates",
                "clinic_id": clinic_id
            }
        )
        
        # Start heartbeat if not already running
        if not self.heartbeat_task or self.heartbeat_task.done():
            self.heartbeat_task = asyncio.create_task(self._heartbeat_loop())
    
    async def disconnect(self, websocket: WebSocket):
        """
        Remove a WebSocket connection
        
        Args:
            websocket: The WebSocket connection to remove
        """
        async with self.lock:
            # Get clinic_id before removing
            metadata = self.connection_metadata.get(websocket, {})
            clinic_id = metadata.get("clinic_id")
            
            if clinic_id and clinic_id in self.active_connections:
                self.active_connections[clinic_id].discard(websocket)
                
                # Remove empty clinic groups
                if not self.active_connections[clinic_id]:
                    del self.active_connections[clinic_id]
            
            # Clean up metadata and subscriptions
            self.connection_metadata.pop(websocket, None)
            self.subscriptions.pop(websocket, None)
            
        logger.info(f"WebSocket disconnected: clinic={clinic_id}")
    
    async def send_personal_message(self, websocket: WebSocket, message: Dict):
        """
        Send a message to a specific WebSocket connection
        
        Args:
            websocket: Target WebSocket connection
            message: Message dictionary to send
        """
        try:
            await websocket.send_json(message)
        except Exception as e:
            logger.error(f"Error sending personal message: {e}")
            await self.disconnect(websocket)
    
    async def broadcast_to_clinic(
        self, 
        clinic_id: str, 
        message: Dict,
        exclude: Optional[WebSocket] = None
    ):
        """
        Broadcast a message to all connections in a clinic
        
        Args:
            clinic_id: Clinic ID to broadcast to
            message: Message dictionary to broadcast
            exclude: Optional WebSocket to exclude from broadcast
        """
        if clinic_id not in self.active_connections:
            return
        
        # Add timestamp to message
        message["timestamp"] = datetime.utcnow().isoformat()
        
        # Get connections to send to
        connections = self.active_connections[clinic_id].copy()
        if exclude:
            connections.discard(exclude)
        
        # Send to all connections
        disconnected = []
        for connection in connections:
            try:
                # Check if connection is subscribed to this type of update
                if self._should_receive_message(connection, message):
                    await connection.send_json(message)
            except Exception as e:
                logger.error(f"Error broadcasting to connection: {e}")
                disconnected.append(connection)
        
        # Clean up disconnected connections
        for connection in disconnected:
            await self.disconnect(connection)
    
    async def broadcast_to_subscribed(
        self,
        table: str,
        message: Dict,
        clinic_id: Optional[str] = None
    ):
        """
        Broadcast a message to all connections subscribed to a specific table
        
        Args:
            table: Table name for subscription filtering
            message: Message dictionary to broadcast
            clinic_id: Optional clinic ID to limit broadcast
        """
        message["table"] = table
        message["timestamp"] = datetime.utcnow().isoformat()
        
        # Get relevant connections
        if clinic_id:
            connections = self.active_connections.get(clinic_id, set()).copy()
        else:
            connections = set()
            for clinic_connections in self.active_connections.values():
                connections.update(clinic_connections)
        
        # Send to subscribed connections
        disconnected = []
        for connection in connections:
            if table in self.subscriptions.get(connection, set()):
                try:
                    await connection.send_json(message)
                except Exception as e:
                    logger.error(f"Error broadcasting to subscribed connection: {e}")
                    disconnected.append(connection)
        
        # Clean up disconnected connections
        for connection in disconnected:
            await self.disconnect(connection)
    
    async def handle_message(self, websocket: WebSocket, message: Dict):
        """
        Handle incoming WebSocket messages
        
        Args:
            websocket: The WebSocket that sent the message
            message: The message dictionary
        """
        msg_type = message.get("type")
        
        if msg_type == MessageType.PING.value:
            # Respond to ping with pong
            await self.send_personal_message(
                websocket,
                {"type": MessageType.PONG.value}
            )
            
        elif msg_type == MessageType.SUBSCRIBE.value:
            # Subscribe to specific tables
            tables = message.get("tables", [])
            async with self.lock:
                if websocket in self.subscriptions:
                    self.subscriptions[websocket].update(tables)
            logger.debug(f"WebSocket subscribed to tables: {tables}")
            
        elif msg_type == MessageType.UNSUBSCRIBE.value:
            # Unsubscribe from specific tables
            tables = message.get("tables", [])
            async with self.lock:
                if websocket in self.subscriptions:
                    for table in tables:
                        self.subscriptions[websocket].discard(table)
            logger.debug(f"WebSocket unsubscribed from tables: {tables}")
    
    def _should_receive_message(self, connection: WebSocket, message: Dict) -> bool:
        """
        Check if a connection should receive a specific message
        
        Args:
            connection: The WebSocket connection
            message: The message to check
            
        Returns:
            True if the connection should receive the message
        """
        # If message has a table, check subscriptions
        if "table" in message:
            table = message["table"]
            subscriptions = self.subscriptions.get(connection, set())
            # If connection has subscriptions, only send if subscribed to this table
            if subscriptions:
                return table in subscriptions
        
        # Default to sending all messages if no subscriptions
        return True
    
    async def _heartbeat_loop(self):
        """
        Send periodic heartbeat to keep connections alive
        """
        while True:
            try:
                await asyncio.sleep(30)  # Send heartbeat every 30 seconds
                
                # Get all connections
                all_connections = set()
                for connections in self.active_connections.values():
                    all_connections.update(connections)
                
                # Send ping to all connections
                disconnected = []
                for connection in all_connections:
                    try:
                        await connection.send_json({"type": MessageType.PING.value})
                    except:
                        disconnected.append(connection)
                
                # Clean up disconnected connections
                for connection in disconnected:
                    await self.disconnect(connection)
                    
            except Exception as e:
                logger.error(f"Error in heartbeat loop: {e}")
                await asyncio.sleep(5)
    
    async def notify_data_update(
        self,
        clinic_id: str,
        table: str,
        record_id: str,
        operation: str,
        data: Optional[Dict] = None
    ):
        """
        Notify clients about data updates
        
        Args:
            clinic_id: Clinic ID for the update
            table: Table that was updated
            record_id: ID of the updated record
            operation: Operation type (insert, update, delete)
            data: Optional data for the update
        """
        message = {
            "type": MessageType.DATA_UPDATE.value,
            "table": table,
            "record_id": record_id,
            "operation": operation,
            "data": data
        }
        
        await self.broadcast_to_subscribed(table, message, clinic_id)
    
    async def notify_sync_status(
        self,
        clinic_id: str,
        status: str,
        details: Optional[Dict] = None
    ):
        """
        Notify clients about sync status changes
        
        Args:
            clinic_id: Clinic ID for the status update
            status: Status message
            details: Optional additional details
        """
        message = {
            "type": MessageType.SYNC_STATUS.value,
            "status": status,
            "details": details or {}
        }
        
        await self.broadcast_to_clinic(clinic_id, message)
    
    def get_connection_stats(self) -> Dict:
        """
        Get statistics about current connections
        
        Returns:
            Dictionary with connection statistics
        """
        total_connections = sum(len(conns) for conns in self.active_connections.values())
        
        clinic_stats = {}
        for clinic_id, connections in self.active_connections.items():
            clinic_stats[clinic_id] = {
                "connection_count": len(connections),
                "subscriptions": {}
            }
            
            # Count subscriptions per table in this clinic
            table_counts = {}
            for conn in connections:
                for table in self.subscriptions.get(conn, set()):
                    table_counts[table] = table_counts.get(table, 0) + 1
            clinic_stats[clinic_id]["subscriptions"] = table_counts
        
        return {
            "total_connections": total_connections,
            "clinics_connected": len(self.active_connections),
            "clinic_stats": clinic_stats
        }


# Global connection manager instance
manager = ConnectionManager()


async def websocket_endpoint(websocket: WebSocket, clinic_id: str, user_id: Optional[str] = None):
    """
    WebSocket endpoint handler
    
    Args:
        websocket: The WebSocket connection
        clinic_id: Clinic ID from path parameter
        user_id: Optional user ID from query parameter
    """
    await manager.connect(websocket, clinic_id, user_id)
    
    try:
        while True:
            # Receive message from client
            data = await websocket.receive_json()
            
            # Handle the message
            await manager.handle_message(websocket, data)
            
    except WebSocketDisconnect:
        await manager.disconnect(websocket)
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        await manager.disconnect(websocket)