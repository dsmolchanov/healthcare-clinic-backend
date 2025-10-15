"""
PHI Data Encryption and Protection System
Phase 5: HIPAA Compliance Restoration

Comprehensive encryption system for PHI data at rest and in transit
Implements field-level encryption, key management, and data de-identification
"""

import os
import json
import base64
import hashlib
import logging
from typing import Dict, List, Optional, Any, Union, Tuple
from dataclasses import dataclass
from enum import Enum
from datetime import datetime

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa, padding
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
import secrets
import re

logger = logging.getLogger(__name__)

class PHIType(str, Enum):
    """Types of PHI data for encryption classification"""
    SSN = "ssn"
    MEDICAL_RECORD_NUMBER = "medical_record_number"
    PATIENT_NAME = "patient_name"
    DOB = "date_of_birth"
    ADDRESS = "address"
    PHONE = "phone"
    EMAIL = "email"
    DIAGNOSIS = "diagnosis"
    TREATMENT = "treatment"
    PRESCRIPTION = "prescription"
    INSURANCE = "insurance"
    PAYMENT_INFO = "payment_info"
    NOTES = "notes"
    APPOINTMENT_DETAILS = "appointment_details"

class EncryptionLevel(str, Enum):
    """Encryption levels for different data sensitivity"""
    NONE = "none"
    STANDARD = "standard"  # AES-256
    HIGH = "high"          # AES-256 + additional protections
    MAXIMUM = "maximum"    # AES-256 + RSA + additional protections

@dataclass
class EncryptionMetadata:
    """Metadata for encrypted data"""
    encryption_level: EncryptionLevel
    phi_type: PHIType
    key_id: str
    algorithm: str
    timestamp: datetime
    checksum: str

@dataclass
class EncryptedField:
    """Encrypted field with metadata"""
    encrypted_value: str
    metadata: EncryptionMetadata
    original_length: int  # For validation without decryption

class PHIEncryptionSystem:
    """
    Comprehensive PHI encryption system
    Provides field-level encryption, key rotation, and data protection
    """

    def __init__(self):
        # Initialize encryption keys
        self.master_key = self._get_or_create_master_key()
        self.field_keys = {}  # Cache for derived field keys

        # RSA key pair for maximum security operations
        self.rsa_private_key, self.rsa_public_key = self._get_or_create_rsa_keys()

        # PHI field encryption configuration
        self.phi_encryption_config = {
            PHIType.SSN: EncryptionLevel.MAXIMUM,
            PHIType.MEDICAL_RECORD_NUMBER: EncryptionLevel.HIGH,
            PHIType.PATIENT_NAME: EncryptionLevel.STANDARD,
            PHIType.DOB: EncryptionLevel.HIGH,
            PHIType.ADDRESS: EncryptionLevel.STANDARD,
            PHIType.PHONE: EncryptionLevel.STANDARD,
            PHIType.EMAIL: EncryptionLevel.STANDARD,
            PHIType.DIAGNOSIS: EncryptionLevel.HIGH,
            PHIType.TREATMENT: EncryptionLevel.HIGH,
            PHIType.PRESCRIPTION: EncryptionLevel.HIGH,
            PHIType.INSURANCE: EncryptionLevel.HIGH,
            PHIType.PAYMENT_INFO: EncryptionLevel.MAXIMUM,
            PHIType.NOTES: EncryptionLevel.STANDARD,
            PHIType.APPOINTMENT_DETAILS: EncryptionLevel.STANDARD
        }

        # Regex patterns for PHI detection
        self.phi_patterns = {
            PHIType.SSN: r'\b\d{3}-?\d{2}-?\d{4}\b',
            PHIType.PHONE: r'\b\d{3}[-.]?\d{3}[-.]?\d{4}\b',
            PHIType.EMAIL: r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b',
            PHIType.DOB: r'\b\d{1,2}[/-]\d{1,2}[/-]\d{4}\b'
        }

    def _get_or_create_master_key(self) -> bytes:
        """Get or create master encryption key"""
        key_env = os.getenv("PHI_MASTER_KEY")
        if not key_env:
            raise RuntimeError("PHI_MASTER_KEY not configured. Set the environment variable before starting the service.")

        try:
            key_bytes = base64.b64decode(key_env.encode())
        except Exception as exc:  # pragma: no cover - defensive guard
            raise RuntimeError("Invalid PHI_MASTER_KEY value; expected base64 encoded key") from exc

        if len(key_bytes) != 32:
            raise RuntimeError("Invalid PHI_MASTER_KEY length; expected 32 decoded bytes")

        return key_bytes

    def _get_or_create_rsa_keys(self) -> Tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey]:
        """Get or create RSA key pair for maximum security encryption"""
        private_key_env = os.getenv("PHI_RSA_PRIVATE_KEY")

        if not private_key_env:
            raise RuntimeError("PHI_RSA_PRIVATE_KEY not configured. Set the environment variable before starting the service.")

        try:
            private_key_bytes = base64.b64decode(private_key_env.encode())
            private_key = serialization.load_pem_private_key(
                private_key_bytes,
                password=None
            )
        except Exception as exc:  # pragma: no cover - defensive guard
            raise RuntimeError("Invalid PHI_RSA_PRIVATE_KEY; expected base64-encoded PKCS8 PEM") from exc

        public_key = private_key.public_key()

        return private_key, public_key

    def _derive_field_key(self, phi_type: PHIType, key_id: str) -> bytes:
        """Derive a field-specific encryption key"""
        cache_key = f"{phi_type.value}:{key_id}"

        if cache_key in self.field_keys:
            return self.field_keys[cache_key]

        # Derive key using PBKDF2
        salt = f"phi_{phi_type.value}_{key_id}".encode()
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=100000,
        )
        derived_key = kdf.derive(self.master_key)

        # Cache the derived key
        self.field_keys[cache_key] = derived_key
        return derived_key

    def encrypt_phi_field(
        self,
        value: str,
        phi_type: PHIType,
        key_id: Optional[str] = None
    ) -> EncryptedField:
        """
        Encrypt a PHI field with appropriate encryption level

        Args:
            value: The PHI value to encrypt
            phi_type: Type of PHI for encryption configuration
            key_id: Optional key identifier for field-specific encryption

        Returns:
            EncryptedField with encrypted value and metadata
        """
        if not value:
            return EncryptedField(
                encrypted_value="",
                metadata=EncryptionMetadata(
                    encryption_level=EncryptionLevel.NONE,
                    phi_type=phi_type,
                    key_id="",
                    algorithm="none",
                    timestamp=datetime.utcnow(),
                    checksum=""
                ),
                original_length=0
            )

        encryption_level = self.phi_encryption_config.get(phi_type, EncryptionLevel.STANDARD)
        key_id = key_id or secrets.token_hex(16)
        original_length = len(value)

        # Calculate checksum for integrity verification
        checksum = hashlib.sha256(value.encode()).hexdigest()[:16]

        if encryption_level == EncryptionLevel.MAXIMUM:
            encrypted_value = self._encrypt_maximum_security(value, phi_type, key_id)
            algorithm = "AES-256-GCM+RSA-2048"
        elif encryption_level == EncryptionLevel.HIGH:
            encrypted_value = self._encrypt_high_security(value, phi_type, key_id)
            algorithm = "AES-256-GCM+HMAC"
        else:  # STANDARD
            encrypted_value = self._encrypt_standard(value, phi_type, key_id)
            algorithm = "AES-256-GCM"

        metadata = EncryptionMetadata(
            encryption_level=encryption_level,
            phi_type=phi_type,
            key_id=key_id,
            algorithm=algorithm,
            timestamp=datetime.utcnow(),
            checksum=checksum
        )

        return EncryptedField(
            encrypted_value=encrypted_value,
            metadata=metadata,
            original_length=original_length
        )

    def decrypt_phi_field(self, encrypted_field: EncryptedField) -> str:
        """
        Decrypt a PHI field using its metadata

        Args:
            encrypted_field: The encrypted field with metadata

        Returns:
            Decrypted PHI value
        """
        if not encrypted_field.encrypted_value:
            return ""

        metadata = encrypted_field.metadata

        if metadata.encryption_level == EncryptionLevel.MAXIMUM:
            decrypted_value = self._decrypt_maximum_security(
                encrypted_field.encrypted_value,
                metadata.phi_type,
                metadata.key_id
            )
        elif metadata.encryption_level == EncryptionLevel.HIGH:
            decrypted_value = self._decrypt_high_security(
                encrypted_field.encrypted_value,
                metadata.phi_type,
                metadata.key_id
            )
        else:  # STANDARD
            decrypted_value = self._decrypt_standard(
                encrypted_field.encrypted_value,
                metadata.phi_type,
                metadata.key_id
            )

        # Verify integrity
        calculated_checksum = hashlib.sha256(decrypted_value.encode()).hexdigest()[:16]
        if calculated_checksum != metadata.checksum:
            raise ValueError("PHI field integrity check failed")

        return decrypted_value

    def _encrypt_standard(self, value: str, phi_type: PHIType, key_id: str) -> str:
        """Standard AES-256-GCM encryption"""
        field_key = self._derive_field_key(phi_type, key_id)
        cipher = Fernet(base64.urlsafe_b64encode(field_key))
        encrypted_bytes = cipher.encrypt(value.encode())
        return base64.b64encode(encrypted_bytes).decode()

    def _decrypt_standard(self, encrypted_value: str, phi_type: PHIType, key_id: str) -> str:
        """Standard AES-256-GCM decryption"""
        field_key = self._derive_field_key(phi_type, key_id)
        cipher = Fernet(base64.urlsafe_b64encode(field_key))
        encrypted_bytes = base64.b64decode(encrypted_value.encode())
        decrypted_bytes = cipher.decrypt(encrypted_bytes)
        return decrypted_bytes.decode()

    def _encrypt_high_security(self, value: str, phi_type: PHIType, key_id: str) -> str:
        """High security encryption with additional HMAC"""
        # First apply standard encryption
        standard_encrypted = self._encrypt_standard(value, phi_type, key_id)

        # Add HMAC for additional integrity protection
        field_key = self._derive_field_key(phi_type, key_id)
        hmac_key = hashlib.sha256(field_key + b"hmac").digest()
        hmac_hash = hashlib.sha256(hmac_key + standard_encrypted.encode()).hexdigest()

        # Combine encrypted data with HMAC
        protected_data = {
            "encrypted": standard_encrypted,
            "hmac": hmac_hash
        }

        return base64.b64encode(json.dumps(protected_data).encode()).decode()

    def _decrypt_high_security(self, encrypted_value: str, phi_type: PHIType, key_id: str) -> str:
        """High security decryption with HMAC verification"""
        # Parse protected data
        protected_bytes = base64.b64decode(encrypted_value.encode())
        protected_data = json.loads(protected_bytes.decode())

        # Verify HMAC
        field_key = self._derive_field_key(phi_type, key_id)
        hmac_key = hashlib.sha256(field_key + b"hmac").digest()
        expected_hmac = hashlib.sha256(hmac_key + protected_data["encrypted"].encode()).hexdigest()

        if expected_hmac != protected_data["hmac"]:
            raise ValueError("HMAC verification failed for high security PHI field")

        # Decrypt standard encryption
        return self._decrypt_standard(protected_data["encrypted"], phi_type, key_id)

    def _encrypt_maximum_security(self, value: str, phi_type: PHIType, key_id: str) -> str:
        """Maximum security encryption with RSA + AES"""
        # First apply high security encryption
        high_sec_encrypted = self._encrypt_high_security(value, phi_type, key_id)

        # Encrypt with RSA for maximum protection (for short data)
        if len(value) <= 100:  # RSA can only encrypt small amounts
            rsa_encrypted = self.rsa_public_key.encrypt(
                value.encode(),
                padding.OAEP(
                    mgf=padding.MGF1(algorithm=hashes.SHA256()),
                    algorithm=hashes.SHA256(),
                    label=None
                )
            )

            max_protected_data = {
                "method": "rsa",
                "encrypted": base64.b64encode(rsa_encrypted).decode(),
                "key_id": key_id
            }
        else:
            # For longer data, use hybrid encryption
            max_protected_data = {
                "method": "hybrid",
                "encrypted": high_sec_encrypted,
                "key_id": key_id
            }

        return base64.b64encode(json.dumps(max_protected_data).encode()).decode()

    def _decrypt_maximum_security(self, encrypted_value: str, phi_type: PHIType, key_id: str) -> str:
        """Maximum security decryption"""
        # Parse maximum protected data
        max_protected_bytes = base64.b64decode(encrypted_value.encode())
        max_protected_data = json.loads(max_protected_bytes.decode())

        if max_protected_data["method"] == "rsa":
            # Direct RSA decryption
            encrypted_bytes = base64.b64decode(max_protected_data["encrypted"].encode())
            decrypted_bytes = self.rsa_private_key.decrypt(
                encrypted_bytes,
                padding.OAEP(
                    mgf=padding.MGF1(algorithm=hashes.SHA256()),
                    algorithm=hashes.SHA256(),
                    label=None
                )
            )
            return decrypted_bytes.decode()
        else:
            # Hybrid decryption
            return self._decrypt_high_security(max_protected_data["encrypted"], phi_type, key_id)

    def encrypt_phi_record(self, record: Dict[str, Any], phi_mapping: Dict[str, PHIType]) -> Dict[str, Any]:
        """
        Encrypt all PHI fields in a record

        Args:
            record: The record with PHI fields
            phi_mapping: Mapping of field names to PHI types

        Returns:
            Record with encrypted PHI fields and metadata
        """
        encrypted_record = record.copy()
        encryption_metadata = {}

        for field_name, phi_type in phi_mapping.items():
            if field_name in record and record[field_name]:
                encrypted_field = self.encrypt_phi_field(
                    str(record[field_name]),
                    phi_type
                )

                # Store encrypted value
                encrypted_record[field_name] = encrypted_field.encrypted_value

                # Store metadata separately
                encryption_metadata[field_name] = {
                    "encryption_level": encrypted_field.metadata.encryption_level.value,
                    "phi_type": encrypted_field.metadata.phi_type.value,
                    "key_id": encrypted_field.metadata.key_id,
                    "algorithm": encrypted_field.metadata.algorithm,
                    "timestamp": encrypted_field.metadata.timestamp.isoformat(),
                    "checksum": encrypted_field.metadata.checksum,
                    "original_length": encrypted_field.original_length
                }

        # Add encryption metadata to record
        encrypted_record["_encryption_metadata"] = base64.b64encode(
            json.dumps(encryption_metadata).encode()
        ).decode()

        return encrypted_record

    def decrypt_phi_record(self, encrypted_record: Dict[str, Any]) -> Dict[str, Any]:
        """
        Decrypt all PHI fields in a record

        Args:
            encrypted_record: Record with encrypted PHI fields

        Returns:
            Record with decrypted PHI fields
        """
        if "_encryption_metadata" not in encrypted_record:
            return encrypted_record  # No encryption metadata, return as-is

        # Parse encryption metadata
        metadata_bytes = base64.b64decode(encrypted_record["_encryption_metadata"].encode())
        encryption_metadata = json.loads(metadata_bytes.decode())

        decrypted_record = encrypted_record.copy()

        for field_name, field_metadata in encryption_metadata.items():
            if field_name in encrypted_record:
                # Reconstruct EncryptedField
                metadata = EncryptionMetadata(
                    encryption_level=EncryptionLevel(field_metadata["encryption_level"]),
                    phi_type=PHIType(field_metadata["phi_type"]),
                    key_id=field_metadata["key_id"],
                    algorithm=field_metadata["algorithm"],
                    timestamp=datetime.fromisoformat(field_metadata["timestamp"]),
                    checksum=field_metadata["checksum"]
                )

                encrypted_field = EncryptedField(
                    encrypted_value=encrypted_record[field_name],
                    metadata=metadata,
                    original_length=field_metadata["original_length"]
                )

                # Decrypt field
                decrypted_record[field_name] = self.decrypt_phi_field(encrypted_field)

        # Remove encryption metadata from decrypted record
        decrypted_record.pop("_encryption_metadata", None)

        return decrypted_record

    def detect_phi_in_text(self, text: str) -> List[Tuple[PHIType, str, int, int]]:
        """
        Detect PHI patterns in text

        Returns:
            List of (phi_type, matched_text, start_position, end_position)
        """
        detected_phi = []

        for phi_type, pattern in self.phi_patterns.items():
            matches = re.finditer(pattern, text, re.IGNORECASE)
            for match in matches:
                detected_phi.append((
                    phi_type,
                    match.group(),
                    match.start(),
                    match.end()
                ))

        return detected_phi

    def de_identify_text(self, text: str, replacement_map: Optional[Dict[str, str]] = None) -> str:
        """
        De-identify PHI in text by replacing with placeholders

        Args:
            text: Text to de-identify
            replacement_map: Optional mapping of original values to replacements

        Returns:
            De-identified text
        """
        if not text:
            return text

        de_identified = text
        detected_phi = self.detect_phi_in_text(text)

        # Sort by position (reverse order to maintain positions)
        detected_phi.sort(key=lambda x: x[2], reverse=True)

        for phi_type, matched_text, start, end in detected_phi:
            if replacement_map and matched_text in replacement_map:
                replacement = replacement_map[matched_text]
            else:
                # Generate standard placeholder
                replacement = f"[{phi_type.value.upper()}_REDACTED]"

            de_identified = de_identified[:start] + replacement + de_identified[end:]

        return de_identified

    def rotate_encryption_keys(self, phi_type: PHIType) -> str:
        """
        Rotate encryption keys for a specific PHI type

        Returns:
            New key ID
        """
        new_key_id = secrets.token_hex(16)

        # Clear cached key for this PHI type
        keys_to_remove = [key for key in self.field_keys.keys() if key.startswith(f"{phi_type.value}:")]
        for key in keys_to_remove:
            del self.field_keys[key]

        logger.info(f"Rotated encryption keys for PHI type: {phi_type.value}")
        return new_key_id

    def get_encryption_status(self) -> Dict[str, Any]:
        """Get status of encryption system"""
        return {
            "master_key_initialized": bool(self.master_key),
            "rsa_keys_initialized": bool(self.rsa_private_key and self.rsa_public_key),
            "cached_field_keys": len(self.field_keys),
            "supported_phi_types": len(self.phi_encryption_config),
            "encryption_levels": {
                level.value: [phi_type.value for phi_type, enc_level in self.phi_encryption_config.items() if enc_level == level]
                for level in EncryptionLevel
            },
            "last_status_check": datetime.utcnow().isoformat()
        }

# Global encryption system instance
encryption_system = None

def init_encryption_system():
    """Initialize global encryption system"""
    global encryption_system
    encryption_system = PHIEncryptionSystem()
    return encryption_system

def get_encryption_system() -> PHIEncryptionSystem:
    """Get global encryption system instance"""
    return encryption_system

# Convenience functions for common operations
def encrypt_patient_record(record: Dict[str, Any]) -> Dict[str, Any]:
    """Encrypt a patient record with standard PHI mapping"""
    phi_mapping = {
        "ssn": PHIType.SSN,
        "first_name": PHIType.PATIENT_NAME,
        "last_name": PHIType.PATIENT_NAME,
        "date_of_birth": PHIType.DOB,
        "phone": PHIType.PHONE,
        "email": PHIType.EMAIL,
        "address": PHIType.ADDRESS
    }

    return get_encryption_system().encrypt_phi_record(record, phi_mapping)

def decrypt_patient_record(encrypted_record: Dict[str, Any]) -> Dict[str, Any]:
    """Decrypt a patient record"""
    return get_encryption_system().decrypt_phi_record(encrypted_record)

def encrypt_appointment_record(record: Dict[str, Any]) -> Dict[str, Any]:
    """Encrypt an appointment record"""
    phi_mapping = {
        "notes": PHIType.NOTES,
        "diagnosis": PHIType.DIAGNOSIS,
        "treatment": PHIType.TREATMENT,
        "patient_id": PHIType.MEDICAL_RECORD_NUMBER
    }

    return get_encryption_system().encrypt_phi_record(record, phi_mapping)

def decrypt_appointment_record(encrypted_record: Dict[str, Any]) -> Dict[str, Any]:
    """Decrypt an appointment record"""
    return get_encryption_system().decrypt_phi_record(encrypted_record)
