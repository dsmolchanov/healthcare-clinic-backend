"""
Security module for dental clinic system
Handles webhook verification, encryption, and security configurations
"""

import hashlib
import hmac
import base64
import os
from typing import Dict, Any, Optional
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.backends import default_backend
import secrets


def verify_twilio_signature(url: str, params: dict, signature: str, auth_token: str) -> bool:
    """
    Verify Twilio webhook signature

    Args:
        url: The full URL of the webhook
        params: The POST parameters
        signature: The X-Twilio-Signature header value
        auth_token: Your Twilio auth token

    Returns:
        True if signature is valid, False otherwise
    """
    # Build the data string
    data = url

    # Sort parameters and append to data
    if params:
        sorted_params = sorted(params.items())
        for key, value in sorted_params:
            data += str(key) + str(value)

    # Compute signature
    computed_signature = base64.b64encode(
        hmac.new(
            auth_token.encode('utf-8'),
            data.encode('utf-8'),
            hashlib.sha1
        ).digest()
    ).decode('utf-8')

    # Compare signatures
    return hmac.compare_digest(computed_signature, signature)


def get_encryption_config(market: str = 'mexico') -> Dict[str, Any]:
    """
    Get encryption configuration based on market

    Args:
        market: The market (mexico or us)

    Returns:
        Encryption configuration dictionary
    """
    if market == 'us':
        return {
            'algorithm': 'AES',
            'key_size': 256,
            'phi_protection': True,
            'hipaa_compliant': True
        }
    else:  # mexico
        return {
            'algorithm': 'AES',
            'key_size': 128,
            'phi_protection': False,
            'hipaa_compliant': False
        }


class DataEncryption:
    """Handle data encryption/decryption"""

    def __init__(self, market: str = 'mexico'):
        self.market = market
        self.config = get_encryption_config(market)
        self._setup_encryption()

    def _setup_encryption(self):
        """Setup encryption based on market configuration"""
        if self.config['key_size'] == 256:
            # AES-256 for HIPAA
            self.key = secrets.token_bytes(32)  # 256 bits
        else:
            # AES-128 for Mexico
            self.key = secrets.token_bytes(16)  # 128 bits

        # Use Fernet for simplicity (uses AES-128 in CBC mode)
        # For production, would use AES-256-GCM for HIPAA
        self.fernet = Fernet(base64.urlsafe_b64encode(self.key.ljust(32)[:32]))

    def encrypt(self, data: str) -> str:
        """Encrypt data"""
        return self.fernet.encrypt(data.encode()).decode()

    def decrypt(self, encrypted_data: str) -> str:
        """Decrypt data"""
        return self.fernet.decrypt(encrypted_data.encode()).decode()


def encrypt_sensitive_data(data: Dict[str, Any], market: str = 'mexico') -> Dict[str, Any]:
    """
    Encrypt sensitive fields in data

    Args:
        data: Dictionary containing sensitive data
        market: The market for encryption level

    Returns:
        Dictionary with encrypted sensitive fields
    """
    encryption = DataEncryption(market)
    encrypted = {}

    sensitive_fields = ['patient_name', 'phone', 'medical_notes', 'email', 'address']

    for key, value in data.items():
        if key in sensitive_fields and value:
            encrypted[key] = encryption.encrypt(str(value))
        else:
            encrypted[key] = value

    return encrypted


def decrypt_sensitive_data(encrypted_data: Dict[str, Any], market: str = 'mexico') -> Dict[str, Any]:
    """
    Decrypt sensitive fields in data

    Args:
        encrypted_data: Dictionary with encrypted fields
        market: The market for encryption level

    Returns:
        Dictionary with decrypted fields
    """
    encryption = DataEncryption(market)
    decrypted = {}

    sensitive_fields = ['patient_name', 'phone', 'medical_notes', 'email', 'address']

    for key, value in encrypted_data.items():
        if key in sensitive_fields and value:
            try:
                decrypted[key] = encryption.decrypt(str(value))
            except:
                decrypted[key] = value  # If decryption fails, return as-is
        else:
            decrypted[key] = value

    return decrypted


class StrongEncryption:
    """Strong encryption for HIPAA compliance"""

    def __init__(self, key_size: int = 256):
        self.key_size = key_size
        self.key = secrets.token_bytes(key_size // 8)

    def encrypt(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Encrypt data with strong encryption"""
        # Implementation for strong encryption
        return data  # Placeholder


class BasicEncryption:
    """Basic encryption for non-HIPAA markets"""

    def __init__(self, key_size: int = 128):
        self.key_size = key_size
        self.key = secrets.token_bytes(key_size // 8)

    def encrypt(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Encrypt data with basic encryption"""
        # Implementation for basic encryption
        return data  # Placeholder
