"""
Webhook handlers for data synchronization between Supabase and NocoDB
"""

from fastapi import APIRouter, Request, BackgroundTasks, HTTPException, Depends
from fastapi.responses import JSONResponse
from typing import Dict, Any
import logging
import json
import hmac
import hashlib
from datetime import datetime

from app.services.sync_service import DataSyncService, SyncDirection
from app.websocket.manager import manager
from app.core.config import settings
from app.db.supabase import get_supabase_client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/sync", tags=["sync"])

# Global sync service instance
sync_service: DataSyncService = None


def get_sync_service() -> DataSyncService:
    """Get or create sync service instance"""
    global sync_service
    if sync_service is None:
        supabase = get_supabase_client()
        sync_service = DataSyncService(
            supabase_client=supabase,
            nocodb_url=settings.NOCODB_URL,
            nocodb_token=settings.NOCODB_API_TOKEN
        )
    return sync_service


def verify_webhook_signature(payload: bytes, signature: str, secret: str) -> bool:
    """Verify webhook signature for security"""
    expected_signature = hmac.new(
        secret.encode(),
        payload,
        hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(signature, expected_signature)


@router.post("/supabase-webhook")
async def handle_supabase_change(
    request: Request,
    background_tasks: BackgroundTasks,
    sync_service: DataSyncService = Depends(get_sync_service)
):
    """
    Process Supabase database changes via webhook
    
    Expected payload format:
    {
        "type": "INSERT" | "UPDATE" | "DELETE",
        "table": "table_name",
        "schema": "healthcare",
        "record": {...},
        "old_record": {...} (for UPDATE/DELETE)
    }
    """
    try:
        # Get raw body for signature verification
        body = await request.body()
        
        # Verify webhook signature if configured
        if settings.SUPABASE_WEBHOOK_SECRET:
            signature = request.headers.get("X-Supabase-Signature", "")
            if not verify_webhook_signature(body, signature, settings.SUPABASE_WEBHOOK_SECRET):
                raise HTTPException(status_code=401, detail="Invalid webhook signature")
        
        # Parse payload
        payload = json.loads(body)
        
        # Log webhook receipt
        logger.info(f"Received Supabase webhook: {payload.get('type')} on {payload.get('table')}")
        
        # Extract relevant information
        operation = payload.get("type")
        table = payload.get("table")
        schema = payload.get("schema", "healthcare")
        record = payload.get("record", {})
        old_record = payload.get("old_record", {})
        
        # Validate required fields
        if not all([operation, table]):
            raise HTTPException(status_code=400, detail="Missing required fields")
        
        # Only process healthcare schema changes
        if schema != "healthcare":
            return JSONResponse({"status": "ignored", "reason": "Not healthcare schema"})
        
        # Prepare sync item
        sync_item = {
            "table": table,
            "record_id": record.get("id") or old_record.get("id"),
            "operation": operation,
            "clinic_id": record.get("clinic_id") or old_record.get("clinic_id"),
            "data": record if operation != "DELETE" else None,
            "source": "supabase",
            "timestamp": datetime.utcnow().isoformat()
        }
        
        # Queue for processing
        background_tasks.add_task(sync_service.queue_sync, sync_item)
        
        # Notify connected clients via WebSocket
        if sync_item["clinic_id"]:
            background_tasks.add_task(
                manager.notify_data_update,
                sync_item["clinic_id"],
                table,
                sync_item["record_id"],
                operation,
                record
            )
        
        return JSONResponse({
            "status": "queued",
            "table": table,
            "operation": operation,
            "record_id": sync_item["record_id"]
        })
        
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")
    except Exception as e:
        logger.error(f"Error processing Supabase webhook: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/nocodb-webhook")
async def handle_nocodb_change(
    request: Request,
    background_tasks: BackgroundTasks,
    sync_service: DataSyncService = Depends(get_sync_service)
):
    """
    Process NocoDB data changes via webhook
    
    Expected payload format:
    {
        "type": "records.after.insert" | "records.after.update" | "records.after.delete",
        "data": {
            "table_name": "table_name",
            "view_name": "view_name",
            "rows": [...]
        }
    }
    """
    try:
        # Get raw body
        body = await request.body()
        
        # Verify webhook signature if configured
        if settings.NOCODB_WEBHOOK_SECRET:
            signature = request.headers.get("X-NocoDB-Signature", "")
            if not verify_webhook_signature(body, signature, settings.NOCODB_WEBHOOK_SECRET):
                raise HTTPException(status_code=401, detail="Invalid webhook signature")
        
        # Parse payload
        payload = json.loads(body)
        
        # Log webhook receipt
        logger.info(f"Received NocoDB webhook: {payload.get('type')}")
        
        # Extract operation type
        webhook_type = payload.get("type", "")
        operation = "INSERT"
        if "update" in webhook_type:
            operation = "UPDATE"
        elif "delete" in webhook_type:
            operation = "DELETE"
        
        # Extract data
        data = payload.get("data", {})
        table_name = data.get("table_name", "")
        rows = data.get("rows", [])
        
        # Map NocoDB table name back to Supabase table name
        # Reverse mapping from tier tables to base tables
        table_mapping_reverse = {
            "t1_appointments": "appointments",
            "t1_doctors": "doctors",
            "t1_patients": "patients",
            "t1_services": "services",
            "t1_rooms": "rooms",
            "t2_schedules": "schedules",
            "t2_staff": "staff",
            "t2_wait_list": "wait_list",
            "t2_inventory": "inventory",
            "t2_time_off": "time_off",
            "t3_equipment": "equipment",
            "t3_insurance_plans": "insurance_plans",
            "t3_pricing_rules": "pricing_rules",
            "t3_specialties": "specialties",
            "t3_referrals": "referrals"
        }
        
        supabase_table = table_mapping_reverse.get(table_name, table_name)
        
        # Process each row
        for row in rows:
            sync_item = {
                "table": supabase_table,
                "record_id": row.get("id"),
                "operation": operation,
                "clinic_id": row.get("clinic_id"),
                "data": row if operation != "DELETE" else None,
                "source": "nocodb",
                "timestamp": datetime.utcnow().isoformat()
            }
            
            # Queue for processing
            background_tasks.add_task(sync_service.queue_sync, sync_item)
        
        return JSONResponse({
            "status": "queued",
            "table": supabase_table,
            "operation": operation,
            "rows_count": len(rows)
        })
        
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")
    except Exception as e:
        logger.error(f"Error processing NocoDB webhook: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/manual-sync/{table_name}")
async def trigger_manual_sync(
    table_name: str,
    request: Request,
    background_tasks: BackgroundTasks,
    sync_service: DataSyncService = Depends(get_sync_service)
):
    """
    Manually trigger synchronization for a specific table
    
    Body:
    {
        "clinic_id": "uuid",
        "direction": "to_nocodb" | "to_supabase" | "bidirectional"
    }
    """
    try:
        body = await request.json()
        clinic_id = body.get("clinic_id")
        direction = body.get("direction", "bidirectional")
        
        if not clinic_id:
            raise HTTPException(status_code=400, detail="clinic_id is required")
        
        # Map direction string to enum
        direction_map = {
            "to_nocodb": SyncDirection.TO_NOCODB,
            "to_supabase": SyncDirection.TO_SUPABASE,
            "bidirectional": SyncDirection.BIDIRECTIONAL
        }
        
        sync_direction = direction_map.get(direction, SyncDirection.BIDIRECTIONAL)
        
        # Queue sync task
        background_tasks.add_task(
            sync_service.sync_table_data,
            table_name,
            clinic_id,
            sync_direction
        )
        
        return JSONResponse({
            "status": "initiated",
            "table": table_name,
            "clinic_id": clinic_id,
            "direction": direction
        })
        
    except Exception as e:
        logger.error(f"Error triggering manual sync: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/status")
async def get_sync_status(sync_service: DataSyncService = Depends(get_sync_service)):
    """Get current sync service status and metrics"""
    try:
        status = await sync_service.get_sync_status()
        return JSONResponse({
            "status": "healthy",
            "sync_metrics": status,
            "timestamp": datetime.utcnow().isoformat()
        })
    except Exception as e:
        logger.error(f"Error getting sync status: {e}")
        return JSONResponse({
            "status": "error",
            "error": str(e),
            "timestamp": datetime.utcnow().isoformat()
        }, status_code=500)


@router.post("/start")
async def start_sync_processing(sync_service: DataSyncService = Depends(get_sync_service)):
    """Start the sync processing service"""
    try:
        await sync_service.start_processing()
        return JSONResponse({"status": "started"})
    except Exception as e:
        logger.error(f"Error starting sync service: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/stop")
async def stop_sync_processing(sync_service: DataSyncService = Depends(get_sync_service)):
    """Stop the sync processing service"""
    try:
        await sync_service.stop_processing()
        return JSONResponse({"status": "stopped"})
    except Exception as e:
        logger.error(f"Error stopping sync service: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/test")
async def test_webhook_endpoint():
    """Test endpoint to verify webhook configuration"""
    return JSONResponse({
        "status": "ok",
        "message": "Webhook endpoint is working",
        "timestamp": datetime.utcnow().isoformat()
    })