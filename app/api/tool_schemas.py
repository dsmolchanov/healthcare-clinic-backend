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
        }
    ]
