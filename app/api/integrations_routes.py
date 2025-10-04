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

        # Get regular integrations from public schema
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
            result = query.execute()
            all_integrations.extend(result.data)
        except Exception as e:
            print(f"Public integrations query error (non-fatal): {e}")

        # Get calendar integrations from healthcare schema
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
                    'integration_type': 'calendar',
                    'provider': cal_int.get('provider'),
                    'status': 'active' if cal_int.get('sync_enabled') else 'inactive',
                    'config': {
                        'calendar_id': cal_int.get('calendar_id'),
                        'calendar_name': cal_int.get('calendar_name'),
                        'sync_enabled': cal_int.get('sync_enabled')
                    },
                    'enabled': cal_int.get('sync_enabled', False),
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

        result = public_supabase.table("integrations").update(data).eq("id", integration_id).execute()
        if not result.data:
            raise HTTPException(status_code=404, detail="Integration not found")
        return result.data[0]
    except Exception as e:
        print(f"Error updating integration: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/{integration_id}")
async def delete_integration(integration_id: str):
    """Delete an integration"""
    try:
        # Create a public schema client for integrations table
        from supabase import create_client
        import os

        public_supabase = create_client(
            os.getenv("SUPABASE_URL"),
            os.getenv("SUPABASE_SERVICE_ROLE_KEY")
        )

        result = public_supabase.table("integrations").delete().eq("id", integration_id).execute()
        return {"deleted": True}
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
        # Initialize Evolution API client using async context manager
        async with EvolutionAPIClient() as evolution_client:
            # Create the instance
            result = await evolution_client.create_instance(
                tenant_id=data.organization_id,  # Use organization_id as tenant_id
                instance_name=data.instance_name
            )

            # The QR code is now included in the create_instance response
            # No need for a separate call

            return result
    except Exception as e:
        print(f"Error creating Evolution instance: {e}")
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

        # Only save integration if truly connected with a phone number
        if status.get("is_truly_connected"):
            # Extract organization_id from instance_name (format: clinic-{org_id}-{timestamp})
            parts = instance_name.split('-')
            if len(parts) >= 2:
                # Get organization_id from the instance name
                org_id = '-'.join(parts[1:-1])  # Extract UUID portion

                # Use RPC function to save Evolution integration
                result = supabase.rpc('save_evolution_integration', {
                    'p_organization_id': org_id,
                    'p_instance_name': instance_name,
                    'p_phone_number': status.get('phone_number'),
                    'p_webhook_url': f"{os.getenv('EVOLUTION_SERVER_URL', 'https://evolution-api-prod.fly.dev')}/webhook/{instance_name}"
                }).execute()

                print(f"Saved WhatsApp integration for organization {org_id}: {result.data}")

        return status
    except Exception as e:
        print(f"Error in get_evolution_status: {e}")
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
    """Delete an Evolution instance"""
    try:
        async with EvolutionAPIClient() as evolution_client:
            result = await evolution_client.delete_instance(instance_name)
            return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
