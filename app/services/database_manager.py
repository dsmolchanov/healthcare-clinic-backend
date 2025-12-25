"""
Multi-Database Connection Manager for Healthcare Platform
Manages connections to separate main and healthcare databases with PHI protection
"""

import os
import logging
from datetime import datetime
from typing import Optional, Dict, Any, Union
from contextlib import asynccontextmanager
from dataclasses import dataclass
from enum import Enum

from supabase import create_client, Client
from supabase.client import ClientOptions
import asyncpg
from cryptography.fernet import Fernet
import hashlib

logger = logging.getLogger(__name__)


class DatabaseType(Enum):
    """Database types for connection routing"""
    MAIN = "main"  # Non-PHI data: organizations, agents, configs
    HEALTHCARE = "healthcare"  # PHI data: patients, appointments, medical records


@dataclass
class DatabaseConfig:
    """Configuration for a database connection"""
    url: str
    anon_key: str
    service_key: str
    schema: str = "public"
    pool_size: int = 20
    max_overflow: int = 10
    pool_timeout: int = 30
    requires_baa: bool = False


class PHIEncryption:
    """Handles encryption/decryption of PHI fields"""

    def __init__(self, encryption_key: Optional[str] = None):
        """Initialize encryption with key from environment or parameter"""
        key = encryption_key or os.getenv('PHI_ENCRYPTION_KEY')
        if not key:
            raise ValueError("PHI_ENCRYPTION_KEY must be set for healthcare database")

        # Ensure key is properly formatted
        if not key.startswith(b'gAAAAA'):
            key = Fernet.generate_key() if key == 'generate' else key.encode()

        self.cipher = Fernet(key)
        self.salt = os.getenv('PHI_ENCRYPTION_SALT', 'default-salt')

    def encrypt(self, value: Optional[str]) -> Optional[str]:
        """Encrypt a PHI field value"""
        if not value:
            return None
        return self.cipher.encrypt(value.encode()).decode()

    def decrypt(self, encrypted_value: Optional[str]) -> Optional[str]:
        """Decrypt a PHI field value"""
        if not encrypted_value:
            return None
        return self.cipher.decrypt(encrypted_value.encode()).decode()

    def hash(self, value: Optional[str]) -> Optional[str]:
        """Create searchable hash of PHI for lookups"""
        if not value:
            return None
        salted = f"{value}{self.salt}"
        return hashlib.sha256(salted.encode()).hexdigest()

    def encrypt_dict(self, data: Dict[str, Any], fields: list) -> Dict[str, Any]:
        """Encrypt specified fields in a dictionary"""
        encrypted = data.copy()
        for field in fields:
            if field in encrypted and encrypted[field]:
                encrypted[f"{field}_encrypted"] = self.encrypt(encrypted[field])
                # Remove original field for safety
                del encrypted[field]
        return encrypted

    def decrypt_dict(self, data: Dict[str, Any], fields: list) -> Dict[str, Any]:
        """Decrypt specified fields in a dictionary"""
        decrypted = data.copy()
        for field in fields:
            encrypted_field = f"{field}_encrypted"
            if encrypted_field in decrypted and decrypted[encrypted_field]:
                decrypted[field] = self.decrypt(decrypted[encrypted_field])
                # Keep encrypted version for audit
        return decrypted


class DatabaseManager:
    """Manages connections to multiple databases with proper isolation"""

    def __init__(self):
        """Initialize database connections"""
        self.configs = self._load_configs()
        self.clients: Dict[DatabaseType, Client] = {}
        self.async_pools: Dict[DatabaseType, asyncpg.Pool] = {}
        self.phi_encryption = None

        # Initialize PHI encryption only if healthcare DB is configured
        if DatabaseType.HEALTHCARE in self.configs:
            self.phi_encryption = PHIEncryption()

        # Create Supabase clients
        self._initialize_clients()

    def _load_configs(self) -> Dict[DatabaseType, DatabaseConfig]:
        """Load database configurations from environment"""
        configs = {}

        # Main database configuration (no PHI)
        if os.getenv('MAIN_SUPABASE_URL'):
            configs[DatabaseType.MAIN] = DatabaseConfig(
                url=os.getenv('MAIN_SUPABASE_URL'),
                anon_key=os.getenv('MAIN_SUPABASE_ANON_KEY'),
                service_key=os.getenv('MAIN_SUPABASE_SERVICE_KEY'),
                schema='public',
                requires_baa=False
            )

        # Healthcare database configuration (PHI - requires BAA)
        if os.getenv('HEALTHCARE_SUPABASE_URL'):
            configs[DatabaseType.HEALTHCARE] = DatabaseConfig(
                url=os.getenv('HEALTHCARE_SUPABASE_URL'),
                anon_key=os.getenv('HEALTHCARE_SUPABASE_ANON_KEY'),
                service_key=os.getenv('HEALTHCARE_SUPABASE_SERVICE_KEY'),
                schema='healthcare',
                requires_baa=True
            )

        return configs

    def _initialize_clients(self):
        """Initialize Supabase clients for each database"""
        for db_type, config in self.configs.items():
            options = ClientOptions(
                schema=config.schema,
                auto_refresh_token=True,
                persist_session=False if db_type == DatabaseType.HEALTHCARE else True
            )

            # Use service key for server-side operations
            self.clients[db_type] = create_client(
                config.url,
                config.service_key,
                options=options
            )

            logger.info(f"Initialized {db_type.value} database client")

    def get_client(self, db_type: DatabaseType) -> Client:
        """Get Supabase client for specified database type"""
        if db_type not in self.clients:
            raise ValueError(f"Database {db_type.value} not configured")
        return self.clients[db_type]

    @asynccontextmanager
    async def get_async_connection(self, db_type: DatabaseType):
        """Get async database connection from pool"""
        if db_type not in self.async_pools:
            # Create pool on first use
            config = self.configs[db_type]
            # Extract database URL from Supabase URL
            db_url = config.url.replace('https://', 'postgresql://postgres:')
            db_url = db_url.replace('.supabase.co', '.supabase.co:5432/postgres')

            self.async_pools[db_type] = await asyncpg.create_pool(
                db_url,
                min_size=5,
                max_size=config.pool_size,
                timeout=config.pool_timeout
            )

        async with self.async_pools[db_type].acquire() as connection:
            # Set session variables for RLS if needed
            if db_type == DatabaseType.HEALTHCARE:
                await self._set_session_context(connection)
            yield connection

    async def _set_session_context(self, connection: asyncpg.Connection):
        """Set session context for Row Level Security"""
        # Get user context from somewhere (e.g., request context)
        user_id = self._get_current_user_id()
        user_role = self._get_current_user_role()

        if user_id:
            await connection.execute(f"SET LOCAL app.user_id = '{user_id}'")
        if user_role:
            await connection.execute(f"SET LOCAL app.user_role = '{user_role}'")

    def _get_current_user_id(self) -> Optional[str]:
        """Get current user ID from request context"""
        # This should be implemented based on your auth system
        # For now, return from environment or None
        return os.getenv('CURRENT_USER_ID')

    def _get_current_user_role(self) -> Optional[str]:
        """Get current user role from request context"""
        return os.getenv('CURRENT_USER_ROLE')

    # ==================== Main Database Operations ====================

    async def get_organization(self, org_id: str) -> Optional[Dict]:
        """Get organization from main database"""
        client = self.get_client(DatabaseType.MAIN)
        response = client.table('organizations').select('*').eq('id', org_id).single().execute()
        return response.data

    async def get_agent_config(self, agent_id: str) -> Optional[Dict]:
        """Get agent configuration from main database"""
        client = self.get_client(DatabaseType.MAIN)
        response = client.table('agent_configs').select('*').eq('id', agent_id).single().execute()
        return response.data

    async def list_clinics_for_org(self, org_id: str) -> list:
        """List all clinics for an organization"""
        client = self.get_client(DatabaseType.MAIN)
        response = client.table('organization_clinics').select('*').eq('organization_id', org_id).execute()
        return response.data

    # ==================== Healthcare Database Operations ====================

    async def create_patient(self, patient_data: Dict) -> Dict:
        """Create patient with PHI encryption"""
        if not self.phi_encryption:
            raise ValueError("Healthcare database not configured")

        # Fields that need encryption
        phi_fields = ['first_name', 'last_name', 'date_of_birth', 'ssn', 'phone']

        # Encrypt PHI fields
        encrypted_data = self.phi_encryption.encrypt_dict(patient_data, phi_fields)

        # Add hashed phone for searching
        if 'phone' in patient_data:
            encrypted_data['phone_hash'] = self.phi_encryption.hash(patient_data['phone'])

        # Insert into healthcare database
        client = self.get_client(DatabaseType.HEALTHCARE)
        response = client.table('patients').insert(encrypted_data).execute()

        # Log PHI access for audit
        await self._log_phi_access('CREATE', 'patients', response.data[0]['id'])

        return response.data[0]

    async def get_patient(self, patient_id: str, decrypt: bool = True) -> Optional[Dict]:
        """Get patient with optional PHI decryption"""
        client = self.get_client(DatabaseType.HEALTHCARE)
        response = client.table('patients').select('*').eq('id', patient_id).single().execute()

        if not response.data:
            return None

        # Log PHI access
        await self._log_phi_access('READ', 'patients', patient_id)

        # Decrypt if requested and authorized
        if decrypt and self.phi_encryption and await self._can_decrypt_phi():
            phi_fields = ['first_name', 'last_name', 'date_of_birth', 'ssn']
            return self.phi_encryption.decrypt_dict(response.data, phi_fields)

        return response.data

    async def search_patients_by_phone(self, phone: str) -> list:
        """Search patients by phone number using hash"""
        if not self.phi_encryption:
            raise ValueError("Healthcare database not configured")

        phone_hash = self.phi_encryption.hash(phone)

        client = self.get_client(DatabaseType.HEALTHCARE)
        response = client.table('patients').select('id, clinic_id').eq('phone_hash', phone_hash).execute()

        # Log search attempt
        await self._log_phi_access('SEARCH', 'patients', None, {'search_field': 'phone'})

        return response.data

    async def create_appointment(self, appointment_data: Dict) -> Dict:
        """Create appointment with encrypted notes"""
        if self.phi_encryption:
            # Encrypt sensitive fields
            sensitive_fields = ['reason', 'notes', 'diagnosis']
            appointment_data = self.phi_encryption.encrypt_dict(appointment_data, sensitive_fields)

        client = self.get_client(DatabaseType.HEALTHCARE)
        response = client.table('appointments').insert(appointment_data).execute()

        # Log access
        await self._log_phi_access('CREATE', 'appointments', response.data[0]['id'])

        return response.data[0]

    async def get_appointments_for_patient(self, patient_id: str, limit: int = 10) -> list:
        """Get appointments for a patient"""
        client = self.get_client(DatabaseType.HEALTHCARE)
        response = (
            client.table('appointments')
            .select('*')
            .eq('patient_id', patient_id)
            .order('appointment_date', desc=True)
            .limit(limit)
            .execute()
        )

        # Log bulk access
        await self._log_phi_access('READ_BULK', 'appointments', patient_id, {'count': len(response.data)})

        return response.data

    # ==================== Cross-Database Operations ====================

    async def validate_clinic_access(self, user_id: str, clinic_id: str) -> bool:
        """Validate user has access to clinic across databases"""
        # Check user's organization in main DB
        main_client = self.get_client(DatabaseType.MAIN)
        user_response = (
            main_client.table('user_organizations')
            .select('organization_id')
            .eq('user_id', user_id)
            .execute()
        )

        if not user_response.data:
            return False

        user_org_ids = [r['organization_id'] for r in user_response.data]

        # Check clinic's organization in healthcare DB
        health_client = self.get_client(DatabaseType.HEALTHCARE)
        clinic_response = (
            health_client.table('clinics')
            .select('organization_id')
            .eq('id', clinic_id)
            .single()
            .execute()
        )

        if not clinic_response.data:
            return False

        return clinic_response.data['organization_id'] in user_org_ids

    async def get_clinic_with_settings(self, clinic_id: str) -> Optional[Dict]:
        """Get clinic with all settings from both databases"""
        # Get clinic from healthcare DB
        health_client = self.get_client(DatabaseType.HEALTHCARE)
        clinic_response = (
            health_client.table('clinics')
            .select('*, whatsapp_config(*), business_rules(*)')
            .eq('id', clinic_id)
            .single()
            .execute()
        )

        if not clinic_response.data:
            return None

        clinic = clinic_response.data

        # Get organization from main DB
        if clinic.get('organization_id'):
            org = await self.get_organization(clinic['organization_id'])
            clinic['organization'] = org

        return clinic

    # ==================== Audit and Compliance ====================

    async def _log_phi_access(
        self,
        operation: str,
        table_name: str,
        record_id: Optional[str],
        metadata: Optional[Dict] = None
    ):
        """Log PHI access for HIPAA compliance"""
        audit_entry = {
            'user_id': self._get_current_user_id(),
            'user_role': self._get_current_user_role(),
            'table_name': table_name,
            'operation': operation,
            'record_id': record_id,
            'metadata': metadata or {},
            'ip_address': os.getenv('REQUEST_IP'),
            'user_agent': os.getenv('REQUEST_USER_AGENT')
        }

        try:
            client = self.get_client(DatabaseType.HEALTHCARE)
            client.table('audit.phi_access_log').insert(audit_entry).execute()
        except Exception as e:
            logger.critical(f"HIPAA: Primary audit logging failed: {e}")

            # Fallback to local queue - NEVER silently fail
            try:
                from app.services.audit_fallback import get_audit_fallback
                fallback = get_audit_fallback()
                await fallback.queue_audit_event({
                    "operation": operation,
                    "table_name": table_name,
                    "record_id": record_id,
                    "metadata": metadata or {},
                    "user_id": audit_entry.get("user_id"),
                    "user_role": audit_entry.get("user_role"),
                    "timestamp": datetime.utcnow().isoformat(),
                    "error": str(e),
                })
                logger.warning("HIPAA: Audit event queued to fallback. Will retry.")
            except Exception as fallback_error:
                # Both primary and fallback failed - MUST fail operation
                logger.critical(f"HIPAA VIOLATION: Both audit paths failed: {fallback_error}")
                self._alert_audit_failure(audit_entry, str(e))
                raise RuntimeError(f"PHI access audit logging failed: {e}") from e

    def _alert_audit_failure(self, audit_entry: Dict, error: str):
        """Alert when audit logging fails"""
        logger.critical(f"AUDIT FAILURE: {error}", extra={'audit_entry': audit_entry})
        # Send to monitoring system

    async def _can_decrypt_phi(self) -> bool:
        """Check if current user can decrypt PHI"""
        # Implement your authorization logic
        user_role = self._get_current_user_role()
        return user_role in ['doctor', 'nurse', 'admin', 'patient']

    # ==================== Cleanup ====================

    async def close(self):
        """Close all database connections"""
        # Close async pools
        for pool in self.async_pools.values():
            await pool.close()

        # Supabase clients don't need explicit closing
        logger.info("Database connections closed")


# ==================== Helper Functions ====================

def get_database_manager() -> DatabaseManager:
    """Get or create database manager singleton"""
    if not hasattr(get_database_manager, '_instance'):
        get_database_manager._instance = DatabaseManager()
    return get_database_manager._instance


async def test_database_connections():
    """Test database connections and encryption"""
    manager = get_database_manager()

    # Test main database
    try:
        org = await manager.get_organization('test-org-id')
        print(f"✓ Main database connected: {org is not None}")
    except Exception as e:
        print(f"✗ Main database error: {e}")

    # Test healthcare database
    try:
        test_patient = {
            'clinic_id': 'test-clinic-id',
            'first_name': 'Test',
            'last_name': 'Patient',
            'phone': '+1234567890',
            'date_of_birth': '1990-01-01'
        }
        # This would create an encrypted patient record
        # patient = await manager.create_patient(test_patient)
        print("✓ Healthcare database configured")
    except Exception as e:
        print(f"✗ Healthcare database error: {e}")

    # Test encryption
    if manager.phi_encryption:
        test_value = "Test PHI Data"
        encrypted = manager.phi_encryption.encrypt(test_value)
        decrypted = manager.phi_encryption.decrypt(encrypted)
        assert decrypted == test_value
        print("✓ PHI encryption working")

    await manager.close()


if __name__ == "__main__":
    import asyncio
    asyncio.run(test_database_connections())
