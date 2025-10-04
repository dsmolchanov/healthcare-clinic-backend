"""
Security middleware for webhook validation and request protection.
"""

import hmac
import hashlib
from typing import Optional, Dict, Any, Callable
from datetime import datetime, timezone
from app.security import verify_twilio_signature
from app.services.audit_logger import get_audit_logger, AuditEventType, AuditSeverity
from app.rate_limiter import RateLimiter

class SecurityMiddleware:
    """Middleware for security enforcement"""

    def __init__(self,
                 rate_limiter: Optional[RateLimiter] = None,
                 require_signature: bool = True,
                 audit_logger=None):
        self.rate_limiter = rate_limiter or RateLimiter()
        self.require_signature = require_signature
        self.audit_logger = audit_logger or get_audit_logger()

    async def validate_webhook(
        self,
        request_data: Dict[str, Any],
        signature: Optional[str],
        url: str,
        auth_token: Optional[str] = None
    ) -> bool:
        """
        Validate incoming webhook request.

        Args:
            request_data: The request body/data
            signature: The signature header value
            url: The webhook URL
            auth_token: Authentication token for validation

        Returns:
            True if valid, False otherwise
        """
        if not self.require_signature:
            return True

        if not signature:
            await self.audit_logger.log_webhook_event(
                success=False,
                source="unknown",
                details={"reason": "missing_signature"}
            )
            return False

        # Verify signature using the security module
        is_valid = verify_twilio_signature(
            auth_token or "",
            signature,
            url,
            request_data
        )

        if not is_valid:
            await self.audit_logger.log_webhook_event(
                success=False,
                source="twilio",
                details={"reason": "invalid_signature"}
            )
        else:
            await self.audit_logger.log_webhook_event(
                success=True,
                source="twilio"
            )

        return is_valid

    async def check_rate_limit(
        self,
        identifier: str,
        max_requests: int = 30,
        window_seconds: int = 60
    ) -> bool:
        """
        Check if request is within rate limits.

        Args:
            identifier: Unique identifier (e.g., phone number, IP)
            max_requests: Maximum requests allowed
            window_seconds: Time window in seconds

        Returns:
            True if within limits, False if exceeded
        """
        is_allowed = await self.rate_limiter.is_allowed(
            identifier,
            max_requests,
            window_seconds
        )

        if not is_allowed:
            await self.audit_logger.log_event(
                event_type=AuditEventType.RATE_LIMIT_EXCEEDED,
                action=f"Rate limit exceeded for {identifier}",
                result="blocked",
                severity=AuditSeverity.WARNING,
                details={
                    "identifier": identifier,
                    "max_requests": max_requests,
                    "window_seconds": window_seconds
                }
            )

        return is_allowed

    async def process_request(
        self,
        request_handler: Callable,
        request_data: Dict[str, Any],
        signature: Optional[str] = None,
        url: Optional[str] = None,
        identifier: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Process a request through security checks.

        Args:
            request_handler: The actual request handler function
            request_data: Request data
            signature: Request signature for validation
            url: Request URL
            identifier: Identifier for rate limiting

        Returns:
            Response from handler or error response
        """
        # Check rate limiting if identifier provided
        if identifier:
            if not await self.check_rate_limit(identifier):
                return {
                    'error': 'Rate limit exceeded',
                    'status': 429
                }

        # Validate webhook signature if required
        if self.require_signature and url:
            if not await self.validate_webhook(request_data, signature, url):
                return {
                    'error': 'Invalid signature',
                    'status': 401
                }

        # Process the request
        try:
            response = await request_handler(request_data)
            return response
        except Exception as e:
            await self.audit_logger.log_event(
                event_type=AuditEventType.SECURITY_ALERT,
                action="Request processing failed",
                result="error",
                severity=AuditSeverity.ERROR,
                details={"error": str(e)}
            )
            return {
                'error': 'Internal server error',
                'status': 500
            }

    def require_https(self, url: str) -> bool:
        """Check if URL uses HTTPS"""
        return url.startswith('https://')

    def sanitize_input(self, data: Any) -> Any:
        """
        Sanitize input data to prevent injection attacks.

        Args:
            data: Input data to sanitize

        Returns:
            Sanitized data
        """
        if isinstance(data, str):
            # Remove potential SQL injection patterns
            dangerous_patterns = ['--', ';', '/*', '*/', 'xp_', 'sp_', 'exec', 'execute']
            sanitized = data
            for pattern in dangerous_patterns:
                sanitized = sanitized.replace(pattern, '')

            # Remove potential XSS patterns
            xss_patterns = ['<script', '</script>', 'javascript:', 'onerror=', 'onclick=']
            for pattern in xss_patterns:
                sanitized = sanitized.replace(pattern, '')

            return sanitized
        elif isinstance(data, dict):
            return {k: self.sanitize_input(v) for k, v in data.items()}
        elif isinstance(data, list):
            return [self.sanitize_input(item) for item in data]
        else:
            return data

async def verify_webhook_security(
    request_data: Dict[str, Any],
    headers: Dict[str, str],
    url: str,
    auth_token: Optional[str] = None
) -> bool:
    """
    Verify webhook security (convenience function).

    Args:
        request_data: Request body data
        headers: Request headers
        url: Webhook URL
        auth_token: Authentication token

    Returns:
        True if secure, False otherwise
    """
    middleware = SecurityMiddleware()

    # Get signature from headers
    signature = headers.get('X-Twilio-Signature') or headers.get('x-twilio-signature')

    return await middleware.validate_webhook(
        request_data,
        signature,
        url,
        auth_token
    )
