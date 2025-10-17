"""
Webhook signature verification for secure webhook endpoints.
Implements HMAC-based signature verification to ensure webhook requests are authentic.
"""

import hmac
import hashlib
import os
from typing import Optional
import logging

logger = logging.getLogger(__name__)


class WebhookVerifier:
    """Handles webhook signature verification for different providers"""

    def __init__(self):
        # Get webhook secrets from environment
        self.evolution_secret = os.getenv("EVOLUTION_WEBHOOK_SECRET", "")

        if not self.evolution_secret:
            logger.warning("EVOLUTION_WEBHOOK_SECRET not configured - webhook verification disabled for Evolution")

    def verify_evolution_signature(
        self,
        body: bytes,
        signature: Optional[str]
    ) -> bool:
        """
        Verify Evolution API webhook signature.

        Args:
            body: Raw request body bytes
            signature: Signature from X-Webhook-Signature header

        Returns:
            True if signature is valid or verification is disabled, False otherwise
        """
        # If no secret configured, reject the request (security requirement)
        if not self.evolution_secret:
            logger.error("Evolution webhook verification FAILED - no secret configured")
            return False

        if not signature:
            logger.warning("Evolution webhook signature missing")
            return False

        # Calculate expected signature
        expected_signature = self._calculate_hmac_signature(body, self.evolution_secret)

        # Compare signatures (use hmac.compare_digest for timing attack protection)
        is_valid = hmac.compare_digest(signature, expected_signature)

        if not is_valid:
            logger.warning(f"Evolution webhook signature mismatch - expected: {expected_signature[:10]}..., got: {signature[:10]}...")

        return is_valid

    def _calculate_hmac_signature(
        self,
        body: bytes,
        secret: str,
        use_base64: bool = False
    ) -> str:
        """
        Calculate HMAC-SHA256 signature.

        Args:
            body: Data to sign
            secret: Secret key
            use_base64: Whether to return base64 encoded signature

        Returns:
            Hex or base64 encoded signature
        """
        signature = hmac.new(
            secret.encode('utf-8'),
            body,
            hashlib.sha256
        )

        if use_base64:
            import base64
            return base64.b64encode(signature.digest()).decode('utf-8')
        else:
            return signature.hexdigest()


# Global instance
webhook_verifier = WebhookVerifier()


def verify_webhook_signature(
    provider: str,
    body: bytes = None,
    signature: str = None
) -> bool:
    """
    Convenience function to verify webhook signatures.

    Args:
        provider: Webhook provider (currently only 'evolution' supported)
        body: Raw request body
        signature: Signature header value

    Returns:
        True if signature is valid or verification is disabled
    """
    if provider.lower() == 'evolution':
        return webhook_verifier.verify_evolution_signature(body, signature)
    else:
        logger.warning(f"Unknown webhook provider: {provider}")
        return False