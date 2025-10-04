"""
Tests for WebSocket reconnection and recovery
"""

import pytest
import asyncio
import json
import time
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from fastapi import WebSocket
from app.websocket.manager import ConnectionManager, MessageType


@pytest.fixture
def manager():
    """Create a fresh ConnectionManager instance"""
    return ConnectionManager()


class TestReconnection:
    """Test WebSocket reconnection scenarios"""
    
    @pytest.mark.asyncio
    async def test_reconnection_preserves_subscriptions(self, manager):
        """Test that subscriptions are preserved on reconnection"""
        clinic_id = "clinic-123"
        user_id = "user-456"
        
        # First connection
        ws1 = AsyncMock(spec=WebSocket)
        await manager.connect(ws1, clinic_id, user_id)
        
        # Subscribe to tables
        tables = ["appointments", "doctors", "patients"]
        await manager.handle_message(ws1, {
            "type": MessageType.SUBSCRIBE.value,
            "tables": tables
        })
        
        # Verify subscriptions
        assert all(table in manager.subscriptions[ws1] for table in tables)
        
        # Simulate disconnect
        await manager.disconnect(ws1)
        
        # Reconnect with new WebSocket but same user/clinic
        ws2 = AsyncMock(spec=WebSocket)
        await manager.connect(ws2, clinic_id, user_id)
        
        # Re-subscribe to same tables (client would do this)
        await manager.handle_message(ws2, {
            "type": MessageType.SUBSCRIBE.value,
            "tables": tables
        })
        
        # Verify subscriptions are restored
        assert all(table in manager.subscriptions[ws2] for table in tables)
    
    @pytest.mark.asyncio
    async def test_multiple_reconnection_attempts(self, manager):
        """Test handling multiple reconnection attempts"""
        clinic_id = "clinic-123"
        
        connections = []
        for i in range(5):
            ws = AsyncMock(spec=WebSocket)
            await manager.connect(ws, clinic_id, f"user-{i}")
            connections.append(ws)
            
            # Simulate quick disconnect/reconnect
            await manager.disconnect(ws)
        
        # Verify clinic group is cleaned up when empty
        assert clinic_id not in manager.active_connections
    
    @pytest.mark.asyncio
    async def test_connection_recovery_after_error(self, manager):
        """Test connection recovery after an error"""
        clinic_id = "clinic-123"
        ws = AsyncMock(spec=WebSocket)
        
        # Connect
        await manager.connect(ws, clinic_id)
        
        # Simulate error during broadcast
        ws.send_json.side_effect = [
            Exception("Network error"),  # First call fails
            None  # Second call succeeds
        ]
        
        # Broadcast should handle the error gracefully
        await manager.broadcast_to_clinic(clinic_id, {"test": "message"})
        
        # Connection should be disconnected after error
        assert ws not in manager.active_connections.get(clinic_id, set())
    
    @pytest.mark.asyncio
    async def test_subscription_persistence_across_reconnects(self, manager):
        """Test that subscription state persists correctly"""
        clinic_id = "clinic-123"
        
        # Track subscription changes
        subscription_history = []
        
        for attempt in range(3):
            ws = AsyncMock(spec=WebSocket)
            await manager.connect(ws, clinic_id)
            
            # Subscribe to increasing number of tables
            tables = [f"table_{i}" for i in range(attempt + 1)]
            await manager.handle_message(ws, {
                "type": MessageType.SUBSCRIBE.value,
                "tables": tables
            })
            
            # Record subscriptions
            subscription_history.append({
                "attempt": attempt,
                "subscriptions": list(manager.subscriptions[ws])
            })
            
            # Disconnect
            await manager.disconnect(ws)
        
        # Verify each connection had independent subscriptions
        assert subscription_history[0]["subscriptions"] == ["table_0"]
        assert set(subscription_history[1]["subscriptions"]) == {"table_0", "table_1"}
        assert set(subscription_history[2]["subscriptions"]) == {"table_0", "table_1", "table_2"}
    
    @pytest.mark.asyncio
    async def test_message_delivery_after_reconnect(self, manager):
        """Test that messages are delivered correctly after reconnection"""
        clinic_id = "clinic-123"
        
        # First connection
        ws1 = AsyncMock(spec=WebSocket)
        await manager.connect(ws1, clinic_id, "user-1")
        manager.subscriptions[ws1] = {"appointments"}
        
        # Disconnect
        await manager.disconnect(ws1)
        
        # Second connection
        ws2 = AsyncMock(spec=WebSocket)
        await manager.connect(ws2, clinic_id, "user-2")
        manager.subscriptions[ws2] = {"appointments"}
        
        # Reset mock to clear connection message
        ws2.send_json.reset_mock()
        
        # Send update - should only go to ws2
        await manager.notify_data_update(
            clinic_id,
            "appointments",
            "rec-123",
            "update",
            {"data": "test"}
        )
        
        # Only ws2 should receive the message
        ws2.send_json.assert_called_once()
        
    @pytest.mark.asyncio
    async def test_heartbeat_cleanup_on_disconnect(self, manager):
        """Test that heartbeat is properly cleaned up on disconnect"""
        clinic_id = "clinic-123"
        ws = AsyncMock(spec=WebSocket)
        
        # Connect
        await manager.connect(ws, clinic_id)
        
        # Verify heartbeat task exists
        assert manager.heartbeat_task is not None
        heartbeat_task = manager.heartbeat_task
        
        # Disconnect
        await manager.disconnect(ws)
        
        # Give some time for cleanup
        await asyncio.sleep(0.1)
        
        # Heartbeat should continue (it's global, not per-connection)
        assert manager.heartbeat_task == heartbeat_task
    
    @pytest.mark.asyncio
    async def test_concurrent_connections_same_clinic(self, manager):
        """Test multiple concurrent connections from same clinic"""
        clinic_id = "clinic-123"
        
        # Create multiple connections concurrently
        connections = []
        tasks = []
        
        for i in range(10):
            ws = AsyncMock(spec=WebSocket)
            connections.append(ws)
            tasks.append(manager.connect(ws, clinic_id, f"user-{i}"))
        
        # Connect all concurrently
        await asyncio.gather(*tasks)
        
        # Verify all connections are registered
        assert len(manager.active_connections[clinic_id]) == 10
        
        # Broadcast to all
        await manager.broadcast_to_clinic(clinic_id, {"test": "concurrent"})
        
        # All should receive the message (plus connection message)
        for ws in connections:
            assert ws.send_json.call_count >= 2
    
    @pytest.mark.asyncio
    async def test_connection_metadata_cleanup(self, manager):
        """Test that connection metadata is properly cleaned up"""
        clinic_id = "clinic-123"
        
        # Create and disconnect many connections
        for i in range(100):
            ws = AsyncMock(spec=WebSocket)
            await manager.connect(ws, clinic_id, f"user-{i}")
            await manager.disconnect(ws)
        
        # Verify no memory leaks
        assert len(manager.connection_metadata) == 0
        assert len(manager.subscriptions) == 0
        assert clinic_id not in manager.active_connections
    
    @pytest.mark.asyncio
    async def test_partial_message_delivery_on_errors(self, manager):
        """Test message delivery continues despite some connection failures"""
        clinic_id = "clinic-123"
        
        # Create connections with different behaviors
        ws_good = AsyncMock(spec=WebSocket)
        ws_bad = AsyncMock(spec=WebSocket)
        ws_good2 = AsyncMock(spec=WebSocket)
        
        await manager.connect(ws_good, clinic_id, "good-1")
        await manager.connect(ws_bad, clinic_id, "bad")
        await manager.connect(ws_good2, clinic_id, "good-2")
        
        # Make ws_bad fail on send
        ws_bad.send_json.side_effect = Exception("Connection error")
        
        # Reset good connections to clear connection messages
        ws_good.send_json.reset_mock()
        ws_good2.send_json.reset_mock()
        
        # Broadcast - should continue despite ws_bad failing
        await manager.broadcast_to_clinic(clinic_id, {"test": "partial"})
        
        # Good connections should still receive the message
        ws_good.send_json.assert_called_once()
        ws_good2.send_json.assert_called_once()
        
        # Bad connection should be removed
        assert ws_bad not in manager.active_connections[clinic_id]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])