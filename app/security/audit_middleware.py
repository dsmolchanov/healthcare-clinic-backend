"""
HIPAA Audit Logging Middleware
Phase 5: HIPAA Compliance Restoration

Automatic audit logging middleware for all PHI access and healthcare operations
Integrates with FastAPI to provide transparent audit trails
"""

import asyncio
import json
import logging
import time
from datetime import datetime
from typing import Callable, Optional, Dict, Any, List
from urllib.parse import urlparse

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from .hipaa_audit_system import (
    HIPAAAuditSystem,
    AuditEventType,
    AuditResult,
    RiskLevel,
    get_audit_system
)
from .phi_encryption import get_encryption_system

logger = logging.getLogger(__name__)

class HIPAAAuditMiddleware(BaseHTTPMiddleware):
    """
    FastAPI middleware for automatic HIPAA audit logging
    Logs all requests that access PHI or perform healthcare operations
    """

    def __init__(self, app, audit_system: Optional[HIPAAAuditSystem] = None):
        super().__init__(app)
        self.audit_system = audit_system or get_audit_system()

        # Define routes that access PHI and their audit types
        self.phi_routes = {
            "/api/appointments": AuditEventType.APPOINTMENT_VIEW,
            "/api/appointments/book": AuditEventType.APPOINTMENT_BOOK,
            "/api/appointments/cancel": AuditEventType.APPOINTMENT_CANCEL,
            "/api/appointments/reschedule": AuditEventType.APPOINTMENT_RESCHEDULE,
            "/api/patients": AuditEventType.PHI_ACCESS,
            "/api/patients/search": AuditEventType.PATIENT_SEARCH,
            "/api/medical-records": AuditEventType.PHI_ACCESS,
            "/api/reports": AuditEventType.REPORT_GENERATE,
            "/api/admin": AuditEventType.ADMIN_ACTION,
            "/api/smart-scheduling": AuditEventType.APPOINTMENT_VIEW,
            "/webhooks": AuditEventType.SYSTEM_ACCESS
        }

        # Routes that should always be audited regardless of PHI content
        self.always_audit_routes = {
            "/api/auth/login": AuditEventType.LOGIN,
            "/api/auth/logout": AuditEventType.LOGOUT,
            "/api/backup": AuditEventType.DATA_BACKUP,
            "/api/restore": AuditEventType.DATA_RESTORE
        }

        # Exclude routes from auditing (health checks, static files, etc.)
        self.excluded_routes = {
            "/health", "/docs", "/openapi.json", "/favicon.ico", "/static"
        }

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        """Process request and response with audit logging"""

        # Skip excluded routes
        if any(request.url.path.startswith(route) for route in self.excluded_routes):
            return await call_next(request)

        # Extract request information
        start_time = time.time()
        request_info = await self._extract_request_info(request)

        # Determine if this request should be audited
        audit_event_type = self._determine_audit_event_type(request)

        if not audit_event_type:
            # No auditing required, process normally
            return await call_next(request)

        # Process request and get response
        response = None
        error = None
        result = AuditResult.SUCCESS

        try:
            response = await call_next(request)

            # Determine result based on status code
            if response.status_code >= 400:
                if response.status_code == 401 or response.status_code == 403:
                    result = AuditResult.DENIED
                else:
                    result = AuditResult.FAILURE

        except Exception as e:
            error = str(e)
            result = AuditResult.ERROR
            # Create error response
            response = Response(
                content=f"Internal server error: {error}",
                status_code=500
            )

        # Calculate request duration
        duration_ms = int((time.time() - start_time) * 1000)

        # Extract response information
        response_info = await self._extract_response_info(response, request)

        # Log audit event
        try:
            await self._log_audit_event(
                event_type=audit_event_type,
                request_info=request_info,
                response_info=response_info,
                result=result,
                duration_ms=duration_ms,
                error=error
            )
        except Exception as e:
            logger.error(f"Failed to log audit event: {str(e)}")

        return response

    async def _extract_request_info(self, request: Request) -> Dict[str, Any]:
        """Extract relevant information from the request"""
        # Get client IP (handle proxies)
        client_ip = request.client.host if request.client else "unknown"
        if "x-forwarded-for" in request.headers:
            client_ip = request.headers["x-forwarded-for"].split(",")[0].strip()
        elif "x-real-ip" in request.headers:
            client_ip = request.headers["x-real-ip"]

        # Extract user information from headers or auth
        user_id = "anonymous"
        user_role = "unknown"
        session_id = "unknown"
        organization_id = "default"

        # Check for authentication headers
        if "authorization" in request.headers:
            # In a real implementation, you would decode the JWT token here
            user_id = "authenticated_user"  # Extract from token
            user_role = "user"  # Extract from token
            session_id = "session_from_token"  # Extract from token

        # Check for custom headers
        if "x-user-id" in request.headers:
            user_id = request.headers["x-user-id"]
        if "x-user-role" in request.headers:
            user_role = request.headers["x-user-role"]
        if "x-session-id" in request.headers:
            session_id = request.headers["x-session-id"]
        if "x-organization-id" in request.headers:
            organization_id = request.headers["x-organization-id"]

        # Try to read request body for PHI detection
        # DISABLED: Reading body here prevents endpoints from reading it
        # TODO: Implement proper body caching using receive() wrapper
        body = None
        phi_elements = []
        patient_id = None

        # Skip body reading for now to avoid consuming the stream
        # The middleware still audits the request based on URL/headers
        logger.debug("Skipping request body reading in audit middleware to preserve stream")

        # Extract patient ID from URL path
        if not patient_id:
            path_parts = request.url.path.split("/")
            if "patients" in path_parts:
                try:
                    patient_index = path_parts.index("patients")
                    if patient_index + 1 < len(path_parts):
                        patient_id = path_parts[patient_index + 1]
                except (ValueError, IndexError):
                    pass

        return {
            "method": request.method,
            "path": request.url.path,
            "query_params": dict(request.query_params),
            "headers": dict(request.headers),
            "user_id": user_id,
            "user_role": user_role,
            "session_id": session_id,
            "organization_id": organization_id,
            "client_ip": client_ip,
            "user_agent": request.headers.get("user-agent", "unknown"),
            "patient_id": patient_id,
            "phi_elements": phi_elements,
            "body_size": len(body) if body else 0
        }

    async def _extract_response_info(self, response: Response, request: Request) -> Dict[str, Any]:
        """Extract relevant information from the response"""
        response_info = {
            "status_code": response.status_code,
            "headers": dict(response.headers),
            "content_length": response.headers.get("content-length", 0)
        }

        # Try to determine data volume from response
        data_volume = 0

        # Check content-length header
        if "content-length" in response.headers:
            try:
                data_volume = int(response.headers["content-length"])
            except ValueError:
                pass

        # For JSON responses, try to count records
        if "application/json" in response.headers.get("content-type", ""):
            try:
                # This is a simplified estimation
                # In practice, you might want to parse the response body
                if "appointments" in request.url.path:
                    # Estimate based on typical appointment record size
                    data_volume = max(1, data_volume // 500)  # ~500 bytes per appointment
                elif "patients" in request.url.path:
                    # Estimate based on typical patient record size
                    data_volume = max(1, data_volume // 1000)  # ~1KB per patient
                else:
                    data_volume = max(1, data_volume // 100)  # Default estimation

            except Exception:
                data_volume = 1

        response_info["estimated_records"] = data_volume
        return response_info

    def _determine_audit_event_type(self, request: Request) -> Optional[AuditEventType]:
        """Determine the type of audit event for this request"""

        # Check always-audit routes first
        for route, event_type in self.always_audit_routes.items():
            if request.url.path.startswith(route):
                return event_type

        # Check PHI routes
        for route, event_type in self.phi_routes.items():
            if request.url.path.startswith(route):
                # Determine specific event type based on method
                if request.method == "POST":
                    if "book" in request.url.path:
                        return AuditEventType.APPOINTMENT_BOOK
                    elif "cancel" in request.url.path:
                        return AuditEventType.APPOINTMENT_CANCEL
                    elif "reschedule" in request.url.path:
                        return AuditEventType.APPOINTMENT_RESCHEDULE
                    else:
                        return AuditEventType.PHI_CREATE
                elif request.method == "PUT" or request.method == "PATCH":
                    return AuditEventType.PHI_UPDATE
                elif request.method == "DELETE":
                    return AuditEventType.PHI_DELETE
                else:  # GET
                    return event_type

        # Check for bulk operations
        if "bulk" in request.url.path or "batch" in request.url.path:
            return AuditEventType.BULK_OPERATION

        # If no specific route matches, check if request contains PHI
        # This would require parsing the request body, which is handled in extract_request_info

        return None

    async def _log_audit_event(
        self,
        event_type: AuditEventType,
        request_info: Dict[str, Any],
        response_info: Dict[str, Any],
        result: AuditResult,
        duration_ms: int,
        error: Optional[str] = None
    ):
        """Log the audit event using the audit system"""

        if not self.audit_system:
            logger.warning("Audit system not available, skipping audit log")
            return

        # Generate reason based on request
        reason = f"{request_info['method']} {request_info['path']}"
        if error:
            reason += f" - Error: {error}"

        # Determine resource accessed
        resource_accessed = request_info['path']
        if request_info['patient_id']:
            resource_accessed += f" (Patient: {request_info['patient_id']})"

        # Log the audit event
        await self.audit_system.log_audit_event(
            event_type=event_type,
            user_id=request_info['user_id'],
            user_role=request_info['user_role'],
            patient_id=request_info['patient_id'],
            result=result,
            resource_accessed=resource_accessed,
            ip_address=request_info['client_ip'],
            user_agent=request_info['user_agent'],
            session_id=request_info['session_id'],
            organization_id=request_info['organization_id'],
            reason=reason,
            phi_elements=request_info['phi_elements'],
            data_volume=response_info.get('estimated_records', 0),
            duration_ms=duration_ms,
            metadata={
                "method": request_info['method'],
                "status_code": response_info['status_code'],
                "query_params": request_info['query_params'],
                "body_size": request_info['body_size'],
                "response_size": response_info.get('content_length', 0),
                "error": error
            }
        )

class AuditContextManager:
    """Context manager for manual audit logging within application code"""

    def __init__(
        self,
        event_type: AuditEventType,
        user_id: str,
        user_role: str = "unknown",
        patient_id: Optional[str] = None,
        resource: str = "",
        reason: str = "",
        audit_system: Optional[HIPAAAuditSystem] = None
    ):
        self.event_type = event_type
        self.user_id = user_id
        self.user_role = user_role
        self.patient_id = patient_id
        self.resource = resource
        self.reason = reason
        self.audit_system = audit_system or get_audit_system()
        self.start_time = None
        self.result = AuditResult.SUCCESS
        self.error = None

    async def __aenter__(self):
        self.start_time = time.time()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        # Determine result based on exception
        if exc_type:
            self.result = AuditResult.ERROR
            self.error = str(exc_val)

        # Calculate duration
        duration_ms = int((time.time() - self.start_time) * 1000) if self.start_time else 0

        # Log audit event
        if self.audit_system:
            try:
                await self.audit_system.log_audit_event(
                    event_type=self.event_type,
                    user_id=self.user_id,
                    user_role=self.user_role,
                    patient_id=self.patient_id,
                    result=self.result,
                    resource_accessed=self.resource,
                    ip_address="internal",
                    user_agent="application",
                    session_id="internal",
                    organization_id="default",
                    reason=self.reason,
                    duration_ms=duration_ms,
                    metadata={"error": self.error} if self.error else {}
                )
            except Exception as e:
                logger.error(f"Failed to log audit event in context manager: {str(e)}")

    def set_result(self, result: AuditResult):
        """Manually set the audit result"""
        self.result = result

# Decorator for automatic function-level audit logging
def audit_phi_operation(
    event_type: AuditEventType,
    resource: str = "",
    extract_patient_id: Callable = None,
    extract_user_info: Callable = None
):
    """
    Decorator for automatic audit logging of PHI operations

    Args:
        event_type: Type of audit event
        resource: Resource being accessed
        extract_patient_id: Function to extract patient_id from arguments
        extract_user_info: Function to extract user info from arguments
    """
    def decorator(func):
        async def async_wrapper(*args, **kwargs):
            # Extract audit information
            patient_id = None
            user_id = "system"
            user_role = "application"

            if extract_patient_id:
                try:
                    patient_id = extract_patient_id(*args, **kwargs)
                except Exception:
                    pass

            if extract_user_info:
                try:
                    user_info = extract_user_info(*args, **kwargs)
                    user_id = user_info.get("user_id", user_id)
                    user_role = user_info.get("user_role", user_role)
                except Exception:
                    pass

            # Use audit context manager
            async with AuditContextManager(
                event_type=event_type,
                user_id=user_id,
                user_role=user_role,
                patient_id=patient_id,
                resource=resource or func.__name__,
                reason=f"Function call: {func.__name__}"
            ) as audit_ctx:
                result = await func(*args, **kwargs)

                # Check if function returned an error indication
                if hasattr(result, 'success') and not result.success:
                    audit_ctx.set_result(AuditResult.FAILURE)

                return result

        def sync_wrapper(*args, **kwargs):
            # For synchronous functions, create a simple log entry
            # Note: This would need to be adapted for synchronous audit logging
            logger.info(f"PHI Operation: {event_type.value} - {func.__name__}")
            return func(*args, **kwargs)

        # Return appropriate wrapper based on function type
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        else:
            return sync_wrapper

    return decorator

# Utility functions for common audit scenarios
async def audit_patient_access(
    patient_id: str,
    user_id: str,
    user_role: str,
    operation: str,
    audit_system: Optional[HIPAAAuditSystem] = None
):
    """Convenience function for auditing patient record access"""
    audit_sys = audit_system or get_audit_system()
    if audit_sys:
        await audit_sys.log_audit_event(
            event_type=AuditEventType.PHI_ACCESS,
            user_id=user_id,
            user_role=user_role,
            patient_id=patient_id,
            result=AuditResult.SUCCESS,
            resource_accessed=f"patient_record:{patient_id}",
            ip_address="internal",
            user_agent="application",
            session_id="internal",
            organization_id="default",
            reason=f"Patient record access: {operation}"
        )

async def audit_appointment_operation(
    appointment_id: str,
    patient_id: str,
    user_id: str,
    operation: AuditEventType,
    audit_system: Optional[HIPAAAuditSystem] = None
):
    """Convenience function for auditing appointment operations"""
    audit_sys = audit_system or get_audit_system()
    if audit_sys:
        await audit_sys.log_audit_event(
            event_type=operation,
            user_id=user_id,
            user_role="user",
            patient_id=patient_id,
            result=AuditResult.SUCCESS,
            resource_accessed=f"appointment:{appointment_id}",
            ip_address="internal",
            user_agent="application",
            session_id="internal",
            organization_id="default",
            reason=f"Appointment {operation.value}: {appointment_id}"
        )