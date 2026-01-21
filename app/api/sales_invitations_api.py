"""API endpoints for sales staff invitations."""
import secrets
import hashlib
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from pydantic import BaseModel, EmailStr
from typing import Optional, List
from app.middleware.auth import require_auth, TokenPayload
from app.services.database_manager import get_database_manager, DatabaseType
from app.services.email_service import get_email_service
from app.rate_limiter import RateLimiter
from app import config
import logging

router = APIRouter(prefix="/api/sales/invitations", tags=["sales-invitations"])
logger = logging.getLogger(__name__)

# Rate limiter: max 5 invitations per minute per user
invite_limiter = RateLimiter()

# Valid roles for sales team members
VALID_SALES_ROLES = ['admin', 'manager', 'rep']


class InviteSalesStaffRequest(BaseModel):
    email: EmailStr
    role: str = 'rep'  # admin, manager, rep
    name: Optional[str] = None
    organization_id: Optional[str] = None  # Required for superadmin inviting to specific org


class InviteSalesStaffResponse(BaseModel):
    invitation_id: str
    email: str
    role: str
    expires_at: str


class InvitationInfo(BaseModel):
    id: str
    email: str
    role: str
    status: str
    invited_at: str
    expires_at: str
    accepted_at: Optional[str] = None


class AcceptSalesInvitationRequest(BaseModel):
    token: str
    password: str
    name: Optional[str] = None


def get_sales_org_for_user(supabase, user_id: str) -> Optional[dict]:
    """Get the sales organization for a user from team_members."""
    try:
        result = supabase.schema('sales').table('team_members')\
            .select('organization_id, role, name, is_superadmin')\
            .eq('user_id', user_id)\
            .single()\
            .execute()
        return result.data
    except Exception as e:
        logger.warning(f"Could not find sales org for user {user_id}: {e}")
        return None


def is_user_superadmin(supabase, user_id: str) -> bool:
    """Check if user is a superadmin."""
    try:
        result = supabase.schema('sales').table('team_members')\
            .select('is_superadmin')\
            .eq('user_id', user_id)\
            .eq('is_superadmin', True)\
            .execute()
        return len(result.data) > 0
    except Exception:
        return False


@router.post("/invite", response_model=InviteSalesStaffResponse)
async def invite_sales_staff(
    request: InviteSalesStaffRequest,
    background_tasks: BackgroundTasks,
    user: TokenPayload = Depends(require_auth)
):
    """
    Invite a sales staff member via email.

    - Superadmins can invite to ANY organization (must provide organization_id)
    - Admins can invite admin, manager, rep to their organization
    - Managers can only invite reps to their organization
    """
    # Rate limiting
    rate_key = f"sales_invite:{user.sub}"
    if not await invite_limiter.is_allowed(rate_key, max_requests=5, window_seconds=60):
        raise HTTPException(status_code=429, detail="Too many invitation requests. Please try again later.")

    db_manager = get_database_manager()
    supabase = db_manager.get_client(DatabaseType.MAIN) if DatabaseType.MAIN in db_manager.clients else list(db_manager.clients.values())[0]
    email_service = get_email_service()

    # Check if user is superadmin
    is_superadmin = is_user_superadmin(supabase, user.sub)

    # Get user's sales organization membership
    membership = get_sales_org_for_user(supabase, user.sub)

    # Determine organization ID and validate permissions
    if is_superadmin:
        # Superadmin can invite to any org, but must specify which one
        if request.organization_id:
            org_id = request.organization_id
            # Verify org exists
            org_check = supabase.schema('sales').table('organizations')\
                .select('id')\
                .eq('id', org_id)\
                .execute()
            if not org_check.data:
                raise HTTPException(status_code=404, detail="Organization not found")
        elif membership:
            # Use their own org if not specified
            org_id = membership['organization_id']
        else:
            raise HTTPException(status_code=400, detail="Superadmin must specify organization_id when not a member of any organization")

        # Superadmin can invite any role
        inviter_name = membership.get('name', 'Platform Admin') if membership else 'Platform Admin'
    else:
        # Regular user must be a member with appropriate role
        if not membership:
            raise HTTPException(status_code=403, detail="You are not a member of any sales organization")

        # Check role permissions (only admin and manager can invite)
        if membership['role'] not in ['admin', 'manager']:
            raise HTTPException(status_code=403, detail="Only admins and managers can invite staff")

        # Managers can only invite reps
        if membership['role'] == 'manager' and request.role != 'rep':
            raise HTTPException(status_code=403, detail="Managers can only invite reps")

        org_id = membership['organization_id']
        inviter_name = membership.get('name', 'Team Admin')

    # Validate role
    if request.role not in VALID_SALES_ROLES:
        raise HTTPException(status_code=400, detail=f"Invalid role. Must be one of: {', '.join(VALID_SALES_ROLES)}")

    # Normalize email
    email_lower = request.email.lower()

    # Check if user already exists in organization
    existing_members = supabase.schema('sales').table('team_members')\
        .select('user_id')\
        .eq('organization_id', org_id)\
        .execute()

    # Check if email matches any existing member
    for member in existing_members.data:
        try:
            member_user = supabase.auth.admin.get_user_by_id(member['user_id'])
            if member_user.user and member_user.user.email and member_user.user.email.lower() == email_lower:
                raise HTTPException(status_code=400, detail="User is already a member of this organization")
        except HTTPException:
            raise
        except Exception as e:
            logger.warning(f"Could not check user {member['user_id']}: {e}")
            continue

    # Check for pending invitation
    pending = supabase.schema('sales').table('staff_invitations')\
        .select('id')\
        .eq('organization_id', org_id)\
        .eq('email', email_lower)\
        .eq('status', 'pending')\
        .execute()

    if pending.data:
        raise HTTPException(status_code=400, detail="Invitation already sent to this email")

    # Generate invitation token
    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode()).hexdigest()

    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(days=7)

    # Create invitation record
    invitation = supabase.schema('sales').table('staff_invitations').insert({
        'organization_id': org_id,
        'email': email_lower,
        'role': request.role,
        'token_hash': token_hash,
        'invited_by': user.sub,
        'expires_at': expires_at.isoformat(),
        'status': 'pending'
    }).execute()

    if not invitation.data:
        raise HTTPException(status_code=500, detail="Failed to create invitation")

    # Get organization name
    org_result = supabase.schema('sales').table('organizations')\
        .select('name')\
        .eq('id', org_id)\
        .single()\
        .execute()

    org_name = org_result.data.get('name', 'the organization') if org_result.data else 'the organization'

    # inviter_name already set above based on superadmin/membership

    # Build invitation URL
    invitation_url = f"{config.FRONTEND_URL}/sales/accept-invitation?token={token}"

    # Send invitation email in background
    background_tasks.add_task(
        email_service.send_sales_invitation,
        to_email=request.email,
        inviter_name=inviter_name,
        org_name=org_name,
        role=request.role,
        invitation_url=invitation_url
    )

    return InviteSalesStaffResponse(
        invitation_id=invitation.data[0]['id'],
        email=request.email,
        role=request.role,
        expires_at=expires_at.isoformat()
    )


@router.get("/pending", response_model=List[InvitationInfo])
async def list_pending_invitations(user: TokenPayload = Depends(require_auth)):
    """List pending invitations for the user's sales organization."""
    db_manager = get_database_manager()
    supabase = db_manager.get_client(DatabaseType.MAIN) if DatabaseType.MAIN in db_manager.clients else list(db_manager.clients.values())[0]

    membership = get_sales_org_for_user(supabase, user.sub)
    if not membership:
        raise HTTPException(status_code=403, detail="You are not a member of any sales organization")

    # Only admin and manager can view invitations
    if membership['role'] not in ['admin', 'manager']:
        raise HTTPException(status_code=403, detail="Only admins and managers can view invitations")

    result = supabase.schema('sales').table('staff_invitations')\
        .select('id, email, role, status, invited_at, expires_at, accepted_at')\
        .eq('organization_id', membership['organization_id'])\
        .eq('status', 'pending')\
        .order('invited_at', desc=True)\
        .execute()

    return [InvitationInfo(**inv) for inv in result.data]


@router.delete("/{invitation_id}")
async def cancel_invitation(invitation_id: str, user: TokenPayload = Depends(require_auth)):
    """Cancel a pending invitation."""
    db_manager = get_database_manager()
    supabase = db_manager.get_client(DatabaseType.MAIN) if DatabaseType.MAIN in db_manager.clients else list(db_manager.clients.values())[0]

    membership = get_sales_org_for_user(supabase, user.sub)
    if not membership:
        raise HTTPException(status_code=403, detail="You are not a member of any sales organization")

    if membership['role'] not in ['admin', 'manager']:
        raise HTTPException(status_code=403, detail="Only admins and managers can cancel invitations")

    # Update invitation status
    result = supabase.schema('sales').table('staff_invitations')\
        .update({'status': 'cancelled'})\
        .eq('id', invitation_id)\
        .eq('organization_id', membership['organization_id'])\
        .eq('status', 'pending')\
        .execute()

    if not result.data:
        raise HTTPException(status_code=404, detail="Invitation not found or already processed")

    return {"success": True}


@router.post("/accept")
async def accept_sales_invitation(request: AcceptSalesInvitationRequest):
    """Accept invitation and create/link user account."""
    db_manager = get_database_manager()
    supabase = db_manager.get_client(DatabaseType.MAIN) if DatabaseType.MAIN in db_manager.clients else list(db_manager.clients.values())[0]

    # Hash token to lookup
    token_hash = hashlib.sha256(request.token.encode()).hexdigest()

    # Get invitation
    invitation = supabase.schema('sales').table('staff_invitations')\
        .select('*')\
        .eq('token_hash', token_hash)\
        .eq('status', 'pending')\
        .single()\
        .execute()

    if not invitation.data:
        raise HTTPException(status_code=404, detail="Invalid or expired invitation")

    inv = invitation.data
    now = datetime.now(timezone.utc)
    expires_at = datetime.fromisoformat(inv['expires_at'].replace('Z', '+00:00'))

    if now > expires_at:
        # Mark as expired
        supabase.schema('sales').table('staff_invitations')\
            .update({'status': 'expired'})\
            .eq('id', inv['id'])\
            .execute()
        raise HTTPException(status_code=400, detail="Invitation has expired")

    # Check if user already exists
    existing_user = None
    try:
        users = supabase.auth.admin.list_users()
        for u in users:
            if u.email and u.email.lower() == inv['email'].lower():
                existing_user = u
                break
    except Exception:
        pass

    user_id = None
    user_name = request.name or inv['email'].split('@')[0]

    if existing_user:
        user_id = existing_user.id
        logger.info(f"Linking existing user {user_id} to sales org {inv['organization_id']}")
    else:
        # Create new user
        try:
            new_user = supabase.auth.admin.create_user({
                'email': inv['email'],
                'password': request.password,
                'email_confirm': True,
                'user_metadata': {
                    'name': user_name,
                    'sales_organization_id': inv['organization_id']
                }
            })
            user_id = new_user.user.id
            logger.info(f"Created new user {user_id}")
        except Exception as e:
            logger.error(f"Failed to create user: {e}")
            raise HTTPException(status_code=400, detail=f"Failed to create user: {str(e)}")

    # Add user to sales team_members (with cleanup on failure)
    try:
        supabase.schema('sales').table('team_members').insert({
            'user_id': user_id,
            'organization_id': inv['organization_id'],
            'role': inv['role'],
            'name': user_name,
            'email': inv['email']
        }).execute()
    except Exception as e:
        # Cleanup orphan user if we just created it
        if not existing_user and user_id:
            try:
                supabase.auth.admin.delete_user(user_id)
                logger.warning(f"Cleaned up orphan user {user_id}")
            except Exception as cleanup_error:
                logger.error(f"Failed to cleanup orphan user: {cleanup_error}")

        logger.error(f"Failed to add user to team: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to add user to team: {str(e)}")

    # Mark invitation as accepted
    supabase.schema('sales').table('staff_invitations')\
        .update({
            'status': 'accepted',
            'accepted_at': now.isoformat()
        })\
        .eq('id', inv['id'])\
        .execute()

    return {
        "success": True,
        "user_id": user_id,
        "existing_user": existing_user is not None,
        "organization_id": inv['organization_id']
    }


@router.get("/validate/{token}")
async def validate_invitation_token(token: str):
    """Validate an invitation token and return basic info (for pre-filling accept form)."""
    db_manager = get_database_manager()
    supabase = db_manager.get_client(DatabaseType.MAIN) if DatabaseType.MAIN in db_manager.clients else list(db_manager.clients.values())[0]

    token_hash = hashlib.sha256(token.encode()).hexdigest()

    invitation = supabase.schema('sales').table('staff_invitations')\
        .select('email, role, organization_id, expires_at, status')\
        .eq('token_hash', token_hash)\
        .single()\
        .execute()

    if not invitation.data:
        raise HTTPException(status_code=404, detail="Invalid invitation")

    inv = invitation.data

    if inv['status'] != 'pending':
        raise HTTPException(status_code=400, detail=f"Invitation already {inv['status']}")

    now = datetime.now(timezone.utc)
    expires_at = datetime.fromisoformat(inv['expires_at'].replace('Z', '+00:00'))

    if now > expires_at:
        raise HTTPException(status_code=400, detail="Invitation has expired")

    # Get organization name
    org_result = supabase.schema('sales').table('organizations')\
        .select('name')\
        .eq('id', inv['organization_id'])\
        .single()\
        .execute()

    org_name = org_result.data.get('name', '') if org_result.data else ''

    # Check if user already exists
    existing_user = False
    try:
        users = supabase.auth.admin.list_users()
        for u in users:
            if u.email and u.email.lower() == inv['email'].lower():
                existing_user = True
                break
    except Exception as e:
        logger.warning(f"Could not check for existing user: {e}")

    return {
        "valid": True,
        "email": inv['email'],
        "role": inv['role'],
        "organization_name": org_name,
        "expires_at": inv['expires_at'],
        "existing_user": existing_user
    }
