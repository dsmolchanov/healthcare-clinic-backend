"""
Default starter pack of scheduling rules applied to new clinics.

Provides a baseline bundle implementing common guardrails and preferences.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_starter_pack_bundle(bundle_id: str = "starter-pack") -> Dict[str, Any]:
    """Return the default starter pack rule bundle."""
    return {
        "schema_version": "1.0.0",
        "bundle_id": bundle_id,
        "generated_at": _now_iso(),
        "description": "Baseline scheduling guardrails and preferences",
        "metadata": {"bundle_type": "starter_pack"},
        "rules": _starter_rules(),
    }


def _starter_rules() -> List[Dict[str, Any]]:
    return [
        {
            "rule_id": "HARD_WORKING_HOURS",
            "status": "active",
            "scope": {"type": "clinic"},
            "precedence": 5,
            "conditions": {
                "all": [
                    {
                        "field": "appointment.within_working_hours",
                        "operator": "equals",
                        "value": False,
                    }
                ]
            },
            "effect": {
                "type": "DENY",
                "reason_code": "OUT_OF_HOURS",
                "explain_template": "Appointment must be scheduled within clinic working hours.",
            },
            "explain_template": "Appointment must be scheduled within clinic working hours.",
            "reason_code": "OUT_OF_HOURS",
        },
        {
            "rule_id": "HARD_ESCALATE_EMERGENCY",
            "status": "active",
            "scope": {"type": "clinic"},
            "precedence": 10,
            "conditions": {
                "all": [
                    {"field": "request.is_emergency", "operator": "equals", "value": True},
                    {
                        "field": "request.human_override",
                        "operator": "not_equals",
                        "value": True,
                    },
                ]
            },
            "effect": {
                "type": "ESCALATE",
                "queue": "emergency_escalations",
                "priority": 1,
                "sla_minutes": 15,
                "reason_code": "EMERGENCY_ESCALATION",
                "explain_template": "Emergency requests require human approval.",
            },
            "explain_template": "Emergency requests require human approval.",
            "reason_code": "EMERGENCY_ESCALATION",
        },
        {
            "rule_id": "SOFT_PACK_SCHEDULE",
            "status": "active",
            "scope": {"type": "clinic"},
            "precedence": 50,
            "salience": 20,
            "conditions": {
                "any": [
                    {
                        "field": "slot.minutes_since_previous",
                        "operator": "less_or_equal",
                        "value": 15,
                    },
                    {
                        "field": "slot.minutes_until_next",
                        "operator": "less_or_equal",
                        "value": 15,
                    },
                ]
            },
            "effect": {
                "type": "ADJUST_SCORE",
                "delta": 3,
                "reason_code": "PREFER_PACKING",
                "explain_template": "Prefer contiguous appointments to reduce gaps.",
            },
            "explain_template": "Prefer contiguous appointments to reduce gaps.",
            "reason_code": "PREFER_PACKING",
        },
        {
            "rule_id": "SOFT_LEAST_BUSY",
            "status": "active",
            "scope": {"type": "clinic"},
            "precedence": 55,
            "salience": 10,
            "conditions": {
                "all": [
                    {
                        "field": "request.preferred_doctor_id",
                        "operator": "is_null",
                        "value": None,
                    },
                    {
                        "field": "doctor.is_least_busy",
                        "operator": "equals",
                        "value": True,
                    },
                ]
            },
            "effect": {
                "type": "ADJUST_SCORE",
                "delta": 4,
                "reason_code": "PREFER_LEAST_BUSY",
                "explain_template": "Prefer least-busy eligible doctor when no preference is set.",
            },
            "explain_template": "Prefer least-busy eligible doctor when no preference is set.",
            "reason_code": "PREFER_LEAST_BUSY",
        },
    ]
