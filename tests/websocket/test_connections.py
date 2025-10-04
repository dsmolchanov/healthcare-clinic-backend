"""
Tests for WebSocket connection management
"""

import pytest
import asyncio
import json
from unittest.mock import Mock, AsyncMock, patch
from fastapi import WebSocket
from fastapi.testclient import TestClient

from app.websocket.manager import ConnectionManager, MessageType


@pytest.fixture
def manager():
    """Create a fresh ConnectionManager instance"""
    return ConnectionManager()


@pytest.fixture
def mock_websocket():
    """Create a mock WebSocket"""
    ws = AsyncMock(spec=WebSocket)
    ws.send_json = AsyncMock()
    ws.accept = AsyncMock()
    ws.close = AsyncMock()
    return ws


class TestConnectionManager:
    """Test ConnectionManager functionality"""
    
    @pytest.mark.asyncio
    async def test_connect_new_connection(self, manager, mock_websocket):
        """Test connecting a new WebSocket"""
        clinic_id = "clinic-123"
        user_id = "user-456"
        
        await manager.connect(mock_websocket, clinic_id, user_id)
        
        # Verify WebSocket was accepted
        mock_websocket.accept.assert_called_once()
        
        # Verify connection was added
        assert clinic_id in manager.active_connections
        assert mock_websocket in manager.active_connections[clinic_id]
        
        # Verify metadata was stored
        metadata = manager.connection_metadata[mock_websocket]
        assert metadata["clinic_id"] == clinic_id
        assert metadata["user_id"] == user_id
        assert "connected_at" in metadata
        
        # Verify subscriptions were initialized
        assert mock_websocket in manager.subscriptions
        assert len(manager.subscriptions[mock_websocket]) == 0
        
        # Verify confirmation message was sent
        mock_websocket.send_json.assert_called()
        call_args = mock_websocket.send_json.call_args[0][0]
        assert call_args["type"] == MessageType.NOTIFICATION.value
        assert "Connected" in call_args["message"]
    
    @pytest.mark.asyncio
    async def test_disconnect_connection(self, manager, mock_websocket):
        """Test disconnecting a WebSocket"""
        clinic_id = "clinic-123"
        
        # Connect first
        await manager.connect(mock_websocket, clinic_id)
        
        # Then disconnect
        await manager.disconnect(mock_websocket)
        
        # Verify connection was removed
        assert mock_websocket not in manager.active_connections.get(clinic_id, set())
        assert mock_websocket not in manager.connection_metadata
        assert mock_websocket not in manager.subscriptions
    
    @pytest.mark.asyncio
    async def test_broadcast_to_clinic(self, manager):
        """Test broadcasting to all connections in a clinic"""
        clinic_id = "clinic-123"
        
        # Create multiple mock connections
        ws1 = AsyncMock(spec=WebSocket)
        ws2 = AsyncMock(spec=WebSocket)
        ws3 = AsyncMock(spec=WebSocket)  # Different clinic
        
        await manager.connect(ws1, clinic_id)
        await manager.connect(ws2, clinic_id)
        await manager.connect(ws3, "clinic-456")
        
        # Broadcast message to clinic
        message = {"type": "test", "data": "hello"}
        await manager.broadcast_to_clinic(clinic_id, message)
        
        # Verify only connections in the same clinic received the message
        ws1.send_json.assert_called()
        ws2.send_json.assert_called()
        
        # ws3 should not have received the message (different clinic)
        # Skip the initial connection notification
        assert ws3.send_json.call_count == 1  # Only connection notification
    
    @pytest.mark.asyncio
    async def test_broadcast_with_exclusion(self, manager):
        """Test broadcasting with connection exclusion"""
        clinic_id = "clinic-123"
        
        ws1 = AsyncMock(spec=WebSocket)
        ws2 = AsyncMock(spec=WebSocket)
        
        await manager.connect(ws1, clinic_id)
        await manager.connect(ws2, clinic_id)
        
        # Reset mock to clear connection messages
        ws1.send_json.reset_mock()
        ws2.send_json.reset_mock()
        
        # Broadcast excluding ws1
        message = {"type": "test", "data": "hello"}
        await manager.broadcast_to_clinic(clinic_id, message, exclude=ws1)
        
        # Only ws2 should receive the message
        ws1.send_json.assert_not_called()
        ws2.send_json.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_handle_ping_pong(self, manager, mock_websocket):
        """Test ping/pong message handling"""
        await manager.connect(mock_websocket, "clinic-123")
        
        # Reset to clear connection message
        mock_websocket.send_json.reset_mock()
        
        # Send ping message
        ping_message = {"type": MessageType.PING.value}
        await manager.handle_message(mock_websocket, ping_message)
        
        # Verify pong was sent
        mock_websocket.send_json.assert_called_once()
        response = mock_websocket.send_json.call_args[0][0]
        assert response["type"] == MessageType.PONG.value
    
    @pytest.mark.asyncio
    async def test_subscribe_to_tables(self, manager, mock_websocket):
        """Test subscribing to table updates"""
        await manager.connect(mock_websocket, "clinic-123")
        
        # Subscribe to tables
        tables = ["appointments", "doctors"]
        subscribe_message = {
            "type": MessageType.SUBSCRIBE.value,
            "tables": tables
        }
        await manager.handle_message(mock_websocket, subscribe_message)
        
        # Verify subscriptions were added
        assert "appointments" in manager.subscriptions[mock_websocket]
        assert "doctors" in manager.subscriptions[mock_websocket]
    
    @pytest.mark.asyncio
    async def test_unsubscribe_from_tables(self, manager, mock_websocket):
        """Test unsubscribing from table updates"""
        await manager.connect(mock_websocket, "clinic-123")
        
        # Subscribe first
        manager.subscriptions[mock_websocket] = {"appointments", "doctors", "patients"}
        
        # Unsubscribe from some tables
        unsubscribe_message = {
            "type": MessageType.UNSUBSCRIBE.value,
            "tables": ["appointments", "doctors"]
        }
        await manager.handle_message(mock_websocket, unsubscribe_message)
        
        # Verify only unsubscribed tables were removed
        assert "appointments" not in manager.subscriptions[mock_websocket]
        assert "doctors" not in manager.subscriptions[mock_websocket]
        assert "patients" in manager.subscriptions[mock_websocket]
    
    @pytest.mark.asyncio
    async def test_broadcast_to_subscribed(self, manager):
        """Test broadcasting to subscribed connections only"""
        clinic_id = "clinic-123"
        
        # Create connections with different subscriptions
        ws1 = AsyncMock(spec=WebSocket)
        ws2 = AsyncMock(spec=WebSocket)
        ws3 = AsyncMock(spec=WebSocket)
        
        await manager.connect(ws1, clinic_id)
        await manager.connect(ws2, clinic_id)
        await manager.connect(ws3, clinic_id)
        
        # Set up subscriptions
        manager.subscriptions[ws1] = {"appointments", "doctors"}
        manager.subscriptions[ws2] = {"appointments"}
        manager.subscriptions[ws3] = {"patients"}
        
        # Reset mocks to clear connection messages
        ws1.send_json.reset_mock()
        ws2.send_json.reset_mock()
        ws3.send_json.reset_mock()
        
        # Broadcast to appointments subscribers
        message = {"type": "update", "data": "test"}
        await manager.broadcast_to_subscribed("appointments", message, clinic_id)
        
        # Only ws1 and ws2 should receive the message
        ws1.send_json.assert_called_once()
        ws2.send_json.assert_called_once()
        ws3.send_json.assert_not_called()
    
    @pytest.mark.asyncio
    async def test_notify_data_update(self, manager):
        """Test data update notification"""
        clinic_id = "clinic-123"
        ws = AsyncMock(spec=WebSocket)
        
        await manager.connect(ws, clinic_id)
        manager.subscriptions[ws] = {"appointments"}
        
        # Reset to clear connection message
        ws.send_json.reset_mock()
        
        # Send data update notification
        await manager.notify_data_update(
            clinic_id,
            "appointments",
            "rec-123",
            "update",
            {"name": "Test"}
        )
        
        # Verify notification was sent
        ws.send_json.assert_called_once()
        notification = ws.send_json.call_args[0][0]
        assert notification["type"] == MessageType.DATA_UPDATE.value
        assert notification["table"] == "appointments"
        assert notification["record_id"] == "rec-123"
        assert notification["operation"] == "update"
    
    @pytest.mark.asyncio
    async def test_notify_sync_status(self, manager):
        """Test sync status notification"""
        clinic_id = "clinic-123"
        ws = AsyncMock(spec=WebSocket)
        
        await manager.connect(ws, clinic_id)
        
        # Reset to clear connection message
        ws.send_json.reset_mock()
        
        # Send sync status notification
        await manager.notify_sync_status(
            clinic_id,
            "syncing",
            {"progress": 50}
        )
        
        # Verify notification was sent
        ws.send_json.assert_called_once()
        notification = ws.send_json.call_args[0][0]
        assert notification["type"] == MessageType.SYNC_STATUS.value
        assert notification["status"] == "syncing"
        assert notification["details"]["progress"] == 50
    
    def test_get_connection_stats(self, manager):
        """Test getting connection statistics"""
        # Create some test connections
        manager.active_connections = {
            "clinic-1": {Mock(), Mock()},
            "clinic-2": {Mock()}
        }
        
        ws1 = Mock()
        ws2 = Mock()
        ws3 = Mock()
        
        manager.subscriptions = {
            ws1: {"appointments", "doctors"},
            ws2: {"appointments"},
            ws3: {"patients"}
        }
        
        # Assign connections to clinics for stats
        manager.active_connections["clinic-1"] = {ws1, ws2}
        manager.active_connections["clinic-2"] = {ws3}
        
        stats = manager.get_connection_stats()
        
        assert stats["total_connections"] == 3
        assert stats["clinics_connected"] == 2
        assert "clinic-1" in stats["clinic_stats"]
        assert stats["clinic_stats"]["clinic-1"]["connection_count"] == 2
        assert stats["clinic_stats"]["clinic-1"]["subscriptions"]["appointments"] == 2
        assert stats["clinic_stats"]["clinic-1"]["subscriptions"]["doctors"] == 1
    
    @pytest.mark.asyncio
    async def test_connection_cleanup_on_error(self, manager):
        """Test that failed connections are properly cleaned up"""
        clinic_id = "clinic-123"
        ws = AsyncMock(spec=WebSocket)
        
        # Connect
        await manager.connect(ws, clinic_id)
        
        # Simulate send error
        ws.send_json.side_effect = Exception("Connection lost")
        
        # Try to send personal message
        await manager.send_personal_message(ws, {"test": "message"})
        
        # Verify connection was cleaned up
        assert ws not in manager.active_connections.get(clinic_id, set())
        assert ws not in manager.connection_metadata
        assert ws not in manager.subscriptions
    
    @pytest.mark.asyncio
    async def test_heartbeat_loop(self, manager):
        """Test heartbeat mechanism"""
        clinic_id = "clinic-123"
        ws = AsyncMock(spec=WebSocket)
        
        await manager.connect(ws, clinic_id)
        
        # Verify heartbeat task was started
        assert manager.heartbeat_task is not None
        assert not manager.heartbeat_task.done()
        
        # Clean up
        await manager.disconnect(ws)


class TestWebSocketEndpoint:
    """Test WebSocket endpoint integration"""
    
    @pytest.mark.asyncio
    async def test_websocket_connection_lifecycle(self):
        """Test complete WebSocket connection lifecycle"""
        from app.websocket.manager import websocket_endpoint
        
        ws = AsyncMock(spec=WebSocket)
        ws.receive_json = AsyncMock()
        
        # Simulate connection and immediate disconnect
        ws.receive_json.side_effect = Exception("Disconnected")
        
        # Run endpoint (should handle disconnect gracefully)
        try:
            await websocket_endpoint(ws, "clinic-123", "user-456")
        except:
            pass  # Expected to raise when disconnected
        
        # Verify connection was accepted
        ws.accept.assert_called_once()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])