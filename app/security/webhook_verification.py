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
        self.twilio_auth_token = os.getenv("TWILIO_AUTH_TOKEN", "")

        if not self.evolution_secret:
            logger.warning("EVOLUTION_WEBHOOK_SECRET not configured - webhook verification disabled for Evolution")
        if not self.twilio_auth_token:
            logger.warning("TWILIO_AUTH_TOKEN not configured - webhook verification disabled for Twilio")

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

    def verify_twilio_signature(
        self,
        url: str,
        params: dict,
        signature: Optional[str]
    ) -> bool:
        """
        Verify Twilio webhook signature.

        Args:
            url: Full URL of the webhook endpoint
            params: Request parameters
            signature: Signature from X-Twilio-Signature header

        Returns:
            True if signature is valid or verification is disabled, False otherwise
        """
        # If no auth token configured, skip verification (development mode)
        if not self.twilio_auth_token:
            logger.debug("Twilio webhook verification skipped - no auth token configured")
            return True

        if not signature:
            logger.warning("Twilio webhook signature missing")
            return False

        # Import Twilio validator if available
        try:
            from twilio.request_validator import RequestValidator
            validator = RequestValidator(self.twilio_auth_token)
            is_valid = validator.validate(url, params, signature)

            if not is_valid:
                logger.warning("Twilio webhook signature validation failed")

            return is_valid
        except ImportError:
            logger.warning("Twilio library not installed - using fallback verification")
            # Fallback to basic HMAC verification
            # Concatenate URL and sorted parameters
            data = url
            if params:
                for key in sorted(params.keys()):
                    data += key + str(params[key])

            expected_signature = self._calculate_hmac_signature(
                data.encode('utf-8'),
                self.twilio_auth_token,
                use_base64=True
            )

            return hmac.compare_digest(signature, expected_signature)

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
    signature: str = None,
    url: str = None,
    params: dict = None
) -> bool:
    """
    Convenience function to verify webhook signatures.

    Args:
        provider: Webhook provider ('evolution' or 'twilio')
        body: Raw request body (for Evolution)
        signature: Signature header value
        url: Full URL (for Twilio)
        params: Request parameters (for Twilio)

    Returns:
        True if signature is valid or verification is disabled
    """
    if provider.lower() == 'evolution':
        return webhook_verifier.verify_evolution_signature(body, signature)
    elif provider.lower() == 'twilio':
        return webhook_verifier.verify_twilio_signature(url, params, signature)
    else:
        logger.warning(f"Unknown webhook provider: {provider}")
        return False