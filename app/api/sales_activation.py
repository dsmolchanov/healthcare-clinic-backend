"""
Sales Activation API - Validation and activation controls.

Features:
- Pre-activation validation (minimum requirements)
- Activation/deactivation with state management
- Support bundle export for debugging
"""
import logging
import re
from typing import List
from datetime import datetime, timezone
from pydantic import BaseModel

from fastapi import APIRouter, Depends, HTTPException
from app.middleware.auth import require_auth, TokenPayload
from app.services.database_manager import get_database_manager, DatabaseType
from app.api.sales_invitations_api import get_sales_org_for_user

router = APIRouter(prefix="/api/sales/activation", tags=["sales-activation"])
logger = logging.getLogger(__name__)


# ============================================================================
# Request/Response Models
# ============================================================================

class ValidationResult(BaseModel):
    """Result of activation validation."""
    valid: bool
    can_activate: bool
    errors: List[str]
    warnings: List[str]


class ActivationResult(BaseModel):
    """Result of activation/deactivation."""
    success: bool
    status: str
    message: str


class SupportBundle(BaseModel):
    """Support bundle for debugging."""
    organization: dict
    integration_status: dict
    recent_events: List[dict]
    recent_errors: List[dict]
    usage_summary: dict
    generated_at: str


# ============================================================================
# Helper Functions
# ============================================================================

def is_valid_email(email: str) -> bool:
    """Basic email validation."""
    pattern = r'^[\w\.-]+@[\w\.-]+\.\w+$'
    return bool(re.match(pattern, email)) if email else False


# ============================================================================
# Endpoints
# ============================================================================

@router.post("/validate", response_model=ValidationResult)
async def validate_activation(user: TokenPayload = Depends(require_auth)):
    """
    Validate organization configuration before allowing activation.

    Checks minimum requirements:
    - Company name (2-255 chars)
    - Company description (10+ chars)
    - Escalation email (valid format)
    - Product info (non-empty general section)
    """
    db_manager = get_database_manager()
    supabase = db_manager.get_client(DatabaseType.MAIN)

    # Get user's organization
    membership = get_sales_org_for_user(supabase, user.sub)
    if not membership:
        raise HTTPException(status_code=403, detail="Not a member of any sales organization")

    organization_id = membership['organization_id']

    # Get organization config
    config_result = supabase.schema('sales').table('organization_configs').select(
        'company_name, company_description, escalation_email, product_info, qualification_questions, scoring_criteria'
    ).eq('organization_id', organization_id).execute()

    errors = []
    warnings = []

    if not config_result.data:
        errors.append("Organization configuration not found. Please complete onboarding.")
        return ValidationResult(
            valid=False,
            can_activate=False,
            errors=errors,
            warnings=warnings
        )

    config = config_result.data[0]

    # Validate company_name
    company_name = config.get('company_name', '')
    if not company_name:
        errors.append("Company name is required")
    elif len(company_name) < 2:
        errors.append("Company name must be at least 2 characters")
    elif len(company_name) > 255:
        errors.append("Company name must be less than 255 characters")

    # Validate company_description
    company_description = config.get('company_description', '')
    if not company_description:
        errors.append("Company description is required")
    elif len(company_description) < 10:
        errors.append("Company description must be at least 10 characters")

    # Validate escalation_email
    escalation_email = config.get('escalation_email', '')
    if not escalation_email:
        errors.append("Escalation email is required")
    elif not is_valid_email(escalation_email):
        errors.append("Please provide a valid escalation email address")

    # Validate product_info
    product_info = config.get('product_info', {})
    if not product_info:
        errors.append("Basic product information is required")
    else:
        general = product_info.get('general', {})
        if not general or not general.get('content'):
            errors.append("General product description is required")

    # Warnings (non-blocking)
    qualification_questions = config.get('qualification_questions', [])
    if not qualification_questions or len(qualification_questions) == 0:
        warnings.append("Consider adding qualification questions to better qualify leads")

    scoring_criteria = config.get('scoring_criteria', {})
    if not scoring_criteria:
        warnings.append("Consider adding scoring criteria to prioritize leads")

    # Check WhatsApp integration status
    integration_result = supabase.schema('sales').table('integrations').select(
        'status, phone_number'
    ).eq('organization_id', organization_id).eq('type', 'whatsapp').eq('enabled', True).execute()

    if not integration_result.data:
        warnings.append("WhatsApp not connected. Agent will only work in web preview mode.")
    elif integration_result.data[0].get('status') != 'connected':
        warnings.append("WhatsApp not fully connected. Scan QR code to complete setup.")

    return ValidationResult(
        valid=len(errors) == 0,
        can_activate=len(errors) == 0,
        errors=errors,
        warnings=warnings
    )


@router.post("/activate", response_model=ActivationResult)
async def activate_organization(user: TokenPayload = Depends(require_auth)):
    """
    Activate the sales agent for the organization.

    Sets activation_status to 'active' which enables:
    - Agent responding to WhatsApp messages
    - Real bookings being created
    - Lead tracking
    """
    db_manager = get_database_manager()
    supabase = db_manager.get_client(DatabaseType.MAIN)

    # Verify ownership
    membership = get_sales_org_for_user(supabase, user.sub)
    if not membership:
        raise HTTPException(status_code=403, detail="Not a member of any sales organization")

    if membership.get('role') not in ('owner', 'admin') and not membership.get('is_superadmin'):
        raise HTTPException(status_code=403, detail="Only admins can activate agents")

    organization_id = membership['organization_id']

    # Validate first
    validation = await validate_activation(user)
    if not validation.can_activate:
        return ActivationResult(
            success=False,
            status="invalid",
            message=f"Cannot activate: {'; '.join(validation.errors)}"
        )

    # Activate
    now = datetime.now(timezone.utc).isoformat()

    supabase.schema('sales').table('organizations').update({
        "activation_status": "active",
        "updated_at": now
    }).eq('id', organization_id).execute()

    # Log activation event
    supabase.schema('sales').table('tenant_events').insert({
        "organization_id": organization_id,
        "event_type": "agent_activated",
        "metadata": {
            "activated_by": user.sub,
            "timestamp": now
        }
    }).execute()

    return ActivationResult(
        success=True,
        status="active",
        message="Sales agent activated successfully. Your agent is now live."
    )


@router.post("/deactivate", response_model=ActivationResult)
async def deactivate_organization(user: TokenPayload = Depends(require_auth)):
    """
    Deactivate (pause) the sales agent for the organization.

    Sets activation_status to 'paused' which means:
    - Agent stops responding to WhatsApp messages
    - Messages queue for human review
    - No new bookings created
    """
    db_manager = get_database_manager()
    supabase = db_manager.get_client(DatabaseType.MAIN)

    # Verify ownership
    membership = get_sales_org_for_user(supabase, user.sub)
    if not membership:
        raise HTTPException(status_code=403, detail="Not a member of any sales organization")

    if membership.get('role') not in ('owner', 'admin') and not membership.get('is_superadmin'):
        raise HTTPException(status_code=403, detail="Only admins can deactivate agents")

    organization_id = membership['organization_id']

    # Deactivate
    now = datetime.now(timezone.utc).isoformat()

    supabase.schema('sales').table('organizations').update({
        "activation_status": "paused",
        "updated_at": now
    }).eq('id', organization_id).execute()

    # Log deactivation event
    supabase.schema('sales').table('tenant_events').insert({
        "organization_id": organization_id,
        "event_type": "agent_deactivated",
        "metadata": {
            "deactivated_by": user.sub,
            "timestamp": now
        }
    }).execute()

    return ActivationResult(
        success=True,
        status="paused",
        message="Sales agent paused. Messages will queue for human review."
    )


@router.get("/status")
async def get_activation_status(user: TokenPayload = Depends(require_auth)):
    """
    Get the current activation status.
    """
    db_manager = get_database_manager()
    supabase = db_manager.get_client(DatabaseType.MAIN)

    membership = get_sales_org_for_user(supabase, user.sub)
    if not membership:
        raise HTTPException(status_code=403, detail="Not a member of any sales organization")

    organization_id = membership['organization_id']

    # Get organization status
    org_result = supabase.schema('sales').table('organizations').select(
        'activation_status, subscription_status, trial_ends_at'
    ).eq('id', organization_id).execute()

    if not org_result.data:
        raise HTTPException(status_code=404, detail="Organization not found")

    org = org_result.data[0]

    return {
        "activation_status": org.get('activation_status', 'inactive'),
        "subscription_status": org.get('subscription_status', 'trial'),
        "trial_ends_at": org.get('trial_ends_at')
    }


@router.get("/support-bundle", response_model=SupportBundle)
async def get_support_bundle(user: TokenPayload = Depends(require_auth)):
    """
    Export support bundle for debugging organization issues.

    Includes:
    - Organization summary
    - Integration status
    - Recent events
    - Recent errors
    - Usage summary
    """
    db_manager = get_database_manager()
    supabase = db_manager.get_client(DatabaseType.MAIN)

    # Verify admin access
    membership = get_sales_org_for_user(supabase, user.sub)
    if not membership:
        raise HTTPException(status_code=403, detail="Not a member of any sales organization")

    if membership.get('role') not in ('owner', 'admin') and not membership.get('is_superadmin'):
        raise HTTPException(status_code=403, detail="Admin access required for support bundle")

    organization_id = membership['organization_id']

    # Get organization summary
    org_result = supabase.schema('sales').table('organizations').select(
        'name, activation_status, subscription_status, trial_ends_at, created_at'
    ).eq('id', organization_id).execute()

    organization = org_result.data[0] if org_result.data else {}

    # Get integration status
    integration_result = supabase.schema('sales').table('integrations').select(
        'type, provider, status, phone_number, instance_name, created_at, updated_at'
    ).eq('organization_id', organization_id).execute()

    integration_status = {
        "integrations": integration_result.data or [],
        "whatsapp_connected": any(
            i.get('type') == 'whatsapp' and i.get('status') == 'connected'
            for i in (integration_result.data or [])
        )
    }

    # Get recent events
    events_result = supabase.schema('sales').table('tenant_events').select(
        'event_type, metadata, created_at'
    ).eq('organization_id', organization_id).order('created_at', desc=True).limit(20).execute()

    recent_events = events_result.data or []

    # Get recent errors (from agent logs)
    # For now, return empty - would integrate with actual logging system
    recent_errors = []

    # Get usage summary
    usage_result = supabase.schema('sales').table('usage_logs').select(
        'period_start, messages_in, messages_out, llm_input_tokens, llm_output_tokens, leads_created, calls_scheduled'
    ).eq('organization_id', organization_id).order('period_start', desc=True).limit(3).execute()

    usage_summary = {
        "periods": usage_result.data or [],
        "current_month": usage_result.data[0] if usage_result.data else None
    }

    return SupportBundle(
        organization=organization,
        integration_status=integration_status,
        recent_events=recent_events,
        recent_errors=recent_errors,
        usage_summary=usage_summary,
        generated_at=datetime.now(timezone.utc).isoformat()
    )
