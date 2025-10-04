"""
OpenAI Function/Tool Definitions for Agent Orchestrator

This module defines the function schemas for OpenAI function calling
that the orchestrator agent can use.
"""

# Price Query Tool Definition for OpenAI Function Calling
PRICE_QUERY_TOOL = {
    "type": "function",
    "function": {
        "name": "query_service_prices",
        "description": "Query service prices from the clinic's services database. Use this when users ask about prices, costs, or fees for dental/medical services. Can search by service name (e.g., 'filling', 'cleaning', 'whitening') or category.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The service name or keyword to search for (e.g., 'filling', 'cleaning', 'consultation'). Can be in any language (English, Russian, Spanish, etc.)"
                },
                "category": {
                    "type": "string",
                    "description": "Optional: filter by service category (e.g., 'Surgery', 'Cleaning', 'Orthodontics')"
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of services to return (default: 5)",
                    "default": 5
                }
            },
            "required": ["query"]
        }
    }
}

# Appointment Booking Tool Definition
APPOINTMENT_BOOKING_TOOL = {
    "type": "function",
    "function": {
        "name": "book_appointment",
        "description": "Book an appointment for a patient at the clinic. Use this when users want to schedule an appointment.",
        "parameters": {
            "type": "object",
            "properties": {
                "date": {
                    "type": "string",
                    "description": "Appointment date in ISO format (YYYY-MM-DD)"
                },
                "time": {
                    "type": "string",
                    "description": "Appointment time in HH:MM format (24-hour)"
                },
                "service_type": {
                    "type": "string",
                    "description": "Type of service requested"
                },
                "patient_notes": {
                    "type": "string",
                    "description": "Any notes or special requests from the patient"
                }
            },
            "required": ["date", "time"]
        }
    }
}

# Availability Check Tool
AVAILABILITY_CHECK_TOOL = {
    "type": "function",
    "function": {
        "name": "check_availability",
        "description": "Check available appointment slots for a specific date or date range.",
        "parameters": {
            "type": "object",
            "properties": {
                "start_date": {
                    "type": "string",
                    "description": "Start date in ISO format (YYYY-MM-DD)"
                },
                "end_date": {
                    "type": "string",
                    "description": "Optional: end date for range query"
                },
                "service_duration": {
                    "type": "integer",
                    "description": "Expected duration in minutes (helps find suitable slots)"
                }
            },
            "required": ["start_date"]
        }
    }
}

# FAQ Query Tool Definition
FAQ_QUERY_TOOL = {
    "type": "function",
    "function": {
        "name": "query_faqs",
        "description": (
            "Search frequently asked questions using full-text search. "
            "Use this for common questions about: hours, location, insurance, "
            "pricing policies, parking, services, cancellation policies, etc. "
            "This is FASTER than knowledge base search and should be tried FIRST "
            "for simple informational queries."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "The user's question in natural language. "
                        "Examples: 'What are your hours?', '¿Aceptan seguro?', "
                        "'Where is parking?', 'How much does a checkup cost?'"
                    )
                },
                "language": {
                    "type": "string",
                    "enum": ["english", "spanish", "russian", "portuguese"],
                    "description": (
                        "Language of the query. If not specified, defaults to English. "
                        "Use 'english' for English, 'spanish' for Spanish, etc."
                    )
                },
                "category": {
                    "type": "string",
                    "enum": [
                        "general", "hours", "location", "insurance",
                        "pricing", "services", "policies", "pre-op",
                        "post-op", "cancellation", "parking", "payment"
                    ],
                    "description": (
                        "Optional category to narrow search. "
                        "Auto-detected from query if not specified."
                    )
                },
                "limit": {
                    "type": "integer",
                    "default": 3,
                    "minimum": 1,
                    "maximum": 5,
                    "description": "Maximum number of FAQs to return (default: 3)"
                }
            },
            "required": ["query"]
        }
    }
}

# All available tools
ALL_TOOLS = [
    PRICE_QUERY_TOOL,
    FAQ_QUERY_TOOL,
    # APPOINTMENT_BOOKING_TOOL,  # Coming soon
    # AVAILABILITY_CHECK_TOOL,   # Coming soon
]
