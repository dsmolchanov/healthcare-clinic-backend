"""
WebSocket Manager for Real-Time Updates
Implements Phase 3: Real-Time Multi-Source Updates
Handles live notifications for appointment changes, calendar conflicts, and availability updates
"""

import json
import asyncio
import logging
from datetime import datetime
from typing import Dict, List, Set, Optional, Any
from dataclasses import dataclass, asdict
from enum import Enum

from fastapi import WebSocket, WebSocketDisconnect
from supabase import create_client, Client
import os

logger = logging.getLogger(__name__)

class NotificationType(Enum):
    APPOINTMENT_CREATED = "appointment_created"
    APPOINTMENT_UPDATED = "appointment_updated"
    APPOINTMENT_CANCELLED = "appointment_cancelled"
    APPOINTMENT_RESCHEDULED = "appointment_rescheduled"
    CALENDAR_CONFLICT = "calendar_conflict"
    AVAILABILITY_UPDATED = "availability_updated"
    CALENDAR_SYNC = "calendar_sync"
    HOLD_EXPIRED = "hold_expired"
    EXTERNAL_CHANGE = "external_change"
    # Dashboard notifications
    CONFLICT_DETECTED = "conflict_detected"
    CONFLICT_RESOLVED = "conflict_resolved"
    CONFLICT_ESCALATED = "conflict_escalated"
    INTERVENTION_REQUIRED = "intervention_required"
    SYSTEM_ALERT = "system_alert"
    METRICS_UPDATE = "metrics_update"

class SubscriptionType(Enum):
    DOCTOR_APPOINTMENTS = "doctor_appointments"
    PATIENT_APPOINTMENTS = "patient_appointments"
    CLINIC_APPOINTMENTS = "clinic_appointments"
    CALENDAR_CONFLICTS = "calendar_conflicts"
    AVAILABILITY_CHANGES = "availability_changes"
    ALL_UPDATES = "all_updates"
    # Dashboard subscriptions
    DASHBOARD = "dashboard"
    MANAGERS = "managers"
    MONITORING = "monitoring"

@dataclass
class WebSocketNotification:
    """Real-time notification structure"""
    type: NotificationType
    data: Dict[str, Any]
    timestamp: str
    target_id: str  # doctor_id, patient_id, or clinic_id
    subscription_type: SubscriptionType
    source: str = "internal"  # internal, google, outlook, etc.

@dataclass
class ConnectionInfo:
    """WebSocket connection information"""
    websocket: WebSocket
    user_id: str
    user_type: str  # doctor, patient, admin, clinic_staff
    subscriptions: Set[SubscriptionType]
    clinic_id: Optional[str] = None
    doctor_id: Optional[str] = None
    patient_id: Optional[str] = None
    connected_at: datetime = None

class WebSocketManager:
    """
    Manages WebSocket connections for real-time updates
    Handles subscription-based notifications and multi-source calendar events
    """

    def __init__(self):
        # Active connections by connection ID
        self.connections: Dict[str, ConnectionInfo] = {}

        # Subscription mapping for efficient broadcasting
        self.doctor_subscriptions: Dict[str, Set[str]] = {}  # doctor_id -> set of connection_ids
        self.patient_subscriptions: Dict[str, Set[str]] = {}  # patient_id -> set of connection_ids
        self.clinic_subscriptions: Dict[str, Set[str]] = {}  # clinic_id -> set of connection_ids

        # Database client for real-time data
        self.supabase: Client = create_client(
            os.environ.get("SUPABASE_URL"),
            os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
        )

    async def connect(
        self,
        websocket: WebSocket,
        user_id: str,
        user_type: str,
        subscriptions: List[str],
        clinic_id: Optional[str] = None,
        doctor_id: Optional[str] = None,
        patient_id: Optional[str] = None
    ) -> str:
        """
        Accept a new WebSocket connection and set up subscriptions
        Returns connection ID
        """
        await websocket.accept()

        connection_id = f"{user_type}_{user_id}_{datetime.now().timestamp()}"

        # Convert subscription strings to enums
        subscription_enums = set()
        for sub in subscriptions:
            try:
                subscription_enums.add(SubscriptionType(sub))
            except ValueError:
                logger.warning(f"Invalid subscription type: {sub}")

        # Create connection info
        connection_info = ConnectionInfo(
            websocket=websocket,
            user_id=user_id,
            user_type=user_type,
            subscriptions=subscription_enums,
            clinic_id=clinic_id,
            doctor_id=doctor_id,
            patient_id=patient_id,
            connected_at=datetime.now()
        )

        # Store connection
        self.connections[connection_id] = connection_info

        # Add to subscription mappings
        await self._add_to_subscriptions(connection_id, connection_info)

        logger.info(f"WebSocket connected: {connection_id} ({user_type}:{user_id})")

        # Send connection confirmation
        await self._send_to_connection(connection_id, {
            "type": "connection_established",
            "connection_id": connection_id,
            "subscriptions": [sub.value for sub in subscription_enums],
            "timestamp": datetime.now().isoformat()
        })

        return connection_id

    async def disconnect(self, connection_id: str):
        """Disconnect and clean up a WebSocket connection"""
        if connection_id in self.connections:
            connection_info = self.connections[connection_id]

            # Remove from subscription mappings
            await self._remove_from_subscriptions(connection_id, connection_info)

            # Close websocket
            try:
                await connection_info.websocket.close()
            except:
                pass  # Connection might already be closed

            # Remove from connections
            del self.connections[connection_id]

            logger.info(f"WebSocket disconnected: {connection_id}")

    async def broadcast_appointment_update(
        self,
        appointment_id: str,
        notification_type: NotificationType,
        appointment_data: Dict[str, Any],
        source: str = "internal"
    ):
        """
        Broadcast appointment update to all relevant subscribers
        """
        try:
            # Get appointment details for targeting
            doctor_id = appointment_data.get('doctor_id')
            patient_id = appointment_data.get('patient_id')
            clinic_id = appointment_data.get('clinic_id')

            notification = WebSocketNotification(
                type=notification_type,
                data={
                    'appointment_id': appointment_id,
                    'appointment_data': appointment_data,
                    'source': source
                },
                timestamp=datetime.now().isoformat(),
                target_id=doctor_id or patient_id or clinic_id,
                subscription_type=SubscriptionType.DOCTOR_APPOINTMENTS,
                source=source
            )

            # Broadcast to relevant subscribers
            await self._broadcast_to_doctor(doctor_id, notification)
            await self._broadcast_to_patient(patient_id, notification)
            await self._broadcast_to_clinic(clinic_id, notification)

        except Exception as e:
            logger.error(f"Failed to broadcast appointment update: {e}")

    async def broadcast_calendar_conflict(
        self,
        conflict_data: Dict[str, Any],
        affected_doctors: List[str],
        source: str = "external"
    ):
        """
        Broadcast calendar conflict detection to affected parties
        """
        try:
            notification = WebSocketNotification(
                type=NotificationType.CALENDAR_CONFLICT,
                data={
                    'conflict_data': conflict_data,
                    'affected_doctors': affected_doctors,
                    'source': source,
                    'severity': conflict_data.get('severity', 'medium'),
                    'resolution_suggestions': conflict_data.get('suggestions', [])
                },
                timestamp=datetime.now().isoformat(),
                target_id=','.join(affected_doctors),
                subscription_type=SubscriptionType.CALENDAR_CONFLICTS,
                source=source
            )

            # Broadcast to all affected doctors
            for doctor_id in affected_doctors:
                await self._broadcast_to_doctor(doctor_id, notification)

            # Also broadcast to clinic admins
            for doctor_id in affected_doctors:
                # Get doctor's clinic and notify clinic admins
                await self._broadcast_to_clinic_admins(doctor_id, notification)

        except Exception as e:
            logger.error(f"Failed to broadcast calendar conflict: {e}")

    async def broadcast_availability_change(
        self,
        doctor_id: str,
        date: str,
        changed_slots: List[Dict[str, Any]],
        source: str = "internal"
    ):
        """
        Broadcast availability changes to subscribers
        """
        try:
            notification = WebSocketNotification(
                type=NotificationType.AVAILABILITY_UPDATED,
                data={
                    'doctor_id': doctor_id,
                    'date': date,
                    'changed_slots': changed_slots,
                    'source': source
                },
                timestamp=datetime.now().isoformat(),
                target_id=doctor_id,
                subscription_type=SubscriptionType.AVAILABILITY_CHANGES,
                source=source
            )

            # Broadcast to availability subscribers
            await self._broadcast_to_availability_subscribers(notification)

        except Exception as e:
            logger.error(f"Failed to broadcast availability change: {e}")

    async def broadcast_external_calendar_change(
        self,
        doctor_id: str,
        provider: str,
        change_data: Dict[str, Any]
    ):
        """
        Broadcast external calendar changes (from Google, Outlook, etc.)
        """
        try:
            notification = WebSocketNotification(
                type=NotificationType.EXTERNAL_CHANGE,
                data={
                    'doctor_id': doctor_id,
                    'provider': provider,
                    'change_data': change_data
                },
                timestamp=datetime.now().isoformat(),
                target_id=doctor_id,
                subscription_type=SubscriptionType.ALL_UPDATES,
                source=provider
            )

            await self._broadcast_to_doctor(doctor_id, notification)

        except Exception as e:
            logger.error(f"Failed to broadcast external calendar change: {e}")

    async def broadcast(self, message: str, channel: str = "all"):
        """
        Broadcast a message to all connections in a channel

        Args:
            message: Message to broadcast
            channel: Target channel
        """
        try:
            # Parse the channel to determine subscription type
            subscription_type = SubscriptionType.ALL_UPDATES
            if channel == "dashboard":
                subscription_type = SubscriptionType.DASHBOARD
            elif channel == "managers":
                subscription_type = SubscriptionType.MANAGERS
            elif channel == "monitoring":
                subscription_type = SubscriptionType.MONITORING

            # Broadcast to all connections with matching subscription
            for ws, subs in self.connections.items():
                if subscription_type in subs or SubscriptionType.ALL_UPDATES in subs:
                    try:
                        await ws.send_text(message)
                    except Exception as e:
                        logger.error(f"Failed to send to connection: {e}")
                        await self._handle_disconnect(ws)

        except Exception as e:
            logger.error(f"Failed to broadcast message: {e}")

    def get_connection_stats(self) -> Dict[str, Any]:
        """
        Get WebSocket connection statistics

        Returns:
            Dictionary with connection statistics
        """
        try:
            total_connections = len(self.connections)
            subscription_counts = {}

            for subs in self.connections.values():
                for sub in subs:
                    sub_name = sub.value
                    subscription_counts[sub_name] = subscription_counts.get(sub_name, 0) + 1

            return {
                "active_connections": total_connections,
                "subscriptions": subscription_counts,
                "total_connections": total_connections,
                "timestamp": datetime.now().isoformat()
            }
        except Exception as e:
            logger.error(f"Failed to get connection stats: {e}")
            return {
                "active_connections": 0,
                "subscriptions": {},
                "total_connections": 0,
                "error": str(e)
            }

    async def broadcast_hold_expiration(
        self,
        reservation_id: str,
        doctor_id: str,
        appointment_data: Dict[str, Any]
    ):
        """
        Broadcast hold expiration notifications
        """
        try:
            notification = WebSocketNotification(
                type=NotificationType.HOLD_EXPIRED,
                data={
                    'reservation_id': reservation_id,
                    'doctor_id': doctor_id,
                    'appointment_data': appointment_data,
                    'action_required': True
                },
                timestamp=datetime.now().isoformat(),
                target_id=doctor_id,
                subscription_type=SubscriptionType.DOCTOR_APPOINTMENTS,
                source="internal"
            )

            await self._broadcast_to_doctor(doctor_id, notification)

        except Exception as e:
            logger.error(f"Failed to broadcast hold expiration: {e}")

    # Private helper methods

    async def _add_to_subscriptions(self, connection_id: str, connection_info: ConnectionInfo):
        """Add connection to subscription mappings"""
        if connection_info.doctor_id:
            if connection_info.doctor_id not in self.doctor_subscriptions:
                self.doctor_subscriptions[connection_info.doctor_id] = set()
            self.doctor_subscriptions[connection_info.doctor_id].add(connection_id)

        if connection_info.patient_id:
            if connection_info.patient_id not in self.patient_subscriptions:
                self.patient_subscriptions[connection_info.patient_id] = set()
            self.patient_subscriptions[connection_info.patient_id].add(connection_id)

        if connection_info.clinic_id:
            if connection_info.clinic_id not in self.clinic_subscriptions:
                self.clinic_subscriptions[connection_info.clinic_id] = set()
            self.clinic_subscriptions[connection_info.clinic_id].add(connection_id)

    async def _remove_from_subscriptions(self, connection_id: str, connection_info: ConnectionInfo):
        """Remove connection from subscription mappings"""
        if connection_info.doctor_id and connection_info.doctor_id in self.doctor_subscriptions:
            self.doctor_subscriptions[connection_info.doctor_id].discard(connection_id)
            if not self.doctor_subscriptions[connection_info.doctor_id]:
                del self.doctor_subscriptions[connection_info.doctor_id]

        if connection_info.patient_id and connection_info.patient_id in self.patient_subscriptions:
            self.patient_subscriptions[connection_info.patient_id].discard(connection_id)
            if not self.patient_subscriptions[connection_info.patient_id]:
                del self.patient_subscriptions[connection_info.patient_id]

        if connection_info.clinic_id and connection_info.clinic_id in self.clinic_subscriptions:
            self.clinic_subscriptions[connection_info.clinic_id].discard(connection_id)
            if not self.clinic_subscriptions[connection_info.clinic_id]:
                del self.clinic_subscriptions[connection_info.clinic_id]

    async def _broadcast_to_doctor(self, doctor_id: str, notification: WebSocketNotification):
        """Broadcast notification to all connections subscribed to a doctor"""
        if doctor_id and doctor_id in self.doctor_subscriptions:
            connection_ids = self.doctor_subscriptions[doctor_id].copy()
            await self._broadcast_to_connections(connection_ids, notification)

    async def _broadcast_to_patient(self, patient_id: str, notification: WebSocketNotification):
        """Broadcast notification to all connections subscribed to a patient"""
        if patient_id and patient_id in self.patient_subscriptions:
            connection_ids = self.patient_subscriptions[patient_id].copy()
            # Create patient-specific notification
            patient_notification = WebSocketNotification(
                type=notification.type,
                data=notification.data,
                timestamp=notification.timestamp,
                target_id=patient_id,
                subscription_type=SubscriptionType.PATIENT_APPOINTMENTS,
                source=notification.source
            )
            await self._broadcast_to_connections(connection_ids, patient_notification)

    async def _broadcast_to_clinic(self, clinic_id: str, notification: WebSocketNotification):
        """Broadcast notification to all connections subscribed to a clinic"""
        if clinic_id and clinic_id in self.clinic_subscriptions:
            connection_ids = self.clinic_subscriptions[clinic_id].copy()
            # Create clinic-specific notification
            clinic_notification = WebSocketNotification(
                type=notification.type,
                data=notification.data,
                timestamp=notification.timestamp,
                target_id=clinic_id,
                subscription_type=SubscriptionType.CLINIC_APPOINTMENTS,
                source=notification.source
            )
            await self._broadcast_to_connections(connection_ids, clinic_notification)

    async def _broadcast_to_availability_subscribers(self, notification: WebSocketNotification):
        """Broadcast to all connections subscribed to availability changes"""
        target_connections = set()

        for connection_id, connection_info in self.connections.items():
            if SubscriptionType.AVAILABILITY_CHANGES in connection_info.subscriptions:
                target_connections.add(connection_id)

        await self._broadcast_to_connections(target_connections, notification)

    async def _broadcast_to_clinic_admins(self, doctor_id: str, notification: WebSocketNotification):
        """Broadcast to clinic admins for a given doctor"""
        try:
            # Get doctor's clinic
            doctor_result = self.supabase.table('doctors')\
                .select('clinic_id')\
                .eq('id', doctor_id)\
                .execute()

            if doctor_result.data:
                clinic_id = doctor_result.data[0]['clinic_id']
                await self._broadcast_to_clinic(clinic_id, notification)
        except Exception as e:
            logger.error(f"Failed to broadcast to clinic admins: {e}")

    async def _broadcast_to_connections(
        self,
        connection_ids: Set[str],
        notification: WebSocketNotification
    ):
        """Send notification to specific connection IDs"""
        message = asdict(notification)

        # Send to all connections in parallel
        tasks = []
        for connection_id in connection_ids:
            if connection_id in self.connections:
                tasks.append(self._send_to_connection(connection_id, message))

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _send_to_connection(self, connection_id: str, message: Dict[str, Any]):
        """Send message to a specific connection"""
        if connection_id not in self.connections:
            return

        connection_info = self.connections[connection_id]

        try:
            await connection_info.websocket.send_text(json.dumps(message))
        except WebSocketDisconnect:
            # Connection closed, remove it
            await self.disconnect(connection_id)
        except Exception as e:
            logger.error(f"Failed to send message to {connection_id}: {e}")
            await self.disconnect(connection_id)

    async def get_connection_stats(self) -> Dict[str, Any]:
        """Get current WebSocket connection statistics"""
        return {
            "total_connections": len(self.connections),
            "doctor_subscriptions": len(self.doctor_subscriptions),
            "patient_subscriptions": len(self.patient_subscriptions),
            "clinic_subscriptions": len(self.clinic_subscriptions),
            "connections_by_type": {
                user_type: len([c for c in self.connections.values() if c.user_type == user_type])
                for user_type in ["doctor", "patient", "admin", "clinic_staff"]
            }
        }

# Global WebSocket manager instance
websocket_manager = WebSocketManager()