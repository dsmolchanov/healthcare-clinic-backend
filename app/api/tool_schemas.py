"""
Tool Schemas for LLM Function Calling

Converts Python tools to OpenAI-compatible function schemas for LLM tool calling.

x_meta contains orchestration metadata for hard enforcement:
- allowed_states: FSM states where this tool is permitted
- depends_on: Tools that must be called first
- requires_prior_result: Fields required from prior tool results
- max_calls_per_turn: Budget limit per message turn
- danger_level: low/medium/high for safety gates
- requires_confirmation: Whether user must confirm before execution

IMPORTANT: x_meta.allowed_states is the SINGLE SOURCE OF TRUTH for tool permissions.
ToolStateGate reads from here - do NOT create separate state-to-tools mappings.
"""

from typing import List, Dict, Any


def get_tool_schemas(clinic_id: str) -> List[Dict[str, Any]]:
    """
    Get LLM-compatible tool schemas for available tools.

    Args:
        clinic_id: Clinic ID to configure tools for

    Returns:
        List of OpenAI function calling format schemas with x_meta orchestration metadata.

    NOTE: x_meta.allowed_states is the SINGLE SOURCE OF TRUTH for tool permissions.
    ToolStateGate reads from here.
    """
    return [
        {
            "type": "function",
            "function": {
                "name": "query_service_prices",
                "description": "REQUIRED for ANY price question. Search clinic services and prices. Call this BEFORE answering about costs. Supports English, Spanish, Russian, Portuguese.",
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
            },
            "x_meta": {
                "category": "information",
                "priority": 5,
                "allowed_states": [
                    "idle", "greeting", "collecting_slots", "presenting_slots",
                    "awaiting_clarification", "info_seeking"
                ],
                "depends_on": [],
                "max_calls_per_turn": 3,
                "danger_level": "low",
                "version": "1.0.0",
                "requires_confirmation": False
            }
        },
        {
            "type": "function",
            "function": {
                "name": "get_clinic_info",
                "description": "Get information about the clinic (doctors, hours, location, services). Use this ONLY if the information is not already present in the conversation context.",
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
            },
            "x_meta": {
                "category": "information",
                "priority": 5,
                "allowed_states": [
                    "idle", "greeting", "collecting_slots", "presenting_slots",
                    "awaiting_clarification", "info_seeking"
                ],
                "depends_on": [],
                "max_calls_per_turn": 2,
                "danger_level": "low",
                "version": "1.0.0",
                "requires_confirmation": False
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
                            "description": "Name or type of service (e.g., 'dental cleaning', 'consultation', 'пломба', 'limpieza'). Supports multilingual queries. If user requests a specific doctor without specifying a service, use 'Consultation'."
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
                    "required": []
                }
            },
            "x_meta": {
                "category": "appointments",
                "priority": 10,
                "allowed_states": [
                    "idle", "greeting", "collecting_slots", "presenting_slots",
                    "awaiting_clarification"
                ],
                "depends_on": [],
                "max_calls_per_turn": 2,
                "danger_level": "low",
                "version": "1.0.0",
                "requires_confirmation": False
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
            },
            "x_meta": {
                "category": "appointments",
                "priority": 20,
                # AI path uses "idle" state, so we must allow it here
                # FSM path uses explicit state transitions
                "allowed_states": ["idle", "collecting_slots", "presenting_slots", "awaiting_confirmation", "booking"],
                "depends_on": ["check_availability"],
                "requires_prior_result": {
                    "check_availability": ["service_id", "datetime_str", "doctor_id"]
                },
                "max_calls_per_turn": 1,
                "danger_level": "high",
                "version": "1.0.0",
                "requires_confirmation": True
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
            },
            "x_meta": {
                "category": "appointments",
                "priority": 15,
                "allowed_states": [
                    "idle", "greeting", "collecting_slots", "presenting_slots",
                    "awaiting_clarification", "completed"
                ],
                "depends_on": [],
                "max_calls_per_turn": 1,
                "danger_level": "medium",
                "version": "1.0.0",
                "requires_confirmation": True
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
            },
            "x_meta": {
                "category": "appointments",
                "priority": 15,
                "allowed_states": [
                    "idle", "greeting", "collecting_slots", "presenting_slots",
                    "awaiting_clarification", "completed"
                ],
                "depends_on": ["check_availability"],
                "requires_prior_result": {
                    "check_availability": ["datetime_str"]
                },
                "max_calls_per_turn": 1,
                "danger_level": "medium",
                "version": "1.0.0",
                "requires_confirmation": True
            }
        }
    ]
