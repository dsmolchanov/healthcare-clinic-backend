"""
Service-to-Doctor Mapping

Resolves which doctors provide a given service using the eligibility matrix.
"""

import logging
from typing import List, Dict, Optional, Any
from supabase import Client

logger = logging.getLogger(__name__)


class ServiceDoctorMapper:
    """Maps services to eligible doctors using eligibility matrix"""

    def __init__(self, supabase: Client):
        self.supabase = supabase

    async def get_doctors_for_service(
        self,
        service_id: str,
        clinic_id: str,
        patient_id: Optional[str] = None,
        limit: int = 10
    ) -> List[Dict[str, Any]]:
        """
        Find eligible doctors for a service with match quality.

        Args:
            service_id: Service UUID
            clinic_id: Clinic UUID
            patient_id: Optional patient UUID for constraint checking
            limit: Max doctors to return

        Returns:
            List of {
                id, name, first_name, last_name, specialization,
                match_type, match_score, reasons,
                requires_supervision, custom_duration, custom_price
            }
        """
        result = self.supabase.rpc(
            'get_doctors_by_service_v2',
            {
                'p_service_id': service_id,
                'p_clinic_id': clinic_id,
                'p_patient_id': patient_id,
                'p_limit': limit
            }
        ).execute()

        if not result.data:
            logger.warning(
                f"No eligible doctors found for service {service_id}"
            )
            return []

        doctors = []
        for row in result.data:
            doctors.append({
                'id': row['id'],
                'name': f"{row['first_name']} {row['last_name']}",
                'first_name': row['first_name'],
                'last_name': row['last_name'],
                'specialization': row.get('specialization', ''),
                'match_type': row['match_type'],
                'match_score': row['match_score'],
                'reasons': row.get('reasons', []),
                'requires_supervision': row.get('requires_supervision', False),
                'custom_duration': row.get('custom_duration_minutes'),
                'custom_price': row.get('custom_price_override'),
            })

        logger.info(
            f"Found {len(doctors)} eligible doctors for service {service_id}: "
            f"scores={[d['match_score'] for d in doctors]}"
        )

        return doctors

    def format_doctor_choice_prompt(
        self,
        doctors: List[Dict],
        service_name: str
    ) -> str:
        """Format doctor list with match quality indicators"""

        if len(doctors) == 1:
            doc = doctors[0]
            return (
                f"Услугу '{service_name}' выполняет {doc['name']} "
                f"({doc['specialization']}). "
                f"Проверю доступность..."
            )

        # Group by match quality
        preferred = [d for d in doctors if d['match_type'] == 'preferred']
        qualified = [d for d in doctors if d['match_type'] in ('allowed', 'derived')]

        lines = []
        if preferred:
            lines.append("⭐ Рекомендуемые врачи:")
            for i, d in enumerate(preferred, 1):
                lines.append(f"  {i}. {d['name']} ({d['specialization']})")

        if qualified:
            if preferred:
                lines.append("\n✓ Квалифицированные врачи:")
            start_num = len(preferred) + 1
            for i, d in enumerate(qualified, start_num):
                lines.append(f"  {i}. {d['name']} ({d['specialization']})")

        lines.append(f"\nК кому хотите записаться на {service_name}?")
        return "\n".join(lines)
