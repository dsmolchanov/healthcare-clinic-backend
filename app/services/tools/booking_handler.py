from typing import Any, Dict
import logging
import uuid
from app.services.tools.base import ToolHandler
from app.services.reservation_tools import ReservationTools

logger = logging.getLogger(__name__)

class BookingHandler(ToolHandler):
    @property
    def tool_name(self) -> str:
        return "book_appointment"

    async def execute(self, args: Dict[str, Any], context: Dict[str, Any]) -> str:
        clinic_id = context.get('clinic_id')
        if not clinic_id:
            return "Error: clinic_id missing from context"

        # Generate idempotency key for WhatsApp messages
        conversation_id = context.get('conversation_id', 'unknown')
        message_id = context.get('message_id', str(uuid.uuid4()))

        # Use standardized format: channel:source:message_id
        idempotency_key = f"whatsapp:{clinic_id}:{message_id}"

        # Extract patient_id from patient_info or session
        patient_id = context.get('patient_id') or args.get('patient_info', {}).get('patient_id')

        reservation_tools = ReservationTools(
            clinic_id=clinic_id,
            patient_id=patient_id
        )

        # Pass idempotency key to booking
        args['idempotency_key'] = idempotency_key

        result = await reservation_tools.book_appointment_tool(**args)

        if result.get('success'):
            appt = result.get('appointment', {})
            confirmation = result.get('confirmation_message', 'Appointment booked successfully')
            result_text = f"✅ {confirmation}\n"
            result_text += f"Appointment ID: {result.get('appointment_id')}\n"
            if appt:
                result_text += f"Doctor: {appt.get('doctor_name', 'TBD')}\n"
                result_text += f"Date: {appt.get('date')} at {appt.get('start_time')}"
        else:
            result_text = f"❌ Booking failed: {result.get('error', 'Unknown error')}"

        logger.info(f"✅ book_appointment tool returned: {result_text[:200]}...")
        return result_text


class CancellationHandler(ToolHandler):
    @property
    def tool_name(self) -> str:
        return "cancel_appointment"

    async def execute(self, args: Dict[str, Any], context: Dict[str, Any]) -> str:
        clinic_id = context.get('clinic_id')
        if not clinic_id:
            return "Error: clinic_id missing from context"

        reservation_tools = ReservationTools(clinic_id=clinic_id)
        result = await reservation_tools.cancel_appointment_tool(**args)

        if result.get('success'):
            result_text = f"✅ Appointment cancelled successfully"
            if result.get('cancelled_count', 0) > 1:
                result_text += f" ({result['cancelled_count']} appointments cancelled)"
        else:
            result_text = f"❌ Cancellation failed: {result.get('error', 'Unknown error')}"

        logger.info(f"✅ cancel_appointment tool returned: {result_text}")
        return result_text


class RescheduleHandler(ToolHandler):
    @property
    def tool_name(self) -> str:
        return "reschedule_appointment"

    async def execute(self, args: Dict[str, Any], context: Dict[str, Any]) -> str:
        clinic_id = context.get('clinic_id')
        if not clinic_id:
            return "Error: clinic_id missing from context"

        reservation_tools = ReservationTools(clinic_id=clinic_id)
        result = await reservation_tools.reschedule_appointment_tool(**args)

        if result.get('success'):
            result_text = f"✅ Appointment rescheduled successfully to {args.get('new_datetime')}"
            if result.get('rescheduled_count', 0) > 1:
                result_text += f" ({result['rescheduled_count']} appointments rescheduled)"
        else:
            result_text = f"❌ Rescheduling failed: {result.get('error', 'Unknown error')}"

        logger.info(f"✅ reschedule_appointment tool returned: {result_text}")
        return result_text
