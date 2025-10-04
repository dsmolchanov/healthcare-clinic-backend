"""
Language-Aware WhatsApp Handler with Audit Logging
Automatically detects and responds in patient's language with complete audit trail
"""

import os
import json
import logging
import asyncio
import uuid
from typing import Dict, Optional
from datetime import datetime, timedelta
import redis.asyncio as redis
from supabase import create_client, Client

from ..services.language_detection_service import LanguageDetectionService
from ..services.audit_logger import get_audit_logger, AuditEventType, AuditSeverity
from ..booking.resource_aware_booking import ResourceAwareBookingService
from ..pubsub.reservation_manager import ReservationPubSubManager

logger = logging.getLogger(__name__)

class LanguageAwareWhatsAppHandler:
    """
    WhatsApp handler that automatically detects and responds in patient's language
    with comprehensive audit logging for compliance
    """

    def __init__(self):
        self.language_service = LanguageDetectionService()
        self.booking_service = ResourceAwareBookingService()
        self.pubsub = ReservationPubSubManager()
        self.supabase: Client = create_client(
            os.environ.get("SUPABASE_URL"),
            os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
        )
        self.redis = redis.Redis(
            host=os.environ.get('REDIS_HOST', 'localhost'),
            port=6379,
            decode_responses=True
        )

        # Language-specific intent patterns
        self.intent_patterns = {
            'book_appointment': {
                'es': ['cita', 'reservar', 'agendar', 'turno', 'consulta'],
                'en': ['appointment', 'book', 'schedule', 'booking', 'reserve'],
                'pt': ['consulta', 'agendar', 'marcar', 'reservar', 'hora'],
                'fr': ['rendez-vous', 'réserver', 'consultation', 'prendre'],
                'de': ['termin', 'buchen', 'vereinbaren', 'reservieren'],
                'it': ['appuntamento', 'prenotare', 'riservare', 'visita'],
                'zh': ['预约', '预定', '挂号', '看病', '门诊'],
                'ja': ['予約', '診察', '診療', '受診', '申込'],
                'ko': ['예약', '진료', '상담', '진찰', '방문'],
                'ar': ['موعد', 'حجز', 'استشارة', 'زيارة'],
                'hi': ['अपॉइंटमेंट', 'बुकिंग', 'मुलाकात', 'समय'],
                'ru': ['запись', 'прием', 'консультация', 'визит'],
                'he': ['תור', 'לקבוע', 'פגישה', 'ביקור', 'להזמין']
            },
            'check_availability': {
                'es': ['disponible', 'horario', 'cuándo', 'hora', 'libre'],
                'en': ['available', 'availability', 'when', 'time', 'open'],
                'pt': ['disponível', 'horário', 'quando', 'livre', 'vaga'],
                'he': ['זמין', 'פנוי', 'מתי', 'שעות', 'לוח זמנים']
            },
            'cancel_appointment': {
                'es': ['cancelar', 'anular', 'cambiar', 'posponer'],
                'en': ['cancel', 'reschedule', 'change', 'postpone'],
                'pt': ['cancelar', 'desmarcar', 'remarcar', 'adiar'],
                'he': ['לבטל', 'ביטול', 'לשנות', 'דחייה']
            },
            'emergency': {
                'es': ['urgente', 'emergencia', 'dolor', 'urgencia'],
                'en': ['urgent', 'emergency', 'pain', 'asap'],
                'pt': ['urgente', 'emergência', 'dor', 'urgência'],
                'he': ['דחוף', 'חירום', 'כאב', 'מיידי']
            }
        }

        # Subscribe to booking events
        asyncio.create_task(self._subscribe_to_events())

    async def handle_message(
        self,
        from_number: str,
        message_text: str,
        clinic_id: str,
        organization_id: str
    ) -> str:
        """
        Handle incoming WhatsApp message with automatic language detection and audit logging
        """
        audit_logger = await get_audit_logger()
        detected_language = None

        try:
            # Log incoming message
            await audit_logger.log_whatsapp_interaction(
                clinic_id=clinic_id,
                phone_number=from_number,
                direction="inbound",
                message_type="text"
            )

            # Step 1: Detect or get patient's language
            detected_language = await self.language_service.get_or_detect_patient_language(
                patient_phone=from_number,
                message_text=message_text,
                clinic_id=clinic_id
            )
            logger.info(f"Using language '{detected_language}' for patient {from_number}")

            # Log language detection
            await audit_logger.log_event(
                event_type=AuditEventType.LANGUAGE_DETECTED,
                clinic_id=clinic_id,
                user_id=audit_logger._hash_sensitive_data(from_number),
                metadata={"language": detected_language}
            )

            # Step 2: Get or create session with language tracking
            session = await self._get_or_create_session(
                from_number,
                clinic_id,
                detected_language
            )

            # Log session activity
            await audit_logger.log_event(
                event_type=AuditEventType.SESSION_START if session.get('new_session') else AuditEventType.PHI_ACCESS,
                clinic_id=clinic_id,
                user_id=audit_logger._hash_sensitive_data(from_number),
                metadata={"session_id": session['id'], "language": detected_language}
            )

            # Step 3: Check if language changed mid-conversation
            if session.get('session_language') and session['session_language'] != detected_language:
                await self.language_service.track_language_switch(
                    session_id=session['id'],
                    from_language=session['session_language'],
                    to_language=detected_language,
                    message_text=message_text
                )

            # Step 4: Parse intent based on detected language
            intent = await self._parse_intent(message_text, detected_language)

            # Step 5: Get clinic info for context
            clinic = await self._get_clinic_info(clinic_id)

            # Step 6: Generate response in detected language
            if intent == 'book_appointment':
                response = await self._handle_booking_flow(
                    from_number,
                    clinic_id,
                    detected_language,
                    audit_logger
                )
            elif intent == 'check_availability':
                response = await self._show_availability(
                    clinic_id,
                    detected_language
                )
            elif intent == 'cancel_appointment':
                response = await self._handle_cancellation(
                    from_number,
                    clinic_id,
                    detected_language,
                    audit_logger
                )
            elif intent == 'emergency':
                response = await self._handle_emergency(
                    clinic_id,
                    detected_language
                )
                # Log emergency request
                await audit_logger.log_event(
                    event_type=AuditEventType.ERROR_CRITICAL,
                    clinic_id=clinic_id,
                    user_id=audit_logger._hash_sensitive_data(from_number),
                    metadata={"type": "emergency_request", "language": detected_language},
                    severity=AuditSeverity.WARNING
                )
            else:
                # Default greeting in detected language
                response = self.language_service.get_greeting(
                    detected_language,
                    clinic['name']
                )

            # Step 7: Store conversation in session
            await self._update_session_conversation(
                session['id'],
                message_text,
                response,
                detected_language
            )

            # Log outbound message
            await audit_logger.log_whatsapp_interaction(
                clinic_id=clinic_id,
                phone_number=from_number,
                direction="outbound",
                message_type="text",
                language=detected_language
            )

            return response

        except Exception as e:
            logger.error(f"Error handling WhatsApp message: {e}")

            # Log critical error
            await audit_logger.log_event(
                event_type=AuditEventType.ERROR_CRITICAL,
                clinic_id=clinic_id,
                user_id=audit_logger._hash_sensitive_data(from_number),
                metadata={"error": str(e), "language": detected_language},
                severity=AuditSeverity.ERROR
            )

            # Error message in detected language or Spanish
            error_messages = {
                'es': "Lo siento, ocurrió un error. Por favor intente nuevamente.",
                'en': "Sorry, an error occurred. Please try again.",
                'pt': "Desculpe, ocorreu um erro. Por favor, tente novamente.",
                'he': "מצטערים, אירעה שגיאה. אנא נסה שוב."
            }
            return error_messages.get(
                detected_language if detected_language else 'es',
                error_messages['es']
            )

    async def _parse_intent(self, message: str, language: str) -> Optional[str]:
        """
        Parse intent from message based on language-specific patterns
        """
        message_lower = message.lower()

        for intent, patterns_by_lang in self.intent_patterns.items():
            patterns = patterns_by_lang.get(language, patterns_by_lang.get('es', []))
            for pattern in patterns:
                if pattern in message_lower:
                    logger.info(f"Detected intent '{intent}' from pattern '{pattern}'")
                    return intent

        return None

    async def _handle_booking_flow(
        self,
        from_number: str,
        clinic_id: str,
        language: str,
        audit_logger
    ) -> str:
        """
        Handle appointment booking flow in patient's language with audit logging
        """
        # Log appointment booking attempt
        await audit_logger.log_event(
            event_type=AuditEventType.APPOINTMENT_CREATED,
            clinic_id=clinic_id,
            user_id=audit_logger._hash_sensitive_data(from_number),
            metadata={"action": "booking_initiated", "language": language}
        )

        # Get available slots
        tomorrow = datetime.now().date() + timedelta(days=1)
        slots = await self.booking_service.find_available_slots(
            clinic_id=clinic_id,
            date=tomorrow
        )

        if not slots:
            # No availability messages
            no_availability = {
                'es': "😔 Lo siento, no hay citas disponibles para mañana. ¿Le gustaría ver otro día?",
                'en': "😔 Sorry, no appointments available for tomorrow. Would you like to check another day?",
                'pt': "😔 Desculpe, não há consultas disponíveis para amanhã. Gostaria de ver outro dia?",
                'fr': "😔 Désolé, aucun rendez-vous disponible pour demain. Voulez-vous vérifier un autre jour?",
                'de': "😔 Leider sind morgen keine Termine verfügbar. Möchten Sie einen anderen Tag prüfen?",
                'it': "😔 Mi dispiace, non ci sono appuntamenti disponibili per domani. Vuole controllare un altro giorno?",
                'zh': "😔 抱歉，明天没有可预约的时间。您想查看其他日期吗？",
                'ja': "😔 申し訳ございません、明日の予約は満席です。他の日をご確認されますか？",
                'ko': "😔 죄송합니다, 내일은 예약이 꽉 찼습니다. 다른 날짜를 확인하시겠습니까?",
                'he': "😔 מצטערים, אין תורים פנויים למחר. האם תרצה לבדוק יום אחר?"
            }
            return no_availability.get(language, no_availability['es'])

        # Format available slots message
        headers = {
            'es': "📅 *Citas disponibles para mañana:*\n\n",
            'en': "📅 *Available appointments for tomorrow:*\n\n",
            'pt': "📅 *Consultas disponíveis para amanhã:*\n\n",
            'fr': "📅 *Rendez-vous disponibles pour demain:*\n\n",
            'de': "📅 *Verfügbare Termine für morgen:*\n\n",
            'it': "📅 *Appuntamenti disponibili per domani:*\n\n",
            'zh': "📅 *明天可预约时间:*\n\n",
            'ja': "📅 *明日の予約可能時間:*\n\n",
            'ko': "📅 *내일 예약 가능 시간:*\n\n",
            'he': "📅 *תורים פנויים למחר:*\n\n"
        }

        message = headers.get(language, headers['es'])

        # Show first 8 slots
        for i, slot in enumerate(slots[:8], 1):
            time_str = slot['start_time'].strftime('%H:%M')
            doctor_name = await self._get_doctor_name(slot.get('doctor_id'))
            cabinet_name = slot.get('cabinet_name', '')

            # Format slot based on language
            if language == 'es':
                message += f"{i}. {time_str} - Dr. {doctor_name} ({cabinet_name})\n"
            elif language == 'en':
                message += f"{i}. {time_str} - Dr. {doctor_name} ({cabinet_name})\n"
            elif language == 'pt':
                message += f"{i}. {time_str} - Dr. {doctor_name} ({cabinet_name})\n"
            elif language == 'zh':
                message += f"{i}. {time_str} - {doctor_name}医生 ({cabinet_name})\n"
            elif language == 'ja':
                message += f"{i}. {time_str} - {doctor_name}先生 ({cabinet_name})\n"
            elif language == 'ko':
                message += f"{i}. {time_str} - {doctor_name} 의사 ({cabinet_name})\n"
            elif language == 'he':
                message += f"{i}. {time_str} - ד\"ר {doctor_name} ({cabinet_name})\n"
            else:
                message += f"{i}. {time_str} - Dr. {doctor_name} ({cabinet_name})\n"

        # Add instructions
        instructions = {
            'es': "\n📱 *Responda con el número de la cita que desea reservar* (1-8)",
            'en': "\n📱 *Reply with the number of the appointment you want to book* (1-8)",
            'pt': "\n📱 *Responda com o número da consulta que deseja marcar* (1-8)",
            'fr': "\n📱 *Répondez avec le numéro du rendez-vous que vous souhaitez réserver* (1-8)",
            'de': "\n📱 *Antworten Sie mit der Nummer des Termins, den Sie buchen möchten* (1-8)",
            'it': "\n📱 *Rispondi con il numero dell'appuntamento che vuoi prenotare* (1-8)",
            'zh': "\n📱 *请回复您想预约的编号* (1-8)",
            'ja': "\n📱 *予約したい番号を返信してください* (1-8)",
            'ko': "\n📱 *예약하고 싶은 번호로 답장해주세요* (1-8)",
            'he': "\n📱 *השיבו עם מספר התור שברצונכם לקבוע* (1-8)"
        }

        message += instructions.get(language, instructions['es'])

        # Store session state for follow-up
        await self._store_session_state(from_number, {
            'step': 'select_slot',
            'slots': slots[:8],
            'clinic_id': clinic_id,
            'language': language
        })

        return message

    async def _handle_cancellation(
        self,
        from_number: str,
        clinic_id: str,
        language: str,
        audit_logger
    ) -> str:
        """
        Handle appointment cancellation in patient's language with audit logging
        """
        # Check for existing appointments
        patient = await self._get_patient_by_phone(from_number)

        if not patient:
            no_appointment = {
                'es': "No encontré ninguna cita registrada con su número.",
                'en': "I couldn't find any appointments registered with your number.",
                'pt': "Não encontrei nenhuma consulta registrada com seu número.",
                'he': "לא מצאתי תורים רשומים עם המספר שלך."
            }
            return no_appointment.get(language, no_appointment['es'])

        # Log PHI access for appointment lookup
        await audit_logger.log_phi_access(
            clinic_id=clinic_id,
            accessor_id="whatsapp_system",
            patient_id=patient['id'],
            data_type="appointments",
            action="read",
            justification="Patient requested cancellation"
        )

        # Get upcoming appointments
        appointments = await self._get_patient_appointments(patient['id'])

        if not appointments:
            no_upcoming = {
                'es': "No tiene citas próximas para cancelar.",
                'en': "You don't have any upcoming appointments to cancel.",
                'pt': "Você não tem consultas próximas para cancelar.",
                'he': "אין לך תורים עתידיים לביטול."
            }
            return no_upcoming.get(language, no_upcoming['es'])

        # Show appointments
        headers = {
            'es': "📋 *Sus citas próximas:*\n\n",
            'en': "📋 *Your upcoming appointments:*\n\n",
            'pt': "📋 *Suas consultas próximas:*\n\n",
            'he': "📋 *התורים הקרובים שלך:*\n\n"
        }

        message = headers.get(language, headers['es'])

        for i, apt in enumerate(appointments[:5], 1):
            date_str = apt['appointment_date'].strftime('%d/%m')
            time_str = apt['start_time'].strftime('%H:%M')
            message += f"{i}. {date_str} - {time_str}\n"

        instructions = {
            'es': "\n❌ Responda con el número de la cita que desea cancelar",
            'en': "\n❌ Reply with the number of the appointment you want to cancel",
            'pt': "\n❌ Responda com o número da consulta que deseja cancelar",
            'he': "\n❌ השיבו עם מספר התור שברצונכם לבטל"
        }

        message += instructions.get(language, instructions['es'])

        # Store session state for follow-up
        await self._store_session_state(from_number, {
            'step': 'select_cancellation',
            'appointments': appointments[:5],
            'patient_id': patient['id'],
            'clinic_id': clinic_id,
            'language': language
        })

        # Log cancellation attempt
        await audit_logger.log_event(
            event_type=AuditEventType.APPOINTMENT_MODIFIED,
            clinic_id=clinic_id,
            user_id=patient['id'],
            metadata={"action": "cancellation_initiated", "language": language}
        )

        return message

    async def _show_availability(
        self,
        clinic_id: str,
        language: str
    ) -> str:
        """
        Show general availability in patient's language
        """
        # Get clinic schedule
        clinic = await self._get_clinic_info(clinic_id)

        schedules = {
            'es': f"""📍 *{clinic['name']}*

🕒 *Horarios de atención:*
Lunes a Viernes: 9:00 AM - 7:00 PM
Sábados: 9:00 AM - 2:00 PM
Domingos: Cerrado

📱 Para agendar una cita, escriba "CITA"
📞 Para urgencias: {clinic.get('emergency_phone', 'N/A')}""",

            'en': f"""📍 *{clinic['name']}*

🕒 *Office Hours:*
Monday to Friday: 9:00 AM - 7:00 PM
Saturday: 9:00 AM - 2:00 PM
Sunday: Closed

📱 To book an appointment, type "APPOINTMENT"
📞 For emergencies: {clinic.get('emergency_phone', 'N/A')}""",

            'pt': f"""📍 *{clinic['name']}*

🕒 *Horário de funcionamento:*
Segunda a Sexta: 9:00 - 19:00
Sábado: 9:00 - 14:00
Domingo: Fechado

📱 Para marcar uma consulta, escreva "CONSULTA"
📞 Para emergências: {clinic.get('emergency_phone', 'N/A')}""",

            'he': f"""📍 *{clinic['name']}*

🕒 *שעות פעילות:*
ראשון-חמישי: 9:00 - 19:00
שישי: 9:00 - 14:00
שבת: סגור

📱 לקביעת תור, כתבו "תור"
📞 למקרי חירום: {clinic.get('emergency_phone', 'N/A')}"""
        }

        return schedules.get(language, schedules['es'])

    async def _handle_emergency(
        self,
        clinic_id: str,
        language: str
    ) -> str:
        """
        Handle emergency requests in patient's language
        """
        clinic = await self._get_clinic_info(clinic_id)

        emergency_messages = {
            'es': f"""🚨 *URGENCIA DENTAL*

Si tiene una emergencia dental, por favor:

1️⃣ Llame inmediatamente a: {clinic.get('emergency_phone', 'N/A')}
2️⃣ O diríjase a nuestra clínica en: {clinic.get('address', 'N/A')}
3️⃣ Si es fuera de horario, acuda al hospital más cercano

⚠️ Este chat no es para emergencias médicas.""",

            'en': f"""🚨 *DENTAL EMERGENCY*

If you have a dental emergency, please:

1️⃣ Call immediately: {clinic.get('emergency_phone', 'N/A')}
2️⃣ Or come to our clinic at: {clinic.get('address', 'N/A')}
3️⃣ If after hours, go to the nearest hospital

⚠️ This chat is not for medical emergencies.""",

            'pt': f"""🚨 *EMERGÊNCIA ODONTOLÓGICA*

Se você tem uma emergência odontológica:

1️⃣ Ligue imediatamente: {clinic.get('emergency_phone', 'N/A')}
2️⃣ Ou venha à nossa clínica: {clinic.get('address', 'N/A')}
3️⃣ Se fora do horário, vá ao hospital mais próximo

⚠️ Este chat não é para emergências médicas.""",

            'he': f"""🚨 *חירום דנטלי*

אם יש לך מקרה חירום דנטלי:

1️⃣ התקשרו מיד: {clinic.get('emergency_phone', 'N/A')}
2️⃣ או הגיעו למרפאה שלנו: {clinic.get('address', 'N/A')}
3️⃣ מחוץ לשעות הפעילות, פנו לבית החולים הקרוב

⚠️ צ'אט זה אינו מיועד למקרי חירום רפואיים."""
        }

        return emergency_messages.get(language, emergency_messages['es'])

    async def _get_or_create_session(
        self,
        phone: str,
        clinic_id: str,
        language: str
    ) -> Dict:
        """
        Get or create conversation session with language tracking
        """
        session_key = f"session:{clinic_id}:{phone}"
        session_data = await self.redis.get(session_key)

        if session_data:
            session = json.loads(session_data)
            session['new_session'] = False
            # Update language if different
            if session.get('session_language') != language:
                session['session_language'] = language
                await self.redis.setex(
                    session_key,
                    86400,
                    json.dumps(session)
                )
        else:
            # Create new session
            session = {
                'id': str(uuid.uuid4()),
                'clinic_id': clinic_id,
                'phone': phone,
                'session_language': language,
                'languages_used': [language],
                'created_at': datetime.utcnow().isoformat(),
                'messages': [],
                'new_session': True
            }
            await self.redis.setex(
                session_key,
                86400,  # 24 hour expiry
                json.dumps(session)
            )

            # Also store in database
            self.supabase.table('core.conversation_sessions').insert({
                'id': session['id'],
                'organization_id': clinic_id,
                'user_identifier': phone,
                'channel': 'whatsapp',
                'session_language': language,
                'languages_used': [language],
                'created_at': datetime.utcnow().isoformat()
            }).execute()

        return session

    async def _update_session_conversation(
        self,
        session_id: str,
        user_message: str,
        bot_response: str,
        language: str
    ):
        """
        Update session with conversation history
        """
        try:
            # Update in database
            self.supabase.table('core.conversation_sessions').update({
                'last_message_at': datetime.utcnow().isoformat(),
                'message_count': self.supabase.raw('message_count + 1').execute(),
                'session_language': language
            }).eq('id', session_id).execute()
        except Exception as e:
            logger.error(f"Failed to update session conversation: {e}")

    async def _subscribe_to_events(self):
        """
        Subscribe to real-time booking events
        """
        await self.pubsub.subscribe_to_events(
            clinic_id='all',
            event_types=[
                'reservation.held',
                'reservation.confirmed',
                'reservation.expired',
                'appointment.cancelled'
            ],
            callback=self._handle_booking_event
        )

    async def _handle_booking_event(self, event_data: Dict):
        """
        Handle real-time booking events with language-aware notifications
        """
        event_type = event_data['event_type']
        data = event_data['data']

        if event_type == 'reservation.expired':
            # Get patient's language
            patient_phone = data.get('patient_identifier')
            if patient_phone:
                patient = await self._get_patient_by_phone(patient_phone)
                language = patient.get('preferred_language', 'es') if patient else 'es'

                # Send expiry message in patient's language
                expiry_messages = {
                    'es': "⏰ Su reserva ha expirado. Por favor, intente nuevamente.",
                    'en': "⏰ Your reservation has expired. Please try again.",
                    'pt': "⏰ Sua reserva expirou. Por favor, tente novamente.",
                    'he': "⏰ ההזמנה שלך פגה. אנא נסה שוב."
                }

                message = expiry_messages.get(language, expiry_messages['es'])
                await self._send_whatsapp_message(patient_phone, message)

    # Helper methods
    async def _get_clinic_info(self, clinic_id: str) -> Dict:
        """Get clinic information"""
        result = self.supabase.table('healthcare.clinics').select('*').eq('id', clinic_id).single().execute()
        return result.data if result.data else {'name': 'Clinic'}

    async def _get_patient_by_phone(self, phone: str) -> Optional[Dict]:
        """Get patient by phone"""
        result = self.supabase.table('healthcare.patients').select('*').eq('phone', phone).single().execute()
        return result.data if result.data else None

    async def _get_doctor_name(self, doctor_id: str) -> str:
        """Get doctor name"""
        if not doctor_id:
            return "Available"
        result = self.supabase.table('healthcare.doctors').select('name').eq('id', doctor_id).single().execute()
        return result.data['name'] if result.data else "Doctor"

    async def _get_patient_appointments(self, patient_id: str) -> list:
        """Get patient's upcoming appointments"""
        result = self.supabase.table('healthcare.appointments').select('*').eq(
            'patient_id', patient_id
        ).gte('appointment_date', datetime.now().date()).execute()
        return result.data if result.data else []

    async def _store_session_state(self, phone: str, state: Dict):
        """Store session state in Redis"""
        key = f"state:{phone}"
        await self.redis.setex(key, 300, json.dumps(state))  # 5 minute expiry

    async def _send_whatsapp_message(self, to_number: str, message: str):
        """Send WhatsApp message (implement with Twilio)"""
        # This would use Twilio API to send message
        logger.info(f"Would send to {to_number}: {message}")
