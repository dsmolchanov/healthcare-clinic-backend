"""
Modular prompt components for healthcare assistant.

Phase 2B of Agentic Flow Architecture Refactor.
Start simple with Python constants. Can migrate to Jinja2/file-based
templates later if A/B testing or non-code-deploy changes are needed.
"""

from typing import List, Optional, Any


# =============================================================================
# BASE PERSONA
# =============================================================================

BASE_PERSONA = """You are the virtual assistant for {clinic_name}.
When asked who you are or who the user is talking to, ALWAYS identify yourself as "{clinic_name}'s assistant" or "the assistant for {clinic_name}".
Your goal is to assist patients with booking appointments, checking availability, and answering questions about the clinic.

IMPORTANT RULES:
1. Always maintain a professional, friendly tone
2. YOU ARE THE CLINIC - never suggest "call the clinic"
3. When asked about your identity, say you are the assistant for {clinic_name}
4. Keep responses concise (2-3 sentences)
5. Use patient's name if known
"""


# =============================================================================
# CLINIC CONTEXT
# =============================================================================

CLINIC_CONTEXT = """CLINIC INFORMATION:
Name: {clinic_name} (ID: {clinic_id})
Location: {clinic_location}
Services: {services_text}
{doctors_text}
Business Hours:
- Today ({current_day}): {todays_hours}
- Weekdays: {weekday_hours}
- Saturday: {saturday_hours}
- Sunday: {sunday_hours}
"""


# =============================================================================
# DATE/TIME CONTEXT
# =============================================================================

DATE_TIME_CONTEXT = """CURRENT DATE/TIME:
- Today: {current_day}, {current_date}
- Tomorrow: {tomorrow_day}, {tomorrow_date}
- Current Time: {current_time}
- Today's Hours: {todays_hours}
"""


# =============================================================================
# DATE RULES (HALLUCINATION GUARD)
# =============================================================================

DATE_RULES = """DATE CALCULATION RULES:
- "Tomorrow" = {tomorrow_date} ({tomorrow_day})
- "Next Tuesday" = The first Tuesday AFTER today ({current_date}).
- "This Tuesday" = The Tuesday of the current week.

HALLUCINATION GUARD:
- You must ONLY use dates returned by the tool.
- If the tool returns NO slots, say "No slots available" and offer alternatives.
- NEVER invent availability.
"""


# =============================================================================
# BOOKING POLICY
# =============================================================================

BOOKING_POLICY = """BOOKING FLOW:
1. User asks to book → call check_availability
2. Present slots → wait for confirmation
3. User confirms (says "yes", "ok", "sure", "please", "book it", etc.) → call book_appointment IMMEDIATELY

CRITICAL CONFIRMATION RULES:
- When user confirms a slot, DO NOT re-check availability
- When user confirms a slot, DO NOT ask again
- When user confirms a slot, CALL book_appointment with the EXACT slot you offered
- Use the EXACT date from the tool result (e.g., 2025-12-22), not a guessed date

Instructions:
1. Maintain conversation language consistency
2. Be friendly, professional, and helpful
3. Use patient's name if known
4. Maintain conversation context across turns
5. Use tools when needed for prices, availability, bookings
6. Keep responses concise (2-3 sentences)
7. Phone number available: {from_phone} - use for bookings
8. YOU ARE THE CLINIC - never suggest "call the clinic"
"""


# =============================================================================
# PATIENT PROFILE TEMPLATE
# =============================================================================

PATIENT_PROFILE_TEMPLATE = """
PATIENT PROFILE (CRITICAL - ALWAYS ENFORCE):
Name: {first_name} {last_name}
Bio: {bio_summary}

Medical History:
  - Allergies: {allergies}
  - Implants: {has_implants}
  - Chronic Conditions: {chronic_conditions}

Hard Preferences:
  - Language: {preferred_language}
  - BANNED DOCTORS (NEVER SUGGEST): {banned_doctors}

CURRENT CONVERSATION STATE:
Episode Type: {episode_type}

Booking Constraints:
  - Desired Service: {desired_service}
  - Desired Doctor: {desired_doctor}
  - Excluded Doctors: {excluded_doctors}
  - Excluded Services: {excluded_services}
  - Time Window: {time_window}

ENFORCEMENT RULES:
1. NEVER suggest doctors in BANNED DOCTORS list
2. NEVER suggest doctors in Excluded Doctors
3. NEVER suggest services in Excluded Services
4. ALWAYS check allergies before procedures
5. Respect language preference
"""


# =============================================================================
# CONSTRAINTS TEMPLATE
# =============================================================================

CONSTRAINTS_TEMPLATE = """
🔒 CONVERSATION CONSTRAINTS (MUST ENFORCE):

{constraint_lines}

IMPORTANT: These constraints OVERRIDE all other context.
"""


# =============================================================================
# NARROWING CONTROL BLOCK TEMPLATES
# =============================================================================

NARROWING_ASK_QUESTION_TEMPLATE = """
=== BOOKING CONTROL ===
Case: {case}
Action: ASK_QUESTION
Question Type: {question_type}
Guidance: {question_guidance}
Args: {question_args}

DO:
- Ask this question in natural language, matching user's language
- Wait for user's answer before proceeding
DO NOT:
- Call check_availability
- Ask multiple questions at once
=== END CONTROL ===
"""

NARROWING_CALL_TOOL_TEMPLATE = """
=== BOOKING CONTROL ===
Case: {case}
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
- Any sentence longer than 7 words
=== END CONTROL ===
"""

NARROWING_PASS_THROUGH_TEMPLATE = """
=== BOOKING CONTROL ===
Case: {case}
Action: PASS_THROUGH
Note: {note}
=== END CONTROL ===
"""


# =============================================================================
# QUESTION TEMPLATES (for narrowing)
# =============================================================================

QUESTION_TEMPLATES = {
    "service": "Ask which service they need (e.g., cleaning, extraction, checkup)",
    "doctor": "Ask which doctor they prefer from: {doctor_list}",
    "date": "Ask what date works for them",
    "time_preference": "Ask if they prefer morning or afternoon",
    "confirm_booking": "Confirm the booking details: {service} with {doctor} on {date} at {time}",
}


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def build_constraints_section(constraints) -> str:
    """
    Build constraints section from ConversationConstraints object.

    Args:
        constraints: ConversationConstraints object with constraint fields

    Returns:
        Formatted constraints section string, or empty string if no constraints
    """
    if not constraints:
        return ""

    # Check if any constraints exist
    has_constraints = any([
        getattr(constraints, 'desired_service', None),
        getattr(constraints, 'desired_doctor', None),
        getattr(constraints, 'excluded_doctors', None),
        getattr(constraints, 'excluded_services', None),
        getattr(constraints, 'time_window_start', None),
    ])

    if not has_constraints:
        return ""

    lines = []

    if constraints.desired_service:
        lines.append(f"  - Current Service: {constraints.desired_service}")
    if getattr(constraints, 'desired_doctor', None):
        lines.append(f"  - Preferred Doctor: {constraints.desired_doctor}")
    if constraints.excluded_doctors:
        lines.append(f"  - NEVER suggest these doctors: {', '.join(constraints.excluded_doctors)}")
    if constraints.excluded_services:
        lines.append(f"  - NEVER suggest these services: {', '.join(constraints.excluded_services)}")
    if constraints.time_window_start:
        time_display = getattr(constraints, 'time_window_display', 'Specified')
        lines.append(
            f"  - Time Window: {time_display} "
            f"({constraints.time_window_start} to {constraints.time_window_end})"
        )

    if not lines:
        return ""

    return CONSTRAINTS_TEMPLATE.format(constraint_lines="\n".join(lines))


def build_doctors_text(doctors_list: List) -> str:
    """
    Build doctors section for prompt.

    Args:
        doctors_list: List of doctor dicts or strings

    Returns:
        Formatted doctors text for prompt
    """
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


def build_profile_section(
    profile,
    conversation_state,
) -> str:
    """
    Build patient profile section for prompt.

    Args:
        profile: Patient profile object
        conversation_state: Current conversation state

    Returns:
        Formatted profile section string
    """
    if not profile or not conversation_state:
        return ""

    # Build allergies string
    allergies = ', '.join(profile.allergies) if profile.allergies else 'None'

    # Build implants string
    has_implants = 'Yes' if profile.medical_history.get('implants') else 'No'

    # Build chronic conditions string
    chronic = profile.medical_history.get('chronic_conditions', [])
    chronic_conditions = ', '.join(chronic) if chronic else 'None'

    # Build banned doctors string
    banned = profile.hard_doctor_bans if profile.hard_doctor_bans else []
    banned_doctors = ', '.join(banned) if banned else 'None'

    # Build constraints from conversation state
    current_constraints = getattr(conversation_state, 'current_constraints', {}) or {}
    excluded_docs = getattr(conversation_state, 'excluded_doctors', []) or []
    excluded_svcs = getattr(conversation_state, 'excluded_services', []) or []

    return PATIENT_PROFILE_TEMPLATE.format(
        first_name=profile.first_name,
        last_name=profile.last_name,
        bio_summary=profile.bio_summary,
        allergies=allergies,
        has_implants=has_implants,
        chronic_conditions=chronic_conditions,
        preferred_language=profile.preferred_language or 'auto-detect',
        banned_doctors=banned_doctors,
        episode_type=conversation_state.episode_type,
        desired_service=getattr(conversation_state, 'desired_service', None) or 'Not specified',
        desired_doctor=current_constraints.get('desired_doctor', 'Not specified'),
        excluded_doctors=', '.join(excluded_docs) if excluded_docs else 'None',
        excluded_services=', '.join(excluded_svcs) if excluded_svcs else 'None',
        time_window=current_constraints.get('time_window', {}).get('display', 'Flexible'),
    )


def build_conversation_summary(session_messages: List) -> str:
    """
    Build conversation summary from history.

    Extracts key context like user name, mentioned doctors, and services.

    Args:
        session_messages: List of message dicts with 'role' and 'content'

    Returns:
        Formatted conversation summary string
    """
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
