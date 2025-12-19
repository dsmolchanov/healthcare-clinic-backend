"""
LLMGenerationStep - Generate AI response using LLM with tools.

Extracted from _generate_response() method (lines 845-1487).

Phase 2A of the Agentic Flow Architecture Refactor.
"""

import os
import re
import json
import asyncio
import time
import logging
from datetime import datetime, timedelta
from typing import Tuple, Dict, Any, List, Optional

from ..base import PipelineStep
from ..context import PipelineContext
from app.domain.preferences.narrowing import NarrowingAction, NarrowingInstruction

logger = logging.getLogger(__name__)


# Question type to template mapping (LLM localizes based on user language)
QUESTION_TEMPLATES = {
    "ask_for_service": "Ask what service the user needs (e.g., cleaning, checkup, whitening)",
    "ask_for_time": "Ask what day and time works best for the user",
    "ask_for_doctor": "Ask if user prefers {doctor_names} or first available",
    "ask_time_with_doctor": "Ask when user would like to see {doctor_name}",
    "ask_time_with_service": "Ask when user would like their {service_name} appointment",
    "ask_today_or_tomorrow": "Ask if user prefers today or tomorrow (urgent case)",
    "suggest_consultation": "Explain no specialists for {service_name}, suggest general consultation",
    "ask_first_available": "Ask if user prefers {doctor_names} or first availability",
}


class LLMGenerationStep(PipelineStep):
    """
    Generate AI response using LLM with tool calling.

    Responsibilities:
    1. Build system prompt with clinic context, constraints, profile
    2. Execute LLM with tool calling support
    3. Handle multi-turn tool execution
    4. Clean response and detect language
    """

    def __init__(
        self,
        llm_factory_getter=None,
        tool_executor=None,
        constraints_manager=None,
        language_service=None
    ):
        """
        Initialize with LLM dependencies.

        Args:
            llm_factory_getter: Async function to get LLMFactory
            tool_executor: ToolExecutor for executing tool calls
            constraints_manager: ConstraintsManager for persisting tool call context
            language_service: LanguageService for response language detection
        """
        self._get_llm_factory = llm_factory_getter
        self._tool_executor = tool_executor
        self._constraints_manager = constraints_manager
        self._language_service = language_service

    @property
    def name(self) -> str:
        return "llm_generation"

    async def execute(self, ctx: PipelineContext) -> Tuple[PipelineContext, bool]:
        """
        Execute LLM generation step.

        Sets on context:
        - response
        - detected_language
        - llm_metrics
        """
        llm_start = time.time()

        try:
            # Build system prompt
            system_prompt = self._build_system_prompt(ctx)

            # Build messages
            messages = self._build_messages(system_prompt, ctx)

            # Get tool schemas
            from app.api.tool_schemas import get_tool_schemas
            from app.tools import conversation_history_tools

            # Set context for history tools
            conversation_history_tools.set_context(
                phone_number=ctx.from_phone,
                clinic_id=ctx.effective_clinic_id
            )

            tool_schemas = get_tool_schemas(ctx.effective_clinic_id)

            # Add conversation history tools
            tool_schemas.append(conversation_history_tools.get_previous_conversations_summary.tool_schema)
            tool_schemas.append(conversation_history_tools.search_detailed_conversation_history.tool_schema)

            logger.info(f"Loaded {len(tool_schemas)} tool schemas for clinic {ctx.effective_clinic_id}")

            # Execute LLM with tools
            response_text = await self._execute_llm_with_tools(
                ctx=ctx,
                messages=messages,
                tool_schemas=tool_schemas,
                llm_start=llm_start
            )

            # Clean response
            ctx.response = self._clean_llm_response(response_text, ctx.message)

            # Detect response language
            if self._language_service:
                ctx.detected_language = self._language_service.detect_sync(ctx.response)

            logger.info(f"💬 LLM response generated in {(time.time() - llm_start) * 1000:.0f}ms")

        except asyncio.TimeoutError:
            logger.error("LLM call exceeded timeout, using fallback")
            ctx.response = self._get_timeout_fallback(ctx)
            ctx.llm_metrics['error_occurred'] = True
            ctx.llm_metrics['error_message'] = 'LLM timeout'

        except Exception as e:
            logger.error(f"Error generating AI response: {e}", exc_info=True)
            ctx.response = self._get_error_fallback(ctx.detected_language)
            ctx.llm_metrics['error_occurred'] = True
            ctx.llm_metrics['error_message'] = str(e)

        return ctx, True

    def _build_system_prompt(self, ctx: PipelineContext) -> str:
        """Build the system prompt with clinic context, constraints, profile."""
        clinic_profile = ctx.clinic_profile or {}

        # Location
        location_parts = []
        if clinic_profile.get('city'):
            location_parts.append(clinic_profile['city'])
        if clinic_profile.get('state'):
            location_parts.append(clinic_profile['state'])
        if clinic_profile.get('country'):
            location_parts.append(clinic_profile['country'])
        profile_location = (
            clinic_profile.get('location')
            or ', '.join([p for p in location_parts if p])
            or clinic_profile.get('timezone')
            or 'Unknown'
        )

        # Services
        services_list = clinic_profile.get('services') or []
        services_text = ', '.join(services_list[:6]) if services_list else "Information available upon request"

        # Doctors
        doctors_list = clinic_profile.get('doctors') or []
        doctors_text = self._build_doctors_text(doctors_list)

        # Business hours
        hours = clinic_profile.get('business_hours') or clinic_profile.get('hours') or {}
        weekday_hours = hours.get('weekdays') or hours.get('monday') or "Not provided"
        saturday_hours = hours.get('saturday') or "Not provided"
        sunday_hours = hours.get('sunday') or "Not provided"

        # Current date/time
        now = datetime.now()
        current_date = now.strftime('%Y-%m-%d')
        current_day = now.strftime('%A')
        current_time = now.strftime('%H:%M')
        tomorrow = now + timedelta(days=1)
        tomorrow_date = tomorrow.strftime('%Y-%m-%d')
        tomorrow_day = tomorrow.strftime('%A')

        # Today's hours
        day_lower = current_day.lower()
        if day_lower == 'sunday':
            todays_hours = sunday_hours
        elif day_lower == 'saturday':
            todays_hours = saturday_hours
        else:
            todays_hours = weekday_hours

        # Profile section
        profile_section = self._build_profile_section(ctx)

        # Conversation summary
        conversation_summary = self._build_conversation_summary(ctx.session_messages)

        # Previous session summary
        previous_summary_section = ""
        if ctx.previous_session_summary:
            previous_summary_section = f"\n\nPREVIOUS SESSION CONTEXT:\n{ctx.previous_session_summary}\n(Use this context if relevant, but prioritize current user request)"

        # Build main prompt
        system_prompt = f"""You are a helpful AI assistant for {ctx.clinic_name}.
Your goal is to assist patients with booking appointments, checking availability, and answering questions about the clinic.

CLINIC INFORMATION:
Name: {ctx.clinic_name} (ID: {ctx.effective_clinic_id})
Location: {profile_location}
Services: {services_text}
{doctors_text}
Business Hours:
- Today ({current_day}): {todays_hours}
- Weekdays: {weekday_hours}
- Saturday: {saturday_hours}
- Sunday: {sunday_hours}

CURRENT DATE/TIME:
- Today: {current_day}, {current_date}
- Tomorrow: {tomorrow_day}, {tomorrow_date}
- Current Time: {current_time}
- Today's Hours: {todays_hours}

DATE CALCULATION RULES:
- "Tomorrow" = {tomorrow_date} ({tomorrow_day})
- "Next Tuesday" = The first Tuesday AFTER today ({current_date}).
- "This Tuesday" = The Tuesday of the current week.

HALLUCINATION GUARD:
- You must ONLY use dates returned by the tool.
- If the tool returns NO slots, say "No slots available" and offer alternatives.
- NEVER invent availability.

Instructions:
1. Maintain conversation language consistency
2. Be friendly, professional, and helpful
3. Use patient's name if known
4. Maintain conversation context across turns
5. Use tools when needed for prices, availability, bookings
6. Keep responses concise (2-3 sentences)
7. Phone number available: {ctx.from_phone} - use for bookings
8. YOU ARE THE CLINIC - never suggest "call the clinic"

BOOKING FLOW:
1. User asks to book → call check_availability
2. Present slots → wait for confirmation
3. User confirms → call book_appointment immediately

{profile_section}

{conversation_summary}

{previous_summary_section}

{ctx.additional_context}"""

        # Add constraints section
        if ctx.constraints:
            constraints_section = self._build_constraints_section(ctx.constraints)
            if constraints_section:
                system_prompt += f"\n\n{constraints_section}"

        # Add narrowing control block at the beginning (most important)
        if ctx.narrowing_instruction:
            control_block = self._build_narrowing_control_block(ctx.narrowing_instruction)
            if control_block:
                system_prompt = control_block + "\n\n" + system_prompt

        return system_prompt

    def _build_doctors_text(self, doctors_list: List) -> str:
        """Build doctors section for prompt."""
        if not doctors_list:
            return "\nCLINIC STAFF: Information available upon request via get_clinic_info tool.\n"

        doctors_text = "\nCLINIC STAFF (DOCTORS):\n"
        for doc in doctors_list:
            if isinstance(doc, dict):
                name = doc.get('name', 'Unknown')
                doc_id = doc.get('id', 'unknown')
                spec = doc.get('specialization', 'General Dentist')
                doctors_text += f"- {name} (ID: {doc_id}) - {spec}\n"
            else:
                doctors_text += f"- {doc}\n"

        return doctors_text

    def _build_profile_section(self, ctx: PipelineContext) -> str:
        """Build patient profile section for prompt."""
        profile = ctx.profile
        conversation_state = ctx.conversation_state

        if not profile or not conversation_state:
            return ""

        return f"""
PATIENT PROFILE (CRITICAL - ALWAYS ENFORCE):
Name: {profile.first_name} {profile.last_name}
Bio: {profile.bio_summary}

Medical History:
  - Allergies: {', '.join(profile.allergies) if profile.allergies else 'None'}
  - Implants: {'Yes' if profile.medical_history.get('implants') else 'No'}
  - Chronic Conditions: {', '.join(profile.medical_history.get('chronic_conditions', []))}

Hard Preferences:
  - Language: {profile.preferred_language or 'auto-detect'}
  - BANNED DOCTORS (NEVER SUGGEST): {', '.join(profile.hard_doctor_bans) if profile.hard_doctor_bans else 'None'}

CURRENT CONVERSATION STATE:
Episode Type: {conversation_state.episode_type}

Booking Constraints:
  - Desired Service: {conversation_state.desired_service or 'Not specified'}
  - Desired Doctor: {conversation_state.current_constraints.get('desired_doctor', 'Not specified')}
  - Excluded Doctors: {', '.join(conversation_state.excluded_doctors) if conversation_state.excluded_doctors else 'None'}
  - Excluded Services: {', '.join(conversation_state.excluded_services) if conversation_state.excluded_services else 'None'}
  - Time Window: {conversation_state.current_constraints.get('time_window', {}).get('display', 'Flexible')}

ENFORCEMENT RULES:
1. NEVER suggest doctors in BANNED DOCTORS list
2. NEVER suggest doctors in Excluded Doctors
3. NEVER suggest services in Excluded Services
4. ALWAYS check allergies before procedures
5. Respect language preference
"""

    def _build_conversation_summary(self, session_messages: List) -> str:
        """Build conversation summary from history."""
        if not session_messages:
            return ""

        user_name = None
        mentioned_doctors = []
        mentioned_services = []

        for msg in session_messages:
            if msg['role'] == 'user':
                content = msg['content']
                content_lower = content.lower()

                # Extract name
                if any(x in content_lower for x in ['me llamo', 'my name is', 'soy']):
                    parts = content.split()
                    for i, part in enumerate(parts):
                        if part.lower() in ['llamo', 'soy', 'is'] and i + 1 < len(parts):
                            potential = parts[i + 1].strip('.,!?')
                            if potential and len(potential) > 2:
                                user_name = potential
                                break

                # Track doctors
                if any(x in content_lower for x in ['doctor', 'доктор', 'врач', 'dr.']):
                    words = content.split()
                    for i, word in enumerate(words):
                        if word and word[0].isupper() and len(word) > 2:
                            context_words = ' '.join(words[max(0, i-2):min(len(words), i+3)]).lower()
                            if any(kw in context_words for kw in ['doctor', 'доктор', 'врач', 'dr']):
                                mentioned_doctors.append(word)

                # Track services
                if any(x in content_lower for x in ['limpieza', 'cleaning', 'чистка']):
                    mentioned_services.append('dental cleaning')
                if any(x in content_lower for x in ['cita', 'appointment', 'запись']):
                    mentioned_services.append('appointment scheduling')

        if not any([user_name, mentioned_doctors, mentioned_services]):
            return ""

        summary = "\n\nIMPORTANT CONTEXT FROM THIS CONVERSATION:\n"
        if user_name:
            summary += f"- The user's name is {user_name}. USE THEIR NAME when appropriate.\n"
        if mentioned_doctors:
            unique_doctors = list(set(mentioned_doctors))
            summary += f"- User has been asking about doctors: {', '.join(unique_doctors)}\n"
        if mentioned_services:
            summary += f"- User has expressed interest in: {', '.join(set(mentioned_services))}\n"

        return summary

    def _build_constraints_section(self, constraints) -> str:
        """Build constraints section for system prompt."""
        if not constraints:
            return ""

        if not (constraints.desired_service or constraints.desired_doctor or
                constraints.excluded_doctors or constraints.excluded_services or
                constraints.time_window_start):
            return ""

        lines = ["\n🔒 CONVERSATION CONSTRAINTS (MUST ENFORCE):\n"]

        if constraints.desired_service:
            lines.append(f"  - Current Service: {constraints.desired_service}")
        if constraints.desired_doctor:
            lines.append(f"  - Preferred Doctor: {constraints.desired_doctor}")
        if constraints.excluded_doctors:
            lines.append(f"  - NEVER suggest these doctors: {', '.join(constraints.excluded_doctors)}")
        if constraints.excluded_services:
            lines.append(f"  - NEVER suggest these services: {', '.join(constraints.excluded_services)}")
        if constraints.time_window_start:
            lines.append(
                f"  - Time Window: {constraints.time_window_display} "
                f"({constraints.time_window_start} to {constraints.time_window_end})"
            )

        lines.append("\nIMPORTANT: These constraints OVERRIDE all other context.\n")

        return "\n".join(lines)

    def _build_narrowing_control_block(self, instruction: Optional[NarrowingInstruction]) -> str:
        """Build control block for LLM based on narrowing instruction."""
        if not instruction:
            return ""

        if instruction.action == NarrowingAction.ASK_QUESTION:
            # Build question guidance from type + args
            question_type_str = instruction.question_type.value if instruction.question_type else ""
            template = QUESTION_TEMPLATES.get(question_type_str, "Ask a clarifying question")

            # Format template with args, handling missing keys gracefully
            try:
                question_guidance = template.format(**instruction.question_args)
            except KeyError:
                question_guidance = template

            return f"""
=== BOOKING CONTROL ===
Case: {instruction.case}
Action: ASK_QUESTION
Question Type: {instruction.question_type}
Guidance: {question_guidance}
Args: {instruction.question_args}

DO:
- Ask this question in natural language, matching user's language
- Wait for user's answer before proceeding
DO NOT:
- Call check_availability
- Ask multiple questions at once
=== END CONTROL ===
"""

        elif instruction.action == NarrowingAction.CALL_TOOL:
            params = instruction.tool_call.params if instruction.tool_call else {}
            return f"""
=== BOOKING CONTROL ===
Case: {instruction.case}
Action: CALL_TOOL
Tool: check_availability
Parameters: {params}

DO:
- Call check_availability with EXACTLY these parameters
- Present results naturally to user following SLOT PRESENTATION RULES below
DO NOT:
- Ask for more information first
- Modify the parameters

=== CRITICAL: SLOT RESPONSE FORMAT ===
Tool returns: "SLOT: [day] [time]"

YOUR RESPONSE MUST BE EXACTLY 5-7 WORDS. No more.
Ask a simple yes/no confirmation in the USER'S LANGUAGE.

Examples:
- SLOT: tomorrow 09:00 → "Завтра в 9 подойдёт?" (if user speaks Russian)
- SLOT: tomorrow 09:00 → "Tomorrow at 9 work?" (if user speaks English)
- SLOT: Monday 14:30 → "¿El lunes a las 2:30?" (if user speaks Spanish)

FORBIDDEN:
- "I found available slots..."
- "Here are the options..."
- "Would you like to book..."
- Any response longer than 10 words

JUST ASK: "[time] [day] ok?"
=== END CONTROL ===
"""

        return ""

    def _build_messages(self, system_prompt: str, ctx: PipelineContext) -> List[Dict]:
        """Build messages list for LLM."""
        messages = [{"role": "system", "content": system_prompt}]

        # Add conversation history
        for msg in ctx.session_messages[-12:]:
            messages.append({
                "role": msg['role'],
                "content": msg['content']
            })

        # Add current message
        messages.append({"role": "user", "content": ctx.message})

        return messages

    async def _execute_llm_with_tools(
        self,
        ctx: PipelineContext,
        messages: List[Dict],
        tool_schemas: List[Dict],
        llm_start: float
    ) -> str:
        """Execute LLM with multi-turn tool calling."""
        try:
            factory = await self._get_llm_factory()

            llm_response = await asyncio.wait_for(
                factory.generate_with_tools(
                    messages=messages,
                    tools=tool_schemas,
                    model=None,
                    temperature=1.0,
                    max_tokens=300
                ),
                timeout=20.0
            )

            # Multi-turn tool execution loop
            max_tool_turns = 5
            current_turn = 0
            prior_tool_results = {}

            # Reset tool state gate counters
            if self._tool_executor:
                self._tool_executor.state_gate.reset_turn_counters()

            current_flow_state = "idle"

            while current_turn < max_tool_turns:
                current_turn += 1

                if llm_response.tool_calls and len(llm_response.tool_calls) > 0:
                    logger.info(f"LLM requesting {len(llm_response.tool_calls)} tool call(s) (Turn {current_turn})")

                    tool_context = {
                        'clinic_id': ctx.effective_clinic_id,
                        'phone_number': ctx.from_phone,
                        'session_history': ctx.session_messages,
                        'supabase_client': None,  # Will be set by executor
                        'calendar_calls_made': 0,
                        'max_calendar_calls': 10
                    }

                    tool_results = []
                    for tool_call in llm_response.tool_calls:
                        tool_name = tool_call.name
                        tool_args = tool_call.arguments if isinstance(tool_call.arguments, dict) else json.loads(tool_call.arguments)

                        if self._tool_executor:
                            result, prior_tool_results = await self._tool_executor.execute(
                                tool_call_id=tool_call.id,
                                tool_name=tool_name,
                                tool_args=tool_args,
                                context=tool_context,
                                constraints=ctx.constraints,
                                current_state=current_flow_state,
                                tool_schemas=tool_schemas,
                                prior_tool_results=prior_tool_results
                            )
                            tool_results.append(result)

                            # Persist context from tool calls
                            if self._constraints_manager and tool_name in ['check_availability', 'book_appointment']:
                                new_service = tool_args.get('service_name')
                                new_doctor_id = tool_args.get('doctor_id')
                                if new_service or new_doctor_id:
                                    await self._constraints_manager.update_constraints(
                                        ctx.session_id,
                                        desired_service=new_service,
                                        desired_doctor=new_doctor_id
                                    )

                    # Add tool results to messages
                    messages.append({
                        "role": "assistant",
                        "content": llm_response.content or "",
                        "tool_calls": [
                            {
                                "id": tc.id,
                                "type": "function",
                                "function": {
                                    "name": tc.name,
                                    "arguments": json.dumps(tc.arguments) if isinstance(tc.arguments, dict) else tc.arguments
                                }
                            }
                            for tc in llm_response.tool_calls
                        ]
                    })

                    for tool_result in tool_results:
                        messages.append(tool_result)

                    # Get next response
                    llm_response = await factory.generate_with_tools(
                        messages=messages,
                        tools=tool_schemas,
                        model=None,
                        temperature=0.7,
                        max_tokens=300
                    )

                    if not llm_response.tool_calls:
                        break
                else:
                    break

            if current_turn >= max_tool_turns:
                logger.warning("Max tool turns reached")

            # Calculate metrics
            llm_latency_ms = int((time.time() - llm_start) * 1000)
            ctx.llm_metrics = {
                'llm_provider': llm_response.provider,
                'llm_model': llm_response.model,
                'llm_tokens_input': llm_response.usage.get('input_tokens', 0),
                'llm_tokens_output': llm_response.usage.get('output_tokens', 0),
                'llm_latency_ms': llm_latency_ms,
                'llm_cost_usd': self._calculate_cost(llm_response)
            }

            return llm_response.content or ""

        except (ValueError, RuntimeError) as factory_error:
            logger.warning(f"LLM Factory not available: {factory_error}, using direct OpenAI")
            return await self._execute_direct_openai(ctx, messages, llm_start)

    async def _execute_direct_openai(
        self,
        ctx: PipelineContext,
        messages: List[Dict],
        llm_start: float
    ) -> str:
        """Fallback to direct OpenAI when factory not available."""
        from openai import AsyncOpenAI

        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY not configured")

        client = AsyncOpenAI(api_key=api_key)
        response = await asyncio.wait_for(
            client.chat.completions.create(
                model="gpt-4o-mini",
                messages=messages,
                max_tokens=300
            ),
            timeout=10.0
        )

        llm_latency_ms = int((time.time() - llm_start) * 1000)
        ctx.llm_metrics = {
            'llm_provider': 'openai',
            'llm_model': 'gpt-4o-mini',
            'llm_tokens_input': response.usage.prompt_tokens if response.usage else 0,
            'llm_tokens_output': response.usage.completion_tokens if response.usage else 0,
            'llm_latency_ms': llm_latency_ms,
            'llm_cost_usd': 0
        }

        return response.choices[0].message.content

    def _calculate_cost(self, llm_response) -> float:
        """Calculate LLM cost from response."""
        try:
            pricing_map = {
                'glm': {'input': 0.60, 'output': 2.20},
                'google': {'input': 0.10, 'output': 0.40},
                'openai': {'input': 0.05, 'output': 0.40},
            }

            provider = llm_response.provider
            if provider not in pricing_map:
                return 0.0

            pricing = pricing_map[provider]
            input_tokens = llm_response.usage.get('input_tokens', 0)
            output_tokens = llm_response.usage.get('output_tokens', 0)

            input_cost = (input_tokens / 1_000_000) * pricing['input']
            output_cost = (output_tokens / 1_000_000) * pricing['output']

            return round(input_cost + output_cost, 6)
        except Exception:
            return 0.0

    def _clean_llm_response(self, response: str, user_message: str = "") -> str:
        """Remove <think> tags and reasoning from response."""
        if not response:
            return self._get_error_fallback('en')

        # Remove complete <think>...</think> blocks
        while re.search(r'<think>.*?</think>', response, flags=re.DOTALL):
            response = re.sub(r'<think>.*?</think>', '', response, flags=re.DOTALL)

        # Remove partial tags
        if '</think>' in response:
            parts = response.split('</think>')
            response = parts[-1]

        if '<think>' in response:
            response = response.split('<think>')[0]

        # Clean up whitespace
        response = re.sub(r'\n{3,}', '\n\n', response)
        response = response.strip()

        if not response:
            return self._get_error_fallback('en')

        return response

    def _get_timeout_fallback(self, ctx: PipelineContext) -> str:
        """Get fallback response for timeout."""
        doctors_text = self._build_doctors_text(ctx.clinic_doctors)
        user_lower = ctx.message.lower()

        is_doctor_query = any(kw in user_lower for kw in ['doctor', 'доктор', 'врач', 'médico'])

        if is_doctor_query and doctors_text:
            fallbacks = {
                'en': f"We have the following doctors:\n\n{doctors_text}",
                'ru': f"У нас работают следующие врачи:\n\n{doctors_text}",
                'es': f"Tenemos los siguientes médicos:\n\n{doctors_text}",
            }
            return fallbacks.get(ctx.detected_language, fallbacks['en'])

        return self._get_error_fallback(ctx.detected_language)

    def _get_error_fallback(self, language: str) -> str:
        """Get generic error fallback message."""
        fallbacks = {
            'en': "I understand. How can I help you today?",
            'ru': "Понимаю. Чем могу помочь?",
            'es': "Entiendo. ¿En qué puedo ayudarte?",
            'he': "אני מבין. במה אוכל לעזור?",
            'pt': "Entendo. Como posso ajudar?"
        }
        return fallbacks.get(language, fallbacks['en'])
