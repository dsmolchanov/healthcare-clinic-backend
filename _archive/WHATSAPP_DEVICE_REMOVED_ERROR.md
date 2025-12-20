# WhatsApp Integration "device_removed" Error - Root Cause Analysis

## Issue Summary

Date: 2025-11-15
Instance: `clinic-4e8ddba1-ad52-4613-9a03-ec64636b3f6c`
Error: `401 Unauthorized - Stream Errored (conflict) - device_removed`

## Root Cause

The `device_removed` error occurred due to **duplicate Evolution API instances** using the same WhatsApp phone number simultaneously.

### Timeline of Events

1. **16:31:14** - First instance created: `clinic-4e8ddba1-ad52-4613-9a03-ec64636b3f6c-1763224272772`
2. **16:31:28** - User scanned QR code, first instance connected successfully
3. **16:32:27** - **First instance disconnected** with `401 device_removed` error
4. **16:38:51** - User deleted pending integration and created new one
5. **16:38:51** - Second instance created: `clinic-4e8ddba1-ad52-4613-9a03-ec64636b3f6c-1763224730687`
6. **16:39:04** - User scanned QR code again, second instance connected
7. **Result** - Two Evolution instances existed, both trying to use the same WhatsApp number

## Why This Happened

### The Problem Flow

```
1. User creates integration → Instance A created in Evolution + Database
2. User scans QR code → Instance A connects to WhatsApp ✓
3. WhatsApp sends history sync → Instance A receives messages ✓
4. User deletes integration → Database record deleted ✓
5. Evolution instance A NOT deleted → Still running in Evolution ✗
6. User creates new integration → Instance B created in Evolution + Database
7. User scans QR code → Instance B connects to WhatsApp ✓
8. WhatsApp detects two devices → Kicks out Instance A with 401 error ✗
```

### The Missing Cleanup

When a user deletes a WhatsApp integration from the database:
- ✅ Database record is deleted (`healthcare.integrations` table)
- ❌ Evolution API instance is NOT deleted
- ❌ Orphaned instance continues running in Evolution

When the user creates a new integration:
- Both old and new instances exist in Evolution
- Both try to authenticate with the same WhatsApp number
- WhatsApp treats this as a security issue and disconnects the first instance

## WhatsApp Multi-Device Behavior

WhatsApp's multi-device feature allows up to **4 linked devices**. When a 5th device tries to link:
- WhatsApp sends `stream:error` with code `401`
- The conflict type is `device_removed`
- The oldest session is terminated
- New session takes its place

## How We Fixed It

1. **Identified duplicate instances** in Evolution API:
   ```bash
   curl "https://evolution-api-prod.fly.dev/instance/fetchInstances"
   # Found two instances for same organization
   ```

2. **Deleted orphaned instance**:
   ```bash
   curl -X DELETE "https://evolution-api-prod.fly.dev/instance/delete/clinic-...-1763224272772"
   ```

3. **Verified active instance** is working:
   ```bash
   curl "https://evolution-api-prod.fly.dev/instance/connectionState/clinic-...-1763224730687"
   # State: "open" (connected)
   ```

4. **Updated database** status:
   ```sql
   UPDATE healthcare.integrations
   SET status = 'connected'
   WHERE organization_id = '...' AND type = 'whatsapp';
   ```

## Prevention Strategy

### 1. Implement Proper Cleanup on Integration Deletion

When user deletes a WhatsApp integration, we must:

```python
# In integrations_routes.py delete_integration endpoint
async def delete_integration(integration_id: str):
    # 1. Get integration details BEFORE deletion
    integration = get_integration(integration_id)

    if integration.type == 'whatsapp' and integration.provider == 'evolution':
        instance_name = integration.config.get('instance_name')

        # 2. Delete Evolution instance FIRST
        evolution_client = EvolutionAPIClient()
        await evolution_client.delete_instance(instance_name)

        # 3. Notify workers about removal
        notifier = InstanceNotifier()
        notifier.notify_removed(instance_name, integration.organization_id)

    # 4. Delete database record
    delete_from_database(integration_id)
```

### 2. Check for Existing Instances Before Creating New Ones

```python
# In create_evolution_instance endpoint
async def create_evolution_instance(data: EvolutionInstanceCreate):
    # 1. Check if organization already has an instance
    existing = await get_existing_whatsapp_integration(data.organization_id)

    if existing:
        instance_name = existing.config.get('instance_name')

        # 2. Check if instance still exists in Evolution
        evolution_status = await evolution_client.get_instance_status(instance_name)

        if evolution_status.exists:
            # 3. Reuse existing instance instead of creating new one
            return {"success": True, "instance_name": instance_name, "reused": True}
        else:
            # 4. Clean up orphaned database record
            await delete_integration(existing.id)

    # 5. Create new instance only if none exists
    return await evolution_client.create_instance(...)
```

### 3. Periodic Cleanup of Orphaned Instances

Create a cron job that runs every hour:

```python
async def cleanup_orphaned_instances():
    """
    Find Evolution instances that exist in Evolution but not in database.
    Delete them to prevent conflicts.
    """
    # 1. Get all Evolution instances
    evolution_instances = await evolution_client.fetch_all_instances()

    # 2. Get all database instances
    db_instances = await supabase.table("integrations").select("config->instance_name").eq("type", "whatsapp").execute()

    db_instance_names = {i['config']['instance_name'] for i in db_instances.data}

    # 3. Find orphans
    for evo_instance in evolution_instances:
        if evo_instance['instanceName'] not in db_instance_names:
            print(f"Found orphaned instance: {evo_instance['instanceName']}")
            await evolution_client.delete_instance(evo_instance['instanceName'])
```

### 4. Add Instance Health Check

Monitor instances periodically and handle disconnections:

```python
async def monitor_instance_health():
    """
    Check all database instances and verify they're connected.
    Update status if disconnected.
    """
    instances = await supabase.table("integrations").select("*").eq("type", "whatsapp").eq("enabled", True).execute()

    for instance in instances.data:
        instance_name = instance['config']['instance_name']
        status = await evolution_client.get_connection_state(instance_name)

        if status.state != 'open' and instance['status'] == 'connected':
            # Instance disconnected - update database
            await supabase.table("integrations").update({
                "status": "disconnected",
                "disconnect_reason": status.get('error', 'unknown')
            }).eq("id", instance['id']).execute()
```

## Multi-Instance Worker Considerations

With the new multi-instance worker infrastructure (Phase 1 & 2), we need to ensure:

1. **Instance notifications** are sent when instances are deleted:
   ```python
   notifier.notify_removed(instance_name, org_id)
   ```

2. **Workers react to removal events** by cleaning up local state:
   ```python
   async def handle_instance_removed(instance_name: str):
       # Stop processing messages for this instance
       # Clean up any cached state
       # Release resources
   ```

3. **Redis pub/sub** properly propagates deletion events to all workers

## Testing Checklist

Before deploying the fix, test:

- [ ] Delete integration → Evolution instance is deleted
- [ ] Create integration with existing orphaned instance → Reuses instance
- [ ] Delete and recreate integration → No duplicates created
- [ ] Multiple organizations → Each has only one instance
- [ ] Worker notification → Workers receive removal events
- [ ] Reconnection after disconnect → Uses same instance, doesn't create new one

## Related Files

- `/apps/healthcare-backend/app/api/integrations_routes.py` - Integration CRUD endpoints
- `/apps/healthcare-backend/app/evolution_api.py` - Evolution API client
- `/apps/healthcare-backend/app/services/whatsapp_queue/pubsub.py` - Pub/sub notifications
- `/migrations/add_instance_name_index.sql` - Database index for fast lookups

## References

- Evolution API Docs: https://doc.evolution-api.com/
- WhatsApp Multi-Device: https://faq.whatsapp.com/1324084875126592/
- Baileys Library: https://github.com/WhiskeySockets/Baileys
