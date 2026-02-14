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

# Get JWT secret - use Supabase JWT secret for verifying Supabase tokens
# Falls back to JWT_SECRET for backward compatibility
JWT_SECRET = os.getenv("SUPABASE_JWT_SECRET") or os.getenv("JWT_SECRET", "development-secret-change-in-production")
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")


class TokenPayload:
    """Decoded JWT token payload."""
    def __init__(self, payload: dict):
        self.sub = payload.get("sub")  # Subject (user ID)

        # Supabase puts custom claims in user_metadata, check there first
        user_metadata = payload.get("user_metadata", {}) or {}
        app_metadata = payload.get("app_metadata", {}) or {}

        # Try user_metadata first, then app_metadata, then root level
        self.clinic_id = (
            user_metadata.get("clinic_id") or
            app_metadata.get("clinic_id") or
            payload.get("clinic_id")
        )
        self.organization_id = (
            user_metadata.get("organization_id") or
            app_metadata.get("organization_id") or
            payload.get("organization_id")
        )
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
            algorithms=[JWT_ALGORITHM],
            audience="authenticated"  # Supabase sets aud to "authenticated"
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


# Permission-based access control
def require_permission(action: str):
    """
    Factory for permission-based access control.

    Usage:
        @app.post("/api/settings/billing")
        async def update_billing(
            user: TokenPayload = Depends(require_permission("settings:billing:update"))
        ):
            return {"success": True}
    """
    async def permission_checker(payload: TokenPayload = Depends(require_auth)) -> TokenPayload:
        from app.services.permission_service import get_permission_service
        permission_service = get_permission_service()

        has_perm = await permission_service.has_permission(
            user_id=payload.sub,
            organization_id=payload.organization_id,
            action=action
        )

        if not has_perm:
            logger.warning(
                f"Permission denied: user {payload.sub} lacks '{action}'"
            )
            raise HTTPException(
                status_code=403,
                detail=f"Missing required permission: {action}"
            )

        return payload

    return permission_checker


def require_superadmin():
    """
    Factory for superadmin-only access control.

    Checks `is_superadmin = TRUE` in `sales.team_members` for the authenticated user.
    Stricter than require_permission() — even users with all permissions but no
    is_superadmin flag are blocked.

    Usage:
        @app.get("/api/superadmin/overview")
        async def overview(user: TokenPayload = Depends(require_superadmin())):
            return {"data": "..."}
    """
    async def superadmin_checker(payload: TokenPayload = Depends(require_auth)) -> TokenPayload:
        from app.services.database_manager import get_database_manager, DatabaseType
        db_manager = get_database_manager()
        supabase = db_manager.get_client(DatabaseType.MAIN)

        result = supabase.schema('sales').table('team_members') \
            .select('is_superadmin') \
            .eq('user_id', payload.sub) \
            .eq('is_superadmin', True) \
            .execute()

        if not result.data:
            logger.warning(f"Superadmin access denied for user {payload.sub}")
            raise HTTPException(
                status_code=403,
                detail="Superadmin access required"
            )
        return payload

    return superadmin_checker


def require_any_permission(*actions: str):
    """
    Factory for checking if user has any of the specified permissions.

    Usage:
        @app.get("/api/dashboard")
        async def dashboard(
            user: TokenPayload = Depends(require_any_permission(
                "reports:view", "appointments:view"
            ))
        ):
            return {"data": "..."}
    """
    async def permission_checker(payload: TokenPayload = Depends(require_auth)) -> TokenPayload:
        from app.services.permission_service import get_permission_service
        permission_service = get_permission_service()

        has_any = await permission_service.has_any_permission(
            user_id=payload.sub,
            organization_id=payload.organization_id,
            actions=list(actions)
        )

        if not has_any:
            logger.warning(
                f"Permission denied: user {payload.sub} lacks any of {actions}"
            )
            raise HTTPException(
                status_code=403,
                detail=f"Missing required permissions: {', '.join(actions)}"
            )

        return payload

    return permission_checker
