"""
Policy Compiler Service
Compiles scheduling rules into optimized policy snapshots
"""

import hashlib
import json
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any
from enum import Enum
import logging

from supabase import Client

logger = logging.getLogger(__name__)


class RuleType(str, Enum):
    HARD_CONSTRAINT = "hard_constraint"
    SOFT_PREFERENCE = "soft_preference"
    MULTI_VISIT_PATTERN = "multi_visit_pattern"


class RuleScope(str, Enum):
    GLOBAL = "global"
    ORGANIZATION = "organization"
    CLINIC = "clinic"
    SERVICE = "service"
    DOCTOR = "doctor"


class PolicyStatus(str, Enum):
    DRAFT = "draft"
    STAGED = "staged"
    ACTIVE = "active"
    DEPRECATED = "deprecated"


class CompiledRule:
    def __init__(
        self,
        id: str,
        name: str,
        rule_type: RuleType,
        scope: RuleScope,
        precedence: int,
        conditions: Dict,
        actions: Dict,
        scope_id: Optional[str] = None,
        description: Optional[str] = None
    ):
        self.id = id
        self.name = name
        self.type = rule_type
        self.scope = scope
        self.scope_id = scope_id
        self.precedence = precedence
        self.conditions = conditions
        self.actions = actions
        self.description = description

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "type": self.type,
            "scope": self.scope,
            "scope_id": self.scope_id,
            "precedence": self.precedence,
            "conditions": self.conditions,
            "actions": self.actions,
            "description": self.description
        }


class VisitPattern:
    def __init__(
        self,
        id: str,
        name: str,
        visits: List[Dict],
        constraints: Dict
    ):
        self.id = id
        self.name = name
        self.visits = visits
        self.constraints = constraints

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "visits": self.visits,
            "constraints": self.constraints
        }


class PolicyCompiler:
    ENGINE_VERSION = "1.0.0"
    DB_MIGRATION_ID = "008_scheduling_rule_engine"
    
    # Precedence ranges for rule families
    PRECEDENCE_RANGES = {
        "legal_safety": (1, 999),
        "hard_constraints": (1000, 4999),
        "preferences": (5000, 9999),
        "optimizations": (10000, 10000)
    }

    def __init__(self, supabase: Client):
        self.supabase = supabase

    async def compile_policy(
        self,
        clinic_id: str,
        status: PolicyStatus = PolicyStatus.DRAFT,
        compiled_by: Optional[str] = None
    ) -> Dict:
        """
        Compile all active rules for a clinic into an optimized policy snapshot
        """
        logger.info(f"Compiling policy for clinic {clinic_id}")
        
        # Fetch rules with inheritance chain
        rules = await self._fetch_rules_with_inheritance(clinic_id)
        
        # Group rules by type
        grouped = self._group_rules_by_type(rules)
        
        # Apply inheritance and deduplication
        deduplicated = self._apply_inheritance(grouped)
        
        # Optimize rules for fast evaluation
        optimized = self._optimize_rules(deduplicated)
        
        # Build policy metadata
        metadata = self._build_metadata(optimized, compiled_by)
        
        # Calculate SHA256 hash
        policy_dict = {
            "version": await self._get_next_version(clinic_id),
            "clinic_id": clinic_id,
            "compiled_at": datetime.now(timezone.utc).isoformat(),
            "constraints": [r.to_dict() for r in optimized["constraints"]],
            "preferences": [r.to_dict() for r in optimized["preferences"]],
            "patterns": [p.to_dict() for p in optimized["patterns"]],
            "metadata": metadata
        }
        
        sha256 = self._calculate_sha256(policy_dict)
        policy_dict["sha256"] = sha256
        policy_dict["status"] = status
        
        # Save to database
        result = await self._save_policy_snapshot(clinic_id, policy_dict)
        
        logger.info(f"Policy compiled successfully: version={result['version']}, sha256={sha256[:8]}...")
        return result

    async def _fetch_rules_with_inheritance(self, clinic_id: str) -> List[Dict]:
        """
        Fetch rules following inheritance hierarchy:
        global → organization → clinic → service → doctor
        """
        # Get clinic's organization
        clinic_response = self.supabase.from_("clinics").select("organization_id").eq("id", clinic_id).execute()
        if not clinic_response.data:
            raise ValueError(f"Clinic {clinic_id} not found")
        
        org_id = clinic_response.data[0].get("organization_id")
        
        # Fetch all applicable rules - using multiple queries instead of complex OR
        # The Python client has limitations with complex OR conditions
        all_rules = []
        
        # 1. Fetch global rules
        global_rules = (
            self.supabase.from_("booking_rules")
            .select("*")
            .eq("active", True)
            .eq("scope", "global")
            .execute()
        )
        all_rules.extend(global_rules.data or [])
        
        # 2. Fetch organization rules
        if org_id:
            org_rules = (
                self.supabase.from_("booking_rules")
                .select("*")
                .eq("active", True)
                .eq("scope", "organization")
                .eq("scope_id", org_id)
                .execute()
            )
            all_rules.extend(org_rules.data or [])
        
        # 3. Fetch clinic-specific rules
        clinic_rules = (
            self.supabase.from_("booking_rules")
            .select("*")
            .eq("active", True)
            .eq("scope", "clinic")
            .eq("scope_id", clinic_id)
            .execute()
        )
        all_rules.extend(clinic_rules.data or [])
        
        # Sort by scope and precedence
        scope_order = {"global": 0, "organization": 1, "clinic": 2, "service": 3, "doctor": 4}
        all_rules.sort(key=lambda r: (scope_order.get(r.get("scope", "global"), 5), r.get("precedence", 0)))
        
        return all_rules

    def _group_rules_by_type(self, rules: List[Dict]) -> Dict:
        """Group rules by their type"""
        grouped = {
            "constraints": [],
            "preferences": [],
            "patterns": []
        }
        
        for rule in rules:
            rule_type = rule.get("rule_type", "hard_constraint")
            if rule_type == "hard_constraint":
                grouped["constraints"].append(rule)
            elif rule_type == "soft_preference":
                grouped["preferences"].append(rule)
            elif rule_type == "multi_visit_pattern":
                grouped["patterns"].append(rule)
        
        return grouped

    def _apply_inheritance(self, grouped: Dict) -> Dict:
        """
        Apply inheritance and deduplication.
        More specific rules override general ones with the same name.
        """
        def deduplicate_by_scope(rules: List[Dict]) -> List[Dict]:
            seen = {}
            for rule in rules:
                name = rule.get("rule_name")
                scope_priority = {
                    "global": 0,
                    "organization": 1,
                    "clinic": 2,
                    "service": 3,
                    "doctor": 4
                }
                priority = scope_priority.get(rule.get("scope", "global"), 0)
                
                if name not in seen or priority > seen[name]["priority"]:
                    seen[name] = {"rule": rule, "priority": priority}
            
            return [item["rule"] for item in seen.values()]
        
        return {
            "constraints": deduplicate_by_scope(grouped["constraints"]),
            "preferences": deduplicate_by_scope(grouped["preferences"]),
            "patterns": grouped["patterns"]  # Patterns don't override
        }

    def _optimize_rules(self, deduplicated: Dict) -> Dict:
        """
        Optimize rules for fast runtime evaluation
        """
        optimized = {
            "constraints": [],
            "preferences": [],
            "patterns": []
        }
        
        # Convert to compiled rules
        for rule in deduplicated["constraints"]:
            optimized["constraints"].append(CompiledRule(
                id=rule["id"],
                name=rule["rule_name"],
                rule_type=RuleType.HARD_CONSTRAINT,
                scope=RuleScope(rule["scope"]),
                scope_id=rule.get("scope_id"),
                precedence=rule["precedence"],
                conditions=rule.get("conditions", {}),
                actions=rule.get("actions", {}),
                description=rule.get("rule_description")
            ))
        
        for rule in deduplicated["preferences"]:
            optimized["preferences"].append(CompiledRule(
                id=rule["id"],
                name=rule["rule_name"],
                rule_type=RuleType.SOFT_PREFERENCE,
                scope=RuleScope(rule["scope"]),
                scope_id=rule.get("scope_id"),
                precedence=rule["precedence"],
                conditions=rule.get("conditions", {}),
                actions=rule.get("actions", {}),
                description=rule.get("rule_description")
            ))
        
        # Sort by precedence for evaluation order
        optimized["constraints"].sort(key=lambda r: r.precedence)
        optimized["preferences"].sort(key=lambda r: r.precedence)
        
        # Convert patterns
        for pattern in deduplicated["patterns"]:
            optimized["patterns"].append(VisitPattern(
                id=pattern["id"],
                name=pattern["name"],
                visits=pattern.get("visits", []),
                constraints=pattern.get("constraints", {})
            ))
        
        return optimized

    def _build_metadata(self, optimized: Dict, compiled_by: Optional[str]) -> Dict:
        """Build comprehensive metadata for the policy"""
        constraint_count = len(optimized["constraints"])
        preference_count = len(optimized["preferences"])
        pattern_count = len(optimized["patterns"])
        total_rules = constraint_count + preference_count
        
        # Estimate evaluation time based on rule count and complexity
        avg_complexity = self._calculate_average_complexity(optimized)
        estimated_eval_time = (constraint_count * 2) + (preference_count * 1) + (avg_complexity * 5)
        
        return {
            "rule_count": total_rules,
            "constraint_count": constraint_count,
            "preference_count": preference_count,
            "pattern_count": pattern_count,
            "avg_complexity": avg_complexity,
            "estimated_eval_time_ms": estimated_eval_time,
            "engine_min_version": self.ENGINE_VERSION,
            "db_migration_id": self.DB_MIGRATION_ID,
            "compiled_by": compiled_by,
            "precedence_ranges": self.PRECEDENCE_RANGES
        }

    def _calculate_average_complexity(self, optimized: Dict) -> float:
        """Calculate average rule complexity for performance estimation"""
        total_complexity = 0
        total_rules = 0
        
        for rule in optimized["constraints"] + optimized["preferences"]:
            # Count condition operators
            conditions = rule.conditions or {}
            complexity = self._count_operators(conditions)
            total_complexity += complexity
            total_rules += 1
        
        return total_complexity / max(total_rules, 1)

    def _count_operators(self, obj: Any, depth: int = 0) -> int:
        """Recursively count operators in a condition tree"""
        if depth > 10:  # Prevent infinite recursion
            return 0
            
        count = 0
        if isinstance(obj, dict):
            for key, value in obj.items():
                if key in ["and", "or", "not", "$and", "$or", "$not"]:
                    count += 1
                count += self._count_operators(value, depth + 1)
        elif isinstance(obj, list):
            for item in obj:
                count += self._count_operators(item, depth + 1)
        
        return count

    def _calculate_sha256(self, policy_dict: Dict) -> str:
        """Calculate SHA256 hash of the policy for integrity verification"""
        # Remove fields that shouldn't affect the hash
        policy_copy = policy_dict.copy()
        policy_copy.pop("sha256", None)
        policy_copy.pop("status", None)
        
        # Serialize deterministically
        json_str = json.dumps(policy_copy, sort_keys=True, default=str)
        return hashlib.sha256(json_str.encode()).hexdigest()

    async def _get_next_version(self, clinic_id: str) -> int:
        """Get the next version number for the clinic's policy"""
        response = self.supabase.from_("policy_snapshots")\
            .select("version")\
            .eq("clinic_id", clinic_id)\
            .order("version", desc=True)\
            .limit(1)\
            .execute()
        
        if response.data:
            return response.data[0]["version"] + 1
        return 1

    async def _save_policy_snapshot(self, clinic_id: str, policy: Dict) -> Dict:
        """Save the compiled policy snapshot to the database"""
        snapshot = {
            "clinic_id": clinic_id,
            "version": policy["version"],
            "sha256": policy["sha256"],
            "status": policy["status"],
            "rules": {
                "constraints": policy["constraints"],
                "preferences": policy["preferences"],
                "patterns": policy["patterns"]
            },
            "constraints": policy["constraints"],
            "preferences": policy["preferences"],
            "patterns": policy["patterns"],
            "metadata": policy["metadata"],
            "compiled_at": policy["compiled_at"],
            "compiled_by": policy["metadata"].get("compiled_by") if policy["metadata"].get("compiled_by") else None
        }
        
        response = self.supabase.from_("policy_snapshots").insert(snapshot).execute()
        
        if not response.data:
            raise RuntimeError("Failed to save policy snapshot")
        
        return response.data[0]

    async def activate_policy(self, clinic_id: str, version: int) -> bool:
        """
        Activate a specific policy version for a clinic
        """
        # Deactivate current active policy
        self.supabase.from_("policy_snapshots")\
            .update({"status": PolicyStatus.DEPRECATED, "active": False})\
            .eq("clinic_id", clinic_id)\
            .eq("status", PolicyStatus.ACTIVE)\
            .execute()
        
        # Activate the specified version
        response = self.supabase.from_("policy_snapshots")\
            .update({"status": PolicyStatus.ACTIVE, "active": True})\
            .eq("clinic_id", clinic_id)\
            .eq("version", version)\
            .execute()
        
        return bool(response.data)

    async def get_active_policy(self, clinic_id: str) -> Optional[Dict]:
        """Get the currently active policy for a clinic"""
        response = self.supabase.from_("policy_snapshots")\
            .select("*")\
            .eq("clinic_id", clinic_id)\
            .eq("status", PolicyStatus.ACTIVE)\
            .single()\
            .execute()
        
        return response.data if response.data else None