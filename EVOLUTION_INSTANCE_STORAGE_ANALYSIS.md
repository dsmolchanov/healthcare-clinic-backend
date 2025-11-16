# Evolution Instance Storage & Management Analysis

## Research Summary

I've completed a comprehensive analysis of where Evolution WhatsApp instances are stored and how they're managed across the system.

---

## Storage Locations

Evolution instances are stored in **3 locations**:

### 1. **Evolution API Server** (evolution-api-prod.fly.dev)
   - **Location:** External service, Evolution API database
   - **Purpose:** Primary source of truth for WhatsApp connections
   - **Data Stored:**
     - Instance name (e.g., `clinic-{org_id}-{timestamp}`)
     - Connection status (connected/disconnected)
     - QR code data
     - WhatsApp session credentials
     - Phone number (when connected)
   - **How to Access:**
     - Create: `POST /instance/create`
     - Delete: `DELETE /instance/delete/{instance_name}`
     - List: `GET /instance/fetchInstances`
     - Status: `GET /instance/connectionState/{instance_name}`

### 2. **Supabase Database** (healthcare.integrations table)
   - **Location:** PostgreSQL (Supabase)
   - **Table:** `healthcare.integrations`
   - **Purpose:** Track integration configuration and metadata
   - **Data Stored:**
     ```sql
     - id (UUID)
     - organization_id (UUID)
     - type ('whatsapp')
     - provider ('evolution')
     - status ('pending', 'active', 'disabled')
     - config (JSONB) {
         instance_name: string
         webhook_url: string
       }
     - enabled (boolean)
     - webhook_token (TEXT)
     - phone_number (TEXT)
     - created_at, updated_at, connected_at, last_seen_at
     ```
   - **How to Access:**
     - Create/Update: `save_evolution_integration()` RPC function
     - Delete: `DELETE FROM healthcare.integrations WHERE id = ...`
     - Query: `SELECT * FROM healthcare.integrations WHERE organization_id = ...`

### 3. **Redis Cache** (whatsapp:instance:{instance_name})
   - **Location:** Redis server
   - **Key Pattern:** `whatsapp:instance:{instance_name}`
   - **Purpose:** Fast lookup for webhook processing (zero DB queries)
   - **TTL:** 1 hour (refreshed on warmup)
   - **Data Stored:**
     ```json
     {
       "clinic_id": "uuid",
       "organization_id": "uuid",
       "name": "clinic name",
       "whatsapp_number": "+1234567890"
     }
     ```
   - **How to Access:**
     - Get: `WhatsAppClinicCache.get_clinic_info(instance_name)`
     - Set: `WhatsAppClinicCache.set_clinic_info(instance_name, ...)`
     - Delete: `WhatsAppClinicCache.invalidate_instance(instance_name)`

---

## Create Flow

When creating a new Evolution instance:

```python
# File: app/api/integrations_routes.py:334

1. Frontend → POST /integrations/evolution/create
   {
     "organizationId": "uuid",
     "instanceName": "clinic-{org_id}-{timestamp}"
   }

2. Backend creates Evolution instance
   evolution_client.create_instance(
     tenant_id=organization_id,
     instance_name=instance_name
   )
   ↓
   Evolution API: POST /instance/create
   ↓
   Creates WhatsApp session, generates QR code

3. Backend saves to database
   supabase.rpc('save_evolution_integration', {
     'p_organization_id': organization_id,
     'p_instance_name': instance_name,
     'p_phone_number': None,  # Not connected yet
     'p_webhook_url': webhook_url
   })
   ↓
   Inserts/Updates healthcare.integrations table

4. Backend notifies workers (Redis pub/sub)
   InstanceNotifier().notify_added(instance_name, organization_id)
   ↓
   Publishes to: wa:instances:added channel

5. Workers pre-warm cache
   WhatsAppClinicCache().set_clinic_info(
     instance_name,
     clinic_id,
     organization_id,
     name,
     whatsapp_number
   )
   ↓
   Sets: whatsapp:instance:{instance_name} in Redis
```

---

## Delete Flow (CURRENT - INCOMPLETE!)

Current implementation at `app/api/integrations_routes.py:508`:

```python
@router.delete("/evolution/{instance_name}")
async def delete_evolution_instance(instance_name: str):
    async with EvolutionAPIClient() as evolution_client:
        result = await evolution_client.delete_instance(instance_name)
        return result
```

### Problem: Only Deletes from Evolution API!

The current delete endpoint **ONLY** removes the instance from Evolution API, leaving:
- ❌ **Database record** in `healthcare.integrations` (orphaned)
- ❌ **Redis cache** with instance mapping (stale)
- ❌ **No notification** to workers about deletion

This causes:
1. UI still shows the integration as "pending" or "disconnected"
2. Webhook messages may still route to non-existent instance
3. Cache pollution with invalid data

---

## RPC Function Issues

The `save_evolution_integration` RPC function has a **critical flaw**:

### Issue: No Duplicate Prevention

```sql
-- From verify_and_fix_rpc.py

-- Check if integration already exists
SELECT * INTO existing_integration
FROM healthcare.integrations
WHERE organization_id = p_organization_id
AND type = 'whatsapp'
AND provider = 'evolution'
LIMIT 1;
```

**Problem:**
- This check happens **AFTER** the Evolution instance is created
- If multiple requests come concurrently, multiple instances are created
- Each gets its own `instance_name` with unique timestamp
- All pass the "not exists" check since they have different instance names

**Result:** Duplicate instances for the same organization (as we saw with 6 instances!)

---

## Recommended Fixes

### Fix 1: Complete Delete Endpoint

```python
@router.delete("/evolution/{instance_name}")
async def delete_evolution_instance(instance_name: str):
    """Delete an Evolution instance completely from all locations"""
    from ..main import supabase
    from ..services.whatsapp_clinic_cache import WhatsAppClinicCache
    from ..services.whatsapp_queue.pubsub import InstanceNotifier

    try:
        # 1. Delete from Evolution API
        async with EvolutionAPIClient() as evolution_client:
            evo_result = await evolution_client.delete_instance(instance_name)

        # 2. Delete from database
        db_result = supabase.schema("healthcare") \\
            .table("integrations") \\
            .delete() \\
            .eq("config->>instance_name", instance_name) \\
            .eq("type", "whatsapp") \\
            .execute()

        # 3. Invalidate Redis cache
        cache = WhatsAppClinicCache()
        await cache.invalidate_instance(instance_name)

        # 4. Notify workers
        notifier = InstanceNotifier()
        # Extract org_id from db_result if available
        org_id = db_result.data[0]["organization_id"] if db_result.data else None
        notifier.notify_removed(instance_name, org_id)

        return {
            "success": True,
            "instance": instance_name,
            "evolution_deleted": evo_result.get("success"),
            "database_deleted": len(db_result.data) > 0,
            "cache_invalidated": True,
            "workers_notified": True
        }

    except Exception as e:
        logger.error(f"Error deleting instance {instance_name}: {e}")
        raise HTTPException(status_code=500, detail=str(e))
```

### Fix 2: Add Unique Constraint to Database

```sql
-- Prevent duplicate WhatsApp integrations per organization

ALTER TABLE healthcare.integrations
ADD CONSTRAINT unique_whatsapp_integration_per_org
UNIQUE (organization_id, type, provider)
WHERE type = 'whatsapp' AND provider = 'evolution' AND enabled = true;
```

### Fix 3: Improve RPC Function - Check BEFORE Creating

Move the duplicate check to the **create endpoint** BEFORE calling Evolution API:

```python
@router.post("/evolution/create")
async def create_evolution_instance(data: EvolutionInstanceCreate):
    from ..main import supabase

    # CHECK FIRST - Before creating instance
    existing = supabase.schema("healthcare") \\
        .table("integrations") \\
        .select("*") \\
        .eq("organization_id", data.organization_id) \\
        .eq("type", "whatsapp") \\
        .eq("provider", "evolution") \\
        .eq("enabled", True) \\
        .execute()

    if existing.data:
        return {
            "success": False,
            "error": "WhatsApp integration already exists for this organization",
            "existing_instance": existing.data[0]["config"]["instance_name"]
        }

    # NOW create instance
    async with EvolutionAPIClient() as evolution_client:
        result = await evolution_client.create_instance(...)
        # ... rest of code
```

---

## Summary

**Storage Locations:**
1. ✅ Evolution API (external service)
2. ✅ Supabase Database (healthcare.integrations)
3. ✅ Redis Cache (whatsapp:instance:{name})

**Current Delete Implementation:**
- ❌ Only deletes from Evolution API
- ❌ Leaves orphaned database records
- ❌ Leaves stale cache entries
- ❌ Doesn't notify workers

**Required Fix:**
- Delete from ALL 3 locations
- Notify workers via pub/sub
- Add unique constraint to prevent duplicates
- Check for existing integrations BEFORE creating instances

This will ensure complete cleanup and prevent the duplicate instance issue from recurring.
