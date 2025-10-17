"""
Prompts, tool specifications, and cost controls for the rule authoring assistant.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

RULE_SCHEMA_URL = "https://schemas.plaintalk.ai/healthcare/rules/rule-schema-v1.json"


def get_rule_authoring_system_prompt() -> str:
    """Return the system prompt used for the rule authoring assistant."""
    return (
        "You are the Rule Authoring Assistant for the Plaingov scheduling platform. "
        "Your job is to help clinic administrators describe regulatory and operational rules in plain language, "
        "then convert them to a machine-readable bundle matching the JSON schema at "
        f"{RULE_SCHEMA_URL}. "
        "Follow these principles:\n"
        "1. Ask clarifying questions until all required fields for a rule bundle are known.\n"
        "2. Always produce valid JSON that conforms exactly to the schema. Do not improvise field names.\n"
        "3. Provide short explanations of how each rule will behave.\n"
        "4. Suggest the starter pack defaults if the user is unsure (working hours guard, emergency escalation, "
        "schedule packing, least-busy doctor preference).\n"
        "5. Use tools to discover entity IDs, schedulable attributes, current rules, and simulation results before "
        "finalizing.\n"
        "6. Do not activate policies automatically—return the proposed bundle for review."
    )


def get_rule_authoring_tools() -> List[Dict[str, Any]]:
    """Return the tool specification list for tool-enabled LLM calls."""
    return [
        {
            "name": "get_schedulable_attributes",
            "description": (
                "Return a catalog of schedulable fields (doctor availability, room capacity, patient metadata) "
                "that can be referenced in rule conditions."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "scope": {
                        "type": "string",
                        "enum": ["clinic", "organization", "tenant"],
                        "description": "Scope for which attributes are requested."
                    }
                },
                "required": ["scope"]
            }
        },
        {
            "name": "list_entities",
            "description": (
                "List entity identifiers (clinics, providers, rooms, services) relevant to the current user context."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "entity_type": {
                        "type": "string",
                        "enum": ["clinic", "doctor", "room", "service"],
                        "description": "Type of entity to list."
                    },
                    "search": {
                        "type": "string",
                        "description": "Optional text to filter results."
                    }
                },
                "required": ["entity_type"]
            }
        },
        {
            "name": "fetch_current_rules",
            "description": (
                "Fetch currently active rule bundles for the clinic so the assistant can avoid duplicate or conflicting rules."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "clinic_id": {"type": "string", "description": "Clinic identifier."},
                    "include_drafts": {"type": "boolean", "default": False}
                },
                "required": ["clinic_id"]
            }
        },
        {
            "name": "run_simulation",
            "description": (
                "Simulate the impact of a proposed rule bundle on historical data. "
                "Use before finalizing to provide an impact summary."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "bundle": {
                        "type": "object",
                        "description": "Draft rule bundle to simulate."
                    },
                    "date_range": {
                        "type": "object",
                        "properties": {
                            "start": {"type": "string", "description": "ISO timestamp for the start of simulation."},
                            "end": {"type": "string", "description": "ISO timestamp for the end of simulation."}
                        },
                        "required": ["start", "end"]
                    }
                },
                "required": ["bundle", "date_range"]
            }
        }
    ]


@dataclass
class RuleAuthoringLLMConfig:
    """Cost and safety configuration for rule authoring conversations."""

    reasoning_model: str = "gpt-4o-mini"
    summarizer_model: str = "gpt-4o-mini"
    temperature: float = 0.2
    max_output_tokens: int = 2_000
    max_conversation_turns: int = 8
    cheap_clarification_model: str = "gpt-4.1-mini"
    high_accuracy_model: str = "gpt-4o"
    budget_usd_per_session: float = 0.75
    audit_log_enabled: bool = True
    allow_tool_usage: bool = True
    max_tool_calls: int = 6
    fallback_model: str = "gpt-4.1-mini"

    def cost_guardrails(self) -> Dict[str, Any]:
        """Return guardrail configuration for runtime cost control."""
        return {
            "max_turns": self.max_conversation_turns,
            "max_tool_calls": self.max_tool_calls,
            "budget_usd": self.budget_usd_per_session,
        }


def get_default_rule_authoring_llm_config() -> RuleAuthoringLLMConfig:
    """Return the default configuration instance."""
    return RuleAuthoringLLMConfig()


def build_rule_authoring_prompt(
    clinic_name: str,
    administrator_name: str,
    starter_pack_enabled: bool = True,
    supplements: Optional[List[str]] = None,
    additional_context: Optional[str] = None
) -> List[Dict[str, str]]:
    """
    Build a conversation primer for the rule authoring assistant.
    """
    system_prompt = get_rule_authoring_system_prompt()
    messages: List[Dict[str, str]] = [
        {"role": "system", "content": system_prompt}
    ]

    intro_lines = [
        f"You are assisting {administrator_name} from {clinic_name}.",
        "Confirm the clinic context and ask for the scheduling scenario."
    ]

    if starter_pack_enabled:
        intro_lines.append(
            "Offer to review the starter pack defaults (working hours guard, emergency escalation, "
            "schedule packing, least-busy doctor preference) before creating custom rules."
        )

    if additional_context:
        intro_lines.append(f"Additional context: {additional_context}")

    if supplements:
        intro_lines.extend(supplements)

    messages.append({"role": "assistant", "content": "\n".join(intro_lines)})
    return messages
