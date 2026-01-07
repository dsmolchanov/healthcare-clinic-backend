from typing import Any, Dict
import logging
from app.services.tools.base import ToolHandler
from app.services.reservation_tools import ReservationTools

logger = logging.getLogger(__name__)

class AvailabilityHandler(ToolHandler):
    @property
    def tool_name(self) -> str:
        return "check_availability"

    async def execute(self, args: Dict[str, Any], context: Dict[str, Any]) -> str:
        clinic_id = context.get('clinic_id')
        session_history = context.get('session_history', [])
        business_hours = context.get('business_hours', {})  # From clinic_profile warmup
        clinic_timezone = context.get('clinic_timezone')  # From clinic_profile warmup

        if not clinic_id:
            return "Error: clinic_id missing from context"

        # Extract patient_id from session if available
        patient_id = None
        if session_history and len(session_history) > 0:
            for msg in session_history:
                if msg.get('metadata', {}).get('patient_id'):
                    patient_id = msg['metadata']['patient_id']
                    break

        reservation_tools = ReservationTools(
            clinic_id=clinic_id,
            patient_id=patient_id,
            business_hours=business_hours,  # Pass pre-loaded hours, no extra DB fetch
            clinic_timezone=clinic_timezone  # Pass pre-loaded timezone, no extra DB fetch
        )

        # Default to Consultation if service_name is missing
        if 'service_name' not in args or not args['service_name']:
            args['service_name'] = 'Consultation'

        result = await reservation_tools.check_availability_tool(**args)

        if result.get("status") == "needs_clarification" or result.get("requires_clarification"):
            result_text = result.get("message") or "Could you clarify the date you prefer?"
        elif result.get('success'):
            slots = result.get('available_slots', [])
            if slots:
                result_text = self._format_clustered_slots(slots, result)
            else:
                result_text = "NO_SLOTS_AVAILABLE"
        else:
            result_text = f"Error checking availability: {result.get('error', 'Unknown error')}"

        logger.info(f"✅ check_availability tool returned: {result_text[:200]}...")
        return result_text

    def _format_clustered_slots(self, slots: list, result: dict) -> str:
        """
        Return top slots with doctor info - let the LLM format it naturally in user's language.
        Includes doctor_name and doctor_id so LLM can pass correct ID to book_appointment.
        """
        from datetime import datetime, date as date_type

        if not slots:
            return "NO_SLOTS"

        # Return up to 3 slots to give user options
        formatted_slots = []
        for slot in slots[:3]:
            slot_datetime = slot.get('datetime', '')
            doctor_name = slot.get('doctor_name', 'Available')
            doctor_id = slot.get('doctor_id', '')

            try:
                dt = datetime.fromisoformat(slot_datetime.replace('Z', '+00:00'))
                date_str = dt.strftime('%Y-%m-%d')
                time_str = dt.strftime('%H:%M')
                weekday = dt.strftime('%A')

                # Calculate relative day
                slot_date = dt.date()
                today = date_type.today()
                days_diff = (slot_date - today).days

                if days_diff == 0:
                    relative = "today"
                elif days_diff == 1:
                    relative = "tomorrow"
                else:
                    relative = weekday

                # Include doctor info for booking
                # Format: "SLOT: tomorrow 2025-11-27 09:00 with Dr. Smith (doc-1)"
                slot_str = f"SLOT: {relative} {date_str} {time_str} with {doctor_name}"
                if doctor_id:
                    slot_str += f" (doctor_id: {doctor_id})"
                formatted_slots.append(slot_str)

            except (ValueError, AttributeError):
                continue

        if not formatted_slots:
            return "NO_SLOTS"

        return "\n".join(formatted_slots)
