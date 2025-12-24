"""
Webhook endpoints for real-time status updates.

Note: WhatsApp message processing is handled by:
- evolution_webhook.py - Evolution API webhook (primary)
- whatsapp_webhook.py - Background processing webhook

FSM system has been removed in Phase 1.3 cleanup. All message processing
now goes through PipelineMessageProcessor -> LangGraph orchestrator.
"""
from fastapi import APIRouter, HTTPException, Request, BackgroundTasks
from fastapi.responses import StreamingResponse
from typing import Dict, Optional, Any
import json
import asyncio
import os
import logging
from datetime import datetime
from supabase import create_client, Client
from supabase.client import ClientOptions
import uuid

logger = logging.getLogger(__name__)

# Initialize Supabase client for webhooks
options = ClientOptions(
    schema='healthcare',
    auto_refresh_token=True,
    persist_session=False
)
supabase: Client = create_client(
    os.environ.get("SUPABASE_URL"),
    os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ.get("SUPABASE_ANON_KEY"),
    options=options
)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

# Store active SSE connections
active_connections: Dict[str, asyncio.Queue] = {}


# ============================================================================
# Integration Status Endpoints (SSE endpoints)
# ============================================================================

@router.get("/integration-status/{integration_id}")
async def integration_status_stream(integration_id: str, request: Request):
    """
    Server-Sent Events endpoint for real-time integration status updates
    """
    async def event_generator():
        # Create a unique connection ID
        connection_id = str(uuid.uuid4())
        queue = asyncio.Queue()
        active_connections[connection_id] = queue

        try:
            # Send initial status
            try:
                # Get current integration status
                result = supabase.table('integrations').select('*').eq('id', integration_id).single().execute()
                if result.data:
                    yield f"data: {json.dumps({'type': 'status', 'integration': result.data})}\n\n"
            except:
                yield f"data: {json.dumps({'type': 'error', 'message': 'Integration not found'})}\n\n"

            # Keep connection alive and send updates
            while True:
                try:
                    # Wait for updates with timeout
                    message = await asyncio.wait_for(queue.get(), timeout=30.0)
                    yield f"data: {json.dumps(message)}\n\n"

                    # If status is connected, send final update and close
                    if message.get('status') == 'active' or message.get('connected'):
                        yield f"data: {json.dumps({'type': 'complete', 'message': 'Integration connected successfully'})}\n\n"
                        break

                except asyncio.TimeoutError:
                    # Send heartbeat to keep connection alive
                    yield f": heartbeat\n\n"

                except asyncio.CancelledError:
                    break

        finally:
            # Clean up connection
            if connection_id in active_connections:
                del active_connections[connection_id]

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "Access-Control-Allow-Origin": "https://nemo.menu",
            "X-Accel-Buffering": "no"  # Disable nginx buffering
        }
    )

@router.post("/integration-status/update")
async def update_integration_status(data: dict):
    """
    Internal endpoint to push status updates to connected clients
    """
    integration_id = data.get('integration_id')
    status_update = {
        'type': 'update',
        'integration_id': integration_id,
        'status': data.get('status'),
        'connected': data.get('connected'),
        'timestamp': datetime.utcnow().isoformat()
    }

    # Send update to all active connections
    for queue in active_connections.values():
        await queue.put(status_update)

    return {"success": True, "connections_notified": len(active_connections)}

@router.post("/calendar-connected/{clinic_id}")
async def calendar_connected_webhook(clinic_id: str, background_tasks: BackgroundTasks):
    """
    Webhook called when calendar OAuth is completed successfully
    """
    try:
        # Get all integrations for this clinic
        # Try both possible column names for compatibility
        try:
            result = supabase.table('integrations').select('*').eq(
                'organization_id', clinic_id
            ).eq('type', 'google_calendar').execute()
        except:
            # Fallback to integration_type if type doesn't exist
            result = supabase.table('integrations').select('*').eq(
                'organization_id', clinic_id
            ).eq('integration_type', 'google_calendar').execute()

        if result.data:
            for integration in result.data:
                # Update integration status to active
                supabase.table('integrations').update({
                    'status': 'active',
                    'webhook_verified': True,
                    'updated_at': datetime.utcnow().isoformat()
                }).eq('id', integration['id']).execute()

                # Notify connected clients
                status_update = {
                    'type': 'calendar_connected',
                    'integration_id': integration['id'],
                    'status': 'active',
                    'connected': True,
                    'clinic_id': clinic_id
                }

                for queue in active_connections.values():
                    await queue.put(status_update)

        return {"success": True, "message": "Calendar connected successfully"}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
