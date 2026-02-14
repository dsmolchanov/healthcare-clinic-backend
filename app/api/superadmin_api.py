"""
Superadmin Platform API — cross-org aggregation endpoints.

Protected by require_superadmin() — only users with is_superadmin=TRUE can access.
"""
import logging
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from typing import Optional

from app.middleware.auth import require_superadmin, TokenPayload
from app.services.database_manager import get_database_manager, DatabaseType

router = APIRouter(prefix="/api/superadmin", tags=["superadmin"])
logger = logging.getLogger(__name__)

SCHEMA = "sales"


def _get_supabase():
    """Get Supabase client bound to the sales schema."""
    db_manager = get_database_manager()
    return db_manager.get_client(DatabaseType.MAIN)


def _current_month_range():
    """Return (first day of current month, first day of next month) as ISO strings."""
    now = datetime.now(timezone.utc)
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if now.month == 12:
        end = start.replace(year=now.year + 1, month=1)
    else:
        end = start.replace(month=now.month + 1)
    return start.isoformat(), end.isoformat()


def _previous_month_range():
    """Return (first day of previous month, first day of current month) as ISO strings."""
    now = datetime.now(timezone.utc)
    current_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if now.month == 1:
        prev_start = current_start.replace(year=now.year - 1, month=12)
    else:
        prev_start = current_start.replace(month=now.month - 1)
    return prev_start.isoformat(), current_start.isoformat()


# ============================================================================
# Endpoint 1: Platform Overview
# ============================================================================

@router.get("/platform-overview")
async def get_platform_overview(user: TokenPayload = Depends(require_superadmin())):
    """
    Aggregated platform KPIs: org counts, user counts, usage totals, onboarding funnel.
    """
    supabase = _get_supabase()

    try:
        # --- Organizations by status ---
        orgs_result = supabase.schema(SCHEMA).table('organizations') \
            .select('id, activation_status, subscription_plan') \
            .execute()

        orgs = orgs_result.data or []
        total_organizations = len(orgs)
        active_organizations = sum(1 for o in orgs if o.get('activation_status') == 'active')
        trial_organizations = sum(1 for o in orgs if o.get('subscription_plan') == 'trial')
        paused_organizations = sum(1 for o in orgs if o.get('activation_status') == 'paused')

        # --- Team members ---
        members_result = supabase.schema(SCHEMA).table('team_members') \
            .select('id, is_superadmin') \
            .execute()

        members = members_result.data or []
        total_users = len(members)
        total_superadmins = sum(1 for m in members if m.get('is_superadmin'))

        # --- Usage: current month ---
        cur_start, cur_end = _current_month_range()
        cur_usage_result = supabase.schema(SCHEMA).table('usage_logs') \
            .select('organization_id, messages_in, messages_out, llm_input_tokens, llm_output_tokens, leads_created, escalations') \
            .gte('period_start', cur_start) \
            .lt('period_start', cur_end) \
            .execute()

        cur_usage = cur_usage_result.data or []
        current_month = {
            "total_messages": sum((r.get('messages_in') or 0) + (r.get('messages_out') or 0) for r in cur_usage),
            "total_tokens": sum((r.get('llm_input_tokens') or 0) + (r.get('llm_output_tokens') or 0) for r in cur_usage),
            "total_leads": sum(r.get('leads_created') or 0 for r in cur_usage),
            "total_escalations": sum(r.get('escalations') or 0 for r in cur_usage),
            "active_orgs": len(set(r.get('organization_id') for r in cur_usage if r.get('organization_id'))),
        }

        # --- Usage: previous month ---
        prev_start, prev_end = _previous_month_range()
        prev_usage_result = supabase.schema(SCHEMA).table('usage_logs') \
            .select('organization_id, messages_in, messages_out, llm_input_tokens, llm_output_tokens, leads_created, escalations') \
            .gte('period_start', prev_start) \
            .lt('period_start', prev_end) \
            .execute()

        prev_usage = prev_usage_result.data or []
        previous_month = {
            "total_messages": sum((r.get('messages_in') or 0) + (r.get('messages_out') or 0) for r in prev_usage),
            "total_tokens": sum((r.get('llm_input_tokens') or 0) + (r.get('llm_output_tokens') or 0) for r in prev_usage),
            "total_leads": sum(r.get('leads_created') or 0 for r in prev_usage),
            "total_escalations": sum(r.get('escalations') or 0 for r in prev_usage),
            "active_orgs": len(set(r.get('organization_id') for r in prev_usage if r.get('organization_id'))),
        }

        # --- Onboarding funnel ---
        onboarding_result = supabase.schema(SCHEMA).table('onboarding_progress') \
            .select('organization_id, company_basics, product_knowledge, qualification, whatsapp_setup, test_and_launch') \
            .execute()

        onboarding = onboarding_result.data or []
        onboarding_funnel = {
            "total": total_organizations,
            "company_basics_done": sum(1 for o in onboarding if o.get('company_basics')),
            "product_knowledge_done": sum(1 for o in onboarding if o.get('product_knowledge')),
            "qualification_done": sum(1 for o in onboarding if o.get('qualification')),
            "whatsapp_connected": sum(1 for o in onboarding if o.get('whatsapp_setup')),
            "test_and_launch_done": sum(1 for o in onboarding if o.get('test_and_launch')),
        }

        return {
            "total_organizations": total_organizations,
            "active_organizations": active_organizations,
            "trial_organizations": trial_organizations,
            "paused_organizations": paused_organizations,
            "total_users": total_users,
            "total_superadmins": total_superadmins,
            "current_month": current_month,
            "previous_month": previous_month,
            "onboarding_funnel": onboarding_funnel,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching platform overview: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch platform overview")


# ============================================================================
# Endpoint 2: Organizations List
# ============================================================================

@router.get("/organizations")
async def get_organizations(
    search: Optional[str] = Query(None, description="Search by org name"),
    status: Optional[str] = Query(None, description="Filter by activation_status"),
    sort: Optional[str] = Query("created_at", description="Sort field"),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    user: TokenPayload = Depends(require_superadmin()),
):
    """
    List all organizations with usage and status. Supports search, filter, pagination.
    """
    supabase = _get_supabase()

    try:
        # Fetch all orgs
        query = supabase.schema(SCHEMA).table('organizations') \
            .select('id, name, slug, activation_status, subscription_plan, subscription_status, trial_ends_at, created_at')

        if status:
            query = query.eq('activation_status', status)

        orgs_result = query.order('created_at', desc=True).execute()
        orgs = orgs_result.data or []

        # Client-side search (Supabase PostgREST textSearch requires FTS config)
        if search:
            search_lower = search.lower()
            orgs = [o for o in orgs if search_lower in (o.get('name') or '').lower() or search_lower in (o.get('slug') or '').lower()]

        total = len(orgs)

        # Paginate
        start = (page - 1) * per_page
        orgs_page = orgs[start:start + per_page]

        if not orgs_page:
            return {"organizations": [], "total": total, "page": page, "per_page": per_page}

        org_ids = [o['id'] for o in orgs_page]

        # Fetch team counts per org
        teams_result = supabase.schema(SCHEMA).table('teams') \
            .select('organization_id, agent_type') \
            .in_('organization_id', org_ids) \
            .execute()

        teams_by_org = {}
        for t in (teams_result.data or []):
            org_id = t.get('organization_id')
            if org_id not in teams_by_org:
                teams_by_org[org_id] = {"count": 0, "agent_types": set()}
            teams_by_org[org_id]["count"] += 1
            if t.get('agent_type'):
                teams_by_org[org_id]["agent_types"].add(t['agent_type'])

        # Fetch member counts per org
        members_result = supabase.schema(SCHEMA).table('team_members') \
            .select('organization_id') \
            .in_('organization_id', org_ids) \
            .execute()

        member_counts = {}
        for m in (members_result.data or []):
            org_id = m.get('organization_id')
            member_counts[org_id] = member_counts.get(org_id, 0) + 1

        # Fetch current month usage per org
        cur_start, cur_end = _current_month_range()
        usage_result = supabase.schema(SCHEMA).table('usage_logs') \
            .select('organization_id, messages_in, messages_out, llm_input_tokens, llm_output_tokens') \
            .in_('organization_id', org_ids) \
            .gte('period_start', cur_start) \
            .lt('period_start', cur_end) \
            .execute()

        usage_by_org = {}
        for u in (usage_result.data or []):
            org_id = u.get('organization_id')
            if org_id not in usage_by_org:
                usage_by_org[org_id] = {"messages": 0, "tokens": 0}
            usage_by_org[org_id]["messages"] += (u.get('messages_in') or 0) + (u.get('messages_out') or 0)
            usage_by_org[org_id]["tokens"] += (u.get('llm_input_tokens') or 0) + (u.get('llm_output_tokens') or 0)

        # Fetch last activity per org from tenant_events
        events_result = supabase.schema(SCHEMA).table('tenant_events') \
            .select('organization_id, created_at') \
            .in_('organization_id', org_ids) \
            .order('created_at', desc=True) \
            .limit(len(org_ids) * 1) \
            .execute()

        last_activity = {}
        for e in (events_result.data or []):
            org_id = e.get('organization_id')
            if org_id not in last_activity:
                last_activity[org_id] = e.get('created_at')

        # Assemble response
        organizations = []
        for org in orgs_page:
            org_id = org['id']
            team_info = teams_by_org.get(org_id, {"count": 0, "agent_types": set()})
            usage = usage_by_org.get(org_id, {"messages": 0, "tokens": 0})

            organizations.append({
                "id": org_id,
                "name": org.get('name'),
                "slug": org.get('slug'),
                "activation_status": org.get('activation_status'),
                "subscription_plan": org.get('subscription_plan'),
                "subscription_status": org.get('subscription_status'),
                "trial_ends_at": org.get('trial_ends_at'),
                "team_count": team_info["count"],
                "member_count": member_counts.get(org_id, 0),
                "agent_types": sorted(team_info["agent_types"]),
                "current_month_messages": usage["messages"],
                "current_month_tokens": usage["tokens"],
                "last_activity_at": last_activity.get(org_id),
                "created_at": org.get('created_at'),
            })

        return {
            "organizations": organizations,
            "total": total,
            "page": page,
            "per_page": per_page,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching organizations: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch organizations")


# ============================================================================
# Endpoint 3: Organization Details
# ============================================================================

@router.get("/organizations/{org_id}/details")
async def get_organization_details(
    org_id: str,
    user: TokenPayload = Depends(require_superadmin()),
):
    """
    Deep-dive for a single organization: teams, members, integrations, usage history, events.
    """
    supabase = _get_supabase()

    try:
        # Organization
        org_result = supabase.schema(SCHEMA).table('organizations') \
            .select('*') \
            .eq('id', org_id) \
            .single() \
            .execute()

        if not org_result.data:
            raise HTTPException(status_code=404, detail="Organization not found")

        # Teams
        teams_result = supabase.schema(SCHEMA).table('teams') \
            .select('id, name, agent_type, is_active, created_at') \
            .eq('organization_id', org_id) \
            .execute()

        # Members
        members_result = supabase.schema(SCHEMA).table('team_members') \
            .select('id, name, role, is_superadmin, created_at') \
            .eq('organization_id', org_id) \
            .execute()

        # Integrations
        integrations_result = supabase.schema(SCHEMA).table('integrations') \
            .select('id, type, provider, status, phone_number, created_at, updated_at') \
            .eq('organization_id', org_id) \
            .execute()

        # Usage history (last 6 months)
        six_months_ago = (datetime.now(timezone.utc) - timedelta(days=180)).isoformat()
        usage_result = supabase.schema(SCHEMA).table('usage_logs') \
            .select('*') \
            .eq('organization_id', org_id) \
            .gte('period_start', six_months_ago) \
            .order('period_start', desc=True) \
            .execute()

        # Recent events (last 50)
        events_result = supabase.schema(SCHEMA).table('tenant_events') \
            .select('event_type, metadata, created_at') \
            .eq('organization_id', org_id) \
            .order('created_at', desc=True) \
            .limit(50) \
            .execute()

        # Onboarding
        onboarding_result = supabase.schema(SCHEMA).table('onboarding_progress') \
            .select('*') \
            .eq('organization_id', org_id) \
            .execute()

        return {
            "organization": org_result.data,
            "teams": teams_result.data or [],
            "members": members_result.data or [],
            "integrations": integrations_result.data or [],
            "usage_history": usage_result.data or [],
            "recent_events": events_result.data or [],
            "onboarding": (onboarding_result.data or [None])[0],
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching org details for {org_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch organization details")


# ============================================================================
# Endpoint 4: Recent Errors
# ============================================================================

@router.get("/recent-errors")
async def get_recent_errors(
    limit: int = Query(100, ge=1, le=500),
    user: TokenPayload = Depends(require_superadmin()),
):
    """
    Recent errors across all organizations from tenant_events.
    """
    supabase = _get_supabase()

    try:
        # Get error events
        events_result = supabase.schema(SCHEMA).table('tenant_events') \
            .select('organization_id, event_type, metadata, created_at') \
            .eq('event_type', 'error_occurred') \
            .order('created_at', desc=True) \
            .limit(limit) \
            .execute()

        events = events_result.data or []

        # Get org names for the error events
        org_ids = list(set(e.get('organization_id') for e in events if e.get('organization_id')))

        org_names = {}
        if org_ids:
            orgs_result = supabase.schema(SCHEMA).table('organizations') \
                .select('id, name') \
                .in_('id', org_ids) \
                .execute()
            org_names = {o['id']: o.get('name') for o in (orgs_result.data or [])}

        errors = []
        for e in events:
            metadata = e.get('metadata') or {}
            errors.append({
                "organization_id": e.get('organization_id'),
                "organization_name": org_names.get(e.get('organization_id'), "Unknown"),
                "error_type": metadata.get('error_type', 'unknown'),
                "error_message": metadata.get('error_message') or metadata.get('message', ''),
                "created_at": e.get('created_at'),
            })

        return {"errors": errors}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching recent errors: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch recent errors")
