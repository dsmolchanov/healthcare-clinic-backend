"""API endpoints for permission management."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from app.middleware.auth import require_auth, require_permission, TokenPayload
from app.services.permission_service import get_permission_service

router = APIRouter(prefix="/api/permissions", tags=["permissions"])

class UserPermissionsResponse(BaseModel):
    permissions: List[str]
    role: str

class CheckPermissionRequest(BaseModel):
    action: str

class ClearCacheRequest(BaseModel):
    user_id: Optional[str] = None
    organization_id: Optional[str] = None

@router.get("/me", response_model=UserPermissionsResponse)
async def get_my_permissions(user: TokenPayload = Depends(require_auth)):
    """Get current user's permissions."""
    permission_service = get_permission_service()

    permissions = await permission_service.get_user_permissions(
        user_id=user.sub,
        organization_id=user.organization_id
    )

    return UserPermissionsResponse(
        permissions=permissions,
        role=user.role
    )

@router.post("/check")
async def check_permission(
    request: CheckPermissionRequest,
    user: TokenPayload = Depends(require_auth)
):
    """Check if user has specific permission."""
    permission_service = get_permission_service()

    has_perm = await permission_service.has_permission(
        user_id=user.sub,
        organization_id=user.organization_id,
        action=request.action
    )

    return {"has_permission": has_perm}

@router.post("/cache/clear")
async def clear_permission_cache(
    request: ClearCacheRequest,
    user: TokenPayload = Depends(require_permission("settings:clinic:update"))
):
    """Clear permission cache (admin only)."""
    permission_service = get_permission_service()
    await permission_service.clear_cache(
        user_id=request.user_id,
        organization_id=request.organization_id
    )
    return {"success": True}
