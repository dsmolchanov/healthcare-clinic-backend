"""
Tool Schemas for LLM Function Calling

Converts Python tools to OpenAI-compatible function schemas for LLM tool calling.
"""

from typing import List, Dict, Any


def get_tool_schemas(clinic_id: str) -> List[Dict[str, Any]]:
    """
    Get LLM-compatible tool schemas for available tools.

    Args:
        clinic_id: Clinic ID to configure tools for

    Returns:
        List of OpenAI function calling format schemas:
        {
            "type": "function",
            "function": {
                "name": "query_service_prices",
                "description": "Get pricing information for medical/dental services",
                "parameters": {...}
            }
        }
    """
    return [
        {
            "type": "function",
            "function": {
                "name": "query_service_prices",
                "description": "Search for service prices. Supports English, Spanish, Russian, Portuguese queries.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Service name or description to search for (e.g., 'veneers', 'cleaning', 'виниры', 'limpieza')"
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Maximum number of results to return",
                            "default": 5
                        }
                    },
                    "required": ["query"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "get_clinic_info",
                "description": "Get information about the clinic (doctors, hours, location, services)",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "info_type": {
                            "type": "string",
                            "enum": ["doctors", "hours", "location", "services", "all"],
                            "description": "Type of information to retrieve"
                        }
                    },
                    "required": ["info_type"]
                }
            }
        },

        # === APPOINTMENT BOOKING TOOLS ===

        {
            "type": "function",
            "function": {
                "name": "check_availability",
                "description": "Check available appointment slots for a medical/dental service with intelligent scheduling",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "service_name": {
                            "type": "string",
                            "description": "Name or type of service (e.g., 'dental cleaning', 'consultation', 'пломба', 'limpieza'). Supports multilingual queries."
                        },
                        "preferred_date": {
                            "type": "string",
                            "description": "Preferred date in YYYY-MM-DD format or relative (e.g., 'tomorrow', 'next Monday'). Optional."
                        },
                        "time_preference": {
                            "type": "string",
                            "enum": ["morning", "afternoon", "evening"],
                            "description": "Preferred time of day. Optional."
                        },
                        "doctor_id": {
                            "type": "string",
                            "description": "Specific doctor UUID if patient requested a particular doctor. Optional."
                        },
                        "flexibility_days": {
                            "type": "integer",
                            "description": "Number of days to search if preferred date unavailable. Default: 7",
                            "default": 7
                        }
                    },
                    "required": ["service_name"]
                }
            }
        },

        {
            "type": "function",
            "function": {
                "name": "book_appointment",
                "description": "Book a medical/dental appointment for a patient. Creates reservation with automatic hold and calendar sync.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "patient_info": {
                            "type": "object",
                            "description": "Patient contact information",
                            "properties": {
                                "name": {"type": "string", "description": "Patient full name"},
                                "phone": {"type": "string", "description": "Patient phone number"},
                                "email": {"type": "string", "description": "Patient email (optional)"}
                            },
                            "required": ["name", "phone"]
                        },
                        "service_id": {
                            "type": "string",
                            "description": "UUID of the service to book (from check_availability result)"
                        },
                        "datetime_str": {
                            "type": "string",
                            "description": "Appointment datetime in ISO format (e.g., '2025-10-17T11:00:00')"
                        },
                        "doctor_id": {
                            "type": "string",
                            "description": "Doctor UUID (optional, system will assign if not provided)"
                        },
                        "notes": {
                            "type": "string",
                            "description": "Additional notes about the appointment (optional)"
                        },
                        "hold_id": {
                            "type": "string",
                            "description": "Hold ID from previous create_appointment_hold call (optional)"
                        }
                    },
                    "required": ["patient_info", "service_id", "datetime_str"]
                }
            }
        },

        {
            "type": "function",
            "function": {
                "name": "cancel_appointment",
                "description": "Cancel an existing appointment and release resources",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "appointment_id": {
                            "type": "string",
                            "description": "UUID of appointment to cancel"
                        },
                        "cancellation_reason": {
                            "type": "string",
                            "description": "Reason for cancellation"
                        },
                        "cancel_all_stages": {
                            "type": "boolean",
                            "description": "For multi-stage appointments, cancel all related appointments. Default: false",
                            "default": False
                        }
                    },
                    "required": ["appointment_id", "cancellation_reason"]
                }
            }
        },

        {
            "type": "function",
            "function": {
                "name": "reschedule_appointment",
                "description": "Reschedule an existing appointment to a new date/time",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "appointment_id": {
                            "type": "string",
                            "description": "UUID of appointment to reschedule"
                        },
                        "new_datetime": {
                            "type": "string",
                            "description": "New appointment datetime in ISO format"
                        },
                        "reschedule_reason": {
                            "type": "string",
                            "description": "Reason for rescheduling (optional)"
                        },
                        "reschedule_all_stages": {
                            "type": "boolean",
                            "description": "For multi-stage appointments, reschedule all related appointments. Default: false",
                            "default": False
                        }
                    },
                    "required": ["appointment_id", "new_datetime"]
                }
            }
        }
    ]
