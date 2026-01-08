"""
Resources API - Efficient endpoints for clinic resource management
Provides both RPC-based data fetching and real-time WebSocket subscriptions
"""

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect, Depends, Query
from fastapi.responses import JSONResponse
from typing import Optional, Dict, Any, List
import asyncio
import json
import logging
from datetime import datetime, date as date_type
from supabase import create_client, Client
import os
from pydantic import BaseModel

from ..services.appointment_enrichment_service import AppointmentEnrichmentService

# Configure logging
logger = logging.getLogger(__name__)

# Create router
router = APIRouter(prefix="/api/resources", tags=["resources"])

# Supabase client initialization
def get_supabase_client() -> Client:
    """Get Supabase client with proper configuration"""
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", os.getenv("SUPABASE_ANON_KEY"))
    
    if not url or not key:
        raise ValueError("Supabase credentials not configured")
    
    return create_client(url, key)

# ============================================================================
# Request/Response Models
# ============================================================================

class DashboardRequest(BaseModel):
    clinic_id: Optional[str] = None
    organization_id: Optional[str] = None

class TableUpdateRequest(BaseModel):
    table_name: str
    clinic_id: str
    data: Dict[str, Any]

class RealtimeSubscription(BaseModel):
    tables: List[str]
    clinic_id: str

# ============================================================================
# Main Dashboard Endpoint
# ============================================================================

@router.post("/dashboard")
async def get_dashboard_data(request: DashboardRequest):
    """
    Get comprehensive dashboard data for all 15 resource tables.
    Uses RPC function for efficient single-query data fetching.
    """
    try:
        supabase = get_supabase_client()
        
        # Call the RPC function
        params = {}
        if request.clinic_id:
            params['p_clinic_id'] = request.clinic_id
        elif request.organization_id:
            params['p_organization_id'] = request.organization_id
            
        # Call the RPC function in healthcare schema
        # Note: Supabase Python client doesn't support schemas directly, so we use a workaround
        result = supabase.rpc('get_clinic_resources_dashboard', params).execute()
        
        if result.data:
            # Log dashboard access for analytics
            try:
                if request.clinic_id:
                    supabase.rpc('log_dashboard_access', {
                        'p_clinic_id': request.clinic_id,
                        'p_tables_accessed': list(result.data.get('tables', {}).keys())
                    }).execute()
            except:
                pass  # Don't fail if logging fails
            
            return JSONResponse(
                content=result.data,
                headers={
                    "Cache-Control": "private, max-age=5",  # Cache for 5 seconds
                    "X-Data-Timestamp": datetime.utcnow().isoformat()
                }
            )
        else:
            raise HTTPException(status_code=404, detail="No data found for specified clinic")
            
    except Exception as e:
        logger.error(f"Error fetching dashboard data: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ============================================================================
# Individual Table Endpoints (for granular updates)
# ============================================================================

@router.get("/appointments/{clinic_id}")
async def get_appointments(
    clinic_id: str,
    date: Optional[str] = None,
    enrich: bool = Query(True, description="Include reminder and HITL status enrichment")
):
    """
    Get appointments for a specific clinic and date.

    When enrich=true (default), each appointment includes:
    - reminder_status: 'pending', 'sent', 'confirmed', 'no_response', 'failed'
    - hitl_status: { needs_attention, control_mode, reason, unread_count }
    """
    try:
        supabase = get_supabase_client()

        params = {'p_clinic_id': clinic_id}
        if date:
            params['p_date'] = date

        result = supabase.rpc('get_appointments_realtime', params).execute()

        appointments = result.data or []

        # Enrich appointments with reminder and HITL status
        if enrich and appointments:
            try:
                enrichment_service = AppointmentEnrichmentService(supabase)
                appointments = await enrichment_service.enrich_appointments(
                    appointments,
                    clinic_id
                )
            except Exception as enrich_error:
                logger.warning(f"Appointment enrichment failed, returning without enrichment: {enrich_error}")
                # Continue without enrichment rather than failing the request

        return {
            "clinic_id": clinic_id,
            "date": date if date else str(date_type.today()),
            "appointments": appointments
        }

    except Exception as e:
        logger.error(f"Error fetching appointments: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/waitlist/{clinic_id}")
async def get_waitlist(clinic_id: str):
    """Get wait list for a specific clinic"""
    try:
        supabase = get_supabase_client()
        
        result = supabase.rpc('get_waitlist_realtime', {
            'p_clinic_id': clinic_id
        }).execute()
        
        return {
            "clinic_id": clinic_id,
            "waitlist": result.data or []
        }
        
    except Exception as e:
        logger.error(f"Error fetching wait list: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/whatsapp-sessions/{clinic_id}")
async def get_whatsapp_sessions(clinic_id: str):
    """Get active WhatsApp sessions for a specific clinic"""
    try:
        supabase = get_supabase_client()
        
        result = supabase.rpc('get_whatsapp_sessions_realtime', {
            'p_clinic_id': clinic_id
        }).execute()
        
        return {
            "clinic_id": clinic_id,
            "sessions": result.data or []
        }
        
    except Exception as e:
        logger.error(f"Error fetching WhatsApp sessions: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ============================================================================
# Generic Table Endpoints
# ============================================================================

@router.get("/{table_name}/{clinic_id}")
async def get_table_data(table_name: str, clinic_id: str, limit: int = 100):
    """
    Get data from any resource table.
    Validates table name against allowed list for security.
    """
    # Whitelist of allowed tables
    ALLOWED_TABLES = [
        'doctors', 'rooms', 'equipment', 'staff', 'patients',
        'services', 'schedules', 'inventory', 'insurance_plans',
        'specialties', 'protocols', 'time_off', 'appointments',
        'wait_list', 'whatsapp_sessions'
    ]
    
    if table_name not in ALLOWED_TABLES:
        raise HTTPException(status_code=400, detail=f"Invalid table name: {table_name}")
    
    try:
        supabase = get_supabase_client()
        
        # Build query
        query = supabase.table(table_name).select("*")
        
        # Add clinic filter for clinic-specific tables
        if table_name not in ['insurance_plans', 'specialties']:
            query = query.eq('clinic_id', clinic_id)
        
        # Add limit
        query = query.limit(limit)
        
        # Execute query
        result = query.execute()
        
        return {
            "table": table_name,
            "clinic_id": clinic_id,
            "count": len(result.data) if result.data else 0,
            "data": result.data or []
        }
        
    except Exception as e:
        logger.error(f"Error fetching {table_name} data: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ============================================================================
# WebSocket for Real-time Updates
# ============================================================================

class ConnectionManager:
    """Manages WebSocket connections for real-time updates"""
    
    def __init__(self):
        self.active_connections: Dict[str, List[WebSocket]] = {}
        self.subscription_tasks: Dict[str, asyncio.Task] = {}
    
    async def connect(self, websocket: WebSocket, clinic_id: str):
        await websocket.accept()
        if clinic_id not in self.active_connections:
            self.active_connections[clinic_id] = []
        self.active_connections[clinic_id].append(websocket)
        
        # Start subscription task if not already running
        if clinic_id not in self.subscription_tasks:
            task = asyncio.create_task(self.subscribe_to_changes(clinic_id))
            self.subscription_tasks[clinic_id] = task
    
    def disconnect(self, websocket: WebSocket, clinic_id: str):
        if clinic_id in self.active_connections:
            self.active_connections[clinic_id].remove(websocket)
            
            # Cancel subscription if no more connections
            if not self.active_connections[clinic_id]:
                del self.active_connections[clinic_id]
                if clinic_id in self.subscription_tasks:
                    self.subscription_tasks[clinic_id].cancel()
                    del self.subscription_tasks[clinic_id]
    
    async def send_message(self, clinic_id: str, message: dict):
        if clinic_id in self.active_connections:
            # Send to all connected clients for this clinic
            disconnected = []
            for connection in self.active_connections[clinic_id]:
                try:
                    await connection.send_json(message)
                except:
                    disconnected.append(connection)
            
            # Clean up disconnected clients
            for conn in disconnected:
                self.disconnect(conn, clinic_id)
    
    async def subscribe_to_changes(self, clinic_id: str):
        """
        Subscribe to Supabase real-time changes for critical tables.
        Polls for changes every second for appointments, wait_list, and whatsapp_sessions.
        """
        supabase = get_supabase_client()
        last_data = {}
        
        while clinic_id in self.active_connections:
            try:
                # Fetch current data for critical tables
                tables_to_monitor = ['appointments', 'wait_list', 'whatsapp_sessions']
                
                for table in tables_to_monitor:
                    # Get current data
                    if table == 'appointments':
                        result = supabase.rpc('get_appointments_realtime', {
                            'p_clinic_id': clinic_id,
                            'p_date': str(date.today())
                        }).execute()
                    elif table == 'wait_list':
                        result = supabase.rpc('get_waitlist_realtime', {
                            'p_clinic_id': clinic_id
                        }).execute()
                    elif table == 'whatsapp_sessions':
                        result = supabase.rpc('get_whatsapp_sessions_realtime', {
                            'p_clinic_id': clinic_id
                        }).execute()
                    else:
                        continue
                    
                    # Check if data changed
                    current_data = json.dumps(result.data, sort_keys=True)
                    if table not in last_data or last_data[table] != current_data:
                        # Data changed, send update
                        await self.send_message(clinic_id, {
                            "type": "table_update",
                            "table": table,
                            "data": result.data,
                            "timestamp": datetime.utcnow().isoformat()
                        })
                        last_data[table] = current_data
                
                # Wait before next poll
                await asyncio.sleep(1)  # Poll every second for real-time feel
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in subscription task for clinic {clinic_id}: {e}")
                await asyncio.sleep(5)  # Wait longer on error

# Create connection manager instance
manager = ConnectionManager()

@router.websocket("/ws/{clinic_id}")
async def websocket_endpoint(websocket: WebSocket, clinic_id: str):
    """
    WebSocket endpoint for real-time updates.
    Clients connect to receive live updates for appointments, wait list, and WhatsApp sessions.
    """
    await manager.connect(websocket, clinic_id)
    
    try:
        # Send initial connection confirmation
        await websocket.send_json({
            "type": "connection",
            "status": "connected",
            "clinic_id": clinic_id,
            "timestamp": datetime.utcnow().isoformat()
        })
        
        # Keep connection alive and handle incoming messages
        while True:
            # Wait for messages from client (like ping/pong or subscription changes)
            data = await websocket.receive_text()
            
            # Handle different message types
            message = json.loads(data)
            
            if message.get("type") == "ping":
                await websocket.send_json({
                    "type": "pong",
                    "timestamp": datetime.utcnow().isoformat()
                })
            elif message.get("type") == "subscribe":
                # Handle subscription requests
                tables = message.get("tables", [])
                await websocket.send_json({
                    "type": "subscription_confirmed",
                    "tables": tables,
                    "timestamp": datetime.utcnow().isoformat()
                })
            
    except WebSocketDisconnect:
        manager.disconnect(websocket, clinic_id)
        logger.info(f"WebSocket disconnected for clinic {clinic_id}")
    except Exception as e:
        logger.error(f"WebSocket error for clinic {clinic_id}: {e}")
        manager.disconnect(websocket, clinic_id)

# ============================================================================
# Data Modification Endpoints (for testing/admin)
# ============================================================================

@router.post("/{table_name}/update")
async def update_table_data(table_name: str, request: TableUpdateRequest):
    """
    Update data in a table (admin endpoint).
    This triggers real-time updates to connected clients.
    """
    ALLOWED_TABLES = [
        'appointments', 'wait_list', 'whatsapp_sessions',
        'doctors', 'rooms', 'equipment', 'staff'
    ]
    
    if table_name not in ALLOWED_TABLES:
        raise HTTPException(status_code=400, detail=f"Updates not allowed for table: {table_name}")
    
    try:
        supabase = get_supabase_client()
        
        # Perform update
        result = supabase.table(table_name).update(request.data).eq(
            'clinic_id', request.clinic_id
        ).execute()
        
        # Notify WebSocket clients of the change
        await manager.send_message(request.clinic_id, {
            "type": "table_update",
            "table": table_name,
            "action": "update",
            "timestamp": datetime.utcnow().isoformat()
        })
        
        return {
            "success": True,
            "table": table_name,
            "updated_records": len(result.data) if result.data else 0
        }
        
    except Exception as e:
        logger.error(f"Error updating {table_name}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ============================================================================
# Health Check
# ============================================================================

@router.get("/health")
async def health_check():
    """Check if resources API is healthy"""
    try:
        supabase = get_supabase_client()
        
        # Test database connection
        result = supabase.table('clinics').select('id').limit(1).execute()
        
        return {
            "status": "healthy",
            "database": "connected",
            "timestamp": datetime.utcnow().isoformat()
        }
    except Exception as e:
        return {
            "status": "unhealthy",
            "error": str(e),
            "timestamp": datetime.utcnow().isoformat()
        }