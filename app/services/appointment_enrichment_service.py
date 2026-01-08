"""
Appointment Enrichment Service

Provides enrichment data for calendar appointments:
- Reminder status from appointment_message_plan
- HITL (Human-in-the-Loop) status from conversation_sessions

Used by the frontend calendar to show visual indicators for:
- Appointment confirmation status (border colors)
- Patients needing staff attention (HITL badge)
"""

import logging
from typing import Dict, List, Optional, Any
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)


class ReminderStatus(str, Enum):
    """Status of reminder messages for an appointment."""
    PENDING = "pending"        # No messages sent yet
    SENT = "sent"              # Confirmation sent, awaiting response
    CONFIRMED = "confirmed"    # Patient confirmed
    NO_RESPONSE = "no_response"  # No response after 24h
    FAILED = "failed"          # Delivery failed


@dataclass
class HITLStatus:
    """Human-in-the-loop status for a patient's conversation session."""
    needs_attention: bool
    control_mode: str  # 'agent', 'human', 'paused'
    reason: Optional[str] = None
    unread_count: int = 0
    locked_by: Optional[str] = None


@dataclass
class AppointmentEnrichment:
    """Enrichment data for a single appointment."""
    appointment_id: str
    reminder_status: ReminderStatus
    hitl_status: Optional[HITLStatus] = None


class AppointmentEnrichmentService:
    """
    Service for enriching appointments with reminder and HITL status.

    Optimized for batch lookups to avoid N+1 queries when fetching
    calendar data for a date range.
    """

    def __init__(self, db_client):
        """
        Initialize the service.

        Args:
            db_client: Supabase client configured for healthcare schema
        """
        self.db = db_client

    async def get_reminder_statuses_batch(
        self,
        appointment_ids: List[str]
    ) -> Dict[str, ReminderStatus]:
        """
        Get reminder status for multiple appointments in a single query.

        Args:
            appointment_ids: List of appointment UUIDs

        Returns:
            Dict mapping appointment_id to ReminderStatus
        """
        if not appointment_ids:
            return {}

        try:
            # Query all message plans for these appointments
            result = self.db.table("appointment_message_plan").select(
                "appointment_id, message_type, status, sent_at, metadata"
            ).in_("appointment_id", appointment_ids).execute()

            # Group by appointment_id
            plans_by_appointment: Dict[str, List[Dict]] = {}
            for plan in result.data or []:
                apt_id = plan["appointment_id"]
                if apt_id not in plans_by_appointment:
                    plans_by_appointment[apt_id] = []
                plans_by_appointment[apt_id].append(plan)

            # Determine status for each appointment
            statuses: Dict[str, ReminderStatus] = {}
            now = datetime.now(timezone.utc)
            threshold_24h = now - timedelta(hours=24)

            for apt_id in appointment_ids:
                plans = plans_by_appointment.get(apt_id, [])
                statuses[apt_id] = self._determine_reminder_status(plans, threshold_24h)

            return statuses

        except Exception as e:
            logger.error(f"Error fetching reminder statuses: {e}")
            # Return pending for all on error
            return {apt_id: ReminderStatus.PENDING for apt_id in appointment_ids}

    def _determine_reminder_status(
        self,
        plans: List[Dict],
        threshold_24h: datetime
    ) -> ReminderStatus:
        """
        Determine the overall reminder status from message plans.

        Priority: confirmed > no_response > sent > failed > pending
        """
        if not plans:
            return ReminderStatus.PENDING

        # Look for confirmation messages
        confirmation_plans = [p for p in plans if p["message_type"] == "confirmation"]

        if not confirmation_plans:
            return ReminderStatus.PENDING

        for plan in confirmation_plans:
            status = plan["status"]
            metadata = plan.get("metadata") or {}

            # Check if patient confirmed
            if metadata.get("patient_confirmed") or metadata.get("response_received") == "confirmed":
                return ReminderStatus.CONFIRMED

            # Check delivery status
            if status == "failed":
                return ReminderStatus.FAILED

            if status in ("sent", "delivered"):
                # Check if 24h has passed without response
                sent_at = plan.get("sent_at")
                if sent_at:
                    sent_datetime = datetime.fromisoformat(sent_at.replace("Z", "+00:00"))
                    # Ensure timezone-aware comparison
                    if sent_datetime.tzinfo is None:
                        sent_datetime = sent_datetime.replace(tzinfo=timezone.utc)
                    if sent_datetime < threshold_24h:
                        return ReminderStatus.NO_RESPONSE
                return ReminderStatus.SENT

        # If we have scheduled but not sent confirmations
        scheduled = [p for p in confirmation_plans if p["status"] == "scheduled"]
        if scheduled:
            return ReminderStatus.PENDING

        return ReminderStatus.PENDING

    async def get_hitl_statuses_batch(
        self,
        patient_phones: List[str],
        clinic_id: str
    ) -> Dict[str, HITLStatus]:
        """
        Get HITL status for multiple patients by their phone numbers.

        Args:
            patient_phones: List of patient phone numbers (E.164 format)
            clinic_id: Clinic UUID to scope the lookup

        Returns:
            Dict mapping phone number to HITLStatus
        """
        if not patient_phones or not clinic_id:
            return {}

        try:
            # Query active sessions for these patients in this clinic
            # Filter by metadata->>clinic_id in Python since JSON operators vary
            result = self.db.table("conversation_sessions").select(
                "user_identifier, control_mode, lock_reason, locked_by, unread_for_human_count, metadata"
            ).in_("user_identifier", patient_phones).eq(
                "status", "active"
            ).execute()

            # Filter by clinic_id and get most recent per user
            sessions_by_phone: Dict[str, Dict] = {}
            for session in result.data or []:
                metadata = session.get("metadata") or {}
                session_clinic = metadata.get("clinic_id")

                if session_clinic == clinic_id:
                    phone = session["user_identifier"]
                    # Keep the session (assuming ordered by updated_at desc or we take any active)
                    if phone not in sessions_by_phone:
                        sessions_by_phone[phone] = session

            # Build HITL status for each phone
            statuses: Dict[str, HITLStatus] = {}
            for phone in patient_phones:
                session = sessions_by_phone.get(phone)
                if session:
                    control_mode = session.get("control_mode", "agent")
                    statuses[phone] = HITLStatus(
                        needs_attention=control_mode in ("human", "paused"),
                        control_mode=control_mode,
                        reason=session.get("lock_reason"),
                        unread_count=session.get("unread_for_human_count", 0) or 0,
                        locked_by=session.get("locked_by")
                    )
                else:
                    statuses[phone] = HITLStatus(
                        needs_attention=False,
                        control_mode="agent"
                    )

            return statuses

        except Exception as e:
            logger.error(f"Error fetching HITL statuses: {e}")
            # Return default (no attention needed) on error
            return {
                phone: HITLStatus(needs_attention=False, control_mode="agent")
                for phone in patient_phones
            }

    async def enrich_appointments(
        self,
        appointments: List[Dict[str, Any]],
        clinic_id: str
    ) -> List[Dict[str, Any]]:
        """
        Enrich a list of appointments with reminder and HITL status.

        This is the main entry point for calendar data enrichment.
        Modifies appointments in-place and returns them.

        Args:
            appointments: List of appointment dicts (must have 'id' and optionally 'patient_phone')
            clinic_id: Clinic UUID

        Returns:
            The same appointments list with added 'reminder_status' and 'hitl_status' fields
        """
        if not appointments:
            return appointments

        # Extract IDs and phones
        appointment_ids = [apt["id"] for apt in appointments if apt.get("id")]

        # Get patient phones - they might be directly on appointment or need lookup
        patient_phones = []
        apt_to_phone = {}

        for apt in appointments:
            phone = apt.get("patient_phone")
            if phone:
                patient_phones.append(phone)
                apt_to_phone[apt["id"]] = phone

        # If no phones on appointments, we need to look them up via patient_id
        if not patient_phones:
            patient_ids = [str(apt.get("patient_id")) for apt in appointments if apt.get("patient_id")]
            if patient_ids:
                apt_to_phone = await self._lookup_patient_phones(appointments, patient_ids)
                patient_phones = list(set(apt_to_phone.values()))

        # Batch fetch statuses
        reminder_statuses = await self.get_reminder_statuses_batch(appointment_ids)
        hitl_statuses = await self.get_hitl_statuses_batch(patient_phones, clinic_id) if patient_phones else {}

        # Enrich appointments
        for apt in appointments:
            apt_id = apt.get("id")
            if not apt_id:
                continue

            phone = apt_to_phone.get(apt_id)

            # Add reminder status
            reminder_status = reminder_statuses.get(apt_id, ReminderStatus.PENDING)
            apt["reminder_status"] = reminder_status.value

            # Add HITL status
            if phone and phone in hitl_statuses:
                hitl = hitl_statuses[phone]
                apt["hitl_status"] = {
                    "needs_attention": hitl.needs_attention,
                    "control_mode": hitl.control_mode,
                    "reason": hitl.reason,
                    "unread_count": hitl.unread_count
                }
            else:
                apt["hitl_status"] = {
                    "needs_attention": False,
                    "control_mode": "agent"
                }

        return appointments

    async def _lookup_patient_phones(
        self,
        appointments: List[Dict],
        patient_ids: List[str]
    ) -> Dict[str, str]:
        """
        Look up patient phone numbers by patient IDs.

        Returns mapping of appointment_id -> phone
        """
        try:
            result = self.db.table("patients").select(
                "id, phone"
            ).in_("id", patient_ids).execute()

            patient_phones = {p["id"]: p["phone"] for p in result.data or [] if p.get("phone")}

            # Map appointment_id -> phone
            apt_to_phone = {}
            for apt in appointments:
                patient_id = apt.get("patient_id")
                if patient_id and patient_id in patient_phones:
                    apt_to_phone[apt["id"]] = patient_phones[patient_id]

            return apt_to_phone

        except Exception as e:
            logger.error(f"Error looking up patient phones: {e}")
            return {}


# Factory function for dependency injection
def get_appointment_enrichment_service(db_client) -> AppointmentEnrichmentService:
    """Create an AppointmentEnrichmentService instance."""
    return AppointmentEnrichmentService(db_client)
