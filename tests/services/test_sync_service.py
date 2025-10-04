"""
Tests for the data synchronization service
"""

import pytest
import asyncio
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from datetime import datetime, timedelta
import json

from app.services.sync_service import (
    DataSyncService,
    SyncDirection,
    ConflictResolution
)


@pytest.fixture
def mock_supabase_client():
    """Create mock Supabase client"""
    client = Mock()
    client.table = Mock()
    return client


@pytest.fixture
def mock_nocodb_session():
    """Create mock aiohttp session for NocoDB"""
    session = AsyncMock()
    return session


@pytest.fixture
async def sync_service(mock_supabase_client):
    """Create sync service instance for testing"""
    service = DataSyncService(
        supabase_client=mock_supabase_client,
        nocodb_url="http://localhost:8080",
        nocodb_token="test_token",
        conflict_strategy=ConflictResolution.MOST_RECENT
    )
    # Mock the session
    service.session = AsyncMock()
    return service


class TestDataSyncService:
    """Test suite for DataSyncService"""
    
    async def test_initialization(self, mock_supabase_client):
        """Test service initialization"""
        service = DataSyncService(
            supabase_client=mock_supabase_client,
            nocodb_url="http://localhost:8080",
            nocodb_token="test_token"
        )
        
        assert service.supabase == mock_supabase_client
        assert service.nocodb_url == "http://localhost:8080"
        assert service.nocodb_token == "test_token"
        assert service.conflict_strategy == ConflictResolution.MOST_RECENT
        assert isinstance(service.sync_queue, asyncio.Queue)
        assert service.processing == False
        
    async def test_sync_key_generation(self, sync_service):
        """Test sync key generation for tracking"""
        key = sync_service._generate_sync_key("appointments", "123-456")
        assert key == "appointments:123-456"
        
    async def test_recently_synced_tracking(self, sync_service):
        """Test tracking of recently synced records"""
        table = "appointments"
        record_id = "123-456"
        
        # Initially not synced
        assert not sync_service._is_recently_synced(table, record_id)
        
        # Mark as synced
        await sync_service._mark_as_synced(table, record_id)
        
        # Should be marked as synced
        assert sync_service._is_recently_synced(table, record_id)
        
    async def test_queue_sync(self, sync_service):
        """Test adding items to sync queue"""
        sync_item = {
            "table": "appointments",
            "record_id": "123",
            "operation": "INSERT",
            "clinic_id": "clinic-1",
            "data": {"id": "123", "status": "confirmed"}
        }
        
        await sync_service.queue_sync(sync_item)
        
        assert sync_service.sync_queue.qsize() == 1
        queued_item = await sync_service.sync_queue.get()
        assert queued_item == sync_item
        
    async def test_conflict_resolution_most_recent(self, sync_service):
        """Test most recent conflict resolution strategy"""
        sync_service.conflict_strategy = ConflictResolution.MOST_RECENT
        
        supabase_data = {
            "id": "123",
            "name": "Supabase",
            "updated_at": "2024-01-10T10:00:00"
        }
        
        nocodb_data = {
            "id": "123",
            "name": "NocoDB",
            "updated_at": "2024-01-10T11:00:00"  # More recent
        }
        
        result = await sync_service.handle_conflict(
            "appointments", "123", supabase_data, nocodb_data
        )
        
        assert result["name"] == "NocoDB"  # Should use more recent data
        
    async def test_conflict_resolution_supabase_wins(self, sync_service):
        """Test Supabase wins conflict resolution"""
        sync_service.conflict_strategy = ConflictResolution.SUPABASE_WINS
        
        supabase_data = {"id": "123", "name": "Supabase"}
        nocodb_data = {"id": "123", "name": "NocoDB"}
        
        result = await sync_service.handle_conflict(
            "appointments", "123", supabase_data, nocodb_data
        )
        
        assert result["name"] == "Supabase"
        
    async def test_conflict_resolution_merge(self, sync_service):
        """Test merge conflict resolution"""
        sync_service.conflict_strategy = ConflictResolution.MERGE
        
        supabase_data = {
            "id": "123",
            "name": "John",
            "email": None,
            "updated_at": "2024-01-10T10:00:00"
        }
        
        nocodb_data = {
            "id": "123",
            "name": "John",
            "email": "john@example.com",
            "phone": "555-1234",
            "updated_at": "2024-01-10T09:00:00"
        }
        
        result = await sync_service.handle_conflict(
            "patients", "123", supabase_data, nocodb_data
        )
        
        assert result["name"] == "John"
        assert result["email"] == "john@example.com"  # Filled from NocoDB
        assert result.get("phone") == "555-1234"  # Added from NocoDB
        
    @pytest.mark.asyncio
    async def test_sync_to_nocodb(self, sync_service, mock_supabase_client):
        """Test syncing data to NocoDB"""
        # Mock Supabase response
        mock_response = Mock()
        mock_response.data = [
            {"id": "1", "clinic_id": "clinic-1", "status": "confirmed"},
            {"id": "2", "clinic_id": "clinic-1", "status": "pending"}
        ]
        
        mock_table = Mock()
        mock_table.select.return_value.eq.return_value.execute.return_value = mock_response
        mock_supabase_client.table.return_value = mock_table
        
        # Mock NocoDB requests
        mock_get = AsyncMock()
        mock_get.status = 404  # Record doesn't exist
        
        mock_post = AsyncMock()
        mock_post.raise_for_status = AsyncMock()
        
        sync_service.session.get.return_value.__aenter__.return_value = mock_get
        sync_service.session.post.return_value.__aenter__.return_value = mock_post
        
        # Execute sync
        await sync_service._sync_to_nocodb("appointments", "clinic-1")
        
        # Verify Supabase was queried
        mock_supabase_client.table.assert_called_with("healthcare.appointments")
        
        # Verify NocoDB inserts were attempted
        assert sync_service.session.post.call_count == 2
        
    @pytest.mark.asyncio
    async def test_sync_to_supabase(self, sync_service, mock_supabase_client):
        """Test syncing data to Supabase"""
        # Mock NocoDB response
        nocodb_data = {
            "list": [
                {"id": "1", "clinic_id": "clinic-1", "status": "confirmed"},
                {"id": "2", "clinic_id": "clinic-1", "status": "pending"}
            ]
        }
        
        mock_response = AsyncMock()
        mock_response.raise_for_status = AsyncMock()
        mock_response.json = AsyncMock(return_value=nocodb_data)
        
        sync_service.session.get.return_value.__aenter__.return_value = mock_response
        
        # Mock Supabase upsert
        mock_table = Mock()
        mock_table.upsert.return_value.execute.return_value = Mock()
        mock_supabase_client.table.return_value = mock_table
        
        # Execute sync
        await sync_service._sync_to_supabase("appointments", "clinic-1")
        
        # Verify NocoDB was queried
        sync_service.session.get.assert_called_once()
        
        # Verify Supabase upserts were attempted
        assert mock_table.upsert.call_count == 2
        
    async def test_handle_delete(self, sync_service, mock_supabase_client):
        """Test delete synchronization"""
        # Test delete from NocoDB (source is Supabase)
        mock_delete_response = AsyncMock()
        mock_delete_response.status = 200
        sync_service.session.delete.return_value.__aenter__.return_value = mock_delete_response
        
        await sync_service._handle_delete(
            "appointments", "123", "supabase", "clinic-1"
        )
        
        sync_service.session.delete.assert_called_once()
        
        # Test delete from Supabase (source is NocoDB)
        mock_table = Mock()
        mock_delete = Mock()
        mock_table.delete.return_value.eq.return_value.eq.return_value.execute.return_value = Mock()
        mock_supabase_client.table.return_value = mock_table
        
        await sync_service._handle_delete(
            "appointments", "456", "nocodb", "clinic-1"
        )
        
        mock_supabase_client.table.assert_called_with("healthcare.appointments")
        
    async def test_get_sync_status(self, sync_service):
        """Test getting sync status"""
        # Add some items to queue
        await sync_service.queue_sync({"test": "item1"})
        await sync_service.queue_sync({"test": "item2"})
        
        # Mark some as synced
        await sync_service._mark_as_synced("test", "123")
        
        status = await sync_service.get_sync_status()
        
        assert status["queue_size"] == 2
        assert status["processing"] == False
        assert status["recently_synced_count"] >= 1
        assert status["conflict_strategy"] == "most_recent"
        
    @pytest.mark.asyncio
    async def test_start_stop_processing(self, sync_service):
        """Test starting and stopping sync processing"""
        assert sync_service.processing == False
        
        await sync_service.start_processing()
        assert sync_service.processing == True
        
        await sync_service.stop_processing()
        assert sync_service.processing == False
        
    async def test_sync_table_data_bidirectional(self, sync_service):
        """Test bidirectional sync"""
        with patch.object(sync_service, '_sync_to_nocodb') as mock_to_nocodb:
            with patch.object(sync_service, '_sync_to_supabase') as mock_to_supabase:
                await sync_service.sync_table_data(
                    "appointments",
                    "clinic-1",
                    SyncDirection.BIDIRECTIONAL
                )
                
                mock_to_nocodb.assert_called_once_with("appointments", "clinic-1")
                mock_to_supabase.assert_called_once_with("appointments", "clinic-1")
                
    async def test_sync_table_data_to_nocodb_only(self, sync_service):
        """Test one-way sync to NocoDB"""
        with patch.object(sync_service, '_sync_to_nocodb') as mock_to_nocodb:
            with patch.object(sync_service, '_sync_to_supabase') as mock_to_supabase:
                await sync_service.sync_table_data(
                    "appointments",
                    "clinic-1",
                    SyncDirection.TO_NOCODB
                )
                
                mock_to_nocodb.assert_called_once_with("appointments", "clinic-1")
                mock_to_supabase.assert_not_called()
                
    async def test_table_mapping(self, sync_service):
        """Test table name mapping between Supabase and NocoDB"""
        assert sync_service.table_mappings["appointments"] == "t1_appointments"
        assert sync_service.table_mappings["schedules"] == "t2_schedules"
        assert sync_service.table_mappings["equipment"] == "t3_equipment"
        
    @pytest.mark.asyncio
    async def test_process_queue(self, sync_service):
        """Test queue processing"""
        # Add test item to queue
        test_item = {
            "table": "appointments",
            "record_id": "123",
            "operation": "INSERT",
            "clinic_id": "clinic-1",
            "data": {"id": "123", "status": "confirmed"},
            "source": "supabase"
        }
        
        await sync_service.queue_sync(test_item)
        
        # Mock the sync single item method
        with patch.object(sync_service, '_sync_single_item') as mock_sync:
            sync_service.processing = True
            
            # Process one iteration
            await sync_service._process_queue()
            
            # Stop processing to prevent infinite loop
            sync_service.processing = False
            
            # Verify item was processed
            mock_sync.assert_called_once_with(test_item)
            
    async def test_error_handling(self, sync_service):
        """Test error handling in sync operations"""
        # Mock Supabase to raise an error
        mock_supabase_client = sync_service.supabase
        mock_supabase_client.table.side_effect = Exception("Database error")
        
        # This should not raise but log the error
        with patch('app.services.sync_service.logger') as mock_logger:
            try:
                await sync_service._sync_to_nocodb("appointments", "clinic-1")
            except Exception:
                pass  # Expected to raise and be caught
                
            # Verify error was logged
            assert mock_logger.error.called