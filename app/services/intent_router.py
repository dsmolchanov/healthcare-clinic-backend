# clinics/backend/app/services/intent_router.py

import re
import logging
from typing import Optional, Dict, Any
from enum import Enum
from app.utils.feature_flags import is_fast_path_enabled

logger = logging.getLogger(__name__)

class Intent(str, Enum):
    """Known intents that can be handled without RAG/LLM"""
    GREETING = "greeting"  # Fast-lane for greetings
    HANDOFF_HUMAN = "handoff_human"
    CONFIRM_TIME = "confirm_time"  # NEW: Time confirmation (e.g., "Yes, at 9 AM")
    BOOK_APPOINTMENT = "book_appointment"
    RESCHEDULE = "reschedule"
    CANCEL = "cancel"
    PRICE_QUERY = "price_query"
    FAQ_QUERY = "faq_query"  # FAQ queries (hours, location, insurance, etc.)
    UNKNOWN = "unknown"

# Multilingual patterns
INTENT_PATTERNS = {
    Intent.GREETING: [
        # English
        r"^(hi|hello|hey|good\s+(morning|afternoon|evening|day))\b",
        # Spanish
        r"^(hola|buenos\s+(días|tardes|noches)|buenas)\b",
        # Russian
        r"^(привет|здравствуйте|добрый\s+(день|вечер|утро)|доброе\s+утро)\b",
        # Hebrew
        r"^(שלום|בוקר\s+טוב|ערב\s+טוב)",
        # Portuguese
        r"^(oi|olá|bom\s+dia)\b",
    ],
    Intent.HANDOFF_HUMAN: [
        r"\b(speak|talk|connect|transfer).{0,20}(human|person|agent|someone|operator|representative)\b",
        r"\b(real|actual).{0,10}(person|agent|human)\b",
        r"\b(manager|supervisor|staff)\b",
        r"\b(living|live)\s+(person|agent|operator)\b",
        # Spanish
        r"\b(hablar|habla).{0,20}(humano|persona|agente)\b",
        r"\bpersona real\b",
        # Russian
        r"(живой\s+оператор|реальный\s+человек|настоящий\s+человек)",
        # Hebrew
        r"(לדבר עם אדם|נציג אמיתי|איש צוות)",
    ],
    Intent.CONFIRM_TIME: [
        # English: "Yes, at 9", "OK for 9:00", "Yeah, 9 AM works"
        r"^(yes|yeah|yep|ok|okay|sure|fine|good|perfect|great)[,\s]*.{0,15}\b(at|for|к)\s*(\d{1,2})(:\d{2})?\s*(am|pm|o'?clock|часов)?\b",
        r"^(да|ага|окей|ок|хорошо|отлично|подходит)[,\s]*.{0,15}\b(на|в|к|for|at)\s*(\d{1,2})(:\d{2})?\s*(часов|утра|вечера|am|pm)?\b",
        # Spanish: "Sí, para mañana, a las 9", "Vale, mañana a las 9", "OK para mañana 9:00"
        r"^(sí|si|claro|vale|ok|de acuerdo|perfecto)[,\s]*.{0,30}\b(para|para el|pa'|pa)\b.{0,30}\b(hoy|mañana|pasado|lunes|martes|miércoles|jueves|viernes|sábado|domingo)\b.{0,30}\b(a\s+las|a)\s*(\d{1,2})(?::(\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)?",
        # Spanish short: "Sí, a las 9", "OK a las 10:30"
        r"^(sí|si|ok|vale|claro)[,\s]*.{0,15}\b(a\s+las|a)\s*(\d{1,2})(?::(\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)?",
        # Just confirmation with time: "да, на 9 часов", "yes, 9 AM"
        r"^(да|yes|ага|ok)[,\s]+.{0,10}(на|в|к|for|at)\s*(\d{1,2})(:\d{2})?\s*(часов|утра|вечера|am|pm|o'?clock)?\b",
        # Numeric time at start: "9 AM", "9:00", "в 9", "к девяти"
        r"^(\d{1,2})(:\d{2})?\s*(am|pm|o'?clock|часов|утра|вечера)?\b",
        # Russian time formats: "к 9", "на девять", "в 9 утра"
        r"^(к|на|в)\s*(\d{1,2}|девят[иь]|десят[иь]|одиннадцат[иь]|двенадцат[иь])\s*(часов|утра|вечера)?\b",
    ],
    Intent.BOOK_APPOINTMENT: [
        r"\b(book|schedule|make|set up).{0,20}(appointment|meeting|visit)\b",
        r"\b(need|want).{0,20}(appointment|see doctor|consultation)\b",
        r"\b(tomorrow|today|this week|next week).{0,30}(appointment|available|time)\b",
    ],
    Intent.RESCHEDULE: [
        r"\b(reschedule|change|move).{0,20}(appointment|booking|meeting)\b",
        r"\bcan.{0,20}(change|move|reschedule)\b",
    ],
    Intent.CANCEL: [
        r"\b(cancel|delete|remove).{0,20}(appointment|booking|meeting)\b",
        r"\bdon't need.{0,20}appointment\b",
    ],
    Intent.PRICE_QUERY: [
        # English
        r"\b(how much|price|cost|fee).{0,30}(for|of|to)\b",
        r"\bwhat.{0,20}(cost|price|charge)\b",
        # Russian: "сколько стоит", "какая цена", "стоимость"
        r"\b(сколько\s+стоит|какая\s+цена|какова\s+стоимость|цена|стоимость)\b",
        # Spanish
        r"\b(cuánto cuesta|precio|costo)\b",
    ],
    Intent.FAQ_QUERY: [
        # English - question patterns with specific topic words
        r"\b(what|how|when|where).{0,30}(hours|location|address|policy|insurance|procedure)\b",
        r"\bdo you (offer|provide|have|accept).{0,30}\b",
        r"\b(tell me|explain|information).{0,30}(about|regarding|on)\b",

        # Spanish - preguntas informacionales
        r"\b(qué|cómo|cuándo|dónde).{0,30}(horario|ubicación|política|seguro|procedimiento)\b",
        r"\b(tienen|ofrecen|aceptan).{0,30}\b",
        r"\b(información|detalles).{0,30}(sobre|acerca de)\b",

        # Russian - информационные вопросы
        r"\b(что|как|когда|где).{0,30}(часы|адрес|политика|страховка|процедура)\b",
        r"\b(информация|объясните).{0,30}(о|об|про)\b",
    ],
}

class IntentRouter:
    """Fast-path intent detection with 300-500ms budget"""

    def detect_intent(self, text: str, language: str = "en") -> Intent:
        """
        Detect user intent using regex patterns (no LLM)

        Args:
            text: User message text
            language: Detected language (for logging)

        Returns:
            Intent enum or Intent.UNKNOWN
        """
        # Feature flag check: Return UNKNOWN if fast-path disabled
        if not is_fast_path_enabled():
            logger.debug("Fast-path disabled via feature flag, returning UNKNOWN")
            return Intent.UNKNOWN

        if not text or len(text) < 3:
            return Intent.UNKNOWN

        text_lower = text.lower()

        # Try each pattern
        for intent, patterns in INTENT_PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, text_lower, re.IGNORECASE):
                    logger.info(f"Fast-path detected: {intent.value} (pattern: {pattern[:50]}...)")
                    return intent

        return Intent.UNKNOWN

    async def route_to_handler(
        self,
        intent: Intent,
        message: Dict[str, Any],
        context: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """
        Route detected intent to fast handler

        Returns:
            Response dict if handled, None if needs full processing
        """
        if intent == Intent.GREETING:
            return await self._handle_greeting(message, context)

        if intent == Intent.HANDOFF_HUMAN:
            return await self._handle_handoff(message, context)

        if intent == Intent.CONFIRM_TIME:
            return await self._handle_time_confirmation(message, context)

        if intent == Intent.PRICE_QUERY:
            return await self._handle_price_query(message, context)

        if intent == Intent.FAQ_QUERY:
            # No fast handler - pass to full processing (FAQ node in orchestrator)
            return None

        # Other intents can still go through full processing
        # We're just optimizing the most common ones first
        return None

    def _detect_language(self, text: str) -> str:
        """Fast language detection (no LLM)"""
        text_lower = text.lower()

        # Russian
        if any(ord(c) >= 0x0400 and ord(c) <= 0x04FF for c in text):
            return 'ru'
        # Hebrew
        if any(ord(c) >= 0x0590 and ord(c) <= 0x05FF for c in text):
            return 'he'
        # Spanish indicators
        if any(word in text_lower for word in ['hola', 'gracias', 'señor', 'está', 'qué']):
            return 'es'
        # Portuguese
        if any(word in text_lower for word in ['olá', 'obrigado', 'você', 'está']):
            return 'pt'

        # Default to English
        return 'en'

    async def _handle_greeting(
        self,
        message: Dict[str, Any],
        context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Handle greeting with localized template (NO RAG, NO LLM)

        Budget: <300ms
        """
        user_text = message.get('body', '')
        lang = self._detect_language(user_text)
        session_id = context.get('session_id')
        user_phone = message.get('from_phone', 'unknown')
        clinic_id = context.get('clinic_id', '')

        # Localized greeting templates
        greetings = {
            'en': "Hello! How can I help you today? Would you like to schedule an appointment?",
            'es': "¡Hola! ¿Cómo puedo ayudarle hoy? ¿Desea programar una cita?",
            'ru': "Здравствуйте! Как я могу помочь вам сегодня? Вы хотите записаться на прием?",
            'he': "שלום! איך אני יכול לעזור לך היום? האם תרצה לקבוע פגישה?",
            'pt': "Olá! Como posso ajudá-lo hoje? Gostaria de agendar uma consulta?"
        }

        response_text = greetings.get(lang, greetings['en'])

        # Store messages (fire-and-forget)
        import asyncio
        from app.memory.conversation_memory import get_memory_manager

        async def store_async():
            try:
                manager = get_memory_manager()
                await manager.store_message(
                    session_id=session_id,
                    role='user',
                    content=user_text,
                    phone_number=user_phone,
                    metadata={'intent': 'greeting', 'lang': lang, 'clinic_id': clinic_id}
                )
                await manager.store_message(
                    session_id=session_id,
                    role='assistant',
                    content=response_text,
                    phone_number=user_phone,
                    metadata={'fast_path': True, 'template': True, 'clinic_id': clinic_id}
                )
            except Exception as e:
                logger.error(f"Failed to store greeting messages: {e}")

        asyncio.create_task(store_async())

        logger.info(f"✅ Fast-path greeting completed in <300ms (lang: {lang})")

        return {
            'response': response_text,
            'metadata': {
                'fast_path': True,
                'intent': 'greeting',
                'language': lang,
                'template': True,
                'processing_time_ms': '<300'
            }
        }

    async def _handle_handoff(
        self,
        message: Dict[str, Any],
        context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Handle human handoff request (NO RAG, NO LLM)

        - Escalate conversation
        - Return holding message
        - Notify staff (fire-and-forget)
        """
        from app.services.escalation_handler import EscalationHandler

        session_id = context.get('session_id')
        user_phone = message.get('from_phone', 'unknown')
        clinic_id = context.get('clinic_id')

        escalation_handler = EscalationHandler()

        # Escalate (this updates DB to 'escalated' status)
        result = await escalation_handler.escalate_conversation(
            session_id=session_id,
            reason="User requested human agent (fast-path)",
            metadata={
                'intent': 'handoff_human',
                'fast_path': True,
                'user_phone': user_phone
            }
        )

        # Store the message (fire-and-forget)
        import asyncio
        from app.memory.conversation_memory import get_memory_manager

        async def store_async():
            try:
                manager = get_memory_manager()
                await manager.store_message(
                    session_id=session_id,
                    role='user',
                    content=message.get('body', ''),
                    phone_number=user_phone,
                    metadata={'intent': 'handoff_human', 'clinic_id': clinic_id}
                )
                await manager.store_message(
                    session_id=session_id,
                    role='assistant',
                    content=result['holding_message'],
                    phone_number=user_phone,
                    metadata={'escalated': True, 'fast_path': True, 'clinic_id': clinic_id}
                )
            except Exception as e:
                logger.error(f"Failed to store handoff messages: {e}")

        asyncio.create_task(store_async())

        logger.info(f"✅ Fast-path handoff completed in <500ms")

        return {
            'response': result['holding_message'],
            'metadata': {
                'fast_path': True,
                'intent': 'handoff_human',
                'escalated': True,
                'processing_time_ms': '<500'
            }
        }

    def _parse_time_from_text(self, text: str) -> Optional[tuple[int, int]]:
        """
        Extract hour and minute from time confirmation text

        Returns:
            (hour, minute) tuple or None if no time found
        """
        text_lower = text.lower()

        # Russian word-to-number mapping
        russian_numbers = {
            'девят': 9, 'десят': 10, 'одиннадцат': 11, 'двенадцат': 12,
            'один': 1, 'два': 2, 'три': 3, 'четыр': 4, 'пят': 5,
            'шест': 6, 'сем': 7, 'восем': 8
        }

        # Try to find numeric time
        time_match = re.search(r'(\d{1,2})(?::(\d{2}))?\s*(am|pm|часов|утра|вечера)?', text_lower)
        if time_match:
            hour = int(time_match.group(1))
            minute = int(time_match.group(2)) if time_match.group(2) else 0
            period = time_match.group(3)

            # Adjust for PM/evening
            if period in ['pm', 'вечера'] and hour < 12:
                hour += 12
            # Adjust for AM (but 12 AM = 0:00)
            elif period in ['am', 'утра'] and hour == 12:
                hour = 0

            return (hour, minute)

        # Try Russian word-based time
        for word, num in russian_numbers.items():
            if word in text_lower:
                return (num, 0)

        return None

    def _parse_date_from_text(self, text: str) -> Optional[str]:
        """
        Extract date reference from text (tomorrow, today, specific day)

        Returns:
            Date string (e.g., "tomorrow", "Monday") or None
        """
        text_lower = text.lower()

        # English date keywords
        if 'tomorrow' in text_lower:
            return 'tomorrow'
        if 'today' in text_lower:
            return 'today'
        if 'monday' in text_lower:
            return 'Monday'
        if 'tuesday' in text_lower:
            return 'Tuesday'
        if 'wednesday' in text_lower:
            return 'Wednesday'
        if 'thursday' in text_lower:
            return 'Thursday'
        if 'friday' in text_lower:
            return 'Friday'
        if 'saturday' in text_lower:
            return 'Saturday'
        if 'sunday' in text_lower:
            return 'Sunday'

        # Spanish
        if 'mañana' in text_lower:
            return 'mañana'
        if 'hoy' in text_lower:
            return 'hoy'
        if 'lunes' in text_lower:
            return 'lunes'
        if 'martes' in text_lower:
            return 'martes'
        if 'miércoles' in text_lower or 'miercoles' in text_lower:
            return 'miércoles'
        if 'jueves' in text_lower:
            return 'jueves'
        if 'viernes' in text_lower:
            return 'viernes'
        if 'sábado' in text_lower or 'sabado' in text_lower:
            return 'sábado'
        if 'domingo' in text_lower:
            return 'domingo'

        # Russian
        if 'завтра' in text_lower:
            return 'завтра'
        if 'сегодня' in text_lower:
            return 'сегодня'

        # Specific date pattern (DD/MM, MM/DD, YYYY-MM-DD)
        date_match = re.search(r'\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?', text_lower)
        if date_match:
            return date_match.group(0)

        return None

    async def _handle_time_confirmation(
        self,
        message: Dict[str, Any],
        context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Handle time confirmation (NO RAG, NO LLM)

        Budget: <700ms (includes DB check)

        Flow:
        1. Parse time from text
        2. Check availability in parallel with patient lookup
        3. Return template response
        4. Background: hold slot if available
        """
        import asyncio
        from app.memory.conversation_memory import get_memory_manager

        user_text = message.get('body', '')
        lang = self._detect_language(user_text)
        session_id = context.get('session_id')
        user_phone = message.get('from_phone', 'unknown')
        clinic_id = context.get('clinic_id')

        # Parse time
        time_info = self._parse_time_from_text(user_text)

        if not time_info:
            # Couldn't parse time - fall back to normal processing
            logger.warning(f"Could not parse time from: {user_text}")
            return None

        hour, minute = time_info
        logger.info(f"Parsed time: {hour:02d}:{minute:02d}")

        # CRITICAL: Check if date is mentioned in the message
        date_info = self._parse_date_from_text(user_text)

        # Templates for responses
        responses = {
            'has_date': {
                'en': f"Perfect! I'm booking you for {date_info} at {hour}:{minute:02d}. Let me check availability with the doctor you requested.",
                'es': f"¡Perfecto! Le reservo para {date_info} a las {hour}:{minute:02d}. Déjeme verificar disponibilidad con el doctor que solicitó.",
                'ru': f"Отлично! Записываю вас на {date_info} на {hour}:{minute:02d}. Проверю доступность доктора.",
                'he': f"מעולה! אני מזמין לך ל-{date_info} ב-{hour}:{minute:02d}. אבדוק זמינות עם הרופא שביקשת.",
                'pt': f"Perfeito! Estou agendando para {date_info} às {hour}:{minute:02d}. Vou verificar a disponibilidade com o médico solicitado."
            },
            'need_date': {
                'en': f"Perfect! For {hour}:{minute:02d}. What day would you like to come in?",
                'es': f"¡Perfecto! Para las {hour}:{minute:02d}. ¿Qué día le gustaría venir?",
                'ru': f"Отлично! На {hour}:{minute:02d}. На какой день вы хотите записаться?",
                'he': f"מעולה! ל-{hour}:{minute:02d}. לאיזה יום תרצה להגיע?",
                'pt': f"Perfeito! Para {hour}:{minute:02d}. Que dia você gostaria de vir?"
            },
            'unavailable': {
                'en': f"I'm sorry, {hour}:{minute:02d} isn't available. Would 10:00 AM or 2:00 PM work better?",
                'es': f"Lo siento, {hour}:{minute:02d} no está disponible. ¿Le vendría bien a las 10:00 o a las 14:00?",
                'ru': f"Извините, {hour}:{minute:02d} недоступно. Вам подойдет 10:00 или 14:00?",
                'he': f"מצטער, {hour}:{minute:02d} לא פנוי. האם 10:00 או 14:00 מתאימים יותר?",
                'pt': f"Desculpe, {hour}:{minute:02d} não está disponível. 10:00 ou 14:00 funcionaria melhor?"
            }
        }

        # If date is mentioned, acknowledge it. Otherwise ask for it.
        if date_info:
            logger.info(f"✅ Date extracted: {date_info}")
            # IMPORTANT: This needs full LLM processing to check doctor availability & complete booking
            # Return None to pass to full processing lane with context
            return None
        else:
            logger.info("⚠️ No date mentioned, asking for date")
            response_text = responses['need_date'].get(lang, responses['need_date']['en'])

        # Store messages (fire-and-forget)
        async def store_async():
            try:
                manager = get_memory_manager()
                await manager.store_message(
                    session_id=session_id,
                    role='user',
                    content=user_text,
                    phone_number=user_phone,
                    metadata={'intent': 'confirm_time', 'hour': hour, 'minute': minute}
                )
                await manager.store_message(
                    session_id=session_id,
                    role='assistant',
                    content=response_text,
                    phone_number=user_phone,
                    metadata={'fast_path': True, 'template': True}
                )
            except Exception as e:
                logger.error(f"Failed to store time confirmation messages: {e}")

        asyncio.create_task(store_async())

        logger.info(f"✅ Fast-path time confirmation completed in <700ms")

        return {
            'response': response_text,
            'metadata': {
                'fast_path': True,
                'intent': 'confirm_time',
                'language': lang,
                'parsed_time': f"{hour:02d}:{minute:02d}",
                'template': True,
                'processing_time_ms': '<700'
            }
        }

    async def _handle_price_query(
        self,
        message: Dict[str, Any],
        context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Handle price query using PriceQueryTool with Redis caching

        Budget: <300ms
        """
        import asyncio
        from app.memory.conversation_memory import get_memory_manager
        from app.tools.price_query_tool import PriceQueryTool
        from app.config import get_redis_client

        user_text = message.get('body', '')
        lang = self._detect_language(user_text)
        session_id = context.get('session_id')
        user_phone = message.get('from_phone', 'unknown')
        clinic_id = context.get('clinic_id', '')

        logger.info(f"Fast-path price query for: {user_text}")

        matched_services = []  # Initialize to avoid UnboundLocalError

        try:
            # Initialize PriceQueryTool with Redis caching
            redis_client = get_redis_client()
            price_tool = PriceQueryTool(clinic_id=clinic_id, redis_client=redis_client)

            # Smart query cleaning with multi-layer fallback
            import re

            # 1) Remove action verbs/nouns that cause false ANDs in FTS
            action_words = [
                # Russian action words
                r'установк\w*', r'постав\w*', r'сдела\w*', r'провед\w*', r'нужн\w*', r'хочу', r'хотел\w*',
                r'сколько', r'стоит', r'стоимость', r'цена', r'цен[аы]', r'на', r'за', r'какая', r'какова',
                # English action words
                r'install\w*', r'need\w*', r'want\w*', r'get\w*', r'make\w*', r'do\w*',
                r'how', r'much', r'does', r'cost', r'price\w*', r'of', r'the', r'a', r'an', r'is', r'for',
                r'what\w*', r'tell', r'me', r'about',
                # Spanish action words
                r'instalar\w*', r'necesit\w*', r'quier\w*', r'hacer\w*',
                r'cuánto', r'cuesta', r'cuestan', r'precio\w*', r'de', r'el', r'la', r'los', r'las', r'un\w*',
                r'qué', r'para',
                # Hebrew action words
                r'להתקי\w*', r'צרי\w*', r'רוצ\w*',
                r'כמה', r'עול\w*', r'מחיר\w*', r'של', r'את', r'ה\w*', r'מה'
            ]

            clean = user_text
            for pattern in action_words:
                clean = re.sub(r'\b' + pattern + r'\b', ' ', clean, flags=re.IGNORECASE)

            # Clean up multiple spaces, punctuation
            clean = re.sub(r'[?!.,:;]', ' ', clean)
            clean = ' '.join(clean.split()).strip()

            query = clean or user_text

            logger.info(f"Querying services with: '{query}'")

            # Use PriceQueryTool which handles cache → DB fallback automatically
            matched_services = await price_tool.get_services_by_query(
                query=query,
                limit=5,
                session_id=session_id
            )

            # Log the search stage if available
            if matched_services:
                search_stage = matched_services[0].get('search_stage', 'unknown')
                logger.info(f"Found {len(matched_services)} services (stage: {search_stage})")
            else:
                logger.info(f"Found 0 services")

            # Build response
            if matched_services:
                # Found specific service(s) via cache or DB
                response_parts = []
                for service in matched_services[:3]:  # Limit to 3 results
                    price = service.get('price', service.get('base_price', 0))
                    name = service.get('name', 'Unknown')
                    duration = service.get('duration_minutes', 0)

                    if lang == 'ru':
                        duration_text = f" ({duration} мин)" if duration else ""
                        response_parts.append(f"• {name}: {price}₽{duration_text}")
                    elif lang == 'es':
                        duration_text = f" ({duration} min)" if duration else ""
                        response_parts.append(f"• {name}: ${price}{duration_text}")
                    else:
                        duration_text = f" ({duration} min)" if duration else ""
                        response_parts.append(f"• {name}: ${price}{duration_text}")

                if lang == 'ru':
                    response_text = "Нашел следующие услуги:\n\n" + "\n".join(response_parts) + "\n\nХотите записаться?"
                elif lang == 'es':
                    response_text = "Encontré los siguientes servicios:\n\n" + "\n".join(response_parts) + "\n\n¿Desea hacer una cita?"
                else:
                    response_text = "I found the following services:\n\n" + "\n".join(response_parts) + "\n\nWould you like to book an appointment?"

            else:
                # No services found via FTS
                if lang == 'ru':
                    response_text = "К сожалению, я не нашел услугу по вашему запросу. Могу показать все наши услуги или уточните, пожалуйста, что именно вас интересует?"
                elif lang == 'es':
                    response_text = "Lo siento, no encontré ese servicio. ¿Puedo mostrarle todos nuestros servicios o puede especificar qué está buscando?"
                else:
                    response_text = "I couldn't find that service. Would you like to see all our services or can you specify what you're looking for?"

        except Exception as e:
            logger.error(f"Price query handler error: {e}")
            # Fallback response
            if lang == 'ru':
                response_text = "Я проверяю цены для вас. Один момент..."
            else:
                response_text = "I'm checking prices for you. One moment..."

        # Store messages (fire-and-forget)
        async def store_async():
            try:
                manager = get_memory_manager()
                await manager.store_message(
                    session_id=session_id,
                    role='user',
                    content=user_text,
                    phone_number=user_phone,
                    metadata={'intent': 'price_query', 'lang': lang, 'clinic_id': clinic_id}
                )
                await manager.store_message(
                    session_id=session_id,
                    role='assistant',
                    content=response_text,
                    phone_number=user_phone,
                    metadata={'fast_path': True, 'template': False, 'clinic_id': clinic_id}
                )
            except Exception as e:
                logger.error(f"Failed to store price query messages: {e}")

        asyncio.create_task(store_async())

        logger.info(f"✅ Fast-path price query completed in <300ms (lang: {lang})")

        return {
            'response': response_text,
            'metadata': {
                'fast_path': True,
                'intent': 'price_query',
                'language': lang,
                'matched_services': len(matched_services),
                'processing_time_ms': '<300'
            }
        }
