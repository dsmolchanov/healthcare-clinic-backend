"""
Escalation Management System for Scheduling

Handles booking requests that cannot be auto-scheduled by:
1. Creating escalation records for manual review
2. Generating alternative suggestions by relaxing constraints
3. Managing escalation queue and SLA tracking
4. Resolving escalations through staff intervention
"""

from uuid import UUID
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any
import logging

logger = logging.getLogger(__name__)


class EscalationManager:
    """
    Manages escalations when auto-scheduling fails.

    Integration points:
    - Called by scheduling_service when zero slots are found
    - Used by API routes for escalation queue management
    - Integrates with appointment service for resolution
    """

    def __init__(self, db):
        """
        Initialize escalation manager.

        Args:
            db: Database connection (Supabase client or similar)
        """
        self.db = db
        self.default_sla_hours = 24
        self.max_suggestions = 5

    async def create_escalation(
        self,
        clinic_id: UUID,
        request: dict,
        reason: str,
        suggestions: Optional[List[dict]] = None
    ) -> dict:
        """
        Create escalation when auto-scheduling fails.

        Args:
            clinic_id: Clinic identifier
            request: Original booking request with keys:
                - service_id: UUID of the service
                - patient_id: UUID of the patient
                - date_range: dict with start_date and end_date (ISO format)
                - hard_constraints: Optional dict with constraints (doctor_id, time_of_day, etc.)
            reason: Why auto-scheduling failed (e.g., "No available slots found")
            suggestions: Alternative options (relaxed constraints). If None, will be auto-generated.

        Returns:
            Escalation record dict with keys:
                - id: UUID of escalation
                - clinic_id: UUID
                - status: 'open'
                - request: Original request
                - reason: Failure reason
                - suggestions: List of alternative suggestions
                - sla_deadline: Datetime when response is due
                - created_at: Datetime when created

        Raises:
            ValueError: If request is invalid or missing required fields
        """
        # Validate request
        self._validate_request(request)

        # Check for duplicate escalations (within last 24 hours)
        existing = await self._check_duplicate_escalation(clinic_id, request)
        if existing:
            logger.info("escalation.duplicate_found", extra={
                "escalation_id": existing['id'],
                "clinic_id": str(clinic_id)
            })
            return existing

        # Calculate SLA deadline
        sla_deadline = datetime.now() + timedelta(hours=self.default_sla_hours)

        # Generate suggestions if not provided
        if suggestions is None:
            suggestions = await self._generate_suggestions(clinic_id, request)

        # Insert into sched_escalations table
        try:
            result = self.db.table('sched_escalations').insert({
                'clinic_id': str(clinic_id),
                'status': 'open',
                'request': request,
                'reason': reason,
                'suggestions': suggestions,
                'sla_deadline': sla_deadline.isoformat()
            }).execute()

            escalation = result.data[0] if result.data else None
            if not escalation:
                raise Exception("Failed to create escalation record")

        except Exception as e:
            logger.error("escalation.creation_failed", extra={
                "error": str(e),
                "clinic_id": str(clinic_id)
            })
            raise

        # Send notification to staff
        await self._notify_staff(clinic_id, escalation)

        logger.info("escalation.created", extra={
            "escalation_id": escalation['id'],
            "clinic_id": str(clinic_id),
            "reason": reason,
            "suggestions_count": len(suggestions),
            "sla_deadline": sla_deadline.isoformat()
        })

        return escalation

    async def resolve_escalation(
        self,
        escalation_id: UUID,
        resolution: dict,
        resolved_by: UUID
    ) -> dict:
        """
        Resolve an escalation (pick suggestion or manual decision).

        Args:
            escalation_id: Escalation identifier
            resolution: Resolution details with one of:
                - selected_suggestion_index: int (0-based index into suggestions array)
                - manual_slot: dict with appointment details (start_time, end_time, doctor_id, etc.)
                - note: Optional str with resolution notes
            resolved_by: UUID of staff member who resolved it

        Returns:
            Created appointment dict with keys:
                - id: Appointment UUID
                - escalation_id: Reference to escalation
                - status: Appointment status
                - [other appointment fields]

        Raises:
            ValueError: If escalation not found or not in 'open' status
        """
        # Fetch escalation
        escalation = await self._get_escalation(escalation_id)

        if escalation['status'] != 'open':
            raise ValueError(f"Escalation {escalation_id} is not open (status: {escalation['status']})")

        # Create appointment from resolution
        appointment = await self._create_appointment_from_resolution(
            escalation, resolution, resolved_by
        )

        # Update escalation status
        try:
            self.db.table('sched_escalations').update({
                'status': 'resolved',
                'resolved_at': datetime.now().isoformat(),
                'resolution_note': resolution.get('note', '')
            }).eq('id', str(escalation_id)).execute()

        except Exception as e:
            logger.error("escalation.resolution_update_failed", extra={
                "error": str(e),
                "escalation_id": str(escalation_id)
            })
            raise

        logger.info("escalation.resolved", extra={
            "escalation_id": str(escalation_id),
            "resolved_by": str(resolved_by),
            "appointment_id": appointment.get('id')
        })

        return appointment

    async def get_escalation_queue(
        self,
        clinic_id: UUID,
        status: str = 'open'
    ) -> List[dict]:
        """
        Get escalations for clinic dashboard.

        Args:
            clinic_id: Clinic identifier
            status: Filter by status ('open', 'assigned', 'resolved', 'declined')

        Returns:
            List of escalation records ordered by SLA deadline (urgent first)
        """
        try:
            result = self.db.table('sched_escalations').select('*').eq(
                'clinic_id', str(clinic_id)
            ).eq('status', status).order('sla_deadline', desc=False).execute()

            escalations = result.data if result.data else []

            logger.info("escalation.queue_fetched", extra={
                "clinic_id": str(clinic_id),
                "status": status,
                "count": len(escalations)
            })

            return escalations

        except Exception as e:
            logger.error("escalation.queue_fetch_failed", extra={
                "error": str(e),
                "clinic_id": str(clinic_id)
            })
            return []

    async def assign_escalation(
        self,
        escalation_id: UUID,
        assigned_to: UUID
    ) -> dict:
        """
        Assign escalation to a staff member.

        Args:
            escalation_id: Escalation identifier
            assigned_to: UUID of staff member

        Returns:
            Updated escalation record
        """
        try:
            result = self.db.table('sched_escalations').update({
                'status': 'assigned',
                'assigned_to': str(assigned_to)
            }).eq('id', str(escalation_id)).execute()

            escalation = result.data[0] if result.data else None
            if not escalation:
                raise ValueError(f"Escalation {escalation_id} not found")

            logger.info("escalation.assigned", extra={
                "escalation_id": str(escalation_id),
                "assigned_to": str(assigned_to)
            })

            return escalation

        except Exception as e:
            logger.error("escalation.assignment_failed", extra={
                "error": str(e),
                "escalation_id": str(escalation_id)
            })
            raise

    async def decline_escalation(
        self,
        escalation_id: UUID,
        reason: str,
        declined_by: UUID
    ) -> dict:
        """
        Decline an escalation (cannot be fulfilled).

        Args:
            escalation_id: Escalation identifier
            reason: Why it's being declined
            declined_by: UUID of staff member

        Returns:
            Updated escalation record
        """
        try:
            result = self.db.table('sched_escalations').update({
                'status': 'declined',
                'resolved_at': datetime.now().isoformat(),
                'resolution_note': f"Declined: {reason}"
            }).eq('id', str(escalation_id)).execute()

            escalation = result.data[0] if result.data else None
            if not escalation:
                raise ValueError(f"Escalation {escalation_id} not found")

            logger.info("escalation.declined", extra={
                "escalation_id": str(escalation_id),
                "declined_by": str(declined_by),
                "reason": reason
            })

            return escalation

        except Exception as e:
            logger.error("escalation.decline_failed", extra={
                "error": str(e),
                "escalation_id": str(escalation_id)
            })
            raise

    # ========== Private Helper Methods ==========

    def _validate_request(self, request: dict) -> None:
        """
        Validate that request contains required fields.

        Raises:
            ValueError: If request is invalid
        """
        required_fields = ['service_id', 'patient_id', 'date_range']
        missing = [f for f in required_fields if f not in request]

        if missing:
            raise ValueError(f"Request missing required fields: {', '.join(missing)}")

        # Validate date_range structure
        date_range = request.get('date_range', {})
        if not isinstance(date_range, dict) or 'start_date' not in date_range or 'end_date' not in date_range:
            raise ValueError("date_range must contain start_date and end_date")

    async def _check_duplicate_escalation(
        self,
        clinic_id: UUID,
        request: dict
    ) -> Optional[dict]:
        """
        Check if similar escalation exists within deduplication window (24 hours).

        Returns:
            Existing escalation or None
        """
        try:
            # Check for escalations with same patient and service in last 24 hours
            cutoff_time = datetime.now() - timedelta(hours=24)

            result = self.db.table('sched_escalations').select('*').eq(
                'clinic_id', str(clinic_id)
            ).eq('status', 'open').gte(
                'created_at', cutoff_time.isoformat()
            ).execute()

            escalations = result.data if result.data else []

            # Check if any match the same patient and service
            for esc in escalations:
                esc_request = esc.get('request', {})
                if (esc_request.get('patient_id') == request.get('patient_id') and
                    esc_request.get('service_id') == request.get('service_id')):
                    return esc

            return None

        except Exception as e:
            logger.warning("escalation.duplicate_check_failed", extra={
                "error": str(e),
                "clinic_id": str(clinic_id)
            })
            return None

    async def _generate_suggestions(
        self,
        clinic_id: UUID,
        request: dict
    ) -> List[dict]:
        """
        Generate alternative suggestions by relaxing constraints.

        Relaxation strategies (in order of preference):
        1. Expand date range (+3 days)
        2. Remove time-of-day preference
        3. Remove doctor preference
        4. Expand date range further (+7 days)
        5. Any available slot in next 14 days

        Returns:
            List of up to 5 suggestions, each with:
                - strategy: str (name of relaxation strategy)
                - request: dict (modified request)
                - description: str (user-friendly explanation)
        """
        suggestions = []
        hard_constraints = request.get('hard_constraints', {})
        date_range = request.get('date_range', {})

        # Strategy 1: Expand date range (+3 days)
        if 'end_date' in date_range:
            try:
                end_date = datetime.fromisoformat(date_range['end_date'])
                expanded_request = request.copy()
                expanded_request['date_range'] = date_range.copy()
                expanded_request['date_range']['end_date'] = (
                    end_date + timedelta(days=3)
                ).isoformat()

                suggestions.append({
                    'strategy': 'expanded_date_range_3d',
                    'request': expanded_request,
                    'description': 'Try dates 3 days later than your preferred range'
                })
            except (ValueError, TypeError) as e:
                logger.warning("Failed to parse end_date for suggestion", extra={"error": str(e)})

        # Strategy 2: Remove time preference
        if hard_constraints.get('time_of_day'):
            relaxed_request = request.copy()
            relaxed_request['hard_constraints'] = hard_constraints.copy()
            del relaxed_request['hard_constraints']['time_of_day']

            suggestions.append({
                'strategy': 'remove_time_preference',
                'request': relaxed_request,
                'description': 'Try any time of day (morning, afternoon, or evening)'
            })

        # Strategy 3: Remove doctor preference
        if hard_constraints.get('doctor_id'):
            relaxed_request = request.copy()
            relaxed_request['hard_constraints'] = hard_constraints.copy()
            del relaxed_request['hard_constraints']['doctor_id']

            suggestions.append({
                'strategy': 'any_doctor',
                'request': relaxed_request,
                'description': 'Try any available doctor at the clinic'
            })

        # Strategy 4: Expand date range further (+7 days)
        if 'end_date' in date_range:
            try:
                end_date = datetime.fromisoformat(date_range['end_date'])
                expanded_request = request.copy()
                expanded_request['date_range'] = date_range.copy()
                expanded_request['date_range']['end_date'] = (
                    end_date + timedelta(days=7)
                ).isoformat()

                suggestions.append({
                    'strategy': 'expanded_date_range_7d',
                    'request': expanded_request,
                    'description': 'Try dates up to 7 days later than your preferred range'
                })
            except (ValueError, TypeError) as e:
                logger.warning("Failed to parse end_date for strategy 4", extra={"error": str(e)})

        # Strategy 5: Remove all preferences - any slot in next 14 days
        if hard_constraints or 'end_date' in date_range:
            fully_relaxed_request = request.copy()
            fully_relaxed_request['hard_constraints'] = {}

            # Set date range to next 14 days
            start_date = date_range.get('start_date', datetime.now().isoformat())
            try:
                start_dt = datetime.fromisoformat(start_date)
                fully_relaxed_request['date_range'] = {
                    'start_date': start_dt.isoformat(),
                    'end_date': (start_dt + timedelta(days=14)).isoformat()
                }

                suggestions.append({
                    'strategy': 'fully_relaxed',
                    'request': fully_relaxed_request,
                    'description': 'Try any available slot in the next 2 weeks'
                })
            except (ValueError, TypeError) as e:
                logger.warning("Failed to create fully relaxed suggestion", extra={"error": str(e)})

        # Return max 5 suggestions
        return suggestions[:self.max_suggestions]

    async def _notify_staff(self, clinic_id: UUID, escalation: dict) -> None:
        """
        Send notification to clinic staff about new escalation.

        For v1: Log notification (email/Slack integration in v2).

        Args:
            clinic_id: Clinic identifier
            escalation: Escalation record
        """
        try:
            # TODO: Integrate with email service (Supabase SMTP or SendGrid)
            # TODO: Integrate with Slack webhook for real-time alerts
            # TODO: Check clinic preferences for notification channels

            # For now, just log at WARNING level so it shows up in monitoring
            logger.warning("escalation.notification_pending", extra={
                "clinic_id": str(clinic_id),
                "escalation_id": escalation['id'],
                "sla_deadline": escalation['sla_deadline'],
                "reason": escalation['reason'],
                "patient_id": escalation['request'].get('patient_id'),
                "service_id": escalation['request'].get('service_id')
            })

        except Exception as e:
            # Don't fail escalation creation if notification fails
            logger.error("escalation.notification_failed", extra={
                "error": str(e),
                "escalation_id": escalation.get('id')
            })

    async def _get_escalation(self, escalation_id: UUID) -> dict:
        """
        Fetch escalation by ID.

        Args:
            escalation_id: Escalation identifier

        Returns:
            Escalation record

        Raises:
            ValueError: If escalation not found
        """
        try:
            result = self.db.table('sched_escalations').select('*').eq(
                'id', str(escalation_id)
            ).execute()

            escalations = result.data if result.data else []
            if not escalations:
                raise ValueError(f"Escalation {escalation_id} not found")

            return escalations[0]

        except Exception as e:
            logger.error("escalation.fetch_failed", extra={
                "error": str(e),
                "escalation_id": str(escalation_id)
            })
            raise

    async def _create_appointment_from_resolution(
        self,
        escalation: dict,
        resolution: dict,
        resolved_by: UUID
    ) -> dict:
        """
        Create appointment from escalation resolution.

        Args:
            escalation: Escalation record
            resolution: Resolution details
            resolved_by: Staff member UUID

        Returns:
            Created appointment record

        Raises:
            ValueError: If resolution is invalid
        """
        # Extract original request
        request = escalation.get('request', {})

        # Determine appointment details from resolution
        if 'selected_suggestion_index' in resolution:
            # Staff selected one of the suggestions
            idx = resolution['selected_suggestion_index']
            suggestions = escalation.get('suggestions', [])

            if idx < 0 or idx >= len(suggestions):
                raise ValueError(f"Invalid suggestion index: {idx}")

            selected = suggestions[idx]
            appointment_request = selected['request']

            logger.info("escalation.resolution_from_suggestion", extra={
                "escalation_id": escalation['id'],
                "suggestion_index": idx,
                "strategy": selected.get('strategy')
            })

        elif 'manual_slot' in resolution:
            # Staff manually selected a slot
            appointment_request = resolution['manual_slot']

            logger.info("escalation.resolution_manual", extra={
                "escalation_id": escalation['id']
            })

        else:
            raise ValueError("Resolution must contain either 'selected_suggestion_index' or 'manual_slot'")

        # TODO: Integrate with existing appointment service to create appointment
        # For now, create a placeholder record
        # In production, this should call:
        # from app.services.unified_appointment_service import UnifiedAppointmentService
        # appointment_service = UnifiedAppointmentService(self.db)
        # appointment = await appointment_service.create_appointment(appointment_request)

        placeholder_appointment = {
            'id': str(UUID(int=0)),  # Placeholder UUID
            'escalation_id': escalation['id'],
            'clinic_id': escalation['clinic_id'],
            'patient_id': request.get('patient_id'),
            'service_id': request.get('service_id'),
            'status': 'scheduled',
            'created_by': str(resolved_by),
            'created_at': datetime.now().isoformat(),
            'note': f"Created from escalation resolution: {resolution.get('note', '')}"
        }

        logger.warning("escalation.appointment_placeholder", extra={
            "escalation_id": escalation['id'],
            "message": "TODO: Integrate with actual appointment service"
        })

        return placeholder_appointment


class NoSlotsAvailableError(Exception):
    """
    Custom exception raised when no slots are available.

    Used by scheduling service to trigger escalation creation.
    """

    def __init__(self, escalation_id: Optional[UUID] = None, message: str = "No available slots found"):
        self.escalation_id = escalation_id
        self.message = message
        super().__init__(self.message)
