"""
Answer Service - Template-based responses for common queries.

Provides smart answers for pricing, hours, address, etc. without LLM.
Future upgrade: RAG integration for complex questions.
"""

from typing import Optional, Dict, Any
import logging
from supabase import Client

logger = logging.getLogger(__name__)


class AnswerService:
    """
    Provides template-based answers for common clinic queries.

    Fetches clinic-specific data from Supabase and composes responses
    using templates. No LLM needed for basic questions.

    Usage:
        >>> service = AnswerService(supabase_client)
        >>> response = await service.answer_pricing(
        ...     clinic_id="clinic1",
        ...     entities={"service": "veneers"}
        ... )
        >>> print(response)
        "Виниры стоят от 18,000 до 35,000₽ за зуб..."
    """

    def __init__(self, supabase_client: Client):
        """
        Initialize AnswerService.

        Args:
            supabase_client: Supabase client for data fetching
        """
        self.supabase = supabase_client

    async def answer_pricing(
        self,
        clinic_id: str,
        entities: Dict[str, Any]
    ) -> str:
        """
        Answer pricing questions.

        Args:
            clinic_id: Clinic identifier
            entities: Extracted entities (e.g., {"service": "veneers"})

        Returns:
            Human-friendly pricing response with CTA

        Examples:
            "Виниры стоят от 18,000 до 35,000₽ за зуб. Точная стоимость зависит
            от материала и количества зубов. Хотите записаться на бесплатную
            консультацию?"
        """
        service = entities.get("service")

        try:
            # TODO: Fetch from clinic_services table
            # For now, use generic response
            if service:
                service_names = {
                    "veneers": "виниры",
                    "cleaning": "профессиональная чистка",
                    "filling": "пломбирование",
                    "implant": "имплантация",
                    "crown": "коронки",
                    "whitening": "отбеливание"
                }
                service_ru = service_names.get(service, service)

                response = (
                    f"Стоимость услуги '{service_ru}' зависит от сложности и материалов. "
                    f"Для точной оценки запишитесь на бесплатную консультацию — "
                    f"врач осмотрит и назовёт цену.\n\n"
                    f"Хотите записаться?"
                )
            else:
                # No specific service mentioned
                response = (
                    "С удовольствием расскажу про цены! О какой услуге вас интересует? "
                    "Например:\n"
                    "• Чистка зубов\n"
                    "• Лечение кариеса\n"
                    "• Виниры\n"
                    "• Имплантация"
                )

            logger.info(f"[AnswerService] Pricing query for service={service}")
            return response

        except Exception as e:
            logger.error(f"[AnswerService] Error answering pricing: {e}")
            return (
                "Для уточнения стоимости, пожалуйста, позвоните нам или "
                "запишитесь на консультацию."
            )

    async def answer_hours(self, clinic_id: str) -> str:
        """
        Answer hours/schedule questions.

        Args:
            clinic_id: Clinic identifier

        Returns:
            Clinic schedule with CTA
        """
        try:
            # TODO: Fetch from clinics table
            # For now, generic response
            response = (
                "График работы:\n"
                "Пн-Пт: 9:00 - 20:00\n"
                "Сб: 10:00 - 18:00\n"
                "Вс: выходной\n\n"
                "Хотите записаться на удобное время?"
            )

            logger.info(f"[AnswerService] Hours query for clinic={clinic_id}")
            return response

        except Exception as e:
            logger.error(f"[AnswerService] Error answering hours: {e}")
            return "Для уточнения графика, позвоните нам."

    async def answer_address(self, clinic_id: str) -> str:
        """
        Answer location/address questions.

        Args:
            clinic_id: Clinic identifier

        Returns:
            Clinic address with directions
        """
        try:
            # TODO: Fetch from clinics table (address, landmark, metro)
            response = (
                "Адрес клиники:\n"
                "ул. Примерная, д. 123, офис 45\n"
                "Метро 'Название станции', 5 минут пешком\n\n"
                "Ориентир: рядом с торговым центром 'Пример'\n\n"
                "Хотите записаться на приём?"
            )

            logger.info(f"[AnswerService] Address query for clinic={clinic_id}")
            return response

        except Exception as e:
            logger.error(f"[AnswerService] Error answering address: {e}")
            return "Для уточнения адреса, позвоните нам."

    async def answer_phone(self, clinic_id: str) -> str:
        """
        Answer phone/contact questions.

        Args:
            clinic_id: Clinic identifier

        Returns:
            Contact information
        """
        try:
            # TODO: Fetch from clinics table
            response = (
                "Телефон для записи:\n"
                "+7 (999) 123-45-67\n\n"
                "Можем записать вас прямо сейчас через WhatsApp. Хотите?"
            )

            logger.info(f"[AnswerService] Phone query for clinic={clinic_id}")
            return response

        except Exception as e:
            logger.error(f"[AnswerService] Error answering phone: {e}")
            return "Для связи с нами, позвоните по номеру в профиле."

    async def answer_services(self, clinic_id: str) -> str:
        """
        Answer questions about available services.

        Args:
            clinic_id: Clinic identifier

        Returns:
            Service list with CTA
        """
        try:
            # TODO: Fetch from clinic_services table
            response = (
                "Наши услуги:\n"
                "• Профессиональная чистка и гигиена\n"
                "• Лечение кариеса и пульпита\n"
                "• Виниры и эстетическая стоматология\n"
                "• Имплантация и протезирование\n"
                "• Отбеливание зубов\n"
                "• Детская стоматология\n\n"
                "Что вас интересует? Могу подробнее рассказать или записать на консультацию."
            )

            logger.info(f"[AnswerService] Services query for clinic={clinic_id}")
            return response

        except Exception as e:
            logger.error(f"[AnswerService] Error answering services: {e}")
            return "Для уточнения услуг, позвоните нам или запишитесь на консультацию."

    async def answer_general(self, clinic_id: str, message: str) -> str:
        """
        Fallback for unclassified topic changes.

        Args:
            clinic_id: Clinic identifier
            message: Original user message

        Returns:
            Helpful menu of options
        """
        logger.info(f"[AnswerService] General query: {message}")

        return (
            "Могу помочь с:\n"
            "• Ценами на услуги\n"
            "• Графиком работы\n"
            "• Адресом и как добраться\n"
            "• Записью на приём\n\n"
            "Что вас интересует?"
        )
