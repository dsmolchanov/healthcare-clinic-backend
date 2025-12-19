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
            patient_id=patient_id
        )

        # Default to Consultation if service_name is missing
        if 'service_name' not in args or not args['service_name']:
            args['service_name'] = 'Consultation'

        result = await reservation_tools.check_availability_tool(**args)

        if result.get('success'):
            slots = result.get('available_slots', [])
            if slots:
                result_text = f"Found {len(slots)} available slots:\n"
                for slot in slots[:5]:  # Show top 5
                    # Slots use 'datetime' not 'date' - parse it
                    slot_datetime = slot.get('datetime', '')
                    slot_date = slot_datetime.split('T')[0] if 'T' in slot_datetime else slot_datetime[:10]
                    slot_time = slot.get('start_time', slot_datetime.split('T')[1][:5] if 'T' in slot_datetime else '')
                    doctor_name = slot.get('doctor_name', 'Doctor')
                    result_text += f"- {slot_date} at {slot_time} with {doctor_name}\n"
                if result.get('recommendation'):
                    result_text += f"\nRecommendation: {result['recommendation']}"
            else:
                result_text = "No available slots found for the requested service and timeframe."
        else:
            result_text = f"Error checking availability: {result.get('error', 'Unknown error')}"

        logger.info(f"✅ check_availability tool returned: {result_text[:200]}...")
        return result_text
