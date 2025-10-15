"""
Compliance Vault for HIPAA/SOC2/GDPR-compliant secrets management
Handles encryption, storage, and retrieval of sensitive credentials
"""

import os
import json
import base64
import hashlib
from typing import Dict, Optional, Any
from datetime import datetime, timedelta
import uuid
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.backends import default_backend
import boto3
from botocore.exceptions import ClientError
from supabase import create_client, Client
from supabase.client import ClientOptions
import logging

logger = logging.getLogger(__name__)


class ComplianceVault:
    """
    Multi-layer encryption vault for healthcare compliance
    Supports HIPAA, SOC2, and GDPR requirements
    """

    def __init__(self):
        # Initialize AWS clients if available
        self.use_aws = os.getenv('USE_AWS_SECRETS', 'false').lower() == 'true'

        if self.use_aws:
            self.kms = boto3.client('kms', region_name=os.getenv('AWS_REGION', 'us-east-1'))
            self.secrets_manager = boto3.client('secretsmanager', region_name=os.getenv('AWS_REGION', 'us-east-1'))
            self.kms_key_id = os.getenv('AWS_KMS_KEY_ID')

        # Initialize Supabase client with healthcare schema
        options = ClientOptions(schema='healthcare')
        self.supabase: Client = create_client(
            os.getenv('SUPABASE_URL'),
            os.getenv('SUPABASE_SERVICE_ROLE_KEY'),  # Use service key for server-side operations
            options=options
        )

        # Application-level encryption key (should be stored securely)
        self.app_key = self._derive_app_key()

    def _derive_app_key(self) -> bytes:
        """Derive application encryption key from master secret"""
        master_secret = os.getenv('MASTER_ENCRYPTION_SECRET', 'change-this-in-production').encode()
        salt = os.getenv('ENCRYPTION_SALT', 'change-this-salt').encode()

        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=100000,
            backend=default_backend()
        )
        return base64.urlsafe_b64encode(kdf.derive(master_secret))

    async def store_calendar_credentials(
        self,
        organization_id: str,
        provider: str,
        credentials: Dict[str, Any],
        user_id: Optional[str] = None
    ) -> str:
        """
        Store calendar OAuth tokens with multi-layer encryption

        Args:
            organization_id: Organization UUID
            provider: Calendar provider ('google', 'outlook', 'apple')
            credentials: OAuth tokens and related data
            user_id: User who initiated the storage

        Returns:
            Reference to the stored secret
        """
        try:
            # Validate input
            if provider not in ['google', 'outlook', 'apple']:
                raise ValueError(f"Invalid provider: {provider}")

            # Add metadata for compliance
            credentials['stored_at'] = datetime.utcnow().isoformat()
            credentials['provider'] = provider
            credentials['organization_id'] = organization_id

            # Layer 1: Application-level encryption
            app_encrypted = self._app_encrypt(credentials)

            if self.use_aws:
                # Layer 2: AWS KMS encryption and storage
                secret_ref = await self._store_in_aws(
                    organization_id, provider, app_encrypted
                )
            else:
                # Alternative: Store in Supabase with encryption
                secret_ref = await self._store_in_supabase(
                    organization_id, provider, app_encrypted
                )

            # Note: organization_secrets table is deprecated - vault_storage is the source of truth

            # Audit log for compliance (optional)
            try:
                await self._audit_secret_operation(
                    action='store',
                    organization_id=organization_id,
                    secret_type=f'calendar_{provider}',
                    user_id=user_id,
                    compliance_flags=['HIPAA', 'SOC2', 'GDPR']
                )
            except Exception as audit_error:
                # Don't fail if audit_logs table doesn't exist
                logger.warning(f"Could not create audit log (non-critical): {audit_error}")

            logger.info(f"Stored calendar credentials for org {organization_id}, provider {provider}")
            return secret_ref

        except Exception as e:
            logger.error(f"Failed to store calendar credentials: {str(e)}")
            raise

    async def retrieve_calendar_credentials(
        self,
        organization_id: str,
        provider: str,
        user_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Retrieve and decrypt calendar credentials

        Args:
            organization_id: Organization UUID
            provider: Calendar provider
            user_id: User requesting the credentials

        Returns:
            Decrypted credentials dictionary
        """
        try:
            # Get vault entry directly with encrypted data (optimized single query)
            vault_key = f'calendar_{provider}'
            vault_result = self.supabase.table('vault_storage').select('id, encrypted_value').eq(
                'organization_id', organization_id
            ).eq(
                'vault_key', vault_key
            ).order('created_at', desc=True).limit(1).execute()

            if not vault_result.data or len(vault_result.data) == 0:
                raise ValueError(f"No credentials found for {provider}")

            encrypted_data = vault_result.data[0]['encrypted_value']

            # Decrypt
            credentials = self._app_decrypt(encrypted_data)

            # Check token expiration
            if 'expires_at' in credentials:
                expires_at = datetime.fromisoformat(credentials['expires_at'])
                if expires_at < datetime.utcnow():
                    logger.warning(f"Token expired for org {organization_id}, provider {provider}")
                    # Could trigger token refresh here

            # Audit log (optional)
            try:
                await self._audit_secret_operation(
                    action='retrieve',
                    organization_id=organization_id,
                    secret_type=f'calendar_{provider}',
                    user_id=user_id,
                    compliance_flags=['HIPAA', 'SOC2', 'GDPR']
                )
            except Exception as audit_error:
                logger.warning(f"Could not create audit log (non-critical): {audit_error}")

            return credentials

        except Exception as e:
            logger.error(f"Failed to retrieve calendar credentials: {str(e)}")
            raise

    def _app_encrypt(self, data: Dict[str, Any]) -> str:
        """Application-level encryption using AES-GCM"""
        aesgcm = AESGCM(self.app_key[:32])  # Use first 32 bytes for AES-256

        # Generate nonce
        nonce = os.urandom(12)

        # Serialize and encrypt
        plaintext = json.dumps(data).encode()
        ciphertext = aesgcm.encrypt(nonce, plaintext, None)

        # Combine nonce and ciphertext
        encrypted = nonce + ciphertext

        return base64.b64encode(encrypted).decode()

    def _app_decrypt(self, encrypted_data: str) -> Dict[str, Any]:
        """Application-level decryption"""
        aesgcm = AESGCM(self.app_key[:32])

        # Decode from base64
        encrypted = base64.b64decode(encrypted_data)

        # Extract nonce and ciphertext
        nonce = encrypted[:12]
        ciphertext = encrypted[12:]

        # Decrypt
        plaintext = aesgcm.decrypt(nonce, ciphertext, None)

        return json.loads(plaintext.decode())

    async def _store_in_aws(
        self,
        organization_id: str,
        provider: str,
        encrypted_data: str
    ) -> str:
        """Store in AWS Secrets Manager with KMS encryption"""
        secret_name = f"clinic/{organization_id}/calendar/{provider}"

        try:
            # Create or update secret
            response = self.secrets_manager.create_secret(
                Name=secret_name,
                SecretString=encrypted_data,
                KmsKeyId=self.kms_key_id,
                Tags=[
                    {'Key': 'Organization', 'Value': organization_id},
                    {'Key': 'Type', 'Value': 'calendar_credentials'},
                    {'Key': 'Provider', 'Value': provider},
                    {'Key': 'Compliance', 'Value': 'HIPAA'}
                ]
            )
            return response['ARN']

        except ClientError as e:
            if e.response['Error']['Code'] == 'ResourceExistsException':
                # Update existing secret
                response = self.secrets_manager.update_secret(
                    SecretId=secret_name,
                    SecretString=encrypted_data
                )
                return f"arn:aws:secretsmanager:{os.getenv('AWS_REGION')}:{response['ARN'].split(':')[4]}:secret:{secret_name}"
            raise

    async def _store_in_supabase(
        self,
        organization_id: str,
        provider: str,
        encrypted_data: str
    ) -> str:
        """Store encrypted data in Supabase vault table"""
        vault_id = str(uuid.uuid4())

        # Store in a dedicated vault table (you may need to create this)
        result = self.supabase.table('vault_storage').insert({
            'id': vault_id,
            'organization_id': organization_id,
            'vault_key': f"calendar_{provider}",
            'encrypted_value': encrypted_data,
            'metadata': {
                'provider': provider,
                'type': 'calendar_credentials',
                'compliance': ['HIPAA', 'SOC2', 'GDPR']
            },
            'expires_at': (datetime.utcnow() + timedelta(days=90)).isoformat()
        }).execute()

        return f"vault:{vault_id}"

    async def _retrieve_from_aws(self, secret_arn: str) -> str:
        """Retrieve from AWS Secrets Manager"""
        try:
            response = self.secrets_manager.get_secret_value(SecretId=secret_arn)
            return response['SecretString']
        except ClientError as e:
            logger.error(f"Failed to retrieve from AWS: {str(e)}")
            raise

    async def _retrieve_from_supabase(self, vault_ref: str) -> str:
        """Retrieve from Supabase vault"""
        if not vault_ref.startswith('vault:'):
            raise ValueError(f"Invalid vault reference: {vault_ref}")

        vault_id = vault_ref.replace('vault:', '')

        result = self.supabase.table('vault_storage').select('encrypted_value').eq(
            'id', vault_id
        ).single().execute()

        if not result.data:
            raise ValueError(f"Vault entry not found: {vault_id}")

        return result.data['encrypted_value']

    async def _store_secret_reference(
        self,
        organization_id: str,
        secret_type: str,
        secret_ref: str,
        created_by: Optional[str] = None
    ):
        """Store reference to the secret in organization_secrets table"""
        self.supabase.table('organization_secrets').upsert({
            'organization_id': organization_id,
            'secret_type': secret_type,
            'secret_name': f"{secret_type.replace('_', ' ').title()} Credentials",
            'encrypted_value': secret_ref,  # This is just the reference, not the actual secret
            'encryption_key_id': self.kms_key_id if self.use_aws else 'supabase-vault',
            'created_by': created_by or organization_id,
            'created_at': datetime.utcnow().isoformat(),
            'updated_at': datetime.utcnow().isoformat()
        }, on_conflict='organization_id,secret_type').execute()

    async def _get_secret_reference(
        self,
        organization_id: str,
        secret_type: str
    ) -> Optional[str]:
        """Get secret reference from vault_storage (organization_secrets table deprecated)"""
        try:
            vault_key = secret_type  # e.g., 'calendar_google'
            vault_result = self.supabase.table('vault_storage').select('id').eq(
                'organization_id', organization_id
            ).eq(
                'vault_key', vault_key
            ).order('created_at', desc=True).limit(1).execute()

            if vault_result.data and len(vault_result.data) > 0:
                vault_id = vault_result.data[0]['id']
                return f"vault:{vault_id}"
            return None
        except Exception as vault_error:
            logger.error(f"Could not retrieve from vault_storage: {vault_error}")
            return None

    async def _audit_secret_operation(
        self,
        action: str,
        organization_id: str,
        secret_type: str,
        user_id: Optional[str] = None,
        compliance_flags: list = None
    ):
        """Create audit log entry for compliance"""
        timestamp = datetime.utcnow()

        # Map to existing audit_logs schema while also populating new fields
        audit_entry = {
            # Existing schema fields (required)
            'operation': f'secret_{action}',  # Maps to event_type
            'table_name': 'vault_storage',     # Maps to event_category
            'record_id': secret_type,          # Maps to resource_id
            'user_id': user_id,
            'accessed_at': timestamp.isoformat(),
            'metadata': {
                'secret_type': secret_type,
                'action': action,
                'organization_id': organization_id,
                'actor_type': 'user' if user_id else 'system',
                'compliance_flags': compliance_flags or [],
                'checksum': self._calculate_checksum({
                    'organization_id': organization_id,
                    'secret_type': secret_type,
                    'action': action
                })
            },
            'contains_phi': True,  # Calendar data may contain PHI

            # New schema fields (added by migration, optional)
            'organization_id': organization_id,
            'event_type': f'secret_{action}',
            'event_category': 'security_event',
            'event_data': {
                'secret_type': secret_type,
                'action': action,
                'timestamp': timestamp.isoformat(),
                'user_id': user_id,
                'actor_type': 'user' if user_id else 'system',
                'compliance_flags': compliance_flags or []
            },
            'resource_type': 'secret',
            'resource_id': secret_type,
            'created_at': timestamp.isoformat()
        }

        self.supabase.table('audit_logs').insert(audit_entry).execute()

    def _calculate_checksum(self, data: Dict[str, Any]) -> str:
        """Calculate SHA-256 checksum for audit integrity"""
        data_str = json.dumps(data, sort_keys=True)
        return hashlib.sha256(data_str.encode()).hexdigest()

    async def rotate_credentials(
        self,
        organization_id: str,
        provider: str,
        new_credentials: Dict[str, Any],
        user_id: Optional[str] = None
    ) -> str:
        """
        Rotate credentials for a calendar provider
        Maintains audit trail of rotation
        """
        # Store new credentials
        new_ref = await self.store_calendar_credentials(
            organization_id, provider, new_credentials, user_id
        )

        # Note: organization_secrets table is deprecated - vault_storage is the source of truth
        # Rotation tracking is implicit via created_at in vault_storage

        # Audit the rotation (optional)
        try:
            await self._audit_secret_operation(
                action='rotate',
                organization_id=organization_id,
                secret_type=f'calendar_{provider}',
                user_id=user_id,
                compliance_flags=['HIPAA', 'SOC2', 'GDPR', 'ROTATION']
            )
        except Exception as audit_error:
            logger.warning(f"Could not create audit log (non-critical): {audit_error}")

        return new_ref
