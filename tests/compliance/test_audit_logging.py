"""
Tests for Audit Logging Service - HIPAA Compliance
"""

import pytest
import asyncio
import json
from datetime import datetime, timedelta
from unittest.mock import Mock, AsyncMock, patch
import uuid

from app.services.audit_service import (
    AuditService,
    AuditEntry,
    AuditEventType,
    AuditSeverity,
    DatabaseStorageBackend,
    FileStorageBackend,
    create_audit_service
)


@pytest.fixture
def mock_supabase():
    """Create a mock Supabase client"""
    client = Mock()
    client.table = Mock()
    return client


@pytest.fixture
def db_storage(mock_supabase):
    """Create a database storage backend"""
    return DatabaseStorageBackend(mock_supabase)


@pytest.fixture
def file_storage(tmp_path):
    """Create a file storage backend"""
    return FileStorageBackend(str(tmp_path))


@pytest.fixture
def audit_service_db(db_storage):
    """Create an audit service with database backend"""
    return AuditService(db_storage)


@pytest.fixture
def audit_service_file(file_storage):
    """Create an audit service with file backend"""
    return AuditService(file_storage)


class TestAuditEntry:
    """Test AuditEntry functionality"""
    
    def test_audit_entry_creation(self):
        """Test creating an audit entry"""
        entry = AuditEntry(
            id="test-id",
            timestamp=datetime.utcnow().isoformat(),
            event_type=AuditEventType.PHI_ACCESS,
            severity=AuditSeverity.INFO,
            user_id="user-123",
            clinic_id="clinic-456",
            resource_type="patient",
            resource_id="patient-789",
            action="view_record",
            outcome="success",
            metadata={"field": "value"}
        )
        
        assert entry.id == "test-id"
        assert entry.event_type == AuditEventType.PHI_ACCESS
        assert entry.severity == AuditSeverity.INFO
        assert entry.action == "view_record"
        assert entry.outcome == "success"
    
    def test_audit_entry_to_dict(self):
        """Test converting audit entry to dictionary"""
        entry = AuditEntry(
            id="test-id",
            timestamp=datetime.utcnow().isoformat(),
            event_type=AuditEventType.PHI_ACCESS,
            severity=AuditSeverity.INFO,
            user_id="user-123",
            clinic_id="clinic-456",
            resource_type="patient",
            resource_id="patient-789",
            action="view_record",
            outcome="success",
            metadata={"field": "value"}
        )
        
        entry_dict = entry.to_dict()
        
        assert entry_dict["id"] == "test-id"
        assert entry_dict["event_type"] == "phi_access"
        assert entry_dict["severity"] == "info"
        assert entry_dict["action"] == "view_record"
        assert entry_dict["metadata"]["field"] == "value"
    
    def test_hash_calculation(self):
        """Test cryptographic hash calculation"""
        entry = AuditEntry(
            id="test-id",
            timestamp="2024-01-15T10:00:00Z",
            event_type=AuditEventType.PHI_ACCESS,
            severity=AuditSeverity.INFO,
            user_id="user-123",
            clinic_id="clinic-456",
            resource_type="patient",
            resource_id="patient-789",
            action="view_record",
            outcome="success",
            metadata={"field": "value"}
        )
        
        # Calculate hash
        hash1 = entry.calculate_hash()
        assert hash1 is not None
        assert len(hash1) == 64  # SHA-256 produces 64 character hex string
        
        # Same data should produce same hash
        hash2 = entry.calculate_hash()
        assert hash1 == hash2
        
        # Different previous hash should produce different hash
        hash3 = entry.calculate_hash("previous-hash")
        assert hash1 != hash3
    
    def test_hash_chain(self):
        """Test hash chain for multiple entries"""
        entries = []
        previous_hash = ""
        
        for i in range(3):
            entry = AuditEntry(
                id=f"entry-{i}",
                timestamp=datetime.utcnow().isoformat(),
                event_type=AuditEventType.PHI_ACCESS,
                severity=AuditSeverity.INFO,
                user_id="user-123",
                clinic_id="clinic-456",
                resource_type="patient",
                resource_id=f"patient-{i}",
                action="view_record",
                outcome="success",
                metadata={}
            )
            
            entry.previous_hash = previous_hash
            entry.hash = entry.calculate_hash(previous_hash)
            entries.append(entry)
            previous_hash = entry.hash
        
        # Verify chain integrity
        for i, entry in enumerate(entries):
            if i == 0:
                assert entry.previous_hash == ""
            else:
                assert entry.previous_hash == entries[i-1].hash
            
            # Verify hash is correct
            expected_hash = entry.calculate_hash(entry.previous_hash)
            assert entry.hash == expected_hash


class TestDatabaseStorageBackend:
    """Test database storage backend"""
    
    @pytest.mark.asyncio
    async def test_write_entry(self, db_storage, mock_supabase):
        """Test writing an audit entry to database"""
        entry = AuditEntry(
            id="test-id",
            timestamp=datetime.utcnow().isoformat(),
            event_type=AuditEventType.PHI_ACCESS,
            severity=AuditSeverity.INFO,
            user_id="user-123",
            clinic_id="clinic-456",
            resource_type="patient",
            resource_id="patient-789",
            action="view_record",
            outcome="success",
            metadata={}
        )
        
        # Mock successful insert
        mock_response = Mock()
        mock_response.data = [entry.to_dict()]
        mock_supabase.table.return_value.insert.return_value.execute.return_value = mock_response
        
        result = await db_storage.write(entry)
        
        assert result == True
        mock_supabase.table.assert_called_with("audit_logs")
        mock_supabase.table.return_value.insert.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_read_entries(self, db_storage, mock_supabase):
        """Test reading audit entries from database"""
        # Mock data
        mock_data = [
            {
                "id": "entry-1",
                "timestamp": datetime.utcnow().isoformat(),
                "event_type": "phi_access",
                "severity": "info",
                "user_id": "user-123",
                "clinic_id": "clinic-456",
                "resource_type": "patient",
                "resource_id": "patient-789",
                "action": "view_record",
                "outcome": "success",
                "metadata": {},
                "hash": "test-hash",
                "previous_hash": ""
            }
        ]
        
        # Mock query chain
        mock_query = Mock()
        mock_query.eq = Mock(return_value=mock_query)
        mock_query.gte = Mock(return_value=mock_query)
        mock_query.lte = Mock(return_value=mock_query)
        mock_query.order = Mock(return_value=mock_query)
        mock_query.limit = Mock(return_value=mock_query)
        mock_query.execute = Mock(return_value=Mock(data=mock_data))
        
        mock_supabase.table.return_value.select.return_value = mock_query
        
        # Read entries
        filters = {
            "user_id": "user-123",
            "start_date": datetime.utcnow() - timedelta(days=1),
            "end_date": datetime.utcnow()
        }
        
        entries = await db_storage.read(filters, limit=10)
        
        assert len(entries) == 1
        assert entries[0].id == "entry-1"
        assert entries[0].event_type == AuditEventType.PHI_ACCESS
    
    @pytest.mark.asyncio
    async def test_verify_integrity(self, db_storage, mock_supabase):
        """Test verifying audit log integrity"""
        # Create test entries with correct hash chain
        entries = []
        previous_hash = ""
        
        for i in range(3):
            entry = AuditEntry(
                id=f"entry-{i}",
                timestamp=datetime.utcnow().isoformat(),
                event_type=AuditEventType.PHI_ACCESS,
                severity=AuditSeverity.INFO,
                user_id="user-123",
                clinic_id="clinic-456",
                resource_type="patient",
                resource_id=f"patient-{i}",
                action="view_record",
                outcome="success",
                metadata={},
                previous_hash=previous_hash
            )
            entry.hash = entry.calculate_hash(previous_hash)
            entries.append(entry.to_dict())
            previous_hash = entry.hash
        
        # Mock query response
        mock_response = Mock()
        mock_response.data = entries
        mock_query = Mock()
        mock_query.gte = Mock(return_value=mock_query)
        mock_query.lte = Mock(return_value=mock_query)
        mock_query.order = Mock(return_value=mock_query)
        mock_query.execute = Mock(return_value=mock_response)
        
        mock_supabase.table.return_value.select.return_value = mock_query
        
        # Verify integrity
        start_date = datetime.utcnow() - timedelta(days=1)
        end_date = datetime.utcnow()
        
        is_valid = await db_storage.verify_integrity(start_date, end_date)
        
        assert is_valid == True


class TestFileStorageBackend:
    """Test file storage backend"""
    
    @pytest.mark.asyncio
    async def test_write_entry(self, file_storage):
        """Test writing an audit entry to file"""
        entry = AuditEntry(
            id="test-id",
            timestamp=datetime.utcnow().isoformat(),
            event_type=AuditEventType.PHI_ACCESS,
            severity=AuditSeverity.INFO,
            user_id="user-123",
            clinic_id="clinic-456",
            resource_type="patient",
            resource_id="patient-789",
            action="view_record",
            outcome="success",
            metadata={}
        )
        
        result = await file_storage.write(entry)
        
        assert result == True
        
        # Verify file was created
        date_str = datetime.utcnow().strftime('%Y%m%d')
        expected_file = file_storage.storage_path / f"audit_{date_str}.jsonl"
        assert expected_file.exists()
        
        # Verify content
        with open(expected_file, 'r') as f:
            line = f.readline()
            data = json.loads(line)
            assert data["id"] == "test-id"
            assert data["action"] == "view_record"
    
    @pytest.mark.asyncio
    async def test_read_entries(self, file_storage):
        """Test reading audit entries from file"""
        # Write some test entries
        for i in range(3):
            entry = AuditEntry(
                id=f"entry-{i}",
                timestamp=datetime.utcnow().isoformat(),
                event_type=AuditEventType.PHI_ACCESS,
                severity=AuditSeverity.INFO,
                user_id="user-123",
                clinic_id="clinic-456",
                resource_type="patient",
                resource_id=f"patient-{i}",
                action="view_record",
                outcome="success",
                metadata={}
            )
            await file_storage.write(entry)
        
        # Read entries
        filters = {
            "user_id": "user-123",
            "start_date": datetime.utcnow() - timedelta(days=1),
            "end_date": datetime.utcnow()
        }
        
        entries = await file_storage.read(filters, limit=10)
        
        assert len(entries) == 3
        assert all(e.user_id == "user-123" for e in entries)
    
    @pytest.mark.asyncio
    async def test_append_only(self, file_storage):
        """Test that file storage is append-only"""
        # Write first entry
        entry1 = AuditEntry(
            id="entry-1",
            timestamp=datetime.utcnow().isoformat(),
            event_type=AuditEventType.PHI_ACCESS,
            severity=AuditSeverity.INFO,
            user_id="user-123",
            clinic_id="clinic-456",
            resource_type="patient",
            resource_id="patient-1",
            action="view_record",
            outcome="success",
            metadata={}
        )
        await file_storage.write(entry1)
        
        # Write second entry
        entry2 = AuditEntry(
            id="entry-2",
            timestamp=datetime.utcnow().isoformat(),
            event_type=AuditEventType.PHI_UPDATE,
            severity=AuditSeverity.INFO,
            user_id="user-456",
            clinic_id="clinic-456",
            resource_type="patient",
            resource_id="patient-2",
            action="update_record",
            outcome="success",
            metadata={}
        )
        await file_storage.write(entry2)
        
        # Read all entries
        entries = await file_storage.read({}, limit=10)
        
        assert len(entries) == 2
        # Entries should be in order (most recent first due to sorting)
        assert entries[0].id == "entry-2"
        assert entries[1].id == "entry-1"


class TestAuditService:
    """Test main audit service"""
    
    @pytest.mark.asyncio
    async def test_log_access(self, audit_service_db, db_storage):
        """Test logging PHI access"""
        # Mock storage write
        db_storage.write = AsyncMock(return_value=True)
        
        result = await audit_service_db.log_access(
            user_id="user-123",
            resource_type="patient",
            resource_id="patient-789",
            action="view_record",
            clinic_id="clinic-456",
            ip_address="192.168.1.1",
            metadata={"field": "value"}
        )
        
        assert result == True
        db_storage.write.assert_called_once()
        
        # Verify the entry passed to storage
        call_args = db_storage.write.call_args[0][0]
        assert call_args.event_type == AuditEventType.PHI_ACCESS
        assert call_args.user_id == "user-123"
        assert call_args.resource_type == "patient"
        assert call_args.resource_id == "patient-789"
    
    @pytest.mark.asyncio
    async def test_log_login(self, audit_service_db, db_storage):
        """Test logging user login"""
        db_storage.write = AsyncMock(return_value=True)
        
        result = await audit_service_db.log_login(
            user_id="user-123",
            ip_address="192.168.1.1",
            success=True
        )
        
        assert result == True
        
        call_args = db_storage.write.call_args[0][0]
        assert call_args.event_type == AuditEventType.USER_LOGIN
        assert call_args.severity == AuditSeverity.INFO
        assert call_args.outcome == "success"
    
    @pytest.mark.asyncio
    async def test_log_failed_login(self, audit_service_db, db_storage):
        """Test logging failed login attempt"""
        db_storage.write = AsyncMock(return_value=True)
        
        result = await audit_service_db.log_login(
            user_id="user-123",
            ip_address="192.168.1.1",
            success=False
        )
        
        assert result == True
        
        call_args = db_storage.write.call_args[0][0]
        assert call_args.event_type == AuditEventType.USER_LOGIN
        assert call_args.severity == AuditSeverity.WARNING
        assert call_args.outcome == "failure"
    
    @pytest.mark.asyncio
    async def test_log_data_export(self, audit_service_db, db_storage):
        """Test logging data export for compliance"""
        db_storage.write = AsyncMock(return_value=True)
        
        result = await audit_service_db.log_data_export(
            user_id="user-123",
            clinic_id="clinic-456",
            export_type="patient_records",
            record_count=100,
            ip_address="192.168.1.1"
        )
        
        assert result == True
        
        call_args = db_storage.write.call_args[0][0]
        assert call_args.event_type == AuditEventType.DATA_EXPORT
        assert call_args.severity == AuditSeverity.WARNING
        assert call_args.metadata["export_type"] == "patient_records"
        assert call_args.metadata["record_count"] == 100
    
    @pytest.mark.asyncio
    async def test_log_rule_violation(self, audit_service_db, db_storage):
        """Test logging business rule violation"""
        db_storage.write = AsyncMock(return_value=True)
        
        result = await audit_service_db.log_rule_violation(
            rule_name="appointment_overlap",
            violation_details={"message": "Double booking detected"},
            user_id="user-123",
            clinic_id="clinic-456"
        )
        
        assert result == True
        
        call_args = db_storage.write.call_args[0][0]
        assert call_args.event_type == AuditEventType.RULE_VIOLATION
        assert call_args.severity == AuditSeverity.WARNING
        assert call_args.metadata["rule_name"] == "appointment_overlap"
    
    @pytest.mark.asyncio
    async def test_hash_chain_integrity(self, audit_service_db, db_storage):
        """Test that hash chain is maintained across multiple logs"""
        db_storage.write = AsyncMock(return_value=True)
        
        # Log multiple events
        await audit_service_db.log_access(
            user_id="user-1",
            resource_type="patient",
            resource_id="patient-1",
            action="view"
        )
        
        await audit_service_db.log_access(
            user_id="user-2",
            resource_type="patient",
            resource_id="patient-2",
            action="update"
        )
        
        await audit_service_db.log_access(
            user_id="user-3",
            resource_type="patient",
            resource_id="patient-3",
            action="delete"
        )
        
        # Verify hash chain
        assert db_storage.write.call_count == 3
        
        # Get all entries
        entries = []
        for call in db_storage.write.call_args_list:
            entries.append(call[0][0])
        
        # First entry should have empty previous hash
        assert entries[0].previous_hash == ""
        
        # Second entry should reference first
        assert entries[1].previous_hash == entries[0].hash
        
        # Third entry should reference second
        assert entries[2].previous_hash == entries[1].hash
    
    @pytest.mark.asyncio
    async def test_query_logs(self, audit_service_db, db_storage):
        """Test querying audit logs"""
        # Mock storage read
        mock_entries = [
            AuditEntry(
                id="entry-1",
                timestamp=datetime.utcnow().isoformat(),
                event_type=AuditEventType.PHI_ACCESS,
                severity=AuditSeverity.INFO,
                user_id="user-123",
                clinic_id="clinic-456",
                resource_type="patient",
                resource_id="patient-1",
                action="view",
                outcome="success",
                metadata={}
            )
        ]
        db_storage.read = AsyncMock(return_value=mock_entries)
        
        # Query logs
        results = await audit_service_db.query_logs(
            user_id="user-123",
            event_type=AuditEventType.PHI_ACCESS,
            limit=10
        )
        
        assert len(results) == 1
        assert results[0].id == "entry-1"
        
        # Verify filters were passed
        call_args = db_storage.read.call_args[0][0]
        assert call_args["user_id"] == "user-123"
        assert call_args["event_type"] == "phi_access"
    
    @pytest.mark.asyncio
    async def test_export_logs_json(self, audit_service_db, db_storage):
        """Test exporting logs as JSON"""
        # Mock entries
        mock_entries = [
            AuditEntry(
                id="entry-1",
                timestamp=datetime.utcnow().isoformat(),
                event_type=AuditEventType.PHI_ACCESS,
                severity=AuditSeverity.INFO,
                user_id="user-123",
                clinic_id="clinic-456",
                resource_type="patient",
                resource_id="patient-1",
                action="view",
                outcome="success",
                metadata={}
            )
        ]
        
        audit_service_db.query_logs = AsyncMock(return_value=mock_entries)
        
        # Export as JSON
        result = await audit_service_db.export_logs(
            start_date=datetime.utcnow() - timedelta(days=1),
            end_date=datetime.utcnow(),
            format="json"
        )
        
        # Verify JSON format
        data = json.loads(result)
        assert len(data) == 1
        assert data[0]["id"] == "entry-1"
        assert data[0]["event_type"] == "phi_access"
    
    @pytest.mark.asyncio
    async def test_export_logs_csv(self, audit_service_db, db_storage):
        """Test exporting logs as CSV"""
        # Mock entries
        mock_entries = [
            AuditEntry(
                id="entry-1",
                timestamp=datetime.utcnow().isoformat(),
                event_type=AuditEventType.PHI_ACCESS,
                severity=AuditSeverity.INFO,
                user_id="user-123",
                clinic_id="clinic-456",
                resource_type="patient",
                resource_id="patient-1",
                action="view",
                outcome="success",
                metadata={}
            )
        ]
        
        audit_service_db.query_logs = AsyncMock(return_value=mock_entries)
        
        # Export as CSV
        result = await audit_service_db.export_logs(
            start_date=datetime.utcnow() - timedelta(days=1),
            end_date=datetime.utcnow(),
            format="csv"
        )
        
        # Verify CSV format
        lines = result.strip().split('\n')
        assert len(lines) == 2  # Header + 1 row
        assert "id" in lines[0]
        assert "entry-1" in lines[1]


class TestFactoryFunction:
    """Test audit service factory"""
    
    def test_create_with_database_backend(self, mock_supabase):
        """Test creating audit service with database backend"""
        service = create_audit_service(supabase_client=mock_supabase, use_file_backend=False)
        
        assert isinstance(service, AuditService)
        assert isinstance(service.storage, DatabaseStorageBackend)
    
    def test_create_with_file_backend(self, tmp_path):
        """Test creating audit service with file backend"""
        with patch('app.services.audit_service.settings.AUDIT_LOG_PATH', str(tmp_path)):
            service = create_audit_service(use_file_backend=True)
        
        assert isinstance(service, AuditService)
        assert isinstance(service.storage, FileStorageBackend)
    
    def test_create_without_client_raises_error(self):
        """Test that creating database backend without client raises error"""
        with pytest.raises(ValueError, match="Supabase client required"):
            create_audit_service(supabase_client=None, use_file_backend=False)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])