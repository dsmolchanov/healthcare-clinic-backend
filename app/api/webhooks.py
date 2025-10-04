"""
Webhook endpoints for real-time status updates
"""
from fastapi import APIRouter, HTTPException, Request, BackgroundTasks
from fastapi.responses import StreamingResponse
from typing import Dict, Optional
import json
import asyncio
from datetime import datetime
from ..main import supabase
import uuid

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

# Store active SSE connections
active_connections: Dict[str, asyncio.Queue] = {}

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