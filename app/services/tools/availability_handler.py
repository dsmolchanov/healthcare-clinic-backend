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
                # Cluster slots by time to avoid listing same time multiple times
                result_text = self._format_clustered_slots(slots, result)
            else:
                result_text = "No available slots found for the requested service and timeframe."
        else:
            result_text = f"Error checking availability: {result.get('error', 'Unknown error')}"

        logger.info(f"✅ check_availability tool returned: {result_text[:200]}...")
        return result_text

    def _format_clustered_slots(self, slots: list, result: dict) -> str:
        """
        Format slots for natural conversation - focus on ONE recommendation.

        Returns a DIRECT response the LLM should echo, not data to process.
        """
        from datetime import datetime, date as date_type
        from collections import OrderedDict

        if not slots:
            return "SAY_TO_USER: К сожалению, на это время нет свободных слотов. Хотите посмотреть другой день?"

        # Cluster slots by (date, time)
        time_clusters = OrderedDict()

        for slot in slots:
            slot_datetime = slot.get('datetime', '')
            if not slot_datetime:
                continue

            try:
                dt = datetime.fromisoformat(slot_datetime.replace('Z', '+00:00'))
                date_str = dt.strftime('%Y-%m-%d')
                time_str = dt.strftime('%H:%M')
                cluster_key = (date_str, time_str)
            except (ValueError, AttributeError):
                continue

            if cluster_key not in time_clusters:
                time_clusters[cluster_key] = {
                    'date': date_str,
                    'time': time_str,
                    'datetime': slot_datetime,
                    'doctors': [],
                    'doctor_ids': [],
                    'weekday': dt.strftime('%A')
                }

            doctor_name = slot.get('doctor_name', 'specialist')
            doctor_id = slot.get('doctor_id', '')
            if doctor_name not in time_clusters[cluster_key]['doctors']:
                time_clusters[cluster_key]['doctors'].append(doctor_name)
                time_clusters[cluster_key]['doctor_ids'].append(doctor_id)

        if not time_clusters:
            return "SAY_TO_USER: К сожалению, свободных слотов не найдено."

        # Get the FIRST available slot as recommendation
        first_slot = list(time_clusters.values())[0]

        # Format time naturally (09:00 -> 9, 10:30 -> 10:30)
        time_str = first_slot['time']
        if time_str.endswith(':00'):
            time_display = time_str.split(':')[0].lstrip('0') or '0'
        else:
            time_display = time_str.lstrip('0')

        # Format date naturally
        slot_date = datetime.strptime(first_slot['date'], '%Y-%m-%d').date()
        today = date_type.today()

        if slot_date == today:
            date_display = "сегодня"
        elif (slot_date - today).days == 1:
            date_display = "завтра"
        else:
            # Russian weekday names
            weekdays_ru = {
                'Monday': 'в понедельник',
                'Tuesday': 'во вторник',
                'Wednesday': 'в среду',
                'Thursday': 'в четверг',
                'Friday': 'в пятницу',
                'Saturday': 'в субботу',
                'Sunday': 'в воскресенье'
            }
            date_display = weekdays_ru.get(first_slot['weekday'], first_slot['date'])

        # Build the EXACT response to say
        # Format: "Завтра в 9 подойдёт?" or "В пятницу в 10:30 подойдёт?"
        response = f"SAY_TO_USER: {date_display.capitalize()} в {time_display} подойдёт?"

        # Add hidden booking data for next step
        response += f"\n[BOOKING: {first_slot['date']} {first_slot['time']} doctor={first_slot['doctor_ids'][0]}]"

        return response
