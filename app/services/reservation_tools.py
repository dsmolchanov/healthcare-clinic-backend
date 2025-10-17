"""
Reservation Management Tools for LangGraph Agents

This module provides reservation tools for healthcare appointment management,
integrating with existing calendar systems and appointment services.
"""

import logging
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime, timedelta
import asyncio
import json
from enum import Enum

from app.services.appointment_booking_service import AppointmentBookingService
from app.services.unified_appointment_service import UnifiedAppointmentService
from app.services.external_calendar_service import ExternalCalendarService
from app.services.intelligent_scheduler import IntelligentScheduler, SchedulingStrategy
from app.services.realtime_conflict_detector import RealtimeConflictDetector
from app.services.redis_session_manager import RedisSessionManager
from app.database import create_supabase_client

logger = logging.getLogger(__name__)

class ReservationStatus(Enum):
    """Status of a reservation"""
    AVAILABLE = "available"
    HELD = "held"
    BOOKED = "booked"
    CANCELLED = "cancelled"
    EXPIRED = "expired"

class ReservationTools:
    """
    Reservation management tools for LangGraph agents.
    Provides methods for checking availability, booking appointments,
    managing holds, and handling cancellations.
    """

    def __init__(self, clinic_id: str, patient_id: Optional[str] = None):
        """
        Initialize reservation tools with clinic context.

        Args:
            clinic_id: ID of the clinic
            patient_id: Optional patient ID for personalized operations
        """
        self.clinic_id = clinic_id
        self.patient_id = patient_id
        self.supabase = create_supabase_client()

        # Initialize services
        self.booking_service = AppointmentBookingService(self.supabase)
        self.unified_service = UnifiedAppointmentService(clinic_id, self.supabase)
        self.calendar_service = ExternalCalendarService(self.supabase)
        self.scheduler = IntelligentScheduler(self.supabase)
        self.conflict_detector = RealtimeConflictDetector()
        self.session_manager = RedisSessionManager()

        logger.info(f"Initialized ReservationTools for clinic {clinic_id}")

    async def check_availability_tool(
        self,
        service_name: str,
        preferred_date: Optional[str] = None,
        time_preference: Optional[str] = None,
        doctor_id: Optional[str] = None,
        flexibility_days: int = 7
    ) -> Dict[str, Any]:
        """
        Check availability for a service with intelligent slot finding.

        Args:
            service_name: Name or type of service
            preferred_date: Preferred date (YYYY-MM-DD format)
            time_preference: Time preference (morning/afternoon/evening)
            doctor_id: Specific doctor ID if requested
            flexibility_days: Number of days to search for availability

        Returns:
            Dictionary with available slots and recommendations
        """
        try:
            # Parse preferred date
            if preferred_date:
                try:
                    start_date = datetime.strptime(preferred_date, "%Y-%m-%d")
                except ValueError:
                    start_date = datetime.now()
            else:
                start_date = datetime.now()

            end_date = start_date + timedelta(days=flexibility_days)

            # Get service details
            service = await self._get_service_by_name(service_name)
            if not service:
                return {
                    "success": False,
                    "error": f"Service '{service_name}' not found",
                    "available_slots": []
                }

            # Check if multi-stage service
            stage_config = service.get('stage_config', {})
            is_multi_stage = stage_config.get('total_stages', 1) > 1

            # Use intelligent scheduler to find slots
            strategy = SchedulingStrategy.AI_OPTIMIZED
            slots = await self.scheduler.find_available_slots(
                service_id=service['id'],
                start_date=start_date,
                end_date=end_date,
                doctor_id=doctor_id,
                duration_minutes=service.get('duration_minutes', 30),
                strategy=strategy
            )

            # Filter by time preference if provided
            if time_preference:
                slots = self._filter_by_time_preference(slots, time_preference)

            # Check conflicts with external calendars
            verified_slots = []
            for slot in slots[:10]:  # Limit to top 10 slots
                # Use ask-hold-reserve pattern to verify availability
                is_available = await self.calendar_service.ask_availability(
                    datetime_str=slot['datetime'],
                    duration_minutes=service.get('duration_minutes', 30),
                    doctor_id=slot.get('doctor_id')
                )

                if is_available:
                    slot['verified'] = True
                    slot['conflicts'] = []
                    verified_slots.append(slot)

            # Format response
            return {
                "success": True,
                "service": {
                    "id": service['id'],
                    "name": service['name'],
                    "duration_minutes": service.get('duration_minutes', 30),
                    "base_price": service.get('base_price'),
                    "is_multi_stage": is_multi_stage,
                    "stage_config": stage_config if is_multi_stage else None
                },
                "available_slots": verified_slots,
                "total_slots_found": len(slots),
                "search_parameters": {
                    "start_date": start_date.isoformat(),
                    "end_date": end_date.isoformat(),
                    "time_preference": time_preference,
                    "doctor_id": doctor_id
                },
                "recommendation": verified_slots[0] if verified_slots else None
            }

        except Exception as e:
            logger.error(f"Error checking availability: {str(e)}")
            return {
                "success": False,
                "error": f"Failed to check availability: {str(e)}",
                "available_slots": []
            }

    async def book_appointment_tool(
        self,
        patient_info: Dict[str, Any],
        service_id: str,
        datetime_str: str,
        doctor_id: Optional[str] = None,
        notes: Optional[str] = None,
        hold_id: Optional[str] = None,
        idempotency_key: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Book an appointment with automatic hold management.

        Args:
            patient_info: Patient information (name, phone, email)
            service_id: ID of the service
            datetime_str: Appointment datetime in ISO format
            doctor_id: Optional doctor ID
            notes: Optional notes for the appointment
            hold_id: Optional hold ID if slot was previously held
            idempotency_key: Optional key for idempotent booking

        Returns:
            Dictionary with booking confirmation details
        """
        try:
            # Parse datetime
            appointment_datetime = datetime.fromisoformat(datetime_str)

            # Get service details from healthcare schema
            service_result = self.supabase.schema('healthcare').table('services').select('*').eq('id', service_id).execute()
            if not service_result.data:
                return {
                    "success": False,
                    "error": "Service not found"
                }
            service = service_result.data[0]

            # Check if multi-stage service
            stage_config = service.get('stage_config', {})
            is_multi_stage = stage_config.get('total_stages', 1) > 1

            # If no hold exists, create one first
            if not hold_id:
                hold_result = await self.create_appointment_hold_tool(
                    slot_datetime=datetime_str,
                    duration_minutes=service['duration_minutes'],
                    service_id=service_id,
                    doctor_id=doctor_id
                )

                if not hold_result['success']:
                    return {
                        "success": False,
                        "error": "Failed to secure appointment slot",
                        "details": hold_result.get('error')
                    }

                hold_id = hold_result['hold_id']

            # Prepare appointment data
            appointment_data = {
                "clinic_id": self.clinic_id,
                "patient_name": patient_info.get('name'),
                "patient_phone": patient_info.get('phone'),
                "patient_email": patient_info.get('email'),
                "patient_id": self.patient_id or patient_info.get('patient_id'),
                "service_id": service_id,
                "service_name": service['name'],
                "doctor_id": doctor_id,
                "scheduled_at": appointment_datetime.isoformat(),
                "duration_minutes": service['duration_minutes'],
                "status": "scheduled",
                "notes": notes,
                "booking_channel": "langgraph_agent",
                "hold_id": hold_id,
                "idempotency_key": idempotency_key
            }

            # Handle multi-stage booking
            if is_multi_stage:
                appointments = await self._book_multi_stage_appointments(
                    appointment_data,
                    service,
                    stage_config
                )

                if not appointments:
                    # Release hold on failure
                    await self.release_appointment_hold_tool(hold_id, "Multi-stage booking failed")
                    return {
                        "success": False,
                        "error": "Failed to book all stages of the appointment"
                    }

                # Confirm the hold for all stages
                await self.confirm_appointment_hold_tool(hold_id, patient_info)

                return {
                    "success": True,
                    "appointment_ids": [apt['id'] for apt in appointments],
                    "appointments": appointments,
                    "is_multi_stage": True,
                    "total_stages": stage_config['total_stages'],
                    "confirmation_message": self._format_multi_stage_confirmation(appointments, service)
                }
            else:
                # Single appointment booking
                # Use the booking service to create appointment
                result = await self.booking_service.book_appointment(
                    patient_phone=appointment_data['patient_phone'],
                    clinic_id=self.clinic_id,
                    appointment_details={
                        'doctor_id': appointment_data['doctor_id'],
                        'service_id': appointment_data['service_id'],
                        'date': appointment_datetime.date().isoformat(),
                        'time': appointment_datetime.time().isoformat(),
                        'duration_minutes': appointment_data['duration_minutes'],
                        'type': appointment_data.get('appointment_type', 'general'),
                        'reason': appointment_data.get('notes'),
                        'first_name': appointment_data.get('patient_name', '').split()[0] if appointment_data.get('patient_name') else 'Pending',
                        'last_name': ' '.join(appointment_data.get('patient_name', '').split()[1:]) if appointment_data.get('patient_name') and len(appointment_data.get('patient_name', '').split()) > 1 else 'Registration',
                        'email': appointment_data.get('patient_email')
                    },
                    idempotency_key=appointment_data.get('idempotency_key')
                )

                if result and 'error' not in result:
                    # Confirm the hold
                    await self.confirm_appointment_hold_tool(hold_id, patient_info)

                    # Sync with external calendars
                    await self.calendar_service.reserve_slot(
                        appointment_id=result['id'],
                        datetime_str=datetime_str,
                        duration_minutes=service['duration_minutes'],
                        doctor_id=doctor_id,
                        patient_info=patient_info
                    )

                    return {
                        "success": True,
                        "appointment_id": result['id'],
                        "appointment": result,
                        "confirmation_message": self._format_confirmation_message(result, service)
                    }
                else:
                    # Release hold on failure
                    await self.release_appointment_hold_tool(hold_id, "Booking failed")
                    return {
                        "success": False,
                        "error": result.get('error', 'Failed to book appointment')
                    }

        except Exception as e:
            logger.error(f"Error booking appointment: {str(e)}")
            if hold_id:
                await self.release_appointment_hold_tool(hold_id, f"Error: {str(e)}")
            return {
                "success": False,
                "error": f"Failed to book appointment: {str(e)}"
            }

    async def cancel_appointment_tool(
        self,
        appointment_id: str,
        cancellation_reason: str,
        cancel_all_stages: bool = False
    ) -> Dict[str, Any]:
        """
        Cancel an appointment with proper cleanup.

        Args:
            appointment_id: ID of the appointment to cancel
            cancellation_reason: Reason for cancellation
            cancel_all_stages: For multi-stage appointments, cancel all stages

        Returns:
            Dictionary with cancellation confirmation
        """
        try:
            # Get appointment details
            appointment = await self._get_appointment_by_id(appointment_id)
            if not appointment:
                return {
                    "success": False,
                    "error": "Appointment not found"
                }

            # Check if part of multi-stage appointment
            parent_id = appointment.get('parent_appointment_id')
            if parent_id or cancel_all_stages:
                # Get all related appointments
                appointments_to_cancel = await self._get_related_appointments(
                    parent_id or appointment_id
                )
            else:
                appointments_to_cancel = [appointment]

            cancelled_ids = []
            for apt in appointments_to_cancel:
                # Update appointment status
                update_result = self.supabase.table('healthcare.appointments').update({
                    'status': 'cancelled',
                    'cancellation_reason': cancellation_reason,
                    'cancelled_at': datetime.now().isoformat(),
                    'cancelled_by': 'langgraph_agent'
                }).eq('id', apt['id']).execute()

                if update_result.data:
                    cancelled_ids.append(apt['id'])

                    # Cancel in external calendars
                    await self.calendar_service.cancel_reservation(
                        appointment_id=apt['id'],
                        doctor_id=apt.get('doctor_id')
                    )

            return {
                "success": True,
                "cancelled_appointment_ids": cancelled_ids,
                "cancellation_reason": cancellation_reason,
                "message": f"Successfully cancelled {len(cancelled_ids)} appointment(s)"
            }

        except Exception as e:
            logger.error(f"Error cancelling appointment: {str(e)}")
            return {
                "success": False,
                "error": f"Failed to cancel appointment: {str(e)}"
            }

    async def reschedule_appointment_tool(
        self,
        appointment_id: str,
        new_datetime: str,
        reschedule_reason: Optional[str] = None,
        reschedule_all_stages: bool = False
    ) -> Dict[str, Any]:
        """
        Reschedule an appointment to a new time.

        Args:
            appointment_id: ID of the appointment to reschedule
            new_datetime: New datetime in ISO format
            reschedule_reason: Optional reason for rescheduling
            reschedule_all_stages: For multi-stage appointments, reschedule all stages

        Returns:
            Dictionary with rescheduling confirmation
        """
        try:
            # Get appointment details
            appointment = await self._get_appointment_by_id(appointment_id)
            if not appointment:
                return {
                    "success": False,
                    "error": "Appointment not found"
                }

            # Parse new datetime
            new_dt = datetime.fromisoformat(new_datetime)

            # Check availability for new slot
            availability = await self.check_availability_tool(
                service_name=appointment['service_name'],
                preferred_date=new_dt.date().isoformat(),
                doctor_id=appointment.get('doctor_id')
            )

            if not availability['success'] or not availability['available_slots']:
                return {
                    "success": False,
                    "error": "Requested time slot is not available"
                }

            # Check if the exact requested time is available
            slot_available = any(
                abs((datetime.fromisoformat(slot['datetime']) - new_dt).total_seconds()) < 900
                for slot in availability['available_slots']
            )

            if not slot_available:
                # Find nearest available slot
                nearest_slot = min(
                    availability['available_slots'],
                    key=lambda s: abs((datetime.fromisoformat(s['datetime']) - new_dt).total_seconds())
                )
                return {
                    "success": False,
                    "error": "Exact time not available",
                    "suggestion": nearest_slot,
                    "available_slots": availability['available_slots'][:5]
                }

            # Create hold for new slot
            hold_result = await self.create_appointment_hold_tool(
                slot_datetime=new_datetime,
                duration_minutes=appointment['duration_minutes'],
                service_id=appointment['service_id'],
                doctor_id=appointment.get('doctor_id')
            )

            if not hold_result['success']:
                return {
                    "success": False,
                    "error": "Failed to secure new appointment slot"
                }

            # Handle multi-stage rescheduling
            if reschedule_all_stages and appointment.get('parent_appointment_id'):
                appointments = await self._get_related_appointments(
                    appointment['parent_appointment_id'] or appointment_id
                )

                rescheduled_ids = []
                base_dt = new_dt

                for i, apt in enumerate(appointments):
                    if i > 0:
                        # Calculate new datetime for subsequent stages
                        days_between = apt.get('stage_config', {}).get('days_between_stages', 7)
                        stage_dt = base_dt + timedelta(days=days_between * i)
                    else:
                        stage_dt = base_dt

                    # Update appointment
                    update_result = self.supabase.table('healthcare.appointments').update({
                        'scheduled_at': stage_dt.isoformat(),
                        'status': 'rescheduled',
                        'previous_scheduled_at': apt['scheduled_at'],
                        'reschedule_reason': reschedule_reason,
                        'rescheduled_at': datetime.now().isoformat(),
                        'rescheduled_by': 'langgraph_agent'
                    }).eq('id', apt['id']).execute()

                    if update_result.data:
                        rescheduled_ids.append(apt['id'])

                # Confirm hold
                await self.confirm_appointment_hold_tool(
                    hold_result['hold_id'],
                    {"appointment_ids": rescheduled_ids}
                )

                return {
                    "success": True,
                    "rescheduled_appointment_ids": rescheduled_ids,
                    "new_datetime": new_datetime,
                    "message": f"Successfully rescheduled {len(rescheduled_ids)} appointment(s)"
                }
            else:
                # Single appointment rescheduling
                update_result = self.supabase.table('healthcare.appointments').update({
                    'scheduled_at': new_dt.isoformat(),
                    'status': 'rescheduled',
                    'previous_scheduled_at': appointment['scheduled_at'],
                    'reschedule_reason': reschedule_reason,
                    'rescheduled_at': datetime.now().isoformat(),
                    'rescheduled_by': 'langgraph_agent'
                }).eq('id', appointment_id).execute()

                if update_result.data:
                    # Confirm hold
                    await self.confirm_appointment_hold_tool(
                        hold_result['hold_id'],
                        {"appointment_id": appointment_id}
                    )

                    # Update external calendars
                    await self.calendar_service.reschedule_reservation(
                        appointment_id=appointment_id,
                        old_datetime=appointment['scheduled_at'],
                        new_datetime=new_datetime,
                        doctor_id=appointment.get('doctor_id')
                    )

                    return {
                        "success": True,
                        "appointment_id": appointment_id,
                        "old_datetime": appointment['scheduled_at'],
                        "new_datetime": new_datetime,
                        "message": "Appointment successfully rescheduled"
                    }
                else:
                    await self.release_appointment_hold_tool(
                        hold_result['hold_id'],
                        "Rescheduling failed"
                    )
                    return {
                        "success": False,
                        "error": "Failed to reschedule appointment"
                    }

        except Exception as e:
            logger.error(f"Error rescheduling appointment: {str(e)}")
            return {
                "success": False,
                "error": f"Failed to reschedule appointment: {str(e)}"
            }

    async def search_appointments_tool(
        self,
        patient_phone: Optional[str] = None,
        date_range: Optional[Dict[str, str]] = None,
        status: Optional[str] = None,
        doctor_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Search for appointments based on various criteria.

        Args:
            patient_phone: Patient phone number
            date_range: Dictionary with 'start_date' and 'end_date'
            status: Appointment status filter
            doctor_id: Doctor ID filter

        Returns:
            Dictionary with found appointments
        """
        try:
            query = self.supabase.table('healthcare.appointments').select('*')

            # Apply filters
            if patient_phone:
                query = query.eq('patient_phone', patient_phone)
            elif self.patient_id:
                query = query.eq('patient_id', self.patient_id)

            if self.clinic_id:
                query = query.eq('clinic_id', self.clinic_id)

            if status:
                query = query.eq('status', status)

            if doctor_id:
                query = query.eq('doctor_id', doctor_id)

            if date_range:
                if date_range.get('start_date'):
                    query = query.gte('scheduled_at', date_range['start_date'])
                if date_range.get('end_date'):
                    query = query.lte('scheduled_at', date_range['end_date'])

            # Execute query
            result = query.order('scheduled_at', desc=False).execute()

            appointments = result.data if result.data else []

            # Group multi-stage appointments
            grouped = self._group_multi_stage_appointments(appointments)

            return {
                "success": True,
                "appointments": grouped,
                "total_count": len(grouped),
                "search_criteria": {
                    "patient_phone": patient_phone,
                    "date_range": date_range,
                    "status": status,
                    "doctor_id": doctor_id
                }
            }

        except Exception as e:
            logger.error(f"Error searching appointments: {str(e)}")
            return {
                "success": False,
                "error": f"Failed to search appointments: {str(e)}",
                "appointments": []
            }

    # Hold Management Tools

    async def create_appointment_hold_tool(
        self,
        slot_datetime: str,
        duration_minutes: int,
        service_id: Optional[str] = None,
        doctor_id: Optional[str] = None,
        hold_duration_minutes: int = 15
    ) -> Dict[str, Any]:
        """
        Create a temporary hold on an appointment slot.

        Args:
            slot_datetime: Datetime of the slot to hold
            duration_minutes: Duration of the appointment
            service_id: Optional service ID
            doctor_id: Optional doctor ID
            hold_duration_minutes: How long to hold the slot (default 15 minutes)

        Returns:
            Dictionary with hold details
        """
        try:
            slot_dt = datetime.fromisoformat(slot_datetime)
            expire_at = datetime.now() + timedelta(minutes=hold_duration_minutes)

            # Create hold in database
            hold_data = {
                "clinic_id": self.clinic_id,
                "slot_datetime": slot_dt.isoformat(),
                "duration_minutes": duration_minutes,
                "service_id": service_id,
                "doctor_id": doctor_id,
                "status": "active",
                "created_at": datetime.now().isoformat(),
                "expire_at": expire_at.isoformat(),
                "created_by": "langgraph_agent"
            }

            result = self.supabase.table('healthcare.appointment_holds').insert(hold_data).execute()

            if result.data:
                hold = result.data[0]

                # Also create hold in external calendars
                await self.calendar_service.hold_slot(
                    datetime_str=slot_datetime,
                    duration_minutes=duration_minutes,
                    doctor_id=doctor_id,
                    hold_id=hold['id']
                )

                return {
                    "success": True,
                    "hold_id": hold['id'],
                    "slot_datetime": slot_datetime,
                    "expire_at": expire_at.isoformat(),
                    "duration_minutes": hold_duration_minutes
                }
            else:
                return {
                    "success": False,
                    "error": "Failed to create appointment hold"
                }

        except Exception as e:
            logger.error(f"Error creating appointment hold: {str(e)}")
            return {
                "success": False,
                "error": f"Failed to create hold: {str(e)}"
            }

    async def confirm_appointment_hold_tool(
        self,
        hold_id: str,
        confirmation_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Confirm a held appointment slot.

        Args:
            hold_id: ID of the hold to confirm
            confirmation_data: Additional confirmation data

        Returns:
            Dictionary with confirmation status
        """
        try:
            # Update hold status
            update_result = self.supabase.table('healthcare.appointment_holds').update({
                "status": "confirmed",
                "confirmed_at": datetime.now().isoformat(),
                "confirmation_data": json.dumps(confirmation_data)
            }).eq('id', hold_id).execute()

            if update_result.data:
                return {
                    "success": True,
                    "hold_id": hold_id,
                    "status": "confirmed",
                    "message": "Appointment hold confirmed successfully"
                }
            else:
                return {
                    "success": False,
                    "error": "Failed to confirm appointment hold"
                }

        except Exception as e:
            logger.error(f"Error confirming appointment hold: {str(e)}")
            return {
                "success": False,
                "error": f"Failed to confirm hold: {str(e)}"
            }

    async def release_appointment_hold_tool(
        self,
        hold_id: str,
        reason: str
    ) -> Dict[str, Any]:
        """
        Release a held appointment slot.

        Args:
            hold_id: ID of the hold to release
            reason: Reason for releasing the hold

        Returns:
            Dictionary with release confirmation
        """
        try:
            # Get hold details first
            hold_result = self.supabase.table('healthcare.appointment_holds').select('*').eq('id', hold_id).execute()

            if not hold_result.data:
                return {
                    "success": False,
                    "error": "Hold not found"
                }

            hold = hold_result.data[0]

            # Update hold status
            update_result = self.supabase.table('healthcare.appointment_holds').update({
                "status": "released",
                "released_at": datetime.now().isoformat(),
                "release_reason": reason
            }).eq('id', hold_id).execute()

            if update_result.data:
                # Release in external calendars
                await self.calendar_service.release_hold(
                    hold_id=hold_id,
                    doctor_id=hold.get('doctor_id')
                )

                return {
                    "success": True,
                    "hold_id": hold_id,
                    "status": "released",
                    "reason": reason,
                    "message": "Appointment hold released successfully"
                }
            else:
                return {
                    "success": False,
                    "error": "Failed to release appointment hold"
                }

        except Exception as e:
            logger.error(f"Error releasing appointment hold: {str(e)}")
            return {
                "success": False,
                "error": f"Failed to release hold: {str(e)}"
            }

    # Helper methods

    async def _get_service_by_name(
        self,
        service_name: str,
        language: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """Get service details by name using hybrid search"""
        try:
            # Initialize hybrid search service (lazy init)
            if not hasattr(self, 'hybrid_search'):
                from app.config import get_redis_client
                from app.services.hybrid_search_service import HybridSearchService, EntityType
                redis = get_redis_client()
                self.hybrid_search = HybridSearchService(self.clinic_id, redis, self.supabase)

            # Search using hybrid service
            search_result = await self.hybrid_search.search(
                query=service_name,
                entity_type=EntityType.SERVICE,
                language=language,
                limit=1
            )

            if search_result['success'] and search_result['results']:
                logger.info(
                    f"✅ Service found via {search_result['search_metadata']['search_stage']}: "
                    f"{search_result['results'][0]['name']}"
                )
                return search_result['results'][0]

            logger.error(f"❌ Service '{service_name}' not found via hybrid search")
            return None

        except Exception as e:
            logger.error(f"Error getting service: {str(e)}")
            return None

    async def _get_appointment_by_id(self, appointment_id: str) -> Optional[Dict[str, Any]]:
        """Get appointment details by ID"""
        try:
            result = self.supabase.table('healthcare.appointments').select('*').eq('id', appointment_id).execute()
            return result.data[0] if result.data else None
        except Exception as e:
            logger.error(f"Error getting appointment: {str(e)}")
            return None

    async def _get_related_appointments(self, parent_id: str) -> List[Dict[str, Any]]:
        """Get all appointments related to a parent appointment"""
        try:
            result = self.supabase.table('healthcare.appointments').select('*').or_(
                f'id.eq.{parent_id},parent_appointment_id.eq.{parent_id}'
            ).order('stage_number', desc=False).execute()
            return result.data if result.data else []
        except Exception as e:
            logger.error(f"Error getting related appointments: {str(e)}")
            return []

    def _filter_by_time_preference(
        self,
        slots: List[Dict[str, Any]],
        preference: str
    ) -> List[Dict[str, Any]]:
        """Filter slots by time preference"""
        filtered = []
        for slot in slots:
            dt = datetime.fromisoformat(slot['datetime'])
            hour = dt.hour

            if preference == 'morning' and 6 <= hour < 12:
                filtered.append(slot)
            elif preference == 'afternoon' and 12 <= hour < 17:
                filtered.append(slot)
            elif preference == 'evening' and 17 <= hour < 21:
                filtered.append(slot)

        return filtered

    async def _book_multi_stage_appointments(
        self,
        base_appointment_data: Dict[str, Any],
        service: Dict[str, Any],
        stage_config: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """Book multiple appointments for multi-stage services"""
        appointments = []
        base_datetime = datetime.fromisoformat(base_appointment_data['scheduled_at'])
        total_stages = stage_config.get('total_stages', 1)
        days_between = stage_config.get('days_between_stages', 7)

        try:
            # Book first appointment as parent
            first_appointment = dict(base_appointment_data)
            first_appointment['stage_number'] = 1
            first_appointment['total_stages'] = total_stages
            first_appointment['stage_config'] = json.dumps(stage_config)

            result = self.supabase.table('healthcare.appointments').insert(first_appointment).execute()
            if not result.data:
                return []

            parent_appointment = result.data[0]
            appointments.append(parent_appointment)

            # Book subsequent stages
            for stage in range(2, total_stages + 1):
                stage_datetime = base_datetime + timedelta(days=days_between * (stage - 1))

                stage_appointment = dict(base_appointment_data)
                stage_appointment['scheduled_at'] = stage_datetime.isoformat()
                stage_appointment['stage_number'] = stage
                stage_appointment['total_stages'] = total_stages
                stage_appointment['parent_appointment_id'] = parent_appointment['id']
                stage_appointment['stage_config'] = json.dumps(stage_config)

                result = self.supabase.table('healthcare.appointments').insert(stage_appointment).execute()
                if result.data:
                    appointments.append(result.data[0])
                else:
                    # Rollback on failure
                    for apt in appointments:
                        self.supabase.table('healthcare.appointments').delete().eq('id', apt['id']).execute()
                    return []

            return appointments

        except Exception as e:
            logger.error(f"Error booking multi-stage appointments: {str(e)}")
            return []

    def _group_multi_stage_appointments(
        self,
        appointments: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Group multi-stage appointments together"""
        grouped = []
        seen_parents = set()

        for apt in appointments:
            parent_id = apt.get('parent_appointment_id')

            if parent_id and parent_id not in seen_parents:
                # This is a child appointment, skip if parent already processed
                continue
            elif not parent_id and apt.get('total_stages', 1) > 1:
                # This is a parent appointment with stages
                seen_parents.add(apt['id'])

                # Find all related stages
                stages = [apt]
                for other in appointments:
                    if other.get('parent_appointment_id') == apt['id']:
                        stages.append(other)

                # Sort by stage number
                stages.sort(key=lambda x: x.get('stage_number', 1))

                grouped.append({
                    "id": apt['id'],
                    "is_multi_stage": True,
                    "stages": stages,
                    "total_stages": apt['total_stages'],
                    "service_name": apt['service_name'],
                    "patient_name": apt['patient_name'],
                    "first_appointment_date": stages[0]['scheduled_at'],
                    "last_appointment_date": stages[-1]['scheduled_at']
                })
            else:
                # Single appointment
                grouped.append(apt)

        return grouped

    def _format_confirmation_message(
        self,
        appointment: Dict[str, Any],
        service: Dict[str, Any]
    ) -> str:
        """Format appointment confirmation message"""
        dt = datetime.fromisoformat(appointment['scheduled_at'])
        return (
            f"Appointment confirmed!\n"
            f"Service: {service['name']}\n"
            f"Date: {dt.strftime('%B %d, %Y')}\n"
            f"Time: {dt.strftime('%I:%M %p')}\n"
            f"Duration: {service.get('duration_minutes', 30)} minutes\n"
            f"Appointment ID: {appointment['id'][:8]}..."
        )

    def _format_multi_stage_confirmation(
        self,
        appointments: List[Dict[str, Any]],
        service: Dict[str, Any]
    ) -> str:
        """Format multi-stage appointment confirmation message"""
        messages = [f"Multi-stage appointment confirmed for {service['name']}!\n"]

        for apt in appointments:
            dt = datetime.fromisoformat(apt['scheduled_at'])
            messages.append(
                f"Stage {apt.get('stage_number', 1)}: "
                f"{dt.strftime('%B %d, %Y at %I:%M %p')}"
            )

        messages.append(f"\nTotal stages: {len(appointments)}")
        messages.append(f"Main appointment ID: {appointments[0]['id'][:8]}...")

        return "\n".join(messages)