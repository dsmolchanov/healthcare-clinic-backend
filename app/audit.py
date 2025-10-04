"""
Audit logging for compliance and security
"""

import hashlib
import json
from datetime import datetime
from typing import Dict, Any, Optional
import os


def hash_phone(phone: str) -> str:
    """
    Hash phone number for privacy in logs

    Args:
        phone: Phone number to hash

    Returns:
        Hashed phone number
    """
    salt = os.environ.get('HASH_SALT', 'default_salt_do_not_use_in_production')
    return hashlib.sha256(f"{salt}{phone}".encode()).hexdigest()


async def log_conversation(clinic_id: str, patient_phone: str, message_type: str, content: str):
    """
    Log conversation for compliance

    Args:
        clinic_id: Clinic identifier
        patient_phone: Patient phone number
        message_type: Type of message (incoming/outgoing)
        content: Message content
    """
    from .database import db

    audit_entry = {
        'organization_id': clinic_id,
        'event_type': 'whatsapp_message',
        'event_category': message_type,
        'event_data': {
            'phone': hash_phone(patient_phone),
            'content_length': len(content),
            'timestamp': datetime.utcnow().isoformat()
        },
        'created_at': datetime.utcnow().isoformat()
    }

    # Store in database
    await db.table('core.audit_logs').insert(audit_entry).execute()


async def get_audit_retention_policy(market: str) -> Dict[str, Any]:
    """
    Get audit retention policy based on market

    Args:
        market: Market identifier (mexico/us)

    Returns:
        Retention policy dictionary
    """
    if market == 'mexico':
        return {
            'retention_years': 5,
            'deletion_allowed': True,
            'encryption_required': False
        }
    elif market == 'us':
        return {
            'retention_years': 7,
            'deletion_allowed': False,  # HIPAA requires immutable logs
            'encryption_required': True
        }
    else:
        return {
            'retention_years': 5,
            'deletion_allowed': True,
            'encryption_required': False
        }


class AuditLogger:
    """
    Audit logger for tracking all system events
    """

    def __init__(self, market: str = 'mexico'):
        self.market = market
        self.policy = None

    async def log_event(self, clinic_id: str, event_type: str, event_data: Dict[str, Any]):
        """
        Log an audit event

        Args:
            clinic_id: Clinic identifier
            event_type: Type of event
            event_data: Event data dictionary
        """
        from .database import db

        # Hash sensitive data
        if 'phone' in event_data:
            event_data['phone'] = hash_phone(event_data['phone'])

        audit_entry = {
            'organization_id': clinic_id,
            'event_type': event_type,
            'event_data': event_data,
            'timestamp': datetime.utcnow().isoformat(),
            'market': self.market
        }

        # Store in database
        await db.table('core.audit_logs').insert(audit_entry).execute()

    async def get_audit_trail(self, clinic_id: str, days: int = 30) -> list:
        """
        Get audit trail for a clinic

        Args:
            clinic_id: Clinic identifier
            days: Number of days to retrieve

        Returns:
            List of audit entries
        """
        from .database import db
        from datetime import timedelta

        since = datetime.utcnow() - timedelta(days=days)

        result = await db.table('core.audit_logs')\
            .select('*')\
            .eq('organization_id', clinic_id)\
            .gte('timestamp', since.isoformat())\
            .execute()

        return result.data if result else []


class ImmutableAuditLogger(AuditLogger):
    """
    Immutable audit logger for HIPAA compliance
    """

    async def log_event(self, clinic_id: str, event_type: str, event_data: Dict[str, Any]):
        """
        Log an immutable audit event

        Args:
            clinic_id: Clinic identifier
            event_type: Type of event
            event_data: Event data dictionary
        """
        # Add cryptographic hash for tamper detection
        event_data['integrity_hash'] = self._calculate_hash(event_data)

        await super().log_event(clinic_id, event_type, event_data)

    def _calculate_hash(self, data: Dict[str, Any]) -> str:
        """Calculate integrity hash for audit entry"""
        data_str = json.dumps(data, sort_keys=True)
        return hashlib.sha256(data_str.encode()).hexdigest()


class StandardAuditLogger(AuditLogger):
    """
    Standard audit logger for non-HIPAA markets
    """
    pass
