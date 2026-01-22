"""API endpoints for sales team member management."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field
from typing import Optional, List, Literal
from app.middleware.auth import require_auth, TokenPayload
from app.services.database_manager import get_database_manager, DatabaseType
from app.api.sales_invitations_api import get_sales_org_for_user
import logging

router = APIRouter(prefix="/api/sales/members", tags=["sales-members"])
logger = logging.getLogger(__name__)


class MemberUpdate(BaseModel):
    name: Optional[str] = None
    role: Optional[Literal['admin', 'manager', 'rep']] = None
    team_id: Optional[str] = None  # Can be null to remove from team
    phone: Optional[str] = Field(None, max_length=20)
    email: Optional[EmailStr] = None
    whatsapp_number: Optional[str] = Field(None, max_length=20)
    preferred_channel: Optional[Literal['email', 'phone', 'whatsapp']] = None
    title: Optional[str] = Field(None, max_length=100)
    avatar_url: Optional[str] = None


class MemberResponse(BaseModel):
    id: str
    user_id: str
    name: Optional[str]
    email: Optional[str]
    role: str
    team_id: Optional[str]
    team_name: Optional[str]
    phone: Optional[str]
    whatsapp_number: Optional[str]
    preferred_channel: str
    title: Optional[str]
    avatar_url: Optional[str]
    is_superadmin: bool
    created_at: str


@router.get("", response_model=List[MemberResponse])
async def list_members(
    team_id: Optional[str] = None,
    user: TokenPayload = Depends(require_auth)
):
    """List all members in the user's organization, optionally filtered by team."""
    db_manager = get_database_manager()
    supabase = db_manager.get_client(DatabaseType.MAIN)

    membership = get_sales_org_for_user(supabase, user.sub)
    if not membership:
        raise HTTPException(status_code=403, detail="Not a member of any sales organization")

    # Build query with team join - exclude superadmins from regular member lists
    query = supabase.schema('sales').table('team_members')\
        .select('*, teams:team_id(name)')\
        .eq('organization_id', membership['organization_id'])\
        .eq('is_superadmin', False)\
        .order('name')

    if team_id:
        query = query.eq('team_id', team_id)

    result = query.execute()

    members = []
    for m in result.data:
        # Get user email from auth if not stored
        user_email = m.get('email')
        if not user_email:
            try:
                auth_user = supabase.auth.admin.get_user_by_id(m['user_id'])
                user_email = auth_user.user.email if auth_user.user else None
            except Exception:
                pass

        members.append(MemberResponse(
            id=m['id'],
            user_id=m['user_id'],
            name=m.get('name'),
            email=user_email,
            role=m['role'],
            team_id=m.get('team_id'),
            team_name=m.get('teams', {}).get('name') if m.get('teams') else None,
            phone=m.get('phone'),
            whatsapp_number=m.get('whatsapp_number'),
            preferred_channel=m.get('preferred_channel', 'email'),
            title=m.get('title'),
            avatar_url=m.get('avatar_url'),
            is_superadmin=m.get('is_superadmin', False),
            created_at=m['created_at']
        ))

    return members


@router.get("/{member_id}", response_model=MemberResponse)
async def get_member(member_id: str, user: TokenPayload = Depends(require_auth)):
    """Get a specific member's details."""
    db_manager = get_database_manager()
    supabase = db_manager.get_client(DatabaseType.MAIN)

    membership = get_sales_org_for_user(supabase, user.sub)
    if not membership:
        raise HTTPException(status_code=403, detail="Not a member of any sales organization")

    result = supabase.schema('sales').table('team_members')\
        .select('*, teams:team_id(name)')\
        .eq('id', member_id)\
        .eq('organization_id', membership['organization_id'])\
        .single()\
        .execute()

    if not result.data:
        raise HTTPException(status_code=404, detail="Member not found")

    m = result.data
    user_email = m.get('email')
    if not user_email:
        try:
            auth_user = supabase.auth.admin.get_user_by_id(m['user_id'])
            user_email = auth_user.user.email if auth_user.user else None
        except Exception:
            pass

    return MemberResponse(
        id=m['id'],
        user_id=m['user_id'],
        name=m.get('name'),
        email=user_email,
        role=m['role'],
        team_id=m.get('team_id'),
        team_name=m.get('teams', {}).get('name') if m.get('teams') else None,
        phone=m.get('phone'),
        whatsapp_number=m.get('whatsapp_number'),
        preferred_channel=m.get('preferred_channel', 'email'),
        title=m.get('title'),
        avatar_url=m.get('avatar_url'),
        is_superadmin=m.get('is_superadmin', False),
        created_at=m['created_at']
    )


@router.put("/{member_id}", response_model=MemberResponse)
async def update_member(
    member_id: str,
    request: MemberUpdate,
    user: TokenPayload = Depends(require_auth)
):
    """Update a team member. Admins can update anyone, managers can update reps, users can update themselves."""
    db_manager = get_database_manager()
    supabase = db_manager.get_client(DatabaseType.MAIN)

    membership = get_sales_org_for_user(supabase, user.sub)
    if not membership:
        raise HTTPException(status_code=403, detail="Not a member of any sales organization")

    # Get target member
    target = supabase.schema('sales').table('team_members')\
        .select('*')\
        .eq('id', member_id)\
        .eq('organization_id', membership['organization_id'])\
        .single()\
        .execute()

    if not target.data:
        raise HTTPException(status_code=404, detail="Member not found")

    target_member = target.data
    is_self = target_member['user_id'] == user.sub
    is_admin = membership['role'] == 'admin' or membership.get('is_superadmin')
    is_manager = membership['role'] == 'manager'

    # Permission checks
    if not is_self:
        if not is_admin and not is_manager:
            raise HTTPException(status_code=403, detail="You can only edit your own profile")
        if is_manager and target_member['role'] != 'rep':
            raise HTTPException(status_code=403, detail="Managers can only edit reps")

    # Role changes require admin
    if request.role and request.role != target_member['role']:
        if not is_admin:
            raise HTTPException(status_code=403, detail="Only admins can change roles")
        # Prevent demoting yourself
        if is_self:
            raise HTTPException(status_code=400, detail="You cannot change your own role")

    # Build update data
    update_data = {}
    for field, value in request.model_dump().items():
        if value is not None or field == 'team_id':  # Allow setting team_id to null
            if field == 'team_id' and value == '':
                update_data[field] = None
            else:
                update_data[field] = value

    if not update_data:
        raise HTTPException(status_code=400, detail="No fields to update")

    # Verify team_id if provided
    if 'team_id' in update_data and update_data['team_id']:
        team_check = supabase.schema('sales').table('teams')\
            .select('id')\
            .eq('id', update_data['team_id'])\
            .eq('organization_id', membership['organization_id'])\
            .execute()
        if not team_check.data:
            raise HTTPException(status_code=400, detail="Invalid team ID")

    result = supabase.schema('sales').table('team_members')\
        .update(update_data)\
        .eq('id', member_id)\
        .execute()

    # Re-fetch with team join
    updated = supabase.schema('sales').table('team_members')\
        .select('*, teams:team_id(name)')\
        .eq('id', member_id)\
        .single()\
        .execute()

    m = updated.data
    user_email = m.get('email')
    if not user_email:
        try:
            auth_user = supabase.auth.admin.get_user_by_id(m['user_id'])
            user_email = auth_user.user.email if auth_user.user else None
        except Exception:
            pass

    return MemberResponse(
        id=m['id'],
        user_id=m['user_id'],
        name=m.get('name'),
        email=user_email,
        role=m['role'],
        team_id=m.get('team_id'),
        team_name=m.get('teams', {}).get('name') if m.get('teams') else None,
        phone=m.get('phone'),
        whatsapp_number=m.get('whatsapp_number'),
        preferred_channel=m.get('preferred_channel', 'email'),
        title=m.get('title'),
        avatar_url=m.get('avatar_url'),
        is_superadmin=m.get('is_superadmin', False),
        created_at=m['created_at']
    )


@router.delete("/{member_id}")
async def remove_member(member_id: str, user: TokenPayload = Depends(require_auth)):
    """Remove a member from the organization. Admin only."""
    db_manager = get_database_manager()
    supabase = db_manager.get_client(DatabaseType.MAIN)

    membership = get_sales_org_for_user(supabase, user.sub)
    if not membership:
        raise HTTPException(status_code=403, detail="Not a member of any sales organization")

    if membership['role'] != 'admin' and not membership.get('is_superadmin'):
        raise HTTPException(status_code=403, detail="Only admins can remove members")

    # Get target member
    target = supabase.schema('sales').table('team_members')\
        .select('user_id')\
        .eq('id', member_id)\
        .eq('organization_id', membership['organization_id'])\
        .single()\
        .execute()

    if not target.data:
        raise HTTPException(status_code=404, detail="Member not found")

    # Prevent self-removal
    if target.data['user_id'] == user.sub:
        raise HTTPException(status_code=400, detail="You cannot remove yourself from the organization")

    result = supabase.schema('sales').table('team_members')\
        .delete()\
        .eq('id', member_id)\
        .execute()

    return {"success": True}
