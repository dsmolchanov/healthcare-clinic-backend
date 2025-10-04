"""
LangGraph Appointment Handler with Reservation Tools Integration

This module provides an enhanced appointment handler node for LangGraph orchestrator
that integrates with the new reservation management tools.
"""

import logging
from typing import Dict, Any, Optional, List
from datetime import datetime, timedelta
import json
import re

from app.services.reservation_tools import ReservationTools
from app.services.whatsapp_confirmation_service import WhatsAppConfirmationService

logger = logging.getLogger(__name__)


class LangGraphAppointmentHandler:
    """
    Enhanced appointment handler for LangGraph orchestrator.
    Integrates reservation tools for comprehensive appointment management.
    """

    def __init__(self, clinic_id: str):
        """
        Initialize the appointment handler.

        Args:
            clinic_id: ID of the clinic
        """
        self.clinic_id = clinic_id
        self.reservation_tools = None
        self.confirmation_service = None
        logger.info(f"Initialized LangGraphAppointmentHandler for clinic {clinic_id}")

    def _get_reservation_tools(self, patient_id: Optional[str] = None) -> ReservationTools:
        """
        Get or create reservation tools instance.

        Args:
            patient_id: Optional patient ID for context

        Returns:
            ReservationTools instance
        """
        if not self.reservation_tools or self.reservation_tools.patient_id != patient_id:
            self.reservation_tools = ReservationTools(self.clinic_id, patient_id)
        return self.reservation_tools

    def _get_confirmation_service(self) -> Optional[Any]:
        """
        Get or create confirmation service instance.

        Returns:
            WhatsAppConfirmationService instance or None
        """
        try:
            if not self.confirmation_service:
                self.confirmation_service = WhatsAppConfirmationService(self.clinic_id)
            return self.confirmation_service
        except Exception as e:
            logger.warning(f"Could not initialize confirmation service: {str(e)}")
            return None

    async def handle_appointment_request(
        self,
        state: Any,  # UnifiedSessionState from orchestrator
        message: str
    ) -> Dict[str, Any]:
        """
        Main handler for appointment-related requests.

        Args:
            state: Current conversation state
            message: User message

        Returns:
            Dictionary with response and state updates
        """
        try:
            # Extract intent sub-type from state
            intent_type = self._determine_appointment_intent(state, message)

            # Get patient context
            patient_id = state.patient.patient_id if state.patient else None
            patient_info = self._extract_patient_info(state)

            # Get reservation tools
            tools = self._get_reservation_tools(patient_id)

            # Route to specific handler
            if intent_type == "check_availability":
                return await self._handle_availability_check(state, message, tools)
            elif intent_type == "book_appointment":
                return await self._handle_booking(state, message, tools, patient_info)
            elif intent_type == "cancel_appointment":
                return await self._handle_cancellation(state, message, tools)
            elif intent_type == "reschedule_appointment":
                return await self._handle_rescheduling(state, message, tools)
            elif intent_type == "search_appointments":
                return await self._handle_search(state, message, tools)
            else:
                return await self._handle_general_appointment_query(state, message, tools)

        except Exception as e:
            logger.error(f"Error handling appointment request: {str(e)}")
            return {
                "response": "I apologize, but I encountered an error processing your appointment request. Please try again or contact the clinic directly.",
                "error": str(e),
                "requires_human": True
            }

    def _determine_appointment_intent(self, state: Any, message: str) -> str:
        """
        Determine the specific appointment-related intent.

        Args:
            state: Current conversation state
            message: User message

        Returns:
            Intent type string
        """
        message_lower = message.lower()

        # Check for specific keywords
        if any(word in message_lower for word in ["available", "availability", "slots", "when can"]):
            return "check_availability"
        elif any(word in message_lower for word in ["book", "schedule", "make an appointment", "reserve"]):
            return "book_appointment"
        elif any(word in message_lower for word in ["cancel", "cancellation"]):
            return "cancel_appointment"
        elif any(word in message_lower for word in ["reschedule", "change", "move"]):
            return "reschedule_appointment"
        elif any(word in message_lower for word in ["search", "find", "my appointments", "upcoming"]):
            return "search_appointments"
        else:
            return "general_inquiry"

    def _extract_patient_info(self, state: Any) -> Dict[str, Any]:
        """
        Extract patient information from state.

        Args:
            state: Current conversation state

        Returns:
            Dictionary with patient info
        """
        patient_info = {}

        if state.patient:
            patient_info = {
                "patient_id": state.patient.patient_id,
                "name": state.patient.name,
                "phone": state.patient.phone,
                "email": state.patient.email
            }

        return patient_info

    async def _handle_availability_check(
        self,
        state: Any,
        message: str,
        tools: ReservationTools
    ) -> Dict[str, Any]:
        """
        Handle availability check requests.

        Args:
            state: Current conversation state
            message: User message
            tools: Reservation tools instance

        Returns:
            Response dictionary
        """
        # Extract entities from state
        entities = state.intent.entities or {}

        # Extract service and preferences
        service_name = entities.get("service") or self._extract_service_from_message(message)
        preferred_date = entities.get("date")
        time_preference = entities.get("time_preference")
        doctor_name = entities.get("doctor")

        # Get doctor ID if doctor name provided
        doctor_id = None
        if doctor_name:
            doctor_id = await self._resolve_doctor_id(doctor_name)

        # Check availability
        result = await tools.check_availability_tool(
            service_name=service_name or "general consultation",
            preferred_date=preferred_date,
            time_preference=time_preference,
            doctor_id=doctor_id,
            flexibility_days=7
        )

        if result["success"] and result["available_slots"]:
            # Format available slots for display
            slots_text = self._format_available_slots(result["available_slots"][:5])

            response = f"I found {len(result['available_slots'])} available slots for {result['service']['name']}.\n\n"
            response += f"Here are the next available times:\n{slots_text}\n\n"

            if result.get("recommendation"):
                rec = result["recommendation"]
                response += f"I recommend: {self._format_slot(rec)}\n\n"

            response += "Would you like to book one of these slots?"

            # Store slots in state for follow-up
            state.workflow.workflow_data["available_slots"] = result["available_slots"]
            state.workflow.workflow_data["service_info"] = result["service"]
            state.workflow.pending_actions.append("confirm_slot_selection")

        else:
            response = f"I couldn't find any available slots for {service_name or 'the requested service'} "
            if preferred_date:
                response += f"on {preferred_date}. "
            response += "Would you like me to check other dates or services?"

        return {
            "response": response,
            "data": result,
            "next_action": "await_slot_selection" if result["success"] else "retry_search"
        }

    async def _handle_booking(
        self,
        state: Any,
        message: str,
        tools: ReservationTools,
        patient_info: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Handle appointment booking requests.

        Args:
            state: Current conversation state
            message: User message
            tools: Reservation tools instance
            patient_info: Patient information

        Returns:
            Response dictionary
        """
        # Check if we have slot selection from previous interaction
        selected_slot = state.workflow.workflow_data.get("selected_slot")
        service_info = state.workflow.workflow_data.get("service_info")

        if not selected_slot:
            # Try to extract slot from message
            selected_slot = self._extract_slot_selection(message, state.workflow.workflow_data.get("available_slots", []))

        if not selected_slot or not service_info:
            # Need to check availability first
            return await self._handle_availability_check(state, message, tools)

        # Ensure we have patient info
        if not patient_info.get("name") or not patient_info.get("phone"):
            return {
                "response": "I need your name and phone number to book the appointment. Please provide them.",
                "next_action": "collect_patient_info",
                "requires_input": ["name", "phone"]
            }

        # Book the appointment
        result = await tools.book_appointment_tool(
            patient_info=patient_info,
            service_id=service_info["id"],
            datetime_str=selected_slot["datetime"],
            doctor_id=selected_slot.get("doctor_id"),
            notes=f"Booked via LangGraph agent. Original request: {message}"
        )

        if result["success"]:
            # Send confirmation if WhatsApp available
            confirmation_sent = False
            if patient_info.get("phone") and self.confirmation_service:
                try:
                    confirm_result = await self.confirmation_service.send_appointment_confirmation(
                        appointment_id=result.get("appointment_id") or result.get("appointment_ids", [None])[0],
                        patient_phone=patient_info["phone"],
                        channel_preference="whatsapp"
                    )
                    confirmation_sent = confirm_result.get("success", False)
                except Exception as e:
                    logger.warning(f"Could not send WhatsApp confirmation: {str(e)}")

            response = result.get("confirmation_message", "Appointment successfully booked!")

            if confirmation_sent:
                response += "\n\nA confirmation has been sent to your WhatsApp."
            else:
                response += "\n\nPlease save your appointment details."

            # Clear workflow data
            state.workflow.workflow_data.pop("available_slots", None)
            state.workflow.workflow_data.pop("selected_slot", None)
            state.workflow.workflow_data.pop("service_info", None)
            state.workflow.pending_actions = []

        else:
            response = f"I couldn't book the appointment: {result.get('error', 'Unknown error')}. "
            response += "Would you like to try a different time slot?"

        return {
            "response": response,
            "data": result,
            "booking_complete": result["success"]
        }

    async def _handle_cancellation(
        self,
        state: Any,
        message: str,
        tools: ReservationTools
    ) -> Dict[str, Any]:
        """
        Handle appointment cancellation requests.

        Args:
            state: Current conversation state
            message: User message
            tools: Reservation tools instance

        Returns:
            Response dictionary
        """
        # Extract appointment ID or search criteria
        appointment_id = self._extract_appointment_id(message)

        if not appointment_id:
            # Search for appointments
            patient_phone = state.patient.phone if state.patient else None
            if not patient_phone:
                return {
                    "response": "I need your phone number to find your appointments. Please provide it.",
                    "next_action": "collect_phone",
                    "requires_input": ["phone"]
                }

            search_result = await tools.search_appointments_tool(
                patient_phone=patient_phone,
                status="scheduled"
            )

            if search_result["success"] and search_result["appointments"]:
                # Show appointments and ask which to cancel
                appointments_text = self._format_appointments_list(search_result["appointments"])
                return {
                    "response": f"I found these upcoming appointments:\n\n{appointments_text}\n\nWhich one would you like to cancel?",
                    "data": search_result,
                    "next_action": "select_appointment_to_cancel"
                }
            else:
                return {
                    "response": "I couldn't find any upcoming appointments for you.",
                    "data": search_result
                }

        # Cancel the appointment
        result = await tools.cancel_appointment_tool(
            appointment_id=appointment_id,
            cancellation_reason=f"Patient requested cancellation. Message: {message}",
            cancel_all_stages=True  # Cancel all stages for multi-stage appointments
        )

        if result["success"]:
            response = "Your appointment has been successfully cancelled."
            if len(result.get("cancelled_appointment_ids", [])) > 1:
                response = f"All {len(result['cancelled_appointment_ids'])} related appointments have been cancelled."
        else:
            response = f"I couldn't cancel the appointment: {result.get('error', 'Unknown error')}"

        return {
            "response": response,
            "data": result,
            "cancellation_complete": result["success"]
        }

    async def _handle_rescheduling(
        self,
        state: Any,
        message: str,
        tools: ReservationTools
    ) -> Dict[str, Any]:
        """
        Handle appointment rescheduling requests.

        Args:
            state: Current conversation state
            message: User message
            tools: Reservation tools instance

        Returns:
            Response dictionary
        """
        # Extract appointment ID and new datetime
        appointment_id = self._extract_appointment_id(message) or state.workflow.workflow_data.get("appointment_to_reschedule")
        new_datetime = self._extract_datetime(message)

        if not appointment_id:
            # Need to find the appointment first
            return await self._handle_search(state, message, tools)

        if not new_datetime:
            # Check availability for new time
            return await self._handle_availability_check(state, message, tools)

        # Reschedule the appointment
        result = await tools.reschedule_appointment_tool(
            appointment_id=appointment_id,
            new_datetime=new_datetime,
            reschedule_reason=f"Patient requested reschedule. Message: {message}",
            reschedule_all_stages=True
        )

        if result["success"]:
            response = f"Your appointment has been successfully rescheduled to {new_datetime}."
        else:
            response = f"I couldn't reschedule the appointment: {result.get('error', 'Unknown error')}"

            if result.get("suggestion"):
                response += f"\n\nThe requested time isn't available, but I found a nearby slot: {self._format_slot(result['suggestion'])}"

        return {
            "response": response,
            "data": result,
            "reschedule_complete": result["success"]
        }

    async def _handle_search(
        self,
        state: Any,
        message: str,
        tools: ReservationTools
    ) -> Dict[str, Any]:
        """
        Handle appointment search requests.

        Args:
            state: Current conversation state
            message: User message
            tools: Reservation tools instance

        Returns:
            Response dictionary
        """
        # Extract search criteria
        patient_phone = state.patient.phone if state.patient else self._extract_phone(message)
        date_range = self._extract_date_range(message)
        status = self._extract_status(message)

        if not patient_phone and not state.patient:
            return {
                "response": "I need your phone number to find your appointments. Please provide it.",
                "next_action": "collect_phone",
                "requires_input": ["phone"]
            }

        # Search appointments
        result = await tools.search_appointments_tool(
            patient_phone=patient_phone,
            date_range=date_range,
            status=status
        )

        if result["success"] and result["appointments"]:
            appointments_text = self._format_appointments_list(result["appointments"])
            response = f"I found {len(result['appointments'])} appointment(s):\n\n{appointments_text}"
        else:
            response = "I couldn't find any appointments matching your criteria."

        return {
            "response": response,
            "data": result,
            "appointments_found": len(result.get("appointments", []))
        }

    async def _handle_general_appointment_query(
        self,
        state: Any,
        message: str,
        tools: ReservationTools
    ) -> Dict[str, Any]:
        """
        Handle general appointment queries.

        Args:
            state: Current conversation state
            message: User message
            tools: Reservation tools instance

        Returns:
            Response dictionary
        """
        response = "I can help you with:\n\n"
        response += "• Checking appointment availability\n"
        response += "• Booking new appointments\n"
        response += "• Cancelling existing appointments\n"
        response += "• Rescheduling appointments\n"
        response += "• Finding your upcoming appointments\n\n"
        response += "What would you like to do?"

        return {
            "response": response,
            "next_action": "await_selection"
        }

    # Helper methods

    def _extract_service_from_message(self, message: str) -> Optional[str]:
        """Extract service name from message"""
        # Common service keywords
        services = {
            "cleaning": "teeth cleaning",
            "checkup": "dental checkup",
            "filling": "dental filling",
            "root canal": "root canal",
            "extraction": "tooth extraction",
            "crown": "dental crown",
            "whitening": "teeth whitening",
            "consultation": "general consultation"
        }

        message_lower = message.lower()
        for keyword, service_name in services.items():
            if keyword in message_lower:
                return service_name

        return None

    async def _resolve_doctor_id(self, doctor_name: str) -> Optional[str]:
        """Resolve doctor name to ID"""
        # This would query the database
        # For now, return None
        return None

    def _format_available_slots(self, slots: List[Dict[str, Any]]) -> str:
        """Format available slots for display"""
        formatted = []
        for i, slot in enumerate(slots, 1):
            formatted.append(f"{i}. {self._format_slot(slot)}")
        return "\n".join(formatted)

    def _format_slot(self, slot: Dict[str, Any]) -> str:
        """Format a single slot"""
        dt = datetime.fromisoformat(slot["datetime"])
        formatted = dt.strftime("%A, %B %d at %I:%M %p")
        if slot.get("doctor_name"):
            formatted += f" with Dr. {slot['doctor_name']}"
        return formatted

    def _format_appointments_list(self, appointments: List[Dict[str, Any]]) -> str:
        """Format appointments list for display"""
        formatted = []
        for apt in appointments:
            if isinstance(apt, dict):
                if apt.get("is_multi_stage"):
                    # Multi-stage appointment
                    formatted.append(
                        f"• {apt['service_name']} ({apt['total_stages']} sessions)\n"
                        f"  First: {self._format_datetime(apt['first_appointment_date'])}\n"
                        f"  Last: {self._format_datetime(apt['last_appointment_date'])}"
                    )
                else:
                    # Single appointment
                    formatted.append(
                        f"• {apt.get('service_name', 'Appointment')} on "
                        f"{self._format_datetime(apt.get('scheduled_at'))}"
                    )
        return "\n".join(formatted)

    def _format_datetime(self, datetime_str: str) -> str:
        """Format datetime string for display"""
        if not datetime_str:
            return "Unknown time"
        try:
            dt = datetime.fromisoformat(datetime_str)
            return dt.strftime("%B %d at %I:%M %p")
        except:
            return datetime_str

    def _extract_slot_selection(self, message: str, available_slots: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """Extract selected slot from user message"""
        # Check for slot number (1, 2, 3, etc.)
        match = re.search(r'\b(\d+)\b', message)
        if match and available_slots:
            slot_num = int(match.group(1))
            if 1 <= slot_num <= len(available_slots):
                return available_slots[slot_num - 1]

        # Check for time mentions
        # This would need more sophisticated parsing
        return None

    def _extract_appointment_id(self, message: str) -> Optional[str]:
        """Extract appointment ID from message"""
        # Look for UUID pattern
        uuid_pattern = r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'
        match = re.search(uuid_pattern, message, re.IGNORECASE)
        if match:
            return match.group(0)
        return None

    def _extract_datetime(self, message: str) -> Optional[str]:
        """Extract datetime from message"""
        # This would need sophisticated date parsing
        # For now, return None - would integrate with dateutil or similar
        return None

    def _extract_phone(self, message: str) -> Optional[str]:
        """Extract phone number from message"""
        # Look for phone pattern
        phone_pattern = r'[\+]?[(]?[0-9]{1,3}[)]?[-\s\.]?[(]?[0-9]{1,4}[)]?[-\s\.]?[0-9]{1,4}[-\s\.]?[0-9]{1,9}'
        match = re.search(phone_pattern, message)
        if match:
            return match.group(0)
        return None

    def _extract_date_range(self, message: str) -> Optional[Dict[str, str]]:
        """Extract date range from message"""
        # This would need date parsing
        # For now, check for keywords
        if "today" in message.lower():
            today = datetime.now().date()
            return {
                "start_date": today.isoformat(),
                "end_date": today.isoformat()
            }
        elif "this week" in message.lower():
            today = datetime.now().date()
            week_end = today + timedelta(days=7)
            return {
                "start_date": today.isoformat(),
                "end_date": week_end.isoformat()
            }
        return None

    def _extract_status(self, message: str) -> Optional[str]:
        """Extract appointment status from message"""
        status_keywords = {
            "upcoming": "scheduled",
            "scheduled": "scheduled",
            "confirmed": "confirmed",
            "cancelled": "cancelled",
            "completed": "completed",
            "past": "completed"
        }

        message_lower = message.lower()
        for keyword, status in status_keywords.items():
            if keyword in message_lower:
                return status

        return None


def create_appointment_handler_node(clinic_id: str):
    """
    Factory function to create an appointment handler node for LangGraph.

    Args:
        clinic_id: ID of the clinic

    Returns:
        Async function that can be used as a LangGraph node
    """
    handler = LangGraphAppointmentHandler(clinic_id)

    async def appointment_node(state):
        """LangGraph node for appointment handling"""
        try:
            # Get the last user message
            messages = state.messages if hasattr(state, 'messages') else []
            last_message = ""
            for msg in reversed(messages):
                if msg.role == "user":
                    last_message = msg.content
                    break

            # Handle the appointment request
            result = await handler.handle_appointment_request(state, last_message)

            # Update state with response
            state.workflow.workflow_data["appointment_result"] = result
            state.workflow.workflow_data["appointment_response"] = result["response"]

            # Add any pending actions
            if result.get("next_action"):
                state.workflow.pending_actions.append(result["next_action"])

            # Mark if human intervention needed
            if result.get("requires_human"):
                state.compliance.requires_human_intervention = True

            return state

        except Exception as e:
            logger.error(f"Error in appointment node: {str(e)}")
            state.workflow.workflow_data["appointment_error"] = str(e)
            state.compliance.requires_human_intervention = True
            return state

    return appointment_node