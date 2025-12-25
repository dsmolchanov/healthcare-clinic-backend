"""
Authentication middleware for API endpoints.

Phase 5 Implementation - JWT-based authentication.
Uses PyJWT for token verification (not python-jose).

JWT_SECRET is validated at startup (Phase 1), so we can trust it exists here.
"""
import os
import logging
from typing import Optional

import jwt  # PyJWT
from fastapi import Request, HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

logger = logging.getLogger(__name__)

security = HTTPBearer(auto_error=False)

# Get JWT secret - validated at startup by startup_validation.py
# In development, falls back to a default (insecure for prod)
JWT_SECRET = os.getenv("JWT_SECRET", "development-secret-change-in-production")
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")


class TokenPayload:
    """Decoded JWT token payload."""
    def __init__(self, payload: dict):
        self.sub = payload.get("sub")  # Subject (user ID)
        self.clinic_id = payload.get("clinic_id")
        self.organization_id = payload.get("organization_id")
        self.role = payload.get("role", "user")
        self.exp = payload.get("exp")
        self.iat = payload.get("iat")
        self._raw = payload

    def __repr__(self) -> str:
        return f"TokenPayload(sub={self.sub}, role={self.role})"


async def verify_token(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security)
) -> Optional[TokenPayload]:
    """
    Verify JWT token from Authorization header.

    Returns:
        TokenPayload if valid token provided, None if no token.

    Raises:
        HTTPException: For invalid or expired tokens.
    """
    if not credentials:
        return None

    token = credentials.credentials

    try:
        payload = jwt.decode(
            token,
            JWT_SECRET,
            algorithms=[JWT_ALGORITHM]
        )
        return TokenPayload(payload)

    except jwt.ExpiredSignatureError:
        logger.warning("Token expired")
        raise HTTPException(
            status_code=401,
            detail="Token expired",
            headers={"WWW-Authenticate": "Bearer"}
        )

    except jwt.InvalidTokenError as e:
        logger.warning(f"Invalid token: {e}")
        raise HTTPException(
            status_code=401,
            detail="Invalid token",
            headers={"WWW-Authenticate": "Bearer"}
        )


def require_auth(
    payload: Optional[TokenPayload] = Depends(verify_token)
) -> TokenPayload:
    """
    Dependency that requires valid authentication.

    Usage:
        @app.post("/api/protected")
        async def protected_endpoint(user: TokenPayload = Depends(require_auth)):
            return {"user_id": user.sub}
    """
    if not payload:
        raise HTTPException(
            status_code=401,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"}
        )
    return payload


def require_role(allowed_roles: list[str]):
    """
    Factory for role-based access control dependency.

    Usage:
        @app.post("/api/admin")
        async def admin_endpoint(user: TokenPayload = Depends(require_role(["admin"]))):
            return {"admin_id": user.sub}
    """
    def role_checker(payload: TokenPayload = Depends(require_auth)) -> TokenPayload:
        if payload.role not in allowed_roles:
            logger.warning(f"Access denied: role {payload.role} not in {allowed_roles}")
            raise HTTPException(
                status_code=403,
                detail=f"Required role: {', '.join(allowed_roles)}"
            )
        return payload
    return role_checker


def require_clinic_access(
    clinic_id: str,
    payload: TokenPayload = Depends(require_auth)
) -> TokenPayload:
    """
    Verify user has access to specific clinic.

    Checks if the token's clinic_id matches the requested clinic,
    or if the user has admin role (can access any clinic).
    """
    if payload.role == "admin":
        return payload

    if payload.clinic_id != clinic_id:
        logger.warning(
            f"Clinic access denied: user clinic {payload.clinic_id} "
            f"!= requested {clinic_id}"
        )
        raise HTTPException(
            status_code=403,
            detail="Access to this clinic is not permitted"
        )

    return payload


# Optional: Get current user without requiring auth
async def get_current_user_optional(
    payload: Optional[TokenPayload] = Depends(verify_token)
) -> Optional[TokenPayload]:
    """
    Get current user if authenticated, None otherwise.

    Use for endpoints that work with or without auth but may behave differently.
    """
    return payload
