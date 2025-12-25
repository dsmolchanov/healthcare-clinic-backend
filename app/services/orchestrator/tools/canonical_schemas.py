"""
Canonical Tool Schemas - Single Source of Truth

These Pydantic models:
1. Define the Python function signature (what the executor calls)
2. Auto-generate OpenAI JSON schema (what the LLM sees)
3. Validate LLM outputs before execution (type safety)

Pattern from Opinion 3: "Schema-Driven Tool Calling"
- The Python function signature IS the schema
- No separate JSON definitions needed
- LLM arguments validated before hitting tool methods

Usage:
    from app.services.orchestrator.tools.canonical_schemas import (
        BookAppointmentInput,
        validate_tool_call,
        get_openai_tool_schema,
    )

    # Validate LLM output before execution
    validated = validate_tool_call("book_appointment", llm_args)
    result = await appointment_tools.book_appointment(validated)

    # Get OpenAI function schema for LLM binding
    schema = get_openai_tool_schema("book_appointment")
"""

from pydantic import BaseModel, Field, field_validator
from typing import Optional, Literal, Dict, Any, List, Type
from datetime import datetime
import re
import logging

logger = logging.getLogger(__name__)


# =============================================================================
# Appointment Tools Schemas
# =============================================================================

class CheckAvailabilityInput(BaseModel):
    """Check available appointment slots for a doctor or clinic."""

    doctor_id: Optional[str] = Field(
        default=None,
        description="Doctor UUID. If not provided, checks all available doctors."
    )
    date: Optional[str] = Field(
        default=None,
        description="Date to check in ISO format (YYYY-MM-DD) or natural language ('tomorrow', 'next Tuesday'). Defaults to today if not specified."
    )
    appointment_type: Literal[
        "consultation", "checkup", "dental_cleaning",
        "emergency", "followup", "procedure", "general"
    ] = Field(
        default="general",
        description="Type of appointment to check availability for"
    )
    duration_minutes: int = Field(
        default=30,
        ge=15,
        le=240,
        description="Expected duration in minutes (15-240)"
    )

    @field_validator('date')
    @classmethod
    def validate_date(cls, v: Optional[str]) -> Optional[str]:
        """Accept ISO dates or natural language - let semantic adapter handle parsing."""
        if v is None:
            return v
        # ISO format is passed through
        if re.match(r'^\d{4}-\d{2}-\d{2}$', v):
            return v
        # Natural language is allowed - semantic adapter will parse
        return v


class BookAppointmentInput(BaseModel):
    """
    Book an appointment - requires confirmation before execution.

    IMPORTANT: patient_id should be injected from session context, not provided by LLM.
    The semantic adapter handles this injection.
    """

    patient_id: str = Field(
        description="Patient UUID (injected from session context by semantic adapter)"
    )
    doctor_id: Optional[str] = Field(
        default=None,
        description="Doctor UUID. Can be a name like 'Dr. Maria' - semantic adapter resolves to UUID."
    )
    datetime_str: str = Field(
        description="Appointment datetime in ISO format (YYYY-MM-DDTHH:MM:SS) or natural language ('tomorrow at 2pm')"
    )
    appointment_type: Literal[
        "consultation", "checkup", "dental_cleaning",
        "emergency", "followup", "procedure", "general"
    ] = Field(
        default="general",
        description="Type of appointment"
    )
    duration_minutes: int = Field(
        default=30,
        ge=15,
        le=240,
        description="Appointment duration in minutes (15-240)"
    )
    notes: Optional[str] = Field(
        default=None,
        max_length=1000,
        description="Patient notes or special requests"
    )

    @field_validator('patient_id')
    @classmethod
    def validate_patient_id(cls, v: str) -> str:
        """Ensure patient_id is provided (semantic adapter injects this)."""
        if not v or v.strip() == '':
            raise ValueError("patient_id is required - should be injected from session context")
        return v


class CancelAppointmentInput(BaseModel):
    """Cancel an existing appointment."""

    appointment_id: str = Field(
        description="Appointment UUID to cancel"
    )
    reason: Optional[str] = Field(
        default=None,
        max_length=500,
        description="Cancellation reason"
    )

    @field_validator('appointment_id')
    @classmethod
    def validate_appointment_id(cls, v: str) -> str:
        """Validate appointment_id format."""
        if not v or v.strip() == '':
            raise ValueError("appointment_id is required")
        return v


class RescheduleAppointmentInput(BaseModel):
    """Reschedule an existing appointment to a new time."""

    appointment_id: str = Field(
        description="Appointment UUID to reschedule"
    )
    new_datetime: str = Field(
        description="New appointment datetime in ISO format or natural language"
    )
    reason: Optional[str] = Field(
        default=None,
        max_length=500,
        description="Reason for rescheduling"
    )


class GetUpcomingAppointmentsInput(BaseModel):
    """Get upcoming appointments for a patient or doctor."""

    patient_id: Optional[str] = Field(
        default=None,
        description="Filter by patient UUID"
    )
    doctor_id: Optional[str] = Field(
        default=None,
        description="Filter by doctor UUID"
    )
    days_ahead: int = Field(
        default=7,
        ge=1,
        le=90,
        description="Number of days to look ahead (1-90)"
    )


# =============================================================================
# Price Query Tool Schemas
# =============================================================================

class QueryPricesInput(BaseModel):
    """Query service prices from the clinic's catalog."""

    query: str = Field(
        min_length=1,
        max_length=200,
        description="Service name or keyword to search (e.g., 'filling', 'cleaning', 'чистка'). Supports English, Spanish, Russian."
    )
    category: Optional[str] = Field(
        default=None,
        description="Optional category filter (e.g., 'Surgery', 'Cleaning', 'Orthodontics')"
    )
    limit: int = Field(
        default=5,
        ge=1,
        le=20,
        description="Maximum number of results to return (1-20)"
    )


# =============================================================================
# FAQ Query Tool Schemas
# =============================================================================

class QueryFAQsInput(BaseModel):
    """Search frequently asked questions about the clinic."""

    query: str = Field(
        min_length=1,
        max_length=500,
        description="The user's question in natural language (e.g., 'What are your hours?', '¿Aceptan seguro?')"
    )
    language: Optional[Literal["english", "spanish", "russian", "portuguese"]] = Field(
        default=None,
        description="Language of the query. Auto-detected if not specified."
    )
    category: Optional[Literal[
        "general", "hours", "location", "insurance",
        "pricing", "services", "policies", "pre-op",
        "post-op", "cancellation", "parking", "payment"
    ]] = Field(
        default=None,
        description="Optional category to narrow search"
    )
    limit: int = Field(
        default=3,
        ge=1,
        le=5,
        description="Maximum number of FAQs to return (1-5)"
    )


# =============================================================================
# Tool Registry and Utilities
# =============================================================================

# Registry mapping tool names to their Pydantic schemas
TOOL_SCHEMAS: Dict[str, Type[BaseModel]] = {
    "check_availability": CheckAvailabilityInput,
    "book_appointment": BookAppointmentInput,
    "cancel_appointment": CancelAppointmentInput,
    "reschedule_appointment": RescheduleAppointmentInput,
    "get_upcoming_appointments": GetUpcomingAppointmentsInput,
    "query_prices": QueryPricesInput,
    "query_service_prices": QueryPricesInput,  # Alias for backwards compatibility
    "query_faqs": QueryFAQsInput,
}


def get_openai_tool_schema(tool_name: str) -> dict:
    """
    Generate OpenAI function schema from Pydantic model.

    This replaces manual JSON definitions in tool_definitions.py.
    The schema is auto-generated from the Pydantic model, ensuring
    the LLM sees exactly what the Python code expects.

    Args:
        tool_name: Name of the tool (must be in TOOL_SCHEMAS)

    Returns:
        OpenAI function calling schema dict

    Raises:
        ValueError: If tool_name is not registered

    Example:
        >>> schema = get_openai_tool_schema("book_appointment")
        >>> # Use with OpenAI/LangChain:
        >>> llm.bind_tools([schema])
    """
    schema_class = TOOL_SCHEMAS.get(tool_name)
    if not schema_class:
        raise ValueError(f"Unknown tool: {tool_name}. Available: {list(TOOL_SCHEMAS.keys())}")

    # Get JSON schema from Pydantic model
    json_schema = schema_class.model_json_schema()

    # Remove Pydantic-specific fields that OpenAI doesn't need
    json_schema.pop('title', None)

    return {
        "type": "function",
        "function": {
            "name": tool_name,
            "description": schema_class.__doc__.strip() if schema_class.__doc__ else f"Execute {tool_name}",
            "parameters": json_schema,
        }
    }


def get_all_tool_schemas() -> List[dict]:
    """
    Get OpenAI schemas for all registered tools.

    Returns:
        List of OpenAI function calling schemas

    Example:
        >>> schemas = get_all_tool_schemas()
        >>> llm_with_tools = llm.bind_tools(schemas)
    """
    # Deduplicate aliases (query_service_prices -> query_prices)
    seen = set()
    schemas = []
    for tool_name, schema_class in TOOL_SCHEMAS.items():
        if schema_class not in seen:
            schemas.append(get_openai_tool_schema(tool_name))
            seen.add(schema_class)
    return schemas


def validate_tool_call(tool_name: str, arguments: Dict[str, Any]) -> BaseModel:
    """
    Validate LLM tool call arguments against canonical schema.

    This is the key function that catches schema mismatches BEFORE
    the tool is executed, preventing runtime errors.

    Args:
        tool_name: Name of the tool being called
        arguments: Arguments from LLM tool call

    Returns:
        Validated Pydantic model instance (type-safe)

    Raises:
        ValueError: If tool_name is not registered
        ValidationError: If arguments don't match schema

    Example:
        >>> try:
        ...     validated = validate_tool_call("book_appointment", llm_args)
        ...     result = await tools.book_appointment(validated)
        ... except ValidationError as e:
        ...     # Schema mismatch - LLM provided wrong args
        ...     logger.error(f"Invalid args for book_appointment: {e}")
    """
    schema_class = TOOL_SCHEMAS.get(tool_name)
    if not schema_class:
        raise ValueError(f"Unknown tool: {tool_name}. Available: {list(TOOL_SCHEMAS.keys())}")

    # Pydantic validates and coerces types
    validated = schema_class.model_validate(arguments)

    logger.debug(f"[canonical] Validated {tool_name} args: {arguments}")
    return validated


def get_tool_description(tool_name: str) -> str:
    """
    Get human-readable description for a tool.

    Useful for building LLM prompts that describe available tools.
    """
    schema_class = TOOL_SCHEMAS.get(tool_name)
    if not schema_class:
        return f"Unknown tool: {tool_name}"

    # Get description from docstring
    description = schema_class.__doc__.strip() if schema_class.__doc__ else f"Execute {tool_name}"

    # Get required fields
    schema = schema_class.model_json_schema()
    required = schema.get('required', [])
    properties = schema.get('properties', {})

    # Build argument list
    args = []
    for name, prop in properties.items():
        is_required = name in required
        arg_type = prop.get('type', 'any')
        arg_desc = prop.get('description', '')
        marker = '*' if is_required else ''
        args.append(f"  - {name}{marker}: {arg_type} - {arg_desc}")

    return f"{description}\n\nArguments (* = required):\n" + "\n".join(args)


# =============================================================================
# Exports
# =============================================================================

__all__ = [
    # Input schemas
    'CheckAvailabilityInput',
    'BookAppointmentInput',
    'CancelAppointmentInput',
    'RescheduleAppointmentInput',
    'GetUpcomingAppointmentsInput',
    'QueryPricesInput',
    'QueryFAQsInput',
    # Registry and utilities
    'TOOL_SCHEMAS',
    'get_openai_tool_schema',
    'get_all_tool_schemas',
    'validate_tool_call',
    'get_tool_description',
]
