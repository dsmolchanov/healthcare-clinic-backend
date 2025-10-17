"""
Language-Specific Fallback Service

Provides tailored responses based on user's detected/stored language preference.
Eliminates irrelevant multilingual fallbacks by detecting user language from:
1. Patient profile (preferred)
2. Recent message history
3. Session metadata
"""

import logging
from typing import Dict, Any, Optional
from datetime import datetime

logger = logging.getLogger(__name__)


class LanguageFallbackService:
    """Provides language-specific fallback responses"""

    def __init__(self):
        self.supported_languages = {'ru', 'es', 'en', 'he', 'pt'}
        self.default_language = 'en'

    async def get_user_language(
        self,
        phone_number: str,
        clinic_id: str,
        supabase_client
    ) -> str:
        """
        Get user's preferred language from multiple sources

        Priority:
        1. Patient profile language preference
        2. Session metadata (from recent conversations)
        3. Language detection from recent messages
        4. Default language

        Args:
            phone_number: User's phone number
            clinic_id: Clinic ID
            supabase_client: Supabase client instance

        Returns:
            Language code (ru/es/en/he/pt)
        """
        try:
            # Strategy 1: Check patient profile for language preference
            patient_result = supabase_client.schema('healthcare').table('patients').select(
                'language_preference'
            ).eq('phone', phone_number).eq('clinic_id', clinic_id).limit(1).execute()

            if patient_result.data and len(patient_result.data) > 0:
                lang = patient_result.data[0].get('language_preference')
                if lang and lang in self.supported_languages:
                    logger.info(f"✅ Using patient profile language: {lang}")
                    return lang

            # Strategy 2: Check session metadata for detected language
            clean_phone = phone_number.replace("@s.whatsapp.net", "")
            session_result = supabase_client.table('conversation_sessions').select(
                'session_language, languages_used'
            ).eq('user_identifier', clean_phone).order(
                'created_at', desc=True
            ).limit(1).execute()

            if session_result.data and len(session_result.data) > 0:
                session_lang = session_result.data[0].get('session_language')
                if session_lang and session_lang in self.supported_languages:
                    logger.info(f"✅ Using session language: {session_lang}")
                    return session_lang

            # Strategy 3: Check recent message history
            messages_result = supabase_client.schema('healthcare').table('conversation_logs').select(
                'detected_language'
            ).eq('from_phone', clean_phone).eq('clinic_id', clinic_id).order(
                'created_at', desc=True
            ).limit(5).execute()

            if messages_result.data:
                # Get most common language from recent messages
                languages = [msg['detected_language'] for msg in messages_result.data if msg.get('detected_language')]
                if languages:
                    most_common = max(set(languages), key=languages.count)
                    if most_common in self.supported_languages:
                        logger.info(f"✅ Using detected language from history: {most_common}")
                        return most_common

        except Exception as e:
            logger.warning(f"Failed to get user language: {e}")

        # Fallback to default
        logger.info(f"Using default language: {self.default_language}")
        return self.default_language

    def get_apology_message(self, language: str, context: str = "") -> str:
        """
        Get language-specific apology message

        Args:
            language: Language code
            context: Optional context for the apology

        Returns:
            Localized apology message
        """
        messages = {
            'ru': {
                'generic': 'Извините за задержку. Я свяжусь с командой и вернусь к вам в ближайшее время.',
                'unavailable': 'К сожалению, я не могу сейчас помочь с этим. Позвольте мне проконсультироваться с командой.',
                'error': 'Произошла ошибка. Я уже работаю над этим и скоро свяжусь с вами.',
                'timeout': 'Мне нужно немного больше времени, чтобы найти эту информацию. Я скоро вернусь.'
            },
            'es': {
                'generic': 'Disculpe la demora. Me comunicaré con el equipo y le responderé pronto.',
                'unavailable': 'Lo siento, no puedo ayudar con eso ahora mismo. Permítame consultar con el equipo.',
                'error': 'Ocurrió un error. Ya estoy trabajando en ello y me comunicaré con usted pronto.',
                'timeout': 'Necesito un poco más de tiempo para encontrar esta información. Volveré pronto.'
            },
            'en': {
                'generic': 'I apologize for the delay. I\'ll check with the team and get back to you shortly.',
                'unavailable': 'I\'m sorry, I can\'t help with that right now. Let me consult with the team.',
                'error': 'An error occurred. I\'m already working on it and will get back to you soon.',
                'timeout': 'I need a bit more time to find this information. I\'ll be back soon.'
            },
            'he': {
                'generic': 'מצטער על העיכוב. אצור קשר עם הצוות ואחזור אליך בקרוב.',
                'unavailable': 'מצטער, לא יכול לעזור עם זה כרגע. תן לי להתייעץ עם הצוות.',
                'error': 'אירעה שגיאה. אני כבר עובד על זה ואחזור אליך בקרוב.',
                'timeout': 'אני צריך קצת יותר זמן למצוא את המידע הזה. אחזור בקרוב.'
            },
            'pt': {
                'generic': 'Desculpe a demora. Vou verificar com a equipe e retorno em breve.',
                'unavailable': 'Desculpe, não posso ajudar com isso agora. Deixe-me consultar a equipe.',
                'error': 'Ocorreu um erro. Já estou trabalhando nisso e retorno em breve.',
                'timeout': 'Preciso de um pouco mais de tempo para encontrar esta informação. Volto logo.'
            }
        }

        # Get language-specific messages
        lang_messages = messages.get(language, messages['en'])

        # Determine which message to use based on context
        if 'timeout' in context.lower():
            return lang_messages['timeout']
        elif 'error' in context.lower():
            return lang_messages['error']
        elif 'unavailable' in context.lower():
            return lang_messages['unavailable']
        else:
            return lang_messages['generic']

    def get_followup_notification(
        self,
        language: str,
        hours: int,
        urgency: str = 'medium'
    ) -> str:
        """
        Get language-specific follow-up notification

        Args:
            language: Language code
            hours: Hours until follow-up
            urgency: Urgency level

        Returns:
            Localized notification message
        """
        # Format time string
        if hours < 1:
            time_str = {
                'ru': 'в течение часа',
                'es': 'en menos de una hora',
                'en': 'within an hour',
                'he': 'תוך שעה',
                'pt': 'em menos de uma hora'
            }
        elif hours == 1:
            time_str = {
                'ru': 'через 1 час',
                'es': 'en 1 hora',
                'en': 'in 1 hour',
                'he': 'בעוד שעה',
                'pt': 'em 1 hora'
            }
        elif hours <= 24:
            time_str = {
                'ru': f'через {hours} часов',
                'es': f'en {hours} horas',
                'en': f'in {hours} hours',
                'he': f'בעוד {hours} שעות',
                'pt': f'em {hours} horas'
            }
        else:
            days = hours // 24
            time_str = {
                'ru': f'через {days} дня' if days == 1 else f'через {days} дней',
                'es': f'en {days} día' if days == 1 else f'en {days} días',
                'en': f'in {days} day' if days == 1 else f'in {days} days',
                'he': f'בעוד {days} ימים',
                'pt': f'em {days} dia' if days == 1 else f'em {days} dias'
            }

        messages = {
            'ru': f"Я свяжусь с вами {time_str['ru']} с обновлением.",
            'es': f"Me comunicaré con usted {time_str['es']} con una actualización.",
            'en': f"I'll follow up with you {time_str['en']} with an update.",
            'he': f"אחזור אליך {time_str['he']} עם עדכון.",
            'pt': f"Entrarei em contato {time_str['pt']} com uma atualização."
        }

        return messages.get(language, messages['en'])

    def get_confirmation_message(self, language: str, action: str) -> str:
        """
        Get language-specific confirmation message

        Args:
            language: Language code
            action: Action being confirmed

        Returns:
            Localized confirmation message
        """
        templates = {
            'ru': f"Отлично! {action}. Я подтвержу детали в ближайшее время.",
            'es': f"¡Perfecto! {action}. Confirmaré los detalles pronto.",
            'en': f"Great! {action}. I'll confirm the details shortly.",
            'he': f"נהדר! {action}. אאשר את הפרטים בקרוב.",
            'pt': f"Ótimo! {action}. Confirmarei os detalhes em breve."
        }

        return templates.get(language, templates['en'])

    def get_error_message(self, language: str, error_type: str = 'generic') -> str:
        """
        Get language-specific error message

        Args:
            language: Language code
            error_type: Type of error (generic, timeout, validation)

        Returns:
            Localized error message
        """
        messages = {
            'ru': {
                'generic': 'Произошла ошибка. Пожалуйста, попробуйте еще раз или свяжитесь с нами напрямую.',
                'timeout': 'Запрос занял слишком много времени. Пожалуйста, попробуйте еще раз.',
                'validation': 'Пожалуйста, проверьте введенную информацию и попробуйте снова.'
            },
            'es': {
                'generic': 'Ocurrió un error. Por favor intente nuevamente o contáctenos directamente.',
                'timeout': 'La solicitud tardó demasiado. Por favor intente nuevamente.',
                'validation': 'Por favor verifique la información ingresada e intente nuevamente.'
            },
            'en': {
                'generic': 'An error occurred. Please try again or contact us directly.',
                'timeout': 'The request took too long. Please try again.',
                'validation': 'Please check the information entered and try again.'
            },
            'he': {
                'generic': 'אירעה שגיאה. נסה שוב או צור איתנו קשר ישירות.',
                'timeout': 'הבקשה לקחה יותר מדי זמן. נסה שוב.',
                'validation': 'אנא בדוק את המידע שהוזן ונסה שוב.'
            },
            'pt': {
                'generic': 'Ocorreu um erro. Por favor tente novamente ou entre em contato conosco diretamente.',
                'timeout': 'A solicitação demorou muito. Por favor tente novamente.',
                'validation': 'Por favor verifique as informações digitadas e tente novamente.'
            }
        }

        lang_messages = messages.get(language, messages['en'])
        return lang_messages.get(error_type, lang_messages['generic'])


# Singleton instance
_language_fallback_service: Optional[LanguageFallbackService] = None


def get_language_fallback_service() -> LanguageFallbackService:
    """Get or create singleton LanguageFallbackService instance"""
    global _language_fallback_service
    if _language_fallback_service is None:
        _language_fallback_service = LanguageFallbackService()
    return _language_fallback_service
