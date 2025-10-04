"""
WebSocket API for Real-Time Updates
Implements Phase 3: Real-Time Multi-Source Updates
Provides WebSocket endpoints for live appointment and calendar notifications
"""

import logging
import json
from datetime import datetime
from typing import List, Optional
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query, HTTPException, Depends
from pydantic import BaseModel

from ..services.websocket_manager import websocket_manager, SubscriptionType

logger = logging.getLogger(__name__)

# Create router for WebSocket endpoints
router = APIRouter(prefix="/ws", tags=["WebSocket"])

class WebSocketConnectionRequest(BaseModel):
    user_id: str
    user_type: str  # doctor, patient, admin, clinic_staff
    subscriptions: List[str]
    clinic_id: Optional[str] = None
    doctor_id: Optional[str] = None
    patient_id: Optional[str] = None

# WebSocket endpoint for real-time updates
@router.websocket("/appointments")
async def websocket_appointments_endpoint(
    websocket: WebSocket,
    user_id: str = Query(..., description="User ID"),
    user_type: str = Query(..., description="User type: doctor, patient, admin, clinic_staff"),
    subscriptions: str = Query(..., description="Comma-separated subscription types"),
    clinic_id: Optional[str] = Query(None, description="Clinic ID (for clinic-level subscriptions)"),
    doctor_id: Optional[str] = Query(None, description="Doctor ID (for doctor-specific subscriptions)"),
    patient_id: Optional[str] = Query(None, description="Patient ID (for patient-specific subscriptions)")
):
    """
    WebSocket endpoint for real-time appointment and calendar updates

    Connection URL format:
    ws://hostname/ws/appointments?user_id=123&user_type=doctor&subscriptions=doctor_appointments,calendar_conflicts

    Subscription types:
    - doctor_appointments: Updates for doctor's appointments
    - patient_appointments: Updates for patient's appointments
    - clinic_appointments: Updates for clinic-wide appointments
    - calendar_conflicts: Calendar conflict notifications
    - availability_changes: Availability updates
    - all_updates: All types of updates
    """

    connection_id = None

    try:
        # Parse subscriptions
        subscription_list = [sub.strip() for sub in subscriptions.split(',') if sub.strip()]

        # Validate subscription types
        valid_subscriptions = []
        for sub in subscription_list:
            try:
                SubscriptionType(sub)
                valid_subscriptions.append(sub)
            except ValueError:
                logger.warning(f"Invalid subscription type: {sub}")

        if not valid_subscriptions:
            await websocket.close(code=4000, reason="No valid subscription types provided")
            return

        # Connect to WebSocket manager
        connection_id = await websocket_manager.connect(
            websocket=websocket,
            user_id=user_id,
            user_type=user_type,
            subscriptions=valid_subscriptions,
            clinic_id=clinic_id,
            doctor_id=doctor_id,
            patient_id=patient_id
        )

        logger.info(f"WebSocket connection established: {connection_id}")

        # Keep connection alive and handle incoming messages
        while True:
            try:
                # Wait for messages from client
                data = await websocket.receive_text()
                message = json.loads(data)

                # Handle client messages
                await handle_client_message(connection_id, message)

            except WebSocketDisconnect:
                logger.info(f"WebSocket client disconnected: {connection_id}")
                break
            except json.JSONDecodeError:
                logger.warning(f"Invalid JSON received from {connection_id}")
                await websocket.send_text(json.dumps({
                    "type": "error",
                    "message": "Invalid JSON format"
                }))
            except Exception as e:
                logger.error(f"Error handling WebSocket message: {e}")
                await websocket.send_text(json.dumps({
                    "type": "error",
                    "message": "Message processing error"
                }))

    except Exception as e:
        logger.error(f"WebSocket connection error: {e}")
        if connection_id:
            await websocket_manager.disconnect(connection_id)
        try:
            await websocket.close(code=4001, reason="Connection error")
        except:
            pass

    finally:
        # Clean up connection
        if connection_id:
            await websocket_manager.disconnect(connection_id)

async def handle_client_message(connection_id: str, message: dict):
    """Handle incoming messages from WebSocket clients"""
    try:
        message_type = message.get('type')

        if message_type == 'ping':
            # Respond to ping with pong
            await websocket_manager._send_to_connection(connection_id, {
                "type": "pong",
                "timestamp": message.get('timestamp')
            })

        elif message_type == 'subscribe':
            # Handle subscription updates
            new_subscriptions = message.get('subscriptions', [])
            # This would update the connection's subscriptions
            logger.info(f"Subscription update request from {connection_id}: {new_subscriptions}")

        elif message_type == 'unsubscribe':
            # Handle unsubscription
            remove_subscriptions = message.get('subscriptions', [])
            logger.info(f"Unsubscription request from {connection_id}: {remove_subscriptions}")

        elif message_type == 'request_status':
            # Send current status
            stats = await websocket_manager.get_connection_stats()
            await websocket_manager._send_to_connection(connection_id, {
                "type": "status",
                "data": stats
            })

        else:
            logger.warning(f"Unknown message type from {connection_id}: {message_type}")

    except Exception as e:
        logger.error(f"Error handling client message: {e}")

# REST endpoints for WebSocket management

@router.get("/connections/stats")
async def get_websocket_stats():
    """Get current WebSocket connection statistics"""
    try:
        stats = await websocket_manager.get_connection_stats()
        return {
            "success": True,
            "data": stats
        }
    except Exception as e:
        logger.error(f"Failed to get WebSocket stats: {e}")
        raise HTTPException(status_code=500, detail="Failed to retrieve connection statistics")

@router.post("/broadcast/test")
async def test_broadcast(
    notification_type: str,
    target_id: str,
    message: str,
    user_type: Optional[str] = "all"
):
    """
    Test endpoint for broadcasting messages
    Useful for testing WebSocket functionality
    """
    try:
        from ..services.websocket_manager import NotificationType, WebSocketNotification, SubscriptionType

        # Validate notification type
        try:
            notif_type = NotificationType(notification_type)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid notification type: {notification_type}")

        # Create test notification
        notification = WebSocketNotification(
            type=notif_type,
            data={
                "test_message": message,
                "target_id": target_id,
                "user_type": user_type
            },
            timestamp=datetime.now().isoformat(),
            target_id=target_id,
            subscription_type=SubscriptionType.ALL_UPDATES,
            source="test"
        )

        # Broadcast based on user type
        if user_type == "doctor" or user_type == "all":
            await websocket_manager._broadcast_to_doctor(target_id, notification)

        if user_type == "patient" or user_type == "all":
            await websocket_manager._broadcast_to_patient(target_id, notification)

        if user_type == "clinic" or user_type == "all":
            await websocket_manager._broadcast_to_clinic(target_id, notification)

        return {
            "success": True,
            "message": "Test broadcast sent",
            "notification_type": notification_type,
            "target_id": target_id
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to send test broadcast: {e}")
        raise HTTPException(status_code=500, detail="Failed to send test broadcast")

@router.delete("/connections/{connection_id}")
async def disconnect_websocket(connection_id: str):
    """Manually disconnect a specific WebSocket connection"""
    try:
        if connection_id in websocket_manager.connections:
            await websocket_manager.disconnect(connection_id)
            return {
                "success": True,
                "message": f"Connection {connection_id} disconnected"
            }
        else:
            raise HTTPException(status_code=404, detail="Connection not found")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to disconnect WebSocket: {e}")
        raise HTTPException(status_code=500, detail="Failed to disconnect WebSocket")

# WebSocket endpoint for availability updates (specialized)
@router.websocket("/availability")
async def websocket_availability_endpoint(
    websocket: WebSocket,
    doctor_id: str = Query(..., description="Doctor ID for availability updates")
):
    """
    Specialized WebSocket endpoint for real-time availability updates
    Optimized for high-frequency availability changes
    """

    connection_id = None

    try:
        # Connect with availability-specific subscriptions
        connection_id = await websocket_manager.connect(
            websocket=websocket,
            user_id=doctor_id,
            user_type="availability_monitor",
            subscriptions=["availability_changes", "calendar_conflicts"],
            doctor_id=doctor_id
        )

        logger.info(f"Availability WebSocket connection established: {connection_id}")

        # Send initial availability data
        from ..services.unified_appointment_service import UnifiedAppointmentService
        appointment_service = UnifiedAppointmentService()

        # Get today's and tomorrow's availability
        from datetime import datetime, timedelta
        today = datetime.now().strftime('%Y-%m-%d')
        tomorrow = (datetime.now() + timedelta(days=1)).strftime('%Y-%m-%d')

        today_slots = await appointment_service.get_available_slots(doctor_id, today)
        tomorrow_slots = await appointment_service.get_available_slots(doctor_id, tomorrow)

        await websocket.send_text(json.dumps({
            "type": "initial_availability",
            "data": {
                "doctor_id": doctor_id,
                "today": {
                    "date": today,
                    "slots": [
                        {
                            "start_time": slot.start_time.isoformat(),
                            "end_time": slot.end_time.isoformat(),
                            "available": slot.available
                        }
                        for slot in today_slots
                    ]
                },
                "tomorrow": {
                    "date": tomorrow,
                    "slots": [
                        {
                            "start_time": slot.start_time.isoformat(),
                            "end_time": slot.end_time.isoformat(),
                            "available": slot.available
                        }
                        for slot in tomorrow_slots
                    ]
                }
            }
        }))

        # Keep connection alive
        while True:
            data = await websocket.receive_text()
            # Handle availability-specific messages
            await handle_availability_message(connection_id, json.loads(data))

    except WebSocketDisconnect:
        logger.info(f"Availability WebSocket disconnected: {connection_id}")
    except Exception as e:
        logger.error(f"Availability WebSocket error: {e}")
    finally:
        if connection_id:
            await websocket_manager.disconnect(connection_id)

async def handle_availability_message(connection_id: str, message: dict):
    """Handle messages for availability WebSocket"""
    try:
        message_type = message.get('type')

        if message_type == 'refresh_availability':
            # Refresh availability data
            doctor_id = message.get('doctor_id')
            date = message.get('date')

            if doctor_id and date:
                from ..services.unified_appointment_service import UnifiedAppointmentService
                appointment_service = UnifiedAppointmentService()

                slots = await appointment_service.get_available_slots(doctor_id, date)

                await websocket_manager._send_to_connection(connection_id, {
                    "type": "availability_refreshed",
                    "data": {
                        "doctor_id": doctor_id,
                        "date": date,
                        "slots": [
                            {
                                "start_time": slot.start_time.isoformat(),
                                "end_time": slot.end_time.isoformat(),
                                "available": slot.available
                            }
                            for slot in slots
                        ]
                    }
                })

    except Exception as e:
        logger.error(f"Error handling availability message: {e}")

# Health check for WebSocket service
@router.get("/health")
async def websocket_health_check():
    """Health check for WebSocket service"""
    try:
        stats = await websocket_manager.get_connection_stats()
        return {
            "status": "healthy",
            "service": "WebSocket Real-Time Updates",
            "connections": stats["total_connections"],
            "features": [
                "real_time_notifications",
                "multi_source_updates",
                "calendar_conflict_detection",
                "availability_broadcasting",
                "subscription_management"
            ]
        }
    except Exception as e:
        logger.error(f"WebSocket health check failed: {e}")
        raise HTTPException(status_code=503, detail="WebSocket service unavailable")