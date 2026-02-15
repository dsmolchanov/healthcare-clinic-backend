"""API endpoints for staff invitations."""
import secrets
import hashlib
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from pydantic import BaseModel, EmailStr
from typing import Optional
from app.middleware.auth import require_permission, TokenPayload
from app.services.database_manager import get_database_manager, DatabaseType
from app.services.email_service import get_email_service
from app.rate_limiter import RateLimiter
from app import config
import logging

router = APIRouter(prefix="/api/invitations", tags=["invitations"])
logger = logging.getLogger(__name__)

# Rate limiter: max 5 invitations per minute per user
invite_limiter = RateLimiter()

class InviteStaffRequest(BaseModel):
    email: EmailStr
    role: str = "member"  # System role: 'admin' or 'member'
    custom_role_id: Optional[str] = None  # UUID of custom role label

class InviteStaffResponse(BaseModel):
    invitation_id: str
    email: str
    role: str
    custom_role_id: Optional[str] = None
    expires_at: str

@router.post("/invite", response_model=InviteStaffResponse)
async def invite_staff_member(
    request: InviteStaffRequest,
    background_tasks: BackgroundTasks,
    user: TokenPayload = Depends(require_permission("team:invite"))
):
    """Invite a staff member via email."""
    # Rate limiting
    rate_key = f"invite:{user.sub}"
    if not await invite_limiter.is_allowed(rate_key, max_requests=5, window_seconds=60):
        raise HTTPException(status_code=429, detail="Too many invitation requests. Please try again later.")

    db_manager = get_database_manager()
    supabase = db_manager.get_client(DatabaseType.MAIN) if DatabaseType.MAIN in db_manager.clients else list(db_manager.clients.values())[0]
    email_service = get_email_service()

    # Validate system role
    valid_roles = ['admin', 'member']
    role = request.role

    # If custom_role_id is provided, derive system role from the custom role
    custom_role_id = request.custom_role_id
    if custom_role_id:
        custom_role = supabase.schema('agents').table('custom_roles')\
            .select('system_role')\
            .eq('id', custom_role_id)\
            .eq('organization_id', user.organization_id)\
            .single()\
            .execute()
        if custom_role.data:
            role = custom_role.data['system_role']
        else:
            raise HTTPException(status_code=400, detail="Invalid custom_role_id")

    if role not in valid_roles:
        raise HTTPException(status_code=400, detail=f"Invalid role. Must be one of: {', '.join(valid_roles)}")

    # Normalize email to lowercase for case-insensitive comparison
    email_lower = request.email.lower()

    # Check if user already exists in organization
    existing_members = supabase.schema('core').table('user_organizations')\
        .select('user_id')\
        .eq('organization_id', user.organization_id)\
        .execute()

    # Check if email matches any existing member
    for member in existing_members.data:
        try:
            member_user = supabase.auth.admin.get_user_by_id(member['user_id'])
            if member_user.user and member_user.user.email and member_user.user.email.lower() == email_lower:
                raise HTTPException(status_code=400, detail="User already member of this organization")
        except Exception as e:
            logger.warning(f"Could not check user {member['user_id']}: {e}")
            continue

    # Check for pending invitation
    pending = supabase.schema('public').table('staff_invitations')\
        .select('id')\
        .eq('organization_id', user.organization_id)\
        .eq('email', email_lower)\
        .eq('status', 'pending')\
        .execute()

    if pending.data:
        raise HTTPException(status_code=400, detail="Invitation already sent to this email")

    # Generate invitation token (raw token sent in email)
    token = secrets.token_urlsafe(32)

    # Hash token for database storage (security: never store raw tokens)
    token_hash = hashlib.sha256(token.encode()).hexdigest()

    # Use timezone-aware datetime
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(days=7)

    # Create invitation record
    invitation_data = {
        'organization_id': user.organization_id,
        'email': email_lower,
        'role': role,
        'token_hash': token_hash,  # Store hash, not raw token
        'invited_by': user.sub,
        'expires_at': expires_at.isoformat(),
        'status': 'pending'
    }
    if custom_role_id:
        invitation_data['custom_role_id'] = custom_role_id

    # Try agents schema first (unified), fall back to public
    try:
        invitation = supabase.schema('agents').table('staff_invitations').insert(invitation_data).execute()
    except Exception:
        invitation = supabase.schema('public').table('staff_invitations').insert(invitation_data).execute()

    if not invitation.data:
        raise HTTPException(status_code=500, detail="Failed to create invitation")

    # Get organization/clinic details
    org_result = supabase.schema('core').table('organizations')\
        .select('name')\
        .eq('id', user.organization_id)\
        .single()\
        .execute()

    clinic_name = org_result.data.get('name', 'the clinic') if org_result.data else 'the clinic'

    # Get inviter name
    try:
        inviter = supabase.auth.admin.get_user_by_id(user.sub)
        inviter_name = inviter.user.user_metadata.get('name', 'Someone') if inviter.user and inviter.user.user_metadata else 'Someone'
    except:
        inviter_name = 'Someone'

    # Build invitation URL (raw token in URL)
    invitation_url = f"{config.FRONTEND_URL}/accept-invitation?token={token}"

    # Send invitation email in background to avoid blocking
    background_tasks.add_task(
        email_service.send_invitation,
        to_email=request.email,
        inviter_name=inviter_name,
        clinic_name=clinic_name,
        role=request.role,
        invitation_url=invitation_url
    )

    return InviteStaffResponse(
        invitation_id=invitation.data[0]['id'],
        email=request.email,
        role=role,
        custom_role_id=custom_role_id,
        expires_at=expires_at.isoformat()
    )

class AcceptInvitationRequest(BaseModel):
    token: str
    password: str

@router.post("/accept")
async def accept_invitation(request: AcceptInvitationRequest):
    """Accept invitation and create user account (or link existing account)."""
    db_manager = get_database_manager()
    supabase = db_manager.get_client(DatabaseType.MAIN) if DatabaseType.MAIN in db_manager.clients else list(db_manager.clients.values())[0]

    # Hash the token to lookup in database
    token_hash = hashlib.sha256(request.token.encode()).hexdigest()

    # Get invitation
    invitation = supabase.schema('public').table('staff_invitations')\
        .select('*')\
        .eq('token_hash', token_hash)\
        .eq('status', 'pending')\
        .single()\
        .execute()

    if not invitation.data:
        raise HTTPException(status_code=404, detail="Invalid or expired invitation")

    inv = invitation.data

    # Use timezone-aware datetime for comparison
    now = datetime.now(timezone.utc)
    expires_at = datetime.fromisoformat(inv['expires_at'].replace('Z', '+00:00'))

    if now > expires_at:
        # Mark as expired
        supabase.schema('public').table('staff_invitations')\
            .update({'status': 'expired'})\
            .eq('id', inv['id'])\
            .execute()
        raise HTTPException(status_code=400, detail="Invitation has expired")

    # Check if user already exists
    existing_user = None
    try:
        existing_user = supabase.auth.admin.list_users()
        # Find user with matching email
        for u in existing_user:
            if u.email and u.email.lower() == inv['email'].lower():
                existing_user = u
                break
        else:
            existing_user = None
    except Exception:
        pass  # User doesn't exist, will create new one

    user_id = None

    if existing_user:
        # User exists - just add to organization
        user_id = existing_user.id
        logger.info(f"Linking existing user {user_id} to organization {inv['organization_id']}")
    else:
        # Create new user account
        try:
            new_user = supabase.auth.admin.create_user({
                'email': inv['email'],
                'password': request.password,
                'email_confirm': True,
                'user_metadata': {
                    'organization_id': inv['organization_id']
                }
            })
            user_id = new_user.user.id
            logger.info(f"Created new user {user_id}")
        except Exception as e:
            logger.error(f"Failed to create user: {e}")
            raise HTTPException(status_code=400, detail=f"Failed to create user: {str(e)}")

    # Add user to organization with role (with cleanup on failure)
    try:
        supabase.schema('core').table('user_organizations').insert({
            'user_id': user_id,
            'organization_id': inv['organization_id'],
            'unified_role': inv['role'],
            'is_active': True,
            'joined_at': now.isoformat()
        }).execute()
    except Exception as e:
        # Cleanup: if we just created the user and DB insert failed, delete the orphan auth user
        if not existing_user and user_id:
            try:
                supabase.auth.admin.delete_user(user_id)
                logger.warning(f"Cleaned up orphan user {user_id} after DB insert failure")
            except Exception as cleanup_error:
                logger.error(f"Failed to cleanup orphan user {user_id}: {cleanup_error}")

        logger.error(f"Failed to add user to organization: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to add user to organization: {str(e)}")

    # Mark invitation as accepted
    supabase.schema('public').table('staff_invitations')\
        .update({
            'status': 'accepted',
            'accepted_at': now.isoformat()
        })\
        .eq('id', inv['id'])\
        .execute()

    return {"success": True, "user_id": user_id, "existing_user": existing_user is not None}
