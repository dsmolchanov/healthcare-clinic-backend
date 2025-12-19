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
        Format slots in a clustered, human-friendly way.

        Groups slots by time (ignoring doctor), limits to 3-4 distinct times,
        and provides a summary that helps the LLM present options naturally.
        """
        from datetime import datetime
        from collections import OrderedDict

        # Cluster slots by (date, time) - ignore doctor for grouping
        time_clusters = OrderedDict()

        for slot in slots:
            slot_datetime = slot.get('datetime', '')
            if not slot_datetime:
                continue

            # Parse datetime
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
                    'doctor_count': 0
                }

            doctor_name = slot.get('doctor_name', 'Available specialist')
            if doctor_name not in time_clusters[cluster_key]['doctors']:
                time_clusters[cluster_key]['doctors'].append(doctor_name)
                time_clusters[cluster_key]['doctor_count'] += 1

        if not time_clusters:
            return "No available slots found."

        # Get unique dates
        unique_dates = list(set(c['date'] for c in time_clusters.values()))
        unique_dates.sort()

        # Limit to first 4 distinct time slots
        clustered_list = list(time_clusters.values())[:4]

        # Build summary message for LLM
        total_slots = len(slots)
        unique_times = len(time_clusters)

        # Format each clustered time slot
        options = []
        for cluster in clustered_list:
            time_display = cluster['time']
            if cluster['doctor_count'] > 1:
                options.append(f"{cluster['date']} at {time_display} ({cluster['doctor_count']} specialists available)")
            else:
                options.append(f"{cluster['date']} at {time_display} with {cluster['doctors'][0]}")

        # Build the result text
        result_text = f"Found {total_slots} slots across {unique_times} time options.\n"
        result_text += "Main options:\n"
        for opt in options:
            result_text += f"- {opt}\n"

        # Add note about more availability if there are more options
        if unique_times > 4:
            result_text += f"\n(Plus {unique_times - 4} more time options available if needed)"

        # Add recommendation if present
        if result.get('recommendation'):
            rec = result['recommendation']
            if isinstance(rec, dict):
                rec_time = rec.get('datetime', rec.get('start_time', ''))
                if rec_time:
                    try:
                        rec_dt = datetime.fromisoformat(rec_time.replace('Z', '+00:00'))
                        result_text += f"\nRecommended: {rec_dt.strftime('%Y-%m-%d')} at {rec_dt.strftime('%H:%M')}"
                    except (ValueError, AttributeError):
                        pass

        return result_text
