"""API endpoints for sales team management."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from typing import Optional, List
from app.middleware.auth import require_auth, TokenPayload
from app.services.database_manager import get_database_manager, DatabaseType
from app.api.sales_invitations_api import get_sales_org_for_user
import logging

router = APIRouter(prefix="/api/sales/teams", tags=["sales-teams"])
logger = logging.getLogger(__name__)


class TeamCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = None
    color: str = Field(default="#6366f1", pattern=r"^#[0-9a-fA-F]{6}$")


class TeamUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    description: Optional[str] = None
    color: Optional[str] = Field(None, pattern=r"^#[0-9a-fA-F]{6}$")


class TeamResponse(BaseModel):
    id: str
    name: str
    description: Optional[str]
    color: str
    member_count: int
    created_at: str


@router.get("", response_model=List[TeamResponse])
async def list_teams(user: TokenPayload = Depends(require_auth)):
    """List all teams in the user's organization."""
    db_manager = get_database_manager()
    supabase = db_manager.get_client(DatabaseType.MAIN)

    membership = get_sales_org_for_user(supabase, user.sub)
    if not membership:
        raise HTTPException(status_code=403, detail="Not a member of any sales organization")

    # Get teams with member counts
    result = supabase.schema('sales').table('teams')\
        .select('id, name, description, color, created_at')\
        .eq('organization_id', membership['organization_id'])\
        .order('name')\
        .execute()

    teams = []
    for team in result.data:
        # Get member count for each team
        count_result = supabase.schema('sales').table('team_members')\
            .select('id', count='exact')\
            .eq('team_id', team['id'])\
            .execute()

        teams.append(TeamResponse(
            id=team['id'],
            name=team['name'],
            description=team.get('description'),
            color=team['color'],
            member_count=count_result.count or 0,
            created_at=team['created_at']
        ))

    return teams


@router.post("", response_model=TeamResponse)
async def create_team(request: TeamCreate, user: TokenPayload = Depends(require_auth)):
    """Create a new team. Admin only."""
    db_manager = get_database_manager()
    supabase = db_manager.get_client(DatabaseType.MAIN)

    membership = get_sales_org_for_user(supabase, user.sub)
    if not membership:
        raise HTTPException(status_code=403, detail="Not a member of any sales organization")

    if membership['role'] != 'admin' and not membership.get('is_superadmin'):
        raise HTTPException(status_code=403, detail="Only admins can create teams")

    try:
        result = supabase.schema('sales').table('teams').insert({
            'organization_id': membership['organization_id'],
            'name': request.name,
            'description': request.description,
            'color': request.color
        }).execute()

        team = result.data[0]
        return TeamResponse(
            id=team['id'],
            name=team['name'],
            description=team.get('description'),
            color=team['color'],
            member_count=0,
            created_at=team['created_at']
        )
    except Exception as e:
        if 'duplicate key' in str(e).lower():
            raise HTTPException(status_code=400, detail="Team with this name already exists")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/{team_id}", response_model=TeamResponse)
async def update_team(team_id: str, request: TeamUpdate, user: TokenPayload = Depends(require_auth)):
    """Update a team. Admin only."""
    db_manager = get_database_manager()
    supabase = db_manager.get_client(DatabaseType.MAIN)

    membership = get_sales_org_for_user(supabase, user.sub)
    if not membership:
        raise HTTPException(status_code=403, detail="Not a member of any sales organization")

    if membership['role'] != 'admin' and not membership.get('is_superadmin'):
        raise HTTPException(status_code=403, detail="Only admins can update teams")

    update_data = {k: v for k, v in request.model_dump().items() if v is not None}
    if not update_data:
        raise HTTPException(status_code=400, detail="No fields to update")

    result = supabase.schema('sales').table('teams')\
        .update(update_data)\
        .eq('id', team_id)\
        .eq('organization_id', membership['organization_id'])\
        .execute()

    if not result.data:
        raise HTTPException(status_code=404, detail="Team not found")

    team = result.data[0]
    count_result = supabase.schema('sales').table('team_members')\
        .select('id', count='exact')\
        .eq('team_id', team_id)\
        .execute()

    return TeamResponse(
        id=team['id'],
        name=team['name'],
        description=team.get('description'),
        color=team['color'],
        member_count=count_result.count or 0,
        created_at=team['created_at']
    )


@router.delete("/{team_id}")
async def delete_team(team_id: str, user: TokenPayload = Depends(require_auth)):
    """Delete a team. Admin only. Members will have team_id set to NULL."""
    db_manager = get_database_manager()
    supabase = db_manager.get_client(DatabaseType.MAIN)

    membership = get_sales_org_for_user(supabase, user.sub)
    if not membership:
        raise HTTPException(status_code=403, detail="Not a member of any sales organization")

    if membership['role'] != 'admin' and not membership.get('is_superadmin'):
        raise HTTPException(status_code=403, detail="Only admins can delete teams")

    result = supabase.schema('sales').table('teams')\
        .delete()\
        .eq('id', team_id)\
        .eq('organization_id', membership['organization_id'])\
        .execute()

    if not result.data:
        raise HTTPException(status_code=404, detail="Team not found")

    return {"success": True}
