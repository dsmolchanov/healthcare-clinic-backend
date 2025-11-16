# Evolution Integration Endpoints - Issues & Fixes

## Problems Found

### 1. `/evolution/create` - No Database Record Created
**Current:** Creates Evolution instance but never saves to database
**Fix:** Must create record in `healthcare.integrations` table with webhook_token

### 2. `/evolution/status` - Wrong Database Logic
**Current:**
- Uses non-existent RPC `save_evolution_integration`
- Extracts org_id incorrectly from instance name
- Uses old webhook URL format
- Checks `is_truly_connected` which is always False

**Fix:** Should directly insert/update `healthcare.integrations` table with proper schema

### 3. `get_real_connection_status` - Never Gets Phone Number
**Current:** Sets `phone_number = None` and never updates it
**Fix:** Must call Evolution API to get phone number when connected

### 4. Integration Stays "Pending" Forever
**Root Cause:** Database never gets updated because:
- Create doesn't save
- Status polling never triggers database update (is_truly_connected always False)

## Required Fixes

### Fix 1: Update `/evolution/create` endpoint

```python
@router.post("/evolution/create")
async def create_evolution_instance(data: EvolutionInstanceCreate):
    """Create a new Evolution API instance for WhatsApp"""
    try:
        from app.db.supabase_client import get_supabase_client
        import uuid
        import secrets
        import base64

        # Generate webhook token
        webhook_token = base64.urlsafe_b64encode(secrets.token_bytes(24)).decode('utf-8').rstrip('=')

        # Create Evolution instance
        async with EvolutionAPIClient() as evolution_client:
            result = await evolution_client.create_instance(
                tenant_id=data.organization_id,
                instance_name=data.instance_name
            )

        # Save to database IMMEDIATELY with webhook_token
        supabase = get_supabase_client()

        # Extract clinic_id from instance name if present
        # Format: clinic-{clinic_id} or clinic-{org_id}-{timestamp}
        clinic_id = None
        if 'clinic-' in data.instance_name:
            parts = data.instance_name.replace('clinic-', '').split('-')
            if len(parts) > 0 and len(parts[0]) == 36:  # UUID length
                clinic_id = parts[0]

        integration_record = {
            "id": str(uuid.uuid4()),
            "organization_id": data.organization_id,
            "clinic_id": clinic_id,
            "type": "whatsapp",
            "provider": "evolution",
            "status": "qr_pending",  # Start as pending
            "enabled": True,
            "webhook_token": webhook_token,
            "display_name": "WhatsApp Integration",
            "config": {
                "instance": data.instance_name,
                "api_url": os.getenv("EVOLUTION_SERVER_URL", "https://evolution-api-prod.fly.dev"),
                "sync_enabled": True
            },
            "credentials_version": "1"
        }

        db_result = supabase.schema("healthcare").table("integrations").insert(
            integration_record
        ).execute()

        # Update Evolution instance with correct webhook URL
        webhook_url = f"https://healthcare-clinic-backend.fly.dev/webhooks/evolution/whatsapp/{webhook_token}"
        # TODO: Update Evolution instance webhook URL

        # Add integration_id to result
        result["integration_id"] = db_result.data[0]["id"] if db_result.data else None
        result["webhook_token"] = webhook_token

        return result

    except Exception as e:
        print(f"Error creating Evolution instance: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))
```

### Fix 2: Update `get_real_connection_status` to get phone number

```python
async def get_real_connection_status(instance_name: str) -> Dict[str, Any]:
    """Get the REAL connection status including phone number"""
    EVOLUTION_URL = os.getenv("EVOLUTION_SERVER_URL", "https://evolution-api-prod.fly.dev")

    async with aiohttp.ClientSession() as session:
        connection_state = "disconnected"
        phone_number = None

        try:
            # Get connection state
            async with session.get(f"{EVOLUTION_URL}/instance/connectionState/{instance_name}") as resp:
                if resp.status == 200:
                    data = await resp.json()
                    instance = data.get("instance", {})
                    state = instance.get("state", "close")

                    if state == "open":
                        connection_state = "connected"
                        # Get phone number from instance data
                        phone_number = instance.get("number") or instance.get("phoneNumber")
                    elif state == "connecting":
                        connection_state = "connecting"
                    else:
                        connection_state = "disconnected"

        except Exception as e:
            print(f"Error checking connection state: {e}")
            connection_state = "disconnected"

        is_truly_connected = (connection_state == "connected" and phone_number is not None)

        return {
            "state": connection_state,
            "phone_number": phone_number,
            "is_truly_connected": is_truly_connected
        }
```

### Fix 3: Update `/evolution/status` endpoint

```python
@router.get("/evolution/status/{instance_name}")
async def get_evolution_status(instance_name: str):
    """Get the connection status and update database"""
    try:
        from app.db.supabase_client import get_supabase_client
        from .evolution_utils import get_real_connection_status

        # Get real connection status
        status = await get_real_connection_status(instance_name)

        # Update database if connected
        if status.get("state") in ["connected", "connecting", "qr"]:
            supabase = get_supabase_client()

            # Find integration by instance name
            result = supabase.schema("healthcare").table("integrations").select("*").eq(
                "config->>instance", instance_name
            ).eq("type", "whatsapp").execute()

            if result.data and len(result.data) > 0:
                integration_id = result.data[0]["id"]

                # Update status
                update_data = {}
                if status.get("state") == "connected":
                    update_data["status"] = "active"
                    update_data["connected_at"] = "now()"
                    if status.get("phone_number"):
                        update_data["phone_number"] = status["phone_number"]
                elif status.get("state") == "connecting":
                    update_data["status"] = "connecting"

                if update_data:
                    supabase.schema("healthcare").table("integrations").update(
                        update_data
                    ).eq("id", integration_id).execute()

        return status

    except Exception as e:
        print(f"Error in get_evolution_status: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))
```

## Testing the Fix

1. Create integration via frontend
2. Should create database record with `status: "qr_pending"`
3. Scan QR code
4. Frontend polls `/evolution/status/{instanceName}`
5. When connected, backend updates database to `status: "active"`
6. Frontend sees "connected" status and stops showing as "pending"
