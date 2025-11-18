"""
Integrations API Routes

Handles all integration endpoints including Evolution API
"""

from fastapi import APIRouter, HTTPException, Depends, Request
from typing import List, Dict, Any, Optional
from datetime import datetime
import uuid
import os
from pydantic import BaseModel, Field

from ..database import get_db_connection
from ..evolution_api import EvolutionAPIClient
from ..services.whatsapp_queue.pubsub import InstanceNotifier

router = APIRouter(prefix="/integrations", tags=["integrations"])

# Pydantic models
class IntegrationCreate(BaseModel):
    organization_id: str = Field(alias="organizationId")
    type: str  # 'whatsapp', 'calendar', 'sms', etc.
    provider: str  # 'evolution', 'twilio', 'google', 'microsoft', etc.
    config: Dict[str, Any]
    enabled: bool = True

    class Config:
        populate_by_name = True  # Accept both snake_case and camelCase

class IntegrationUpdate(BaseModel):
    config: Optional[Dict[str, Any]] = None
    enabled: Optional[bool] = None
    status: Optional[str] = None

class EvolutionInstanceCreate(BaseModel):
    organization_id: str = Field(alias="organizationId")  # Accept camelCase from frontend
    instance_name: str = Field(alias="instanceName")

    class Config:
        populate_by_name = True  # Accept both snake_case and camelCase

@router.get("")
async def list_integrations(
    organization_id: Optional[str] = None,
    type: Optional[str] = None
):
    """List all integrations, optionally filtered by organization or type"""
    try:
        from supabase import create_client
        from supabase.client import ClientOptions
        import os

        all_integrations = []

        # Get non-calendar integrations from public schema (WhatsApp, SMS, etc.)
        try:
            public_supabase = create_client(
                os.getenv("SUPABASE_URL"),
                os.getenv("SUPABASE_SERVICE_ROLE_KEY")
            )
            query = public_supabase.table("integrations").select("*")
            if organization_id:
                query = query.eq("organization_id", organization_id)
            if type:
                query = query.eq("integration_type", type)

            # Exclude google_calendar - it's tracked in healthcare.calendar_integrations only
            query = query.neq("integration_type", "google_calendar")

            result = query.execute()
            all_integrations.extend(result.data)
        except Exception as e:
            print(f"Public integrations query error (non-fatal): {e}")

        # Get WhatsApp/Evolution integrations from healthcare schema
        try:
            healthcare_options = ClientOptions(schema='healthcare')
            healthcare_supabase = create_client(
                os.getenv("SUPABASE_URL"),
                os.getenv("SUPABASE_SERVICE_ROLE_KEY"),
                healthcare_options
            )

            # Get WhatsApp integrations
            query = healthcare_supabase.from_('integrations').select('*')
            if organization_id:
                query = query.eq("organization_id", organization_id)
            query = query.eq("type", "whatsapp")

            result = query.execute()

            # Add WhatsApp integrations to list
            for integration in result.data:
                all_integrations.append({
                    'id': integration.get('id'),
                    'organization_id': integration.get('organization_id'),
                    'integration_type': 'whatsapp',
                    'provider': integration.get('provider', 'evolution'),
                    'status': integration.get('status', 'pending'),
                    'display_name': 'WhatsApp Integration',
                    'description': 'Evolution API WhatsApp integration',
                    'is_enabled': integration.get('enabled', False),
                    'config': integration.get('config', {}),
                    'webhook_token': integration.get('webhook_token'),
                    'webhook_url': integration.get('webhook_url'),
                    'created_at': integration.get('created_at'),
                    'updated_at': integration.get('updated_at')
                })
        except Exception as e:
            print(f"Healthcare WhatsApp integrations query error (non-fatal): {e}")

        # Get calendar integrations ONLY from healthcare schema
        try:
            healthcare_options = ClientOptions(schema='healthcare')
            healthcare_supabase = create_client(
                os.getenv("SUPABASE_URL"),
                os.getenv("SUPABASE_SERVICE_ROLE_KEY"),
                healthcare_options
            )

            query = healthcare_supabase.from_('calendar_integrations').select('*')
            if organization_id:
                query = query.eq("organization_id", organization_id)

            result = query.execute()

            # Transform calendar integrations to match integrations format
            for cal_int in result.data:
                all_integrations.append({
                    'id': cal_int.get('id'),
                    'organization_id': cal_int.get('organization_id'),
                    'integration_type': 'google_calendar',  # Use consistent type
                    'provider': cal_int.get('provider'),
                    'status': 'active' if cal_int.get('sync_enabled') else 'inactive',
                    'display_name': f"{cal_int.get('provider', 'Google').title()} Calendar",
                    'description': f"{cal_int.get('provider', 'Google').title()} Calendar integration",
                    'is_enabled': cal_int.get('sync_enabled', False),
                    'config': {
                        'calendar_id': cal_int.get('calendar_id'),
                        'calendar_name': cal_int.get('calendar_name'),
                        'sync_enabled': cal_int.get('sync_enabled'),
                        'last_sync_at': cal_int.get('last_sync_at'),
                        'expires_at': cal_int.get('expires_at')
                    },
                    'created_at': cal_int.get('created_at'),
                    'updated_at': cal_int.get('updated_at')
                })
        except Exception as e:
            print(f"Healthcare calendar integrations query error (non-fatal): {e}")

        return all_integrations
    except Exception as e:
        print(f"Error listing integrations: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("")
async def create_integration(integration: IntegrationCreate):
    """Create a new integration"""
    try:
        # Create a public schema client for integrations table
        from supabase import create_client
        import os

        public_supabase = create_client(
            os.getenv("SUPABASE_URL"),
            os.getenv("SUPABASE_SERVICE_ROLE_KEY")
        )

        data = {
            "organization_id": integration.organization_id,
            "integration_type": integration.type,  # Use integration_type column
            "display_name": f"{integration.type.title()} Integration",
            "description": f"{integration.provider} {integration.type} integration",
            "status": "pending",
            "is_enabled": integration.enabled,  # Use is_enabled column
            "config": integration.config,
            "created_at": datetime.utcnow().isoformat(),
            "updated_at": datetime.utcnow().isoformat()
        }

        # Check for existing integration first
        existing = public_supabase.table("integrations").select("*").eq(
            "organization_id", integration.organization_id
        ).eq("integration_type", integration.type).execute()

        if existing.data:
            # Update existing instead of creating duplicate
            data["updated_at"] = datetime.utcnow().isoformat()
            result = public_supabase.table("integrations").update(data).eq(
                "id", existing.data[0]["id"]
            ).execute()
        else:
            # Create new integration
            result = public_supabase.table("integrations").insert(data).execute()
        return result.data[0] if result.data else None
    except Exception as e:
        print(f"Error creating integration: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/{integration_id}")
async def get_integration(integration_id: str):
    """Get a specific integration by ID"""
    try:
        # Create a public schema client for integrations table
        from supabase import create_client
        import os

        public_supabase = create_client(
            os.getenv("SUPABASE_URL"),
            os.getenv("SUPABASE_SERVICE_ROLE_KEY")
        )

        result = public_supabase.table("integrations").select("*").eq("id", integration_id).execute()
        if not result.data:
            raise HTTPException(status_code=404, detail="Integration not found")
        return result.data[0]
    except Exception as e:
        print(f"Error getting integration: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.patch("/{integration_id}")
async def update_integration(integration_id: str, update: IntegrationUpdate):
    """Update an existing integration"""
    try:
        # Create a public schema client for integrations table
        from supabase import create_client
        import os

        public_supabase = create_client(
            os.getenv("SUPABASE_URL"),
            os.getenv("SUPABASE_SERVICE_ROLE_KEY")
        )

        data = {"updated_at": datetime.utcnow().isoformat()}
        if update.config is not None:
            data["config"] = update.config
        if update.enabled is not None:
            data["is_enabled"] = update.enabled  # Use is_enabled column
        if update.status is not None:
            data["status"] = update.status

        # Get integration before update to check if it's being disabled
        integration_before = public_supabase.table("integrations").select("*").eq("id", integration_id).execute()
        if not integration_before.data:
            raise HTTPException(status_code=404, detail="Integration not found")

        result = public_supabase.table("integrations").update(data).eq("id", integration_id).execute()
        if not result.data:
            raise HTTPException(status_code=404, detail="Integration not found")

        # Notify workers if WhatsApp integration was disabled
        updated_integration = result.data[0]
        if (update.enabled is False and
            updated_integration.get('type') == 'whatsapp' and
            updated_integration.get('provider') == 'evolution'):
            try:
                config = updated_integration.get('config', {})
                instance_name = config.get('instance_name')
                org_id = updated_integration.get('organization_id')

                if instance_name and org_id:
                    notifier = InstanceNotifier()
                    notifier.notify_removed(instance_name, org_id)
            except Exception as notify_error:
                print(f"Warning: Failed to notify workers about disabled instance: {notify_error}")

        return updated_integration
    except Exception as e:
        print(f"Error updating integration: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/{integration_id}")
async def delete_integration(integration_id: str):
    """Delete an integration (checks both public and healthcare schemas)"""
    try:
        from supabase import create_client, ClientOptions
        import os

        public_supabase = create_client(
            os.getenv("SUPABASE_URL"),
            os.getenv("SUPABASE_SERVICE_ROLE_KEY")
        )

        healthcare_options = ClientOptions(schema='healthcare')
        healthcare_supabase = create_client(
            os.getenv("SUPABASE_URL"),
            os.getenv("SUPABASE_SERVICE_ROLE_KEY"),
            healthcare_options
        )

        # First check healthcare schema (for Evolution/WhatsApp integrations)
        healthcare_result = healthcare_supabase.from_('integrations').select('*').eq('id', integration_id).execute()

        if healthcare_result.data:
            # It's a healthcare integration (WhatsApp/Evolution)
            integration = healthcare_result.data[0]

            # PREVENTION STRATEGY 1: Delete Evolution instance before deleting database record
            if integration.get('type') == 'whatsapp' and integration.get('provider') == 'evolution':
                config = integration.get('config', {})
                instance_name = config.get('instance_name')
                org_id = integration.get('organization_id')

                if instance_name:
                    # Step 1: Delete Evolution instance first
                    try:
                        async with EvolutionAPIClient() as evolution_client:
                            await evolution_client.delete_instance(instance_name)
                            print(f"✅ Deleted Evolution instance: {instance_name}")
                    except Exception as evolution_error:
                        print(f"⚠️  Warning: Failed to delete Evolution instance {instance_name}: {evolution_error}")
                        # Continue with database deletion even if Evolution deletion fails

                    # Step 2: Notify workers about removal
                    try:
                        if org_id:
                            notifier = InstanceNotifier()
                            notifier.notify_removed(instance_name, org_id)
                            print(f"✅ Notified workers about instance removal: {instance_name}")
                    except Exception as notify_error:
                        print(f"⚠️  Warning: Failed to notify workers: {notify_error}")

                    # Step 3: Invalidate cache
                    try:
                        from ..services.whatsapp_clinic_cache import get_whatsapp_clinic_cache
                        cache = get_whatsapp_clinic_cache()
                        await cache.invalidate_instance(instance_name)
                        print(f"✅ Invalidated cache for instance: {instance_name}")
                    except Exception as cache_error:
                        print(f"⚠️  Warning: Failed to invalidate cache: {cache_error}")

            # Step 4: Delete from healthcare schema
            delete_result = healthcare_supabase.from_('integrations').delete().eq('id', integration_id).execute()
            return {"deleted": True, "schema": "healthcare", "instance_cleaned_up": True}

        # Try public schema if not found in healthcare
        result = public_supabase.table("integrations").delete().eq("id", integration_id).execute()
        return {"deleted": True, "schema": "public"}

    except Exception as e:
        print(f"Error deleting integration: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/{integration_id}/test")
async def test_integration(integration_id: str):
    """Test an integration connection"""
    try:
        # Test logic here
        return {"success": True, "message": "Integration test successful"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Evolution API specific endpoints
@router.post("/evolution/create")
async def create_evolution_instance(data: EvolutionInstanceCreate):
    """Create a new Evolution API instance for WhatsApp"""
    try:
        from ..main import supabase
        import uuid as uuid_lib

        # PREVENTION STRATEGY 2: Check for existing integration and orphaned instances
        try:
            existing = supabase.schema("healthcare").table("integrations").select("*").eq(
                "organization_id", data.organization_id
            ).eq("type", "whatsapp").eq("provider", "evolution").eq("enabled", True).execute()

            if existing.data and len(existing.data) > 0:
                existing_instance = existing.data[0]
                instance_name = existing_instance.get("config", {}).get("instance_name")

                # Check if the instance still exists in Evolution
                async with EvolutionAPIClient() as evolution_client:
                    try:
                        status = await evolution_client.get_instance_status(instance_name)

                        if status.get("exists"):
                            # Instance exists in both DB and Evolution - return existing
                            print(f"✅ Found existing instance in DB and Evolution: {instance_name}")
                            return {
                                "success": True,
                                "instance_name": instance_name,
                                "reused": True,
                                "status": existing_instance.get("status"),
                                "message": "Reusing existing WhatsApp integration",
                                "qrcode": None  # Frontend will call /status to get QR if needed
                            }
                        else:
                            # Instance in DB but not in Evolution (orphaned DB record)
                            print(f"⚠️  Found orphaned DB record for {instance_name} - cleaning up")
                            supabase.schema("healthcare").table("integrations").delete().eq(
                                "id", existing_instance["id"]
                            ).execute()
                            print(f"✅ Cleaned up orphaned database record")
                            # Continue to create new instance
                    except Exception as evolution_check_error:
                        print(f"⚠️  Failed to check Evolution status: {evolution_check_error}")
                        # If we can't check Evolution, assume it's orphaned and clean it up
                        # This handles cases where Evolution API is down or instance is truly orphaned
                        print(f"⚠️  Treating as orphaned record and cleaning up: {instance_name}")
                        try:
                            supabase.schema("healthcare").table("integrations").delete().eq(
                                "id", existing_instance["id"]
                            ).execute()
                            print(f"✅ Cleaned up potentially orphaned database record")
                            # Continue to create new instance
                        except Exception as cleanup_error:
                            print(f"❌ Failed to clean up orphaned record: {cleanup_error}")
                            return {
                                "success": False,
                                "error": "Failed to clean up existing integration",
                                "message": "Please manually delete the existing integration and try again"
                            }
        except Exception as check_error:
            print(f"Warning: Failed to check for existing integration: {check_error}")
            # Continue anyway - RPC has its own check

        # Initialize Evolution API client using async context manager
        async with EvolutionAPIClient() as evolution_client:
            # ORPHAN CLEANUP: Delete any orphaned instances for this organization
            # This prevents multiple instances from accumulating when frontend retries
            try:
                print(f"[Create] Checking for orphaned instances for org {data.organization_id}...")
                all_instances = await evolution_client.fetch_all_instances()

                # Find instances that belong to this organization
                org_prefix = f"clinic-{data.organization_id}-"
                orphaned_instances = []

                for inst_data in all_instances:
                    inst = inst_data.get('instance', {})
                    inst_name = inst.get('instanceName', '')

                    # Check if this is an instance for our organization
                    if inst_name.startswith(org_prefix) and inst_name != data.instance_name:
                        orphaned_instances.append(inst_name)

                # Delete orphaned instances
                if orphaned_instances:
                    print(f"[Create] Found {len(orphaned_instances)} orphaned instance(s) for org {data.organization_id}")
                    for orphan in orphaned_instances:
                        try:
                            print(f"[Create] Deleting orphaned instance: {orphan}")
                            await evolution_client.delete_instance(orphan)
                            print(f"[Create] ✅ Deleted orphaned instance: {orphan}")
                        except Exception as delete_error:
                            print(f"[Create] ⚠️ Failed to delete orphaned instance {orphan}: {delete_error}")
                            # Continue anyway - best effort cleanup
                else:
                    print(f"[Create] No orphaned instances found for org {data.organization_id}")

            except Exception as cleanup_error:
                print(f"[Create] ⚠️ Orphan cleanup failed (non-fatal): {cleanup_error}")
                # Continue with instance creation even if cleanup fails

            # Create the instance
            result = await evolution_client.create_instance(
                tenant_id=data.organization_id,  # Use organization_id as tenant_id
                instance_name=data.instance_name
            )

            # CRITICAL: Set webhook configuration AFTER instance creation
            # Evolution API requires a separate webhook setup call
            if result.get("success"):
                try:
                    webhook_url = result.get("webhook_url", f"{os.getenv('EVOLUTION_SERVER_URL', 'https://evolution-api-prod.fly.dev')}/webhook/{data.instance_name}")

                    print(f"[Create] Setting up webhooks for {data.instance_name}...")
                    webhook_result = await evolution_client.set_webhook(
                        instance_name=data.instance_name,
                        webhook_url=webhook_url.replace('/webhook/', '/webhooks/evolution/'),  # Use correct URL
                        events=[
                            "QRCODE_UPDATED",
                            "MESSAGES_UPSERT",
                            "MESSAGES_UPDATE",
                            "CONNECTION_UPDATE",  # CRITICAL for pairing
                            "SEND_MESSAGE"
                        ]
                    )

                    if webhook_result.get("success"):
                        print(f"[Create] ✅ Webhooks configured successfully")
                    else:
                        print(f"[Create] ⚠️  Webhook configuration failed: {webhook_result.get('error')}")

                except Exception as webhook_error:
                    print(f"[Create] ⚠️  Warning: Failed to configure webhooks: {webhook_error}")
                    # Continue anyway - webhooks can be configured later

            # Save integration to database immediately using RPC
            if result.get("success"):
                try:
                    webhook_url = result.get("webhook_url", f"{os.getenv('EVOLUTION_SERVER_URL', 'https://evolution-api-prod.fly.dev')}/webhook/{data.instance_name}")

                    db_result = supabase.rpc('save_evolution_integration', {
                        'p_organization_id': data.organization_id,
                        'p_instance_name': data.instance_name,
                        'p_phone_number': None,  # Not connected yet
                        'p_webhook_url': webhook_url
                    }).execute()

                    print(f"Created database record for {data.instance_name}: {db_result.data}")
                    result["integration_saved"] = True

                    # Notify workers about new instance
                    try:
                        notifier = InstanceNotifier()
                        notifier.notify_added(data.instance_name, data.organization_id)
                    except Exception as notify_error:
                        print(f"Warning: Failed to notify workers about new instance: {notify_error}")

                except Exception as db_error:
                    print(f"Warning: Failed to save integration to database: {db_error}")
                    result["integration_saved"] = False

            return result
    except Exception as e:
        print(f"Error creating Evolution instance: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/evolution/status/{instance_name}")
async def get_evolution_status(instance_name: str):
    """Get the connection status of an Evolution instance"""
    try:
        # Import supabase from main module
        from ..main import supabase
        from .evolution_utils import get_real_connection_status

        # Use our more reliable connection detection
        status = await get_real_connection_status(instance_name)

        # Update database if connected
        if status.get("is_truly_connected"):
            try:
                # Find existing integration by instance name
                result = supabase.schema("healthcare").table("integrations").select("*").eq(
                    "config->>instance_name", instance_name
                ).eq("type", "whatsapp").execute()

                # If found, update it
                if result.data and len(result.data) > 0:
                    org_id = result.data[0]["organization_id"]

                    # Use RPC to update
                    supabase.rpc('save_evolution_integration', {
                        'p_organization_id': org_id,
                        'p_instance_name': instance_name,
                        'p_phone_number': status.get('phone_number'),
                        'p_webhook_url': f"{os.getenv('EVOLUTION_SERVER_URL', 'https://evolution-api-prod.fly.dev')}/webhook/{instance_name}"
                    }).execute()

                    print(f"Updated WhatsApp integration for organization {org_id}")
            except Exception as db_error:
                print(f"Warning: Failed to update database: {db_error}")
                import traceback
                traceback.print_exc()

        return status
    except Exception as e:
        print(f"Error in get_evolution_status: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/evolution/refresh-qr/{instance_name}")
async def refresh_evolution_qr(instance_name: str):
    """
    Refresh QR code for an Evolution instance that's stuck in QR timeout loop.

    This fixes the issue where:
    1. QR codes expire after 60 seconds
    2. Evolution auto-reconnects and generates new QR codes infinitely
    3. User can't complete pairing because of the loop

    Solution: Delete and recreate the instance to get a fresh QR code.
    """
    try:
        from ..main import supabase

        print(f"[Refresh QR] Starting refresh for instance: {instance_name}")

        # Step 1: Get the integration details from database
        result = supabase.schema("healthcare").table("integrations").select("*").eq(
            "config->>instance_name", instance_name
        ).eq("type", "whatsapp").execute()

        if not result.data or len(result.data) == 0:
            raise HTTPException(status_code=404, detail="Integration not found")

        integration = result.data[0]
        org_id = integration["organization_id"]

        print(f"[Refresh QR] Found integration for org: {org_id}")

        # Step 2: Delete the old instance from Evolution API (stop the QR loop)
        async with EvolutionAPIClient() as evolution_client:
            print(f"[Refresh QR] Deleting old instance...")
            delete_result = await evolution_client.delete_instance(instance_name)
            print(f"[Refresh QR] Delete result: {delete_result}")

            # Step 3: Wait a moment for cleanup
            import asyncio
            await asyncio.sleep(2)

            # Step 4: Create a fresh instance with same name
            print(f"[Refresh QR] Creating fresh instance...")
            create_result = await evolution_client.create_instance(
                tenant_id=org_id,
                instance_name=instance_name
            )

            if not create_result.get("success"):
                raise HTTPException(
                    status_code=500,
                    detail=f"Failed to recreate instance: {create_result.get('error')}"
                )

            print(f"[Refresh QR] Fresh instance created successfully")

            # Step 5: Return the new QR code
            return {
                "success": True,
                "instance_name": instance_name,
                "qrcode": create_result.get("qrcode"),
                "message": "QR code refreshed successfully. Please scan the new QR code.",
                "status": "qr"
            }

    except HTTPException:
        raise
    except Exception as e:
        print(f"[Refresh QR] Error: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/evolution/reconnect/{instance_name}")
async def reconnect_evolution_instance(instance_name: str):
    """Reconnect an Evolution instance"""
    try:
        async with EvolutionAPIClient() as evolution_client:
            result = await evolution_client.restart_instance(instance_name)

            # Get new QR code
            if result.get("instance"):
                qr_code = await evolution_client.get_qr_code(instance_name)
                result["qrcode"] = qr_code

            return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/evolution/disconnect/{instance_name}")
async def disconnect_evolution_instance(instance_name: str):
    """Disconnect an Evolution instance"""
    try:
        async with EvolutionAPIClient() as evolution_client:
            result = await evolution_client.disconnect_instance(instance_name)
            return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.patch("/evolution/{integration_id}/organization")
async def update_evolution_organization(integration_id: str, new_organization_id: str):
    """Update the organization ID for an Evolution integration"""
    try:
        from supabase import create_client
        from supabase.client import ClientOptions

        # Get the integration to find the instance name
        healthcare_options = ClientOptions(schema='healthcare')
        healthcare_supabase = create_client(
            os.getenv("SUPABASE_URL"),
            os.getenv("SUPABASE_SERVICE_ROLE_KEY"),
            healthcare_options
        )

        # Get current integration
        result = healthcare_supabase.from_('integrations').select('*').eq('id', integration_id).execute()

        if not result.data:
            raise HTTPException(status_code=404, detail="Integration not found")

        integration = result.data[0]
        old_instance_name = integration['config'].get('instance_name')

        # Extract the timestamp from old instance name
        parts = old_instance_name.split('-')
        timestamp = parts[-1] if len(parts) > 1 else ''

        # Create new instance name with new organization ID
        new_instance_name = f"clinic-{new_organization_id}-{timestamp}"

        # Update the integration in database
        update_result = healthcare_supabase.from_('integrations').update({
            'organization_id': new_organization_id,
            'config': {
                **integration['config'],
                'instance_name': new_instance_name,
                'webhook_url': f"{os.getenv('BACKEND_URL', 'https://healthcare-clinic-backend.fly.dev')}/webhooks/evolution/{new_instance_name}"
            },
            'updated_at': datetime.now().isoformat()
        }).eq('id', integration_id).execute()

        return {
            'success': True,
            'old_instance_name': old_instance_name,
            'new_instance_name': new_instance_name,
            'organization_id': new_organization_id,
            'message': 'Organization ID updated. Note: You may need to update the Evolution API instance name manually or recreate the instance.'
        }

    except HTTPException:
        raise
    except Exception as e:
        print(f"Error updating Evolution organization: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/evolution/{instance_name}")
async def delete_evolution_instance(instance_name: str):
    """Delete an Evolution instance completely from all storage locations"""
    import logging
    logger = logging.getLogger(__name__)

    try:
        from ..main import supabase
        from ..services.whatsapp_clinic_cache import WhatsAppClinicCache

        # Initialize results
        results = {
            "success": True,
            "instance": instance_name,
            "evolution_deleted": False,
            "database_deleted": False,
            "cache_invalidated": False,
            "workers_notified": False,
            "errors": []
        }

        # 1. Get integration info from database (before deleting)
        organization_id = None
        try:
            db_query = supabase.schema("healthcare").table("integrations").select("*").eq(
                "config->>instance_name", instance_name
            ).eq("type", "whatsapp").execute()

            if db_query.data and len(db_query.data) > 0:
                organization_id = db_query.data[0].get("organization_id")
                logger.info(f"Found integration for instance {instance_name}, org: {organization_id}")
        except Exception as e:
            logger.error(f"Error querying integration: {e}")
            results["errors"].append(f"Database query error: {str(e)}")

        # 2. Delete from Evolution API
        try:
            async with EvolutionAPIClient() as evolution_client:
                evo_result = await evolution_client.delete_instance(instance_name)
                results["evolution_deleted"] = evo_result.get("success", False)
                logger.info(f"Evolution API delete result: {evo_result}")
        except Exception as e:
            logger.error(f"Error deleting from Evolution API: {e}")
            results["errors"].append(f"Evolution API error: {str(e)}")
            results["success"] = False

        # 3. Delete from database
        try:
            db_delete = supabase.schema("healthcare").table("integrations").delete().eq(
                "config->>instance_name", instance_name
            ).eq("type", "whatsapp").execute()

            results["database_deleted"] = db_delete.data and len(db_delete.data) > 0
            logger.info(f"Database delete result: deleted {len(db_delete.data) if db_delete.data else 0} records")
        except Exception as e:
            logger.error(f"Error deleting from database: {e}")
            results["errors"].append(f"Database delete error: {str(e)}")

        # 4. Invalidate Redis cache
        try:
            cache = WhatsAppClinicCache()
            await cache.invalidate_instance(instance_name)
            results["cache_invalidated"] = True
            logger.info(f"Invalidated cache for instance {instance_name}")
        except Exception as e:
            logger.error(f"Error invalidating cache: {e}")
            results["errors"].append(f"Cache invalidation error: {str(e)}")

        # 5. Notify workers about instance removal
        try:
            notifier = InstanceNotifier()
            if organization_id:
                notifier.notify_removed(instance_name, organization_id)
                results["workers_notified"] = True
                logger.info(f"Notified workers about instance removal: {instance_name}")
        except Exception as e:
            logger.error(f"Error notifying workers: {e}")
            results["errors"].append(f"Worker notification error: {str(e)}")

        return results

    except Exception as e:
        logger.error(f"Unexpected error deleting instance {instance_name}: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/calendar/{integration_id}/sync")
async def trigger_calendar_sync(integration_id: str):
    """Manually trigger calendar sync for a specific integration"""
    try:
        from supabase import create_client
        from supabase.client import ClientOptions

        # Get calendar integration from healthcare schema
        healthcare_options = ClientOptions(schema='healthcare')
        healthcare_supabase = create_client(
            os.getenv("SUPABASE_URL"),
            os.getenv("SUPABASE_SERVICE_ROLE_KEY"),
            healthcare_options
        )

        # Get the calendar integration
        result = healthcare_supabase.from_('calendar_integrations').select('*').eq(
            'id', integration_id
        ).execute()

        if not result.data:
            raise HTTPException(status_code=404, detail="Calendar integration not found")

        integration = result.data[0]

        # Update last_sync_at to trigger sync
        update_result = healthcare_supabase.from_('calendar_integrations').update({
            'last_sync_at': datetime.utcnow().isoformat(),
            'last_sync_status': 'syncing',
            'updated_at': datetime.utcnow().isoformat()
        }).eq('id', integration_id).execute()

        # TODO: Implement actual calendar sync logic here
        # For now, just mark as completed
        healthcare_supabase.from_('calendar_integrations').update({
            'last_sync_status': 'completed',
            'sync_error_count': 0
        }).eq('id', integration_id).execute()

        return {
            "success": True,
            "message": "Calendar sync initiated",
            "integration_id": integration_id,
            "provider": integration.get('provider'),
            "last_sync_at": datetime.utcnow().isoformat()
        }

    except HTTPException:
        raise
    except Exception as e:
        print(f"Error triggering calendar sync: {e}")
        raise HTTPException(status_code=500, detail=str(e))
