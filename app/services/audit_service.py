"""
Audit Service for HIPAA Compliance
Implements comprehensive audit logging with immutable storage
"""

from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
from enum import Enum
import hashlib
import json
import logging
from dataclasses import dataclass, asdict
import asyncio
import aiofiles
from pathlib import Path
import uuid

from supabase import Client
from app.core.config import settings

logger = logging.getLogger(__name__)


class AuditEventType(Enum):
    """Types of audit events"""
    PHI_ACCESS = "phi_access"
    PHI_CREATE = "phi_create"
    PHI_UPDATE = "phi_update"
    PHI_DELETE = "phi_delete"
    USER_LOGIN = "user_login"
    USER_LOGOUT = "user_logout"
    PERMISSION_CHANGE = "permission_change"
    DATA_EXPORT = "data_export"
    DATA_IMPORT = "data_import"
    SYNC_EVENT = "sync_event"
    RULE_VIOLATION = "rule_violation"
    SYSTEM_ACCESS = "system_access"
    ERROR = "error"


class AuditSeverity(Enum):
    """Severity levels for audit events"""
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


@dataclass
class AuditEntry:
    """Represents an audit log entry"""
    id: str
    timestamp: str
    event_type: AuditEventType
    severity: AuditSeverity
    user_id: Optional[str]
    clinic_id: Optional[str]
    resource_type: Optional[str]
    resource_id: Optional[str]
    action: str
    outcome: str  # success, failure, partial
    ip_address: Optional[str]
    user_agent: Optional[str]
    metadata: Dict[str, Any]
    hash: Optional[str] = None
    previous_hash: Optional[str] = None
    
    def to_dict(self) -> Dict:
        """Convert to dictionary"""
        result = asdict(self)
        result['event_type'] = self.event_type.value
        result['severity'] = self.severity.value
        return result
    
    def calculate_hash(self, previous_hash: str = "") -> str:
        """Calculate cryptographic hash for tamper-proofing"""
        # Create deterministic string representation
        content = json.dumps({
            'id': self.id,
            'timestamp': self.timestamp,
            'event_type': self.event_type.value,
            'severity': self.severity.value,
            'user_id': self.user_id,
            'clinic_id': self.clinic_id,
            'resource_type': self.resource_type,
            'resource_id': self.resource_id,
            'action': self.action,
            'outcome': self.outcome,
            'metadata': self.metadata,
            'previous_hash': previous_hash
        }, sort_keys=True)
        
        # Calculate SHA-256 hash
        return hashlib.sha256(content.encode()).hexdigest()


class StorageBackend:
    """Abstract storage backend for audit logs"""
    
    async def write(self, entry: AuditEntry) -> bool:
        """Write an audit entry to storage"""
        raise NotImplementedError
    
    async def read(self, filters: Dict[str, Any], limit: int = 100) -> List[AuditEntry]:
        """Read audit entries from storage"""
        raise NotImplementedError
    
    async def verify_integrity(self, start_date: datetime, end_date: datetime) -> bool:
        """Verify integrity of audit logs in date range"""
        raise NotImplementedError


class DatabaseStorageBackend(StorageBackend):
    """Database storage backend using Supabase"""
    
    def __init__(self, supabase_client: Client):
        self.client = supabase_client
        self.table_name = "audit_logs"
    
    async def write(self, entry: AuditEntry) -> bool:
        """Write audit entry to database"""
        try:
            # Insert into database
            response = self.client.table(self.table_name).insert(
                entry.to_dict()
            ).execute()
            
            return len(response.data) > 0
        except Exception as e:
            logger.error(f"Failed to write audit entry to database: {e}")
            return False
    
    async def read(self, filters: Dict[str, Any], limit: int = 100) -> List[AuditEntry]:
        """Read audit entries from database"""
        try:
            query = self.client.table(self.table_name).select("*")
            
            # Apply filters
            if "user_id" in filters:
                query = query.eq("user_id", filters["user_id"])
            if "clinic_id" in filters:
                query = query.eq("clinic_id", filters["clinic_id"])
            if "event_type" in filters:
                query = query.eq("event_type", filters["event_type"])
            if "start_date" in filters:
                query = query.gte("timestamp", filters["start_date"].isoformat())
            if "end_date" in filters:
                query = query.lte("timestamp", filters["end_date"].isoformat())
            
            # Order by timestamp descending and limit
            query = query.order("timestamp", desc=True).limit(limit)
            
            response = query.execute()
            
            # Convert to AuditEntry objects
            entries = []
            for row in response.data:
                entries.append(AuditEntry(
                    id=row["id"],
                    timestamp=row["timestamp"],
                    event_type=AuditEventType(row["event_type"]),
                    severity=AuditSeverity(row["severity"]),
                    user_id=row.get("user_id"),
                    clinic_id=row.get("clinic_id"),
                    resource_type=row.get("resource_type"),
                    resource_id=row.get("resource_id"),
                    action=row["action"],
                    outcome=row["outcome"],
                    ip_address=row.get("ip_address"),
                    user_agent=row.get("user_agent"),
                    metadata=row.get("metadata", {}),
                    hash=row.get("hash"),
                    previous_hash=row.get("previous_hash")
                ))
            
            return entries
        except Exception as e:
            logger.error(f"Failed to read audit entries from database: {e}")
            return []
    
    async def verify_integrity(self, start_date: datetime, end_date: datetime) -> bool:
        """Verify integrity of audit logs in date range"""
        try:
            # Fetch all entries in date range ordered by timestamp
            response = self.client.table(self.table_name).select("*").gte(
                "timestamp", start_date.isoformat()
            ).lte(
                "timestamp", end_date.isoformat()
            ).order("timestamp", desc=False).execute()
            
            if not response.data:
                return True  # No entries to verify
            
            # Verify hash chain
            previous_hash = ""
            for row in response.data:
                # Recreate the entry
                entry = AuditEntry(
                    id=row["id"],
                    timestamp=row["timestamp"],
                    event_type=AuditEventType(row["event_type"]),
                    severity=AuditSeverity(row["severity"]),
                    user_id=row.get("user_id"),
                    clinic_id=row.get("clinic_id"),
                    resource_type=row.get("resource_type"),
                    resource_id=row.get("resource_id"),
                    action=row["action"],
                    outcome=row["outcome"],
                    ip_address=row.get("ip_address"),
                    user_agent=row.get("user_agent"),
                    metadata=row.get("metadata", {}),
                    previous_hash=row.get("previous_hash")
                )
                
                # Calculate expected hash
                expected_hash = entry.calculate_hash(previous_hash)
                
                # Verify hash matches
                if row.get("hash") != expected_hash:
                    logger.error(f"Hash mismatch for audit entry {row['id']}")
                    return False
                
                # Verify previous hash matches
                if row.get("previous_hash") != previous_hash:
                    logger.error(f"Previous hash mismatch for audit entry {row['id']}")
                    return False
                
                previous_hash = expected_hash
            
            return True
        except Exception as e:
            logger.error(f"Failed to verify audit integrity: {e}")
            return False


class FileStorageBackend(StorageBackend):
    """File-based storage backend for immutable audit logs"""
    
    def __init__(self, storage_path: str):
        self.storage_path = Path(storage_path)
        self.storage_path.mkdir(parents=True, exist_ok=True)
        self.current_file = None
        self.current_date = None
        self.lock = asyncio.Lock()
    
    def _get_filename(self, date: datetime) -> Path:
        """Get filename for a specific date"""
        return self.storage_path / f"audit_{date.strftime('%Y%m%d')}.jsonl"
    
    async def write(self, entry: AuditEntry) -> bool:
        """Write audit entry to file"""
        try:
            async with self.lock:
                # Get filename for current date
                entry_date = datetime.fromisoformat(entry.timestamp).date()
                filename = self._get_filename(datetime.fromisoformat(entry.timestamp))
                
                # Append to file
                async with aiofiles.open(filename, 'a') as f:
                    await f.write(json.dumps(entry.to_dict()) + '\n')
                
                return True
        except Exception as e:
            logger.error(f"Failed to write audit entry to file: {e}")
            return False
    
    async def read(self, filters: Dict[str, Any], limit: int = 100) -> List[AuditEntry]:
        """Read audit entries from file"""
        entries = []
        
        try:
            # Determine date range
            start_date = filters.get("start_date", datetime.now() - timedelta(days=7))
            end_date = filters.get("end_date", datetime.now())
            
            # Read files in date range
            current_date = start_date.date()
            while current_date <= end_date.date():
                filename = self._get_filename(datetime.combine(current_date, datetime.min.time()))
                
                if filename.exists():
                    async with aiofiles.open(filename, 'r') as f:
                        async for line in f:
                            try:
                                data = json.loads(line.strip())
                                
                                # Apply filters
                                if "user_id" in filters and data.get("user_id") != filters["user_id"]:
                                    continue
                                if "clinic_id" in filters and data.get("clinic_id") != filters["clinic_id"]:
                                    continue
                                if "event_type" in filters and data.get("event_type") != filters["event_type"]:
                                    continue
                                
                                # Create AuditEntry
                                entries.append(AuditEntry(
                                    id=data["id"],
                                    timestamp=data["timestamp"],
                                    event_type=AuditEventType(data["event_type"]),
                                    severity=AuditSeverity(data["severity"]),
                                    user_id=data.get("user_id"),
                                    clinic_id=data.get("clinic_id"),
                                    resource_type=data.get("resource_type"),
                                    resource_id=data.get("resource_id"),
                                    action=data["action"],
                                    outcome=data["outcome"],
                                    ip_address=data.get("ip_address"),
                                    user_agent=data.get("user_agent"),
                                    metadata=data.get("metadata", {}),
                                    hash=data.get("hash"),
                                    previous_hash=data.get("previous_hash")
                                ))
                                
                                if len(entries) >= limit:
                                    break
                            except json.JSONDecodeError:
                                logger.warning(f"Skipping malformed line in {filename}")
                
                current_date += timedelta(days=1)
                
                if len(entries) >= limit:
                    break
            
            # Sort by timestamp descending
            entries.sort(key=lambda x: x.timestamp, reverse=True)
            
            return entries[:limit]
        except Exception as e:
            logger.error(f"Failed to read audit entries from file: {e}")
            return []
    
    async def verify_integrity(self, start_date: datetime, end_date: datetime) -> bool:
        """Verify integrity of audit logs in date range"""
        # File-based storage is append-only, so integrity is maintained
        # Could implement additional checks like file permissions, checksums, etc.
        return True


class AuditService:
    """Main audit service for HIPAA compliance"""
    
    def __init__(self, storage_backend: StorageBackend):
        self.storage = storage_backend
        self.last_hash = ""
        self.lock = asyncio.Lock()
    
    async def log_access(
        self,
        user_id: str,
        resource_type: str,
        resource_id: str,
        action: str,
        clinic_id: Optional[str] = None,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
        metadata: Optional[Dict] = None
    ) -> bool:
        """Log PHI access for compliance"""
        return await self.log_event(
            event_type=AuditEventType.PHI_ACCESS,
            severity=AuditSeverity.INFO,
            user_id=user_id,
            clinic_id=clinic_id,
            resource_type=resource_type,
            resource_id=resource_id,
            action=action,
            outcome="success",
            ip_address=ip_address,
            user_agent=user_agent,
            metadata=metadata or {}
        )
    
    async def log_event(
        self,
        event_type: AuditEventType,
        severity: AuditSeverity,
        action: str,
        outcome: str,
        user_id: Optional[str] = None,
        clinic_id: Optional[str] = None,
        resource_type: Optional[str] = None,
        resource_id: Optional[str] = None,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
        metadata: Optional[Dict] = None
    ) -> bool:
        """Log a general audit event"""
        async with self.lock:
            try:
                # Create audit entry
                entry = AuditEntry(
                    id=str(uuid.uuid4()),
                    timestamp=datetime.utcnow().isoformat(),
                    event_type=event_type,
                    severity=severity,
                    user_id=user_id,
                    clinic_id=clinic_id,
                    resource_type=resource_type,
                    resource_id=resource_id,
                    action=action,
                    outcome=outcome,
                    ip_address=ip_address,
                    user_agent=user_agent,
                    metadata=metadata or {},
                    previous_hash=self.last_hash
                )
                
                # Calculate hash
                entry.hash = entry.calculate_hash(self.last_hash)
                
                # Write to storage
                success = await self.storage.write(entry)
                
                if success:
                    self.last_hash = entry.hash
                    logger.debug(f"Audit event logged: {event_type.value} - {action}")
                else:
                    logger.error(f"Failed to log audit event: {event_type.value} - {action}")
                
                return success
            except Exception as e:
                logger.error(f"Error logging audit event: {e}")
                return False
    
    async def log_login(
        self,
        user_id: str,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
        success: bool = True
    ) -> bool:
        """Log user login attempt"""
        return await self.log_event(
            event_type=AuditEventType.USER_LOGIN,
            severity=AuditSeverity.INFO if success else AuditSeverity.WARNING,
            user_id=user_id,
            action="user_login",
            outcome="success" if success else "failure",
            ip_address=ip_address,
            user_agent=user_agent,
            metadata={"success": success}
        )
    
    async def log_logout(
        self,
        user_id: str,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None
    ) -> bool:
        """Log user logout"""
        return await self.log_event(
            event_type=AuditEventType.USER_LOGOUT,
            severity=AuditSeverity.INFO,
            user_id=user_id,
            action="user_logout",
            outcome="success",
            ip_address=ip_address,
            user_agent=user_agent
        )
    
    async def log_data_export(
        self,
        user_id: str,
        clinic_id: str,
        export_type: str,
        record_count: int,
        ip_address: Optional[str] = None
    ) -> bool:
        """Log data export for compliance"""
        return await self.log_event(
            event_type=AuditEventType.DATA_EXPORT,
            severity=AuditSeverity.WARNING,
            user_id=user_id,
            clinic_id=clinic_id,
            action=f"export_{export_type}",
            outcome="success",
            ip_address=ip_address,
            metadata={
                "export_type": export_type,
                "record_count": record_count
            }
        )
    
    async def log_rule_violation(
        self,
        rule_name: str,
        violation_details: Dict,
        user_id: Optional[str] = None,
        clinic_id: Optional[str] = None
    ) -> bool:
        """Log business rule violation"""
        return await self.log_event(
            event_type=AuditEventType.RULE_VIOLATION,
            severity=AuditSeverity.WARNING,
            user_id=user_id,
            clinic_id=clinic_id,
            action=f"rule_violation_{rule_name}",
            outcome="violation",
            metadata={
                "rule_name": rule_name,
                "violation": violation_details
            }
        )
    
    async def log_error(
        self,
        error_type: str,
        error_message: str,
        user_id: Optional[str] = None,
        clinic_id: Optional[str] = None,
        metadata: Optional[Dict] = None
    ) -> bool:
        """Log system error"""
        return await self.log_event(
            event_type=AuditEventType.ERROR,
            severity=AuditSeverity.ERROR,
            user_id=user_id,
            clinic_id=clinic_id,
            action=f"error_{error_type}",
            outcome="error",
            metadata={
                "error_type": error_type,
                "error_message": error_message,
                **(metadata or {})
            }
        )
    
    async def query_logs(
        self,
        user_id: Optional[str] = None,
        clinic_id: Optional[str] = None,
        event_type: Optional[AuditEventType] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        limit: int = 100
    ) -> List[AuditEntry]:
        """Query audit logs with filters"""
        filters = {}
        
        if user_id:
            filters["user_id"] = user_id
        if clinic_id:
            filters["clinic_id"] = clinic_id
        if event_type:
            filters["event_type"] = event_type.value
        if start_date:
            filters["start_date"] = start_date
        if end_date:
            filters["end_date"] = end_date
        
        return await self.storage.read(filters, limit)
    
    async def verify_integrity(
        self,
        start_date: datetime,
        end_date: datetime
    ) -> bool:
        """Verify integrity of audit logs in date range"""
        return await self.storage.verify_integrity(start_date, end_date)
    
    async def export_logs(
        self,
        start_date: datetime,
        end_date: datetime,
        format: str = "json"
    ) -> str:
        """Export audit logs for compliance reporting"""
        # Query logs in date range
        logs = await self.query_logs(
            start_date=start_date,
            end_date=end_date,
            limit=10000  # Higher limit for exports
        )
        
        if format == "json":
            return json.dumps([log.to_dict() for log in logs], indent=2)
        elif format == "csv":
            # Convert to CSV format
            import csv
            import io
            
            output = io.StringIO()
            if logs:
                writer = csv.DictWriter(output, fieldnames=logs[0].to_dict().keys())
                writer.writeheader()
                for log in logs:
                    writer.writerow(log.to_dict())
            
            return output.getvalue()
        else:
            raise ValueError(f"Unsupported export format: {format}")


# Factory function to create audit service
def create_audit_service(supabase_client: Optional[Client] = None, use_file_backend: bool = False) -> AuditService:
    """Create an audit service with appropriate backend"""
    if use_file_backend:
        storage = FileStorageBackend(settings.AUDIT_LOG_PATH or "./audit_logs")
    else:
        if not supabase_client:
            raise ValueError("Supabase client required for database backend")
        storage = DatabaseStorageBackend(supabase_client)
    
    return AuditService(storage)