"""
Sales WhatsApp Integration API - Secure multi-tenant WhatsApp setup.

Features:
- Hash-based instance naming (collision-free)
- Webhook token hashing + rotation
- Auto-configure with retry + manual fallback
- QR code polling for auto-advance
- Connection verification
"""
import os
import logging
import secrets
import hashlib
from typing import Optional
from datetime import datetime, timedelta, timezone
from pydantic import BaseModel, Field

from fastapi import APIRouter, Depends, HTTPException
from app.middleware.auth import require_auth, TokenPayload
from app.services.database_manager import get_database_manager, DatabaseType
from app.api.sales_invitations_api import get_sales_org_for_user
from app.evolution_api import EvolutionAPIClient
from tenacity import retry, stop_after_attempt, wait_exponential

router = APIRouter(prefix="/api/sales/whatsapp", tags=["sales-whatsapp"])
logger = logging.getLogger(__name__)


# ============================================================================
# Request/Response Models
# ============================================================================

class WhatsAppSetupRequest(BaseModel):
    """Request to set up WhatsApp integration."""
    custom_instance_name: Optional[str] = None  # Optional custom name


class WhatsAppSetupResponse(BaseModel):
    """Response from WhatsApp setup."""
    success: bool
    instance_name: str
    webhook_url: str
    webhook_token: str
    qr_code: Optional[str] = None
    status: str
    message: str
    manual_instructions: Optional[str] = None


class WebhookConfigResponse(BaseModel):
    """Response from webhook configuration."""
    success: bool
    webhook_url: str
    verified: bool
    error: Optional[str] = None
    manual_instructions: Optional[str] = None


class ConnectionStatusResponse(BaseModel):
    """WhatsApp connection status."""
    instance_name: str
    status: str  # disconnected, connecting, connected
    phone_number: Optional[str] = None
    qr_code: Optional[str] = None
    qr_code_updated_at: Optional[str] = None


class TokenRotationResponse(BaseModel):
    """Response from token rotation."""
    success: bool
    new_webhook_url: str
    old_token_valid_until: str
    message: str


# ============================================================================
# Helper Functions
# ============================================================================

def generate_instance_name(organization_id: str) -> str:
    """
    Generate collision-free Evolution instance name using org_id hash.

    Args:
        organization_id: UUID of the organization

    Returns:
        Instance name like "sales-a1b2c3d4"
    """
    org_hash = hashlib.sha256(organization_id.encode()).hexdigest()[:8]
    return f"sales-{org_hash}"


def generate_webhook_token() -> tuple[str, str]:
    """
    Generate webhook token and its hash.

    Returns:
        Tuple of (plain_token, hashed_token)
    """
    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    return token, token_hash


def verify_webhook_token(token: str, stored_hash: str) -> bool:
    """
    Verify token against stored hash.

    Args:
        token: Plain text token
        stored_hash: SHA-256 hash stored in database

    Returns:
        True if token matches hash
    """
    computed_hash = hashlib.sha256(token.encode()).hexdigest()
    return secrets.compare_digest(computed_hash, stored_hash)


def get_webhook_base_url() -> str:
    """Get the base URL for webhooks."""
    return os.environ.get(
        "SALES_WEBHOOK_BASE_URL",
        "https://claude-agent.fly.dev/webhooks/evolution/whatsapp"
    )


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
async def configure_evolution_webhook_with_retry(
    instance_name: str,
    webhook_url: str
) -> dict:
    """
    Configure Evolution webhook with exponential backoff retry.

    Args:
        instance_name: Evolution instance name
        webhook_url: Webhook URL to configure

    Returns:
        Result dict with success status
    """
    async with EvolutionAPIClient() as client:
        result = await client.set_webhook(
            instance_name=instance_name,
            webhook_url=webhook_url,
            events=[
                "QRCODE_UPDATED",
                "MESSAGES_UPSERT",
                "MESSAGES_UPDATE",
                "CONNECTION_UPDATE",
                "SEND_MESSAGE"
            ]
        )
        return result


async def verify_webhook_connection(instance_name: str, token: str) -> bool:
    """
    Verify that webhook is properly configured and reachable.

    Args:
        instance_name: Evolution instance name
        token: Webhook token

    Returns:
        True if webhook is working
    """
    # For now, just check if the webhook was set successfully
    # In production, you might want to send a test message
    try:
        async with EvolutionAPIClient() as client:
            webhook_info = await client.get_webhook(instance_name)
            return webhook_info.get("success", False)
    except Exception as e:
        logger.warning(f"Webhook verification failed: {e}")
        return False


# ============================================================================
# Endpoints
# ============================================================================

@router.post("/setup", response_model=WhatsAppSetupResponse)
async def setup_whatsapp(
    request: WhatsAppSetupRequest,
    user: TokenPayload = Depends(require_auth)
):
    """
    Set up WhatsApp integration for the user's organization.

    This endpoint:
    - Creates an Evolution API instance with collision-free naming
    - Generates secure webhook token
    - Auto-configures webhook with retry
    - Returns QR code for pairing
    """
    db_manager = get_database_manager()
    supabase = db_manager.get_client(DatabaseType.MAIN)

    # Get user's organization
    membership = get_sales_org_for_user(supabase, user.sub)
    if not membership:
        raise HTTPException(status_code=403, detail="Not a member of any sales organization")

    organization_id = membership['organization_id']

    # Check for existing integration
    existing = supabase.schema('sales').table('integrations').select(
        'id, instance_name, webhook_token, status, phone_number'
    ).eq('organization_id', organization_id).eq('type', 'whatsapp').eq('enabled', True).execute()

    if existing.data:
        existing_integration = existing.data[0]
        instance_name = existing_integration.get('instance_name')
        webhook_token = existing_integration.get('webhook_token')

        # Check if instance exists in Evolution
        try:
            async with EvolutionAPIClient() as client:
                status = await client.get_instance_status(instance_name)
                if status.get("exists"):
                    # Instance exists, get QR code if not connected
                    qr_code = None
                    connection_status = "disconnected"

                    connection_state = await client.get_connection_status(instance_name)
                    if connection_state.get("state") == "open":
                        connection_status = "connected"
                    else:
                        qr_code = await client.get_qr_code(instance_name)
                        if qr_code:
                            connection_status = "waiting_qr"

                    webhook_url = f"{get_webhook_base_url()}/{webhook_token}"

                    return WhatsAppSetupResponse(
                        success=True,
                        instance_name=instance_name,
                        webhook_url=webhook_url,
                        webhook_token=webhook_token,
                        qr_code=qr_code,
                        status=connection_status,
                        message="Reusing existing WhatsApp integration"
                    )
        except Exception as e:
            logger.warning(f"Failed to check existing instance: {e}")
            # Continue to create new instance

    # Generate instance name using hash-based strategy
    instance_name = generate_instance_name(organization_id)

    # Generate webhook token
    webhook_token, webhook_token_hash = generate_webhook_token()
    webhook_url = f"{get_webhook_base_url()}/{webhook_token}"

    try:
        # Create Evolution instance
        async with EvolutionAPIClient() as client:
            create_result = await client.create_instance(
                tenant_id=organization_id,
                instance_name=instance_name
            )

            if not create_result.get("success"):
                raise HTTPException(
                    status_code=500,
                    detail=f"Failed to create Evolution instance: {create_result.get('error')}"
                )

            # Configure webhook with retry
            webhook_configured = False
            manual_instructions = None

            try:
                webhook_result = await configure_evolution_webhook_with_retry(
                    instance_name=instance_name,
                    webhook_url=webhook_url
                )
                webhook_configured = webhook_result.get("success", False)
            except Exception as webhook_error:
                logger.error(f"Webhook configuration failed after retries: {webhook_error}")
                manual_instructions = (
                    f"Webhook auto-configuration failed. Please configure manually:\n"
                    f"URL: {webhook_url}\n"
                    f"Events: QRCODE_UPDATED, MESSAGES_UPSERT, MESSAGES_UPDATE, CONNECTION_UPDATE, SEND_MESSAGE"
                )

            # Save integration to database
            now = datetime.now(timezone.utc).isoformat()
            integration_data = {
                "organization_id": organization_id,
                "type": "whatsapp",
                "provider": "evolution",
                "instance_name": instance_name,
                "webhook_token": webhook_token,  # Store plain token for now (will migrate to hash)
                "webhook_token_hash": webhook_token_hash,
                "webhook_url": webhook_url,
                "status": "pending_qr",
                "enabled": True,
                "config": {
                    "webhook_configured": webhook_configured,
                    "created_at": now
                },
                "created_at": now,
                "updated_at": now
            }

            supabase.schema('sales').table('integrations').upsert(
                integration_data,
                on_conflict="organization_id,type"
            ).execute()

            # Get QR code
            qr_code = await client.get_qr_code(instance_name)

            return WhatsAppSetupResponse(
                success=True,
                instance_name=instance_name,
                webhook_url=webhook_url,
                webhook_token=webhook_token,
                qr_code=qr_code,
                status="waiting_qr" if qr_code else "pending",
                message="WhatsApp instance created successfully",
                manual_instructions=manual_instructions
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"WhatsApp setup failed: {e}")
        raise HTTPException(status_code=500, detail=f"Setup failed: {str(e)}")


@router.get("/status", response_model=ConnectionStatusResponse)
async def get_connection_status(user: TokenPayload = Depends(require_auth)):
    """
    Get WhatsApp connection status for the user's organization.

    This endpoint supports polling for QR code connection status.
    """
    db_manager = get_database_manager()
    supabase = db_manager.get_client(DatabaseType.MAIN)

    # Get user's organization
    membership = get_sales_org_for_user(supabase, user.sub)
    if not membership:
        raise HTTPException(status_code=403, detail="Not a member of any sales organization")

    organization_id = membership['organization_id']

    # Get existing integration
    result = supabase.schema('sales').table('integrations').select(
        'instance_name, status, phone_number'
    ).eq('organization_id', organization_id).eq('type', 'whatsapp').eq('enabled', True).execute()

    if not result.data:
        return ConnectionStatusResponse(
            instance_name="",
            status="not_configured",
            phone_number=None,
            qr_code=None
        )

    integration = result.data[0]
    instance_name = integration.get('instance_name')

    if not instance_name:
        return ConnectionStatusResponse(
            instance_name="",
            status="not_configured",
            phone_number=None,
            qr_code=None
        )

    try:
        async with EvolutionAPIClient() as client:
            # Get connection status
            connection_state = await client.get_connection_status(instance_name)
            state = connection_state.get("state", "close")

            if state == "open":
                # Update database with phone number
                phone_number = connection_state.get("phone_number")
                if phone_number:
                    supabase.schema('sales').table('integrations').update({
                        "status": "connected",
                        "phone_number": phone_number,
                        "updated_at": datetime.now(timezone.utc).isoformat()
                    }).eq('organization_id', organization_id).eq('type', 'whatsapp').execute()

                return ConnectionStatusResponse(
                    instance_name=instance_name,
                    status="connected",
                    phone_number=phone_number,
                    qr_code=None
                )

            # Get QR code if not connected
            qr_code = await client.get_qr_code(instance_name)

            return ConnectionStatusResponse(
                instance_name=instance_name,
                status="waiting_qr" if qr_code else "disconnected",
                phone_number=None,
                qr_code=qr_code,
                qr_code_updated_at=datetime.now(timezone.utc).isoformat() if qr_code else None
            )

    except Exception as e:
        logger.error(f"Failed to get connection status: {e}")
        return ConnectionStatusResponse(
            instance_name=instance_name,
            status="error",
            phone_number=None,
            qr_code=None
        )


@router.post("/rotate-token", response_model=TokenRotationResponse)
async def rotate_webhook_token(user: TokenPayload = Depends(require_auth)):
    """
    Rotate the webhook token with a grace period for the old token.

    The old token remains valid for 1 hour to prevent message loss during rotation.
    """
    db_manager = get_database_manager()
    supabase = db_manager.get_client(DatabaseType.MAIN)

    # Verify user is admin or owner
    membership = get_sales_org_for_user(supabase, user.sub)
    if not membership:
        raise HTTPException(status_code=403, detail="Not a member of any sales organization")

    if membership.get('role') not in ('owner', 'admin') and not membership.get('is_superadmin'):
        raise HTTPException(status_code=403, detail="Only admins can rotate tokens")

    organization_id = membership['organization_id']

    # Get existing integration
    result = supabase.schema('sales').table('integrations').select(
        'id, instance_name, webhook_token_hash'
    ).eq('organization_id', organization_id).eq('type', 'whatsapp').eq('enabled', True).execute()

    if not result.data:
        raise HTTPException(status_code=404, detail="No WhatsApp integration found")

    integration = result.data[0]
    instance_name = integration.get('instance_name')

    # Generate new token
    new_token, new_token_hash = generate_webhook_token()
    new_webhook_url = f"{get_webhook_base_url()}/{new_token}"
    old_token_valid_until = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()

    # Update database
    supabase.schema('sales').table('integrations').update({
        "webhook_token": new_token,
        "webhook_token_hash": new_token_hash,
        "webhook_url": new_webhook_url,
        "token_rotated_at": datetime.now(timezone.utc).isoformat(),
        "previous_token_valid_until": old_token_valid_until,
        "updated_at": datetime.now(timezone.utc).isoformat()
    }).eq('id', integration['id']).execute()

    # Update webhook in Evolution
    try:
        await configure_evolution_webhook_with_retry(instance_name, new_webhook_url)
    except Exception as e:
        logger.warning(f"Failed to update webhook in Evolution: {e}")
        # The old token will still work for 1 hour

    return TokenRotationResponse(
        success=True,
        new_webhook_url=new_webhook_url,
        old_token_valid_until=old_token_valid_until,
        message="Token rotated successfully. Old token valid for 1 hour."
    )


@router.post("/disconnect")
async def disconnect_whatsapp(user: TokenPayload = Depends(require_auth)):
    """
    Disconnect WhatsApp integration (logout from WhatsApp, keep instance).
    """
    db_manager = get_database_manager()
    supabase = db_manager.get_client(DatabaseType.MAIN)

    membership = get_sales_org_for_user(supabase, user.sub)
    if not membership:
        raise HTTPException(status_code=403, detail="Not a member of any sales organization")

    organization_id = membership['organization_id']

    # Get existing integration
    result = supabase.schema('sales').table('integrations').select(
        'id, instance_name'
    ).eq('organization_id', organization_id).eq('type', 'whatsapp').eq('enabled', True).execute()

    if not result.data:
        raise HTTPException(status_code=404, detail="No WhatsApp integration found")

    integration = result.data[0]
    instance_name = integration.get('instance_name')

    try:
        async with EvolutionAPIClient() as client:
            await client.logout_instance(instance_name)

        # Update status in database
        supabase.schema('sales').table('integrations').update({
            "status": "disconnected",
            "phone_number": None,
            "updated_at": datetime.now(timezone.utc).isoformat()
        }).eq('id', integration['id']).execute()

        return {"success": True, "message": "WhatsApp disconnected successfully"}

    except Exception as e:
        logger.error(f"Failed to disconnect WhatsApp: {e}")
        raise HTTPException(status_code=500, detail=f"Disconnect failed: {str(e)}")


@router.delete("/delete")
async def delete_whatsapp_integration(user: TokenPayload = Depends(require_auth)):
    """
    Delete WhatsApp integration completely (removes Evolution instance).
    """
    db_manager = get_database_manager()
    supabase = db_manager.get_client(DatabaseType.MAIN)

    # Verify user is admin or owner
    membership = get_sales_org_for_user(supabase, user.sub)
    if not membership:
        raise HTTPException(status_code=403, detail="Not a member of any sales organization")

    if membership.get('role') not in ('owner', 'admin') and not membership.get('is_superadmin'):
        raise HTTPException(status_code=403, detail="Only admins can delete integrations")

    organization_id = membership['organization_id']

    # Get existing integration
    result = supabase.schema('sales').table('integrations').select(
        'id, instance_name'
    ).eq('organization_id', organization_id).eq('type', 'whatsapp').execute()

    if not result.data:
        return {"success": True, "message": "No integration to delete"}

    integration = result.data[0]
    instance_name = integration.get('instance_name')

    # Delete Evolution instance
    if instance_name:
        try:
            async with EvolutionAPIClient() as client:
                await client.delete_instance(instance_name)
        except Exception as e:
            logger.warning(f"Failed to delete Evolution instance: {e}")
            # Continue to delete database record

    # Delete from database
    supabase.schema('sales').table('integrations').delete().eq('id', integration['id']).execute()

    return {"success": True, "message": "WhatsApp integration deleted"}
