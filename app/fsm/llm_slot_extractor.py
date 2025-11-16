"""
LLM Slot Extractor Module

Uses LLM with function calling to extract appointment booking slots from natural language.
This is the "smart" part of the hybrid FSM - while the FSM controls flow, the LLM handles
understanding varied user input.

Key Features:
- Function calling for structured slot extraction
- Handles natural language variations ("tomorrow afternoon", "any doctor", etc.)
- Confidence scoring for each extracted slot
- Graceful degradation (returns empty dict on failure, not exceptions)
- Fast extraction using GPT-4o-mini (~500ms)

Design Philosophy:
    The LLM's job is ONLY to extract structured data, NOT to generate responses.
    Response generation remains in the FSM's hands for consistency and control.
"""

import logging
from typing import Dict, Any, List, Optional
from datetime import datetime
import json

from ..services.llm.llm_factory import get_llm_factory

logger = logging.getLogger(__name__)


class LLMSlotExtractor:
    """
    Extracts appointment booking slots using LLM function calling.

    This class replaces brittle regex patterns with intelligent NLU while
    keeping extraction focused and cost-effective.

    Example:
        >>> extractor = LLMSlotExtractor()
        >>> slots = await extractor.extract_slots(
        ...     message="Book me with any therapist tomorrow afternoon",
        ...     missing_slots=["doctor", "date", "time"],
        ...     clinic_id="clinic_123"
        ... )
        >>> print(slots)
        {
            "doctor": {"value": "терапевт", "confidence": 0.9, "type": "specialty"},
            "date": {"value": "tomorrow", "confidence": 1.0},
            "time": {"value": "afternoon", "confidence": 0.8}
        }
    """

    def __init__(self):
        """Initialize extractor with LLM factory."""
        self.llm_factory = None  # Lazy-loaded on first use
        logger.info("LLMSlotExtractor initialized")

    async def _get_llm_factory(self):
        """Lazy-load LLM factory to avoid circular imports."""
        if not self.llm_factory:
            self.llm_factory = await get_llm_factory()
        return self.llm_factory

    async def extract_slots(
        self,
        message: str,
        missing_slots: List[str],
        clinic_id: str,
        conversation_history: Optional[List[Dict[str, str]]] = None
    ) -> Dict[str, Dict[str, Any]]:
        """
        Extract appointment slots from user message using LLM.

        Args:
            message: User's message text
            missing_slots: List of slots still needed (e.g., ["doctor", "date", "time"])
            clinic_id: Clinic identifier for context
            conversation_history: Recent messages for context (optional)

        Returns:
            Dict mapping slot names to extracted values with metadata:
            {
                "doctor": {
                    "value": "Иванов" | "терапевт" | "any",
                    "confidence": 0.95,
                    "type": "name" | "specialty" | "any"
                },
                "date": {
                    "value": "завтра" | "2024-10-15" | "15.10",
                    "confidence": 0.9
                },
                "time": {
                    "value": "14:00" | "afternoon" | "morning",
                    "confidence": 0.8
                }
            }

        Example:
            >>> slots = await extractor.extract_slots(
            ...     "запишите меня к терапевту на завтра утром",
            ...     missing_slots=["doctor", "date", "time"],
            ...     clinic_id="clinic_123"
            ... )
            >>> print(slots["doctor"]["value"])  # "терапевт"
            >>> print(slots["date"]["value"])    # "завтра"
            >>> print(slots["time"]["value"])    # "утро"
        """
        try:
            factory = await self._get_llm_factory()

            # Build function schema for slot extraction
            tool_schema = self._build_extraction_schema(missing_slots)

            # Build system prompt
            system_prompt = self._build_system_prompt(missing_slots)

            # Build messages
            messages = [{"role": "system", "content": system_prompt}]

            # Add conversation history if provided (last 3 messages for context)
            if conversation_history:
                for msg in conversation_history[-3:]:
                    messages.append(msg)

            # Add current message
            messages.append({
                "role": "user",
                "content": message
            })

            # Call LLM with function calling
            logger.info(f"Extracting slots from: '{message}' (missing: {missing_slots})")

            response = await factory.generate_with_tools(
                messages=messages,
                tools=[tool_schema],
                model=None,  # Auto-select (will use GPT-4o-mini for speed)
                temperature=0.1,  # Low temperature for deterministic extraction
                max_tokens=200  # Small - we just need structured data
            )

            # Parse tool call response
            if response.tool_calls and len(response.tool_calls) > 0:
                tool_call = response.tool_calls[0]
                if tool_call.name == "extract_appointment_slots":
                    extracted_data = tool_call.arguments

                    logger.info(f"✅ LLM extracted slots: {extracted_data}")

                    # Transform to our format with metadata
                    return self._transform_extracted_data(extracted_data)

            # No tool call - LLM couldn't extract anything
            logger.warning(f"⚠️ LLM didn't extract any slots from: '{message}'")
            return {}

        except Exception as e:
            logger.error(f"❌ LLM slot extraction failed: {e}", exc_info=True)
            # Graceful degradation - return empty dict, FSM will ask for clarification
            return {}

    def _build_extraction_schema(self, missing_slots: List[str]) -> Dict[str, Any]:
        """
        Build OpenAI function schema for slot extraction.

        Args:
            missing_slots: List of slots that still need to be filled

        Returns:
            Function schema dict for OpenAI tools parameter
        """
        # Build dynamic properties based on missing slots
        properties = {}

        if "doctor" in missing_slots:
            properties["doctor"] = {
                "type": "object",
                "description": "Doctor or medical specialty mentioned by the user",
                "properties": {
                    "value": {
                        "type": "string",
                        "description": (
                            "Doctor's name (e.g., 'Иванов', 'Dr. Smith') OR "
                            "medical specialty (e.g., 'терапевт', 'therapist', 'dentist') OR "
                            "'any' if user wants any available doctor"
                        )
                    },
                    "type": {
                        "type": "string",
                        "enum": ["name", "specialty", "any"],
                        "description": "Whether the value is a doctor's name, a specialty, or 'any doctor'"
                    },
                    "confidence": {
                        "type": "number",
                        "description": "Confidence score between 0 and 1",
                        "minimum": 0,
                        "maximum": 1
                    }
                },
                "required": ["value", "type", "confidence"]
            }

        if "date" in missing_slots:
            properties["date"] = {
                "type": "object",
                "description": "Appointment date mentioned by the user",
                "properties": {
                    "value": {
                        "type": "string",
                        "description": (
                            "Date in any format: "
                            "relative ('завтра', 'tomorrow', 'сегодня', 'послезавтра'), "
                            "absolute ('15.10', '15.10.2024', '2024-10-15'), or "
                            "day of week ('понедельник', 'Monday')"
                        )
                    },
                    "confidence": {
                        "type": "number",
                        "description": "Confidence score between 0 and 1",
                        "minimum": 0,
                        "maximum": 1
                    }
                },
                "required": ["value", "confidence"]
            }

        if "time" in missing_slots:
            properties["time"] = {
                "type": "object",
                "description": "Appointment time mentioned by the user",
                "properties": {
                    "value": {
                        "type": "string",
                        "description": (
                            "Time in any format: "
                            "specific ('14:00', '2pm', '14:30'), "
                            "relative ('утро', 'morning', 'afternoon', 'вечер'), or "
                            "flexible ('any time', 'whenever')"
                        )
                    },
                    "confidence": {
                        "type": "number",
                        "description": "Confidence score between 0 and 1",
                        "minimum": 0,
                        "maximum": 1
                    }
                },
                "required": ["value", "confidence"]
            }

        return {
            "type": "function",
            "function": {
                "name": "extract_appointment_slots",
                "description": (
                    "Extract appointment booking information from the user's message. "
                    "Only extract information that is explicitly mentioned in the current message. "
                    "Do not infer or assume information that is not present."
                ),
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": []  # Nothing is strictly required - extract what's available
                }
            }
        }

    def _build_system_prompt(self, missing_slots: List[str]) -> str:
        """
        Build system prompt for slot extraction.

        Args:
            missing_slots: List of slots that still need to be filled

        Returns:
            System prompt string
        """
        slots_needed = ", ".join(missing_slots)

        return f"""You are a precise information extraction system for medical appointment booking.

Your ONLY job is to extract appointment details from the user's message using the extract_appointment_slots function.

Current missing information: {slots_needed}

EXTRACTION RULES:
1. ONLY extract information explicitly mentioned in the user's current message
2. DO NOT infer or assume information that is not stated
3. Handle both Russian and English naturally
4. Be flexible with formats:
   - Doctor: Can be a name ("Иванов"), specialty ("терапевт"), or "any"
   - Date: Can be relative ("завтра"), absolute ("15.10"), or day ("Monday")
   - Time: Can be specific ("14:00"), period ("afternoon"), or "any time"
5. Set confidence based on clarity:
   - 1.0: Explicit and unambiguous ("завтра в 14:00")
   - 0.8-0.9: Clear but could have slight ambiguity ("tomorrow afternoon")
   - 0.5-0.7: Mentioned but vague ("sometime next week")

EXAMPLES:
- "запишите меня к терапевту" → doctor: {{"value": "терапевт", "type": "specialty", "confidence": 1.0}}
- "tomorrow at 2pm" → date: {{"value": "tomorrow", "confidence": 1.0}}, time: {{"value": "14:00", "confidence": 1.0}}
- "к любому врачу" → doctor: {{"value": "any", "type": "any", "confidence": 1.0}}
- "на следующей неделе" → date: {{"value": "next week", "confidence": 0.7}}

Remember: Extract ONLY what's in the message. If nothing relevant is mentioned, call the function with no parameters."""

    def _transform_extracted_data(self, extracted_data: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
        """
        Transform LLM extraction output to our internal format.

        Args:
            extracted_data: Raw extracted data from LLM function call

        Returns:
            Transformed dict with slot metadata
        """
        result = {}

        for slot_name, slot_data in extracted_data.items():
            if isinstance(slot_data, dict) and "value" in slot_data:
                # Ensure confidence is present and valid
                confidence = slot_data.get("confidence", 0.8)
                if not (0 <= confidence <= 1):
                    confidence = 0.8

                result[slot_name] = {
                    "value": slot_data["value"],
                    "confidence": confidence
                }

                # Preserve doctor type if present
                if slot_name == "doctor" and "type" in slot_data:
                    result[slot_name]["type"] = slot_data["type"]

        return result
