"""
Sales preview chat API - secure preview endpoint for testing sales agent.

Features:
- Authentication required (only org owners/admins can preview)
- Rate limiting (20/minute per user)
- Sandbox mode (no real bookings or external actions)
- Separate preview sessions (not mixed with production)
"""
import logging
import secrets
from typing import Optional, List
from datetime import datetime, timedelta, timezone
from pydantic import BaseModel, Field

from fastapi import APIRouter, Depends, HTTPException, Request
from app.middleware.auth import require_auth, TokenPayload
from app.middleware.rate_limiter import rate_limit
from app.services.database_manager import get_database_manager, DatabaseType
from app.api.sales_invitations_api import get_sales_org_for_user

router = APIRouter(prefix="/api/sales/preview", tags=["sales-preview"])
logger = logging.getLogger(__name__)


# ============================================================================
# Request/Response Models
# ============================================================================

class PreviewMessageRequest(BaseModel):
    """Request to send a message to the preview agent."""
    message: str = Field(..., min_length=1, max_length=4000)
    session_token: Optional[str] = None


class PreviewMessageResponse(BaseModel):
    """Response from preview agent."""
    response: str
    session_token: str
    tool_calls: List[dict] = []
    sandbox_mode: bool = True
    metadata: dict = {}


class SuggestedQuestion(BaseModel):
    """Suggested question for preview testing."""
    label: str
    message: str


class PreviewSessionInfo(BaseModel):
    """Preview session information."""
    session_token: str
    created_at: str
    expires_at: str
    message_count: int
    organization_id: str


# ============================================================================
# Helper Functions
# ============================================================================

def get_sales_org_for_user_with_role(supabase, user_id: str) -> Optional[dict]:
    """Get user's sales organization membership with role check."""
    membership = get_sales_org_for_user(supabase, user_id)
    if not membership:
        return None

    # Only owners and admins can use preview
    if membership.get('role') not in ('owner', 'admin') and not membership.get('is_superadmin'):
        return None

    return membership


async def get_or_create_preview_session(
    supabase,
    organization_id: str,
    user_id: str,
    session_token: Optional[str] = None,
    expires_in_minutes: int = 30
) -> dict:
    """Get existing preview session or create new one."""
    now = datetime.now(timezone.utc)

    if session_token:
        # Try to get existing session
        result = supabase.schema('sales').table('preview_sessions').select(
            'id, session_token, messages, expires_at, created_at'
        ).eq('session_token', session_token).eq('user_id', user_id).single().execute()

        if result.data:
            expires_at = datetime.fromisoformat(result.data['expires_at'].replace('Z', '+00:00'))
            if expires_at > now:
                return result.data
            else:
                # Session expired, delete it
                supabase.schema('sales').table('preview_sessions').delete().eq(
                    'id', result.data['id']
                ).execute()

    # Create new session
    new_token = secrets.token_urlsafe(32)
    expires_at = now + timedelta(minutes=expires_in_minutes)

    result = supabase.schema('sales').table('preview_sessions').insert({
        'organization_id': organization_id,
        'user_id': user_id,
        'session_token': new_token,
        'messages': [],
        'expires_at': expires_at.isoformat(),
        'created_at': now.isoformat()
    }).execute()

    return result.data[0]


async def append_preview_message(
    supabase,
    session_id: str,
    user_message: str,
    agent_response: str,
    tool_calls: Optional[List[dict]] = None
):
    """Append messages to preview session."""
    # Get current messages
    result = supabase.schema('sales').table('preview_sessions').select(
        'messages'
    ).eq('id', session_id).single().execute()

    messages = result.data.get('messages', []) if result.data else []

    # Add new messages
    messages.append({
        'role': 'user',
        'content': user_message,
        'timestamp': datetime.now(timezone.utc).isoformat()
    })
    messages.append({
        'role': 'assistant',
        'content': agent_response,
        'tool_calls': tool_calls or [],
        'timestamp': datetime.now(timezone.utc).isoformat()
    })

    # Update session
    supabase.schema('sales').table('preview_sessions').update({
        'messages': messages
    }).eq('id', session_id).execute()


async def get_org_config(supabase, organization_id: str) -> Optional[dict]:
    """Get organization config for preview."""
    try:
        result = supabase.schema('sales').table('organization_configs').select(
            '*'
        ).eq('organization_id', organization_id).single().execute()
        return result.data
    except Exception as e:
        logger.warning(f"Failed to get org config: {e}")
        return None


async def track_preview_usage(supabase, organization_id: str, tokens_used: int = 0):
    """Track preview usage in usage_logs."""
    now = datetime.now(timezone.utc)
    period_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    try:
        # Upsert usage log
        supabase.schema('sales').rpc('increment_usage_counter', {
            'p_organization_id': organization_id,
            'p_period_start': period_start.date().isoformat(),
            'p_field': 'messages_out',
            'p_amount': 1
        }).execute()
    except Exception as e:
        logger.warning(f"Failed to track preview usage: {e}")


# ============================================================================
# Preview Endpoints
# ============================================================================

@router.post("/chat", response_model=PreviewMessageResponse)
@rate_limit(requests_per_minute=20, burst_size=5)
async def preview_chat(
    request: Request,
    body: PreviewMessageRequest,
    user: TokenPayload = Depends(require_auth)
):
    """
    Send a message to the preview sales agent.

    This endpoint:
    - Requires authentication
    - Rate limits to 20 requests per minute
    - Runs in sandbox mode (no real bookings)
    - Uses separate preview sessions
    """
    db_manager = get_database_manager()
    supabase = db_manager.get_client(DatabaseType.MAIN)

    # Verify user has access to a sales organization
    membership = get_sales_org_for_user_with_role(supabase, user.sub)
    if not membership:
        raise HTTPException(
            status_code=403,
            detail="You must be an owner or admin of a sales organization to use preview"
        )

    organization_id = membership['organization_id']

    # Get or create preview session
    session = await get_or_create_preview_session(
        supabase=supabase,
        organization_id=organization_id,
        user_id=user.sub,
        session_token=body.session_token,
        expires_in_minutes=30
    )

    # Get org config for context
    org_config = await get_org_config(supabase, organization_id)
    if not org_config:
        raise HTTPException(
            status_code=400,
            detail="Organization configuration not found. Complete onboarding first."
        )

    # Build preview context (sandbox mode)
    preview_context = {
        'organization_id': organization_id,
        'sandbox': True,  # CRITICAL: Enable sandbox mode
        'is_preview': True,
        'org_config': org_config,
        'lead_name': 'Preview User',
        'contact_name': 'Preview User',
        'is_existing_lead': False,
        'preferred_language': org_config.get('primary_language', 'en'),
    }

    try:
        # Import sales agent dynamically to avoid circular imports
        import sys
        import os

        # Add claude-agent to path if needed
        claude_agent_path = os.path.join(
            os.path.dirname(__file__),
            '..', '..', '..', 'claude-agent', 'src'
        )
        if claude_agent_path not in sys.path:
            sys.path.insert(0, os.path.abspath(claude_agent_path))

        from agent.sales_agent import get_sales_agent

        sales_agent = get_sales_agent()

        # Process message through agent
        session_id = f"preview_{session['session_token']}"
        response = await sales_agent.process_message(
            message=body.message,
            session_id=session_id,
            context=preview_context
        )

        # Extract response content and tool calls
        response_content = response.content if hasattr(response, 'content') else str(response)
        tool_calls = []
        if hasattr(response, 'tool_calls') and response.tool_calls:
            tool_calls = [
                {
                    'name': tc.get('name', 'unknown'),
                    'result': tc.get('result', {}),
                    'sandbox': True
                }
                for tc in response.tool_calls
            ]

        # Store in preview session
        await append_preview_message(
            supabase=supabase,
            session_id=session['id'],
            user_message=body.message,
            agent_response=response_content,
            tool_calls=tool_calls
        )

        # Track usage
        await track_preview_usage(supabase, organization_id)

        return PreviewMessageResponse(
            response=response_content,
            session_token=session['session_token'],
            tool_calls=tool_calls,
            sandbox_mode=True,
            metadata={
                'organization_id': organization_id,
                'session_id': session['id']
            }
        )

    except ImportError as e:
        logger.error(f"Failed to import sales agent: {e}")
        # Fallback to a simple echo response for development
        fallback_response = (
            f"[Preview Mode] I received your message: \"{body.message}\"\n\n"
            "The sales agent is configured for your organization. "
            "In production, I would respond based on your product knowledge and qualification setup."
        )

        await append_preview_message(
            supabase=supabase,
            session_id=session['id'],
            user_message=body.message,
            agent_response=fallback_response,
            tool_calls=[]
        )

        return PreviewMessageResponse(
            response=fallback_response,
            session_token=session['session_token'],
            tool_calls=[],
            sandbox_mode=True,
            metadata={'fallback': True}
        )

    except Exception as e:
        logger.error(f"Preview chat error: {e}")
        raise HTTPException(status_code=500, detail=f"Preview error: {str(e)}")


@router.get("/suggestions", response_model=List[SuggestedQuestion])
async def get_suggestions(user: TokenPayload = Depends(require_auth)):
    """
    Get suggested questions for testing the preview agent.

    These help users test key agent capabilities.
    """
    db_manager = get_database_manager()
    supabase = db_manager.get_client(DatabaseType.MAIN)

    membership = get_sales_org_for_user_with_role(supabase, user.sub)
    if not membership:
        raise HTTPException(status_code=403, detail="Not authorized")

    # Get org config to customize suggestions
    org_config = await get_org_config(supabase, membership['organization_id'])
    company_name = org_config.get('company_name', 'your company') if org_config else 'your company'

    suggestions = [
        SuggestedQuestion(
            label="Ask about company",
            message=f"Tell me about {company_name}"
        ),
        SuggestedQuestion(
            label="Ask about pricing",
            message="What are your pricing options?"
        ),
        SuggestedQuestion(
            label="Ask about features",
            message="What features do you offer?"
        ),
        SuggestedQuestion(
            label="Try booking",
            message="I'd like to schedule a demo call"
        ),
        SuggestedQuestion(
            label="Test qualification",
            message="I'm looking for a solution for my 50-person team, we need it within 2 months"
        ),
    ]

    return suggestions


@router.get("/session", response_model=PreviewSessionInfo)
async def get_session_info(
    session_token: str,
    user: TokenPayload = Depends(require_auth)
):
    """Get information about a preview session."""
    db_manager = get_database_manager()
    supabase = db_manager.get_client(DatabaseType.MAIN)

    result = supabase.schema('sales').table('preview_sessions').select(
        'id, session_token, created_at, expires_at, messages, organization_id, user_id'
    ).eq('session_token', session_token).single().execute()

    if not result.data:
        raise HTTPException(status_code=404, detail="Session not found")

    session = result.data

    # Verify user owns this session
    if session['user_id'] != user.sub:
        raise HTTPException(status_code=403, detail="Not authorized")

    messages = session.get('messages', [])

    return PreviewSessionInfo(
        session_token=session['session_token'],
        created_at=session['created_at'],
        expires_at=session['expires_at'],
        message_count=len(messages),
        organization_id=session['organization_id']
    )


@router.delete("/session")
async def delete_session(
    session_token: str,
    user: TokenPayload = Depends(require_auth)
):
    """Delete a preview session."""
    db_manager = get_database_manager()
    supabase = db_manager.get_client(DatabaseType.MAIN)

    result = supabase.schema('sales').table('preview_sessions').delete().eq(
        'session_token', session_token
    ).eq('user_id', user.sub).execute()

    if not result.data:
        raise HTTPException(status_code=404, detail="Session not found")

    return {"success": True}


@router.get("/history")
async def get_session_history(
    session_token: str,
    user: TokenPayload = Depends(require_auth)
):
    """Get message history for a preview session."""
    db_manager = get_database_manager()
    supabase = db_manager.get_client(DatabaseType.MAIN)

    result = supabase.schema('sales').table('preview_sessions').select(
        'messages, user_id'
    ).eq('session_token', session_token).single().execute()

    if not result.data:
        raise HTTPException(status_code=404, detail="Session not found")

    if result.data['user_id'] != user.sub:
        raise HTTPException(status_code=403, detail="Not authorized")

    return {"messages": result.data.get('messages', [])}
