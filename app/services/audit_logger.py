"""
Audit Logging Service for security and compliance tracking.
Logs all sensitive operations for HIPAA and LFPDPPP compliance.
"""

import json
import hashlib
import logging
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
from enum import Enum
from dataclasses import dataclass, asdict
import asyncio
from collections import deque

class AuditEventType(Enum):
    """Types of audit events"""
    AUTHENTICATION = "authentication"
    AUTHORIZATION = "authorization"
    DATA_ACCESS = "data_access"
    DATA_MODIFICATION = "data_modification"
    DATA_DELETION = "data_deletion"
    CONSENT_GRANTED = "consent_granted"
    CONSENT_REVOKED = "consent_revoked"
    APPOINTMENT_CREATED = "appointment_created"
    APPOINTMENT_MODIFIED = "appointment_modified"
    APPOINTMENT_CANCELLED = "appointment_cancelled"
    MESSAGE_SENT = "message_sent"
    MESSAGE_RECEIVED = "message_received"
    PHI_ACCESS = "phi_access"
    PHI_MODIFICATION = "phi_modification"
    SECURITY_ALERT = "security_alert"
    RATE_LIMIT_EXCEEDED = "rate_limit_exceeded"
    SESSION_CREATED = "session_created"
    SESSION_EXPIRED = "session_expired"
    WEBHOOK_RECEIVED = "webhook_received"
    WEBHOOK_VALIDATION_FAILED = "webhook_validation_failed"

class AuditSeverity(Enum):
    """Severity levels for audit events"""
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"

@dataclass
class AuditEvent:
    """Represents an audit event"""
    timestamp: str
    event_type: AuditEventType
    severity: AuditSeverity
    clinic_id: Optional[str]
    user_id: Optional[str]
    session_id: Optional[str]
    ip_address: Optional[str]
    user_agent: Optional[str]
    action: str
    resource: Optional[str]
    result: str
    details: Dict[str, Any]
    compliance_flags: List[str]  # e.g., ['HIPAA', 'LFPDPPP']

class AuditLogger:
    """Service for logging audit events"""

    def __init__(self, clinic_id: Optional[str] = None, market: str = 'mexico'):
        self.clinic_id = clinic_id
        self.market = market
        self.logger = logging.getLogger(__name__)

        # In-memory buffer for recent events (for testing)
        self.recent_events = deque(maxlen=1000)

        # Storage backend (would be database in production)
        self.storage_backend = None

        # Configure logging level based on market
        if market == 'us':
            self.logger.setLevel(logging.DEBUG)  # More verbose for HIPAA
        else:
            self.logger.setLevel(logging.INFO)

    def hash_pii(self, value: str) -> str:
        """
        Hash PII data for privacy protection.
        Uses SHA-256 with a salt for security.
        """
        if not value:
            return ""

        # In production, use a proper salt management system
        salt = "dental_clinic_salt_2024"
        salted = f"{salt}{value}"
        return hashlib.sha256(salted.encode()).hexdigest()[:16]

    async def log_event(
        self,
        event_type: AuditEventType,
        action: str,
        result: str,
        severity: AuditSeverity = AuditSeverity.INFO,
        user_id: Optional[str] = None,
        session_id: Optional[str] = None,
        resource: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
        phone_number: Optional[str] = None
    ) -> None:
        """
        Log an audit event.

        Args:
            event_type: Type of event
            action: Action performed
            result: Result of the action (success/failure)
            severity: Severity level
            user_id: User identifier
            session_id: Session identifier
            resource: Resource accessed/modified
            details: Additional event details
            ip_address: Client IP address
            user_agent: Client user agent
            phone_number: Phone number (will be hashed)
        """
        # Hash phone number if provided
        if phone_number:
            if details is None:
                details = {}
            details['phone_hash'] = self.hash_pii(phone_number)

        # Determine compliance flags based on event type and market
        compliance_flags = []

        if self.market == 'us':
            # HIPAA-relevant events
            if event_type in [
                AuditEventType.PHI_ACCESS,
                AuditEventType.PHI_MODIFICATION,
                AuditEventType.DATA_ACCESS,
                AuditEventType.DATA_MODIFICATION,
                AuditEventType.DATA_DELETION
            ]:
                compliance_flags.append('HIPAA')

        # LFPDPPP-relevant events for Mexico
        if event_type in [
            AuditEventType.CONSENT_GRANTED,
            AuditEventType.CONSENT_REVOKED,
            AuditEventType.DATA_ACCESS,
            AuditEventType.DATA_MODIFICATION,
            AuditEventType.DATA_DELETION
        ]:
            compliance_flags.append('LFPDPPP')

        # Create audit event
        event = AuditEvent(
            timestamp=datetime.now(timezone.utc).isoformat(),
            event_type=event_type,
            severity=severity,
            clinic_id=self.clinic_id,
            user_id=user_id,
            session_id=session_id,
            ip_address=ip_address,
            user_agent=user_agent,
            action=action,
            resource=resource,
            result=result,
            details=details or {},
            compliance_flags=compliance_flags
        )

        # Store in memory buffer
        self.recent_events.append(event)

        # Log to standard logger
        log_message = f"AUDIT: {event_type.value} - {action} - {result}"
        if resource:
            log_message += f" - Resource: {resource}"

        if severity == AuditSeverity.DEBUG:
            self.logger.debug(log_message, extra=asdict(event))
        elif severity == AuditSeverity.INFO:
            self.logger.info(log_message, extra=asdict(event))
        elif severity == AuditSeverity.WARNING:
            self.logger.warning(log_message, extra=asdict(event))
        elif severity == AuditSeverity.ERROR:
            self.logger.error(log_message, extra=asdict(event))
        elif severity == AuditSeverity.CRITICAL:
            self.logger.critical(log_message, extra=asdict(event))

        # In production, also store to database
        if self.storage_backend:
            await self._store_to_database(event)

    async def _store_to_database(self, event: AuditEvent) -> None:
        """Store audit event to database (placeholder for production)"""
        # This would connect to actual database in production
        pass

    async def log_authentication_attempt(
        self,
        success: bool,
        user_id: Optional[str] = None,
        method: str = "password",
        ip_address: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None
    ) -> None:
        """Log an authentication attempt"""
        await self.log_event(
            event_type=AuditEventType.AUTHENTICATION,
            action=f"Authentication attempt via {method}",
            result="success" if success else "failure",
            severity=AuditSeverity.INFO if success else AuditSeverity.WARNING,
            user_id=user_id,
            ip_address=ip_address,
            details=details
        )

    async def log_data_access(
        self,
        resource: str,
        user_id: str,
        session_id: str,
        operation: str = "read",
        details: Optional[Dict[str, Any]] = None
    ) -> None:
        """Log data access event"""
        await self.log_event(
            event_type=AuditEventType.DATA_ACCESS,
            action=f"Data {operation} operation",
            result="success",
            user_id=user_id,
            session_id=session_id,
            resource=resource,
            details=details
        )

    async def log_consent_change(
        self,
        user_id: str,
        consent_type: str,
        granted: bool,
        details: Optional[Dict[str, Any]] = None
    ) -> None:
        """Log consent grant or revocation"""
        event_type = AuditEventType.CONSENT_GRANTED if granted else AuditEventType.CONSENT_REVOKED
        await self.log_event(
            event_type=event_type,
            action=f"Consent {consent_type} {'granted' if granted else 'revoked'}",
            result="success",
            user_id=user_id,
            details=details
        )

    async def log_security_event(
        self,
        event_description: str,
        severity: AuditSeverity = AuditSeverity.WARNING,
        ip_address: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None
    ) -> None:
        """Log a security-related event"""
        await self.log_event(
            event_type=AuditEventType.SECURITY_ALERT,
            action=event_description,
            result="detected",
            severity=severity,
            ip_address=ip_address,
            details=details
        )

    async def log_webhook_event(
        self,
        success: bool,
        source: str,
        ip_address: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None
    ) -> None:
        """Log webhook reception and validation"""
        event_type = (
            AuditEventType.WEBHOOK_RECEIVED
            if success
            else AuditEventType.WEBHOOK_VALIDATION_FAILED
        )
        await self.log_event(
            event_type=event_type,
            action=f"Webhook from {source}",
            result="success" if success else "validation_failed",
            severity=AuditSeverity.INFO if success else AuditSeverity.WARNING,
            ip_address=ip_address,
            details=details
        )

    async def get_recent_events(
        self,
        limit: int = 100,
        event_type: Optional[AuditEventType] = None,
        severity: Optional[AuditSeverity] = None
    ) -> List[AuditEvent]:
        """Get recent audit events from memory buffer"""
        events = list(self.recent_events)

        # Filter by event type if specified
        if event_type:
            events = [e for e in events if e.event_type == event_type]

        # Filter by severity if specified
        if severity:
            events = [e for e in events if e.severity == severity]

        # Return most recent events up to limit
        return events[-limit:]

    async def generate_compliance_report(
        self,
        start_date: datetime,
        end_date: datetime,
        compliance_standard: str = "LFPDPPP"
    ) -> Dict[str, Any]:
        """Generate a compliance report for auditors"""
        events = list(self.recent_events)

        # Filter events by date range and compliance standard
        filtered_events = [
            e for e in events
            if (compliance_standard in e.compliance_flags and
                start_date.isoformat() <= e.timestamp <= end_date.isoformat())
        ]

        # Generate summary statistics
        event_counts = {}
        for event in filtered_events:
            event_type = event.event_type.value
            event_counts[event_type] = event_counts.get(event_type, 0) + 1

        return {
            'compliance_standard': compliance_standard,
            'period': {
                'start': start_date.isoformat(),
                'end': end_date.isoformat()
            },
            'total_events': len(filtered_events),
            'event_breakdown': event_counts,
            'clinic_id': self.clinic_id,
            'generated_at': datetime.now(timezone.utc).isoformat()
        }

# Global audit logger instance
_audit_logger_instance = None

def get_audit_logger(clinic_id: Optional[str] = None, market: str = 'mexico') -> AuditLogger:
    """Get or create the global audit logger instance"""
    global _audit_logger_instance
    if _audit_logger_instance is None:
        _audit_logger_instance = AuditLogger(clinic_id=clinic_id, market=market)
    return _audit_logger_instance
