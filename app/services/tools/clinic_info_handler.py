from typing import Any, Dict
import logging
from app.services.tools.base import ToolHandler
from app.tools.clinic_info_tool import ClinicInfoTool
from app.config import get_redis_client
from app.services.clinic_data_cache import ClinicDataCache

logger = logging.getLogger(__name__)

class ClinicInfoHandler(ToolHandler):
    @property
    def tool_name(self) -> str:
        return "get_clinic_info"

    async def execute(self, args: Dict[str, Any], context: Dict[str, Any]) -> str:
        clinic_id = context.get('clinic_id')
        supabase_client = context.get('supabase_client')
        
        if not clinic_id or not supabase_client:
            return "Error: clinic_id or supabase_client missing from context"

        redis_client = get_redis_client()
        tool = ClinicInfoTool(clinic_id=clinic_id, redis_client=redis_client)
        
        info_type = args.get('info_type', 'all')

        if info_type == 'doctors':
            result = await tool.get_doctor_count(supabase_client)
            if result.get('doctor_details'):
                doctor_lines = []
                for doc in result['doctor_details']:
                    doctor_lines.append(
                        f"{doc['name']} (specialization: {doc['specialization']}, doctor_id: {doc['id']})"
                    )
                info = f"The clinic has {result['total_doctors']} doctors:\n" + "\n".join(doctor_lines)
            elif result.get('specializations'):
                doctor_details = []
                for spec, doctors in result['specializations'].items():
                    for doc in doctors:
                        if isinstance(doc, dict):
                            doctor_details.append(
                                f"{doc['name']} (specialization: {spec}, doctor_id: {doc['id']})"
                            )
                        else:
                            doctor_details.append(f"{doc} (specialization: {spec})")
                info = f"The clinic has {result['total_doctors']} doctors:\n" + "\n".join(doctor_details)
            else:
                info = f"The clinic has {result['total_doctors']} doctors: {', '.join(result['doctor_list'])}"

        elif info_type == 'location':
            clinic_info = await tool.get_clinic_info(supabase_client)
            address_parts = [clinic_info.get('address', 'Not available')]
            if clinic_info.get('city'):
                address_parts.append(clinic_info.get('city'))
            if clinic_info.get('state'):
                address_parts.append(clinic_info.get('state'))
            if clinic_info.get('country'):
                address_parts.append(clinic_info.get('country'))
            full_address = ', '.join(address_parts)
            info = f"Address: {full_address}\nPhone: {clinic_info.get('phone', 'Not available')}\nEmail: {clinic_info.get('email', 'Not available')}"

        elif info_type == 'hours':
            clinic_info = await tool.get_clinic_info(supabase_client)
            hours = clinic_info.get('business_hours') or clinic_info.get('hours', {})
            if hours:
                info = "Business Hours:\n" + "\n".join([f"{day.capitalize()}: {time}" for day, time in hours.items()])
            else:
                info = "Business hours not available"

        elif info_type == 'services':
            cache = ClinicDataCache(redis_client, default_ttl=3600)
            services = await cache.get_services(clinic_id, supabase_client)
            if services:
                service_names = [s.get('name', '') for s in services[:10]]
                info = f"We offer {len(services)} services including: {', '.join(service_names)}"
                if len(services) > 10:
                    info += f" and {len(services) - 10} more..."
            else:
                info = "Service information not available"

        else:  # 'all' or unknown
            doctor_result = await tool.get_doctor_count(supabase_client)
            clinic_info = await tool.get_clinic_info(supabase_client)

            info_parts = []
            if clinic_info.get('name'):
                info_parts.append(f"Clinic: {clinic_info['name']}")
            if clinic_info.get('address'):
                info_parts.append(f"Address: {clinic_info['address']}")
            if doctor_result.get('total_doctors'):
                info_parts.append(f"Doctors: {doctor_result['total_doctors']}")
                if doctor_result.get('doctor_details'):
                    doctor_lines = [
                        f"{d['name']} (specialization: {d['specialization']}, doctor_id: {d['id']})"
                        for d in doctor_result['doctor_details']
                    ]
                    info_parts.append(f"Doctor details:\n" + "\n".join(doctor_lines))
                else:
                    info_parts.append(f"Doctor list: {', '.join(doctor_result['doctor_list'])}")

            info = "\n".join(info_parts) if info_parts else "Clinic information not available"

        logger.info(f"✅ get_clinic_info tool (type={info_type}) returned {len(info)} chars")
        return info
