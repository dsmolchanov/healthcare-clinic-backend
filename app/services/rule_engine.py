"""
Business Rules Engine for Healthcare Platform
Validates and enforces operational constraints
"""

from typing import Dict, Any, List, Optional, Callable
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from enum import Enum
import json
import re
import logging
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)


class RuleType(Enum):
    """Types of business rules"""
    VALIDATION = "validation"  # Data validation rules
    CONSTRAINT = "constraint"  # Business constraints
    WORKFLOW = "workflow"  # Workflow automation rules
    CALCULATION = "calculation"  # Computed field rules
    NOTIFICATION = "notification"  # Alert/notification rules
    SCHEDULING = "scheduling"  # Appointment scheduling rules


class RulePriority(Enum):
    """Rule execution priority"""
    CRITICAL = 100  # Must be executed first
    HIGH = 75
    MEDIUM = 50
    LOW = 25
    OPTIONAL = 0


class RuleOperator(Enum):
    """Comparison operators for conditions"""
    EQUALS = "=="
    NOT_EQUALS = "!="
    GREATER_THAN = ">"
    LESS_THAN = "<"
    GREATER_EQUAL = ">="
    LESS_EQUAL = "<="
    CONTAINS = "contains"
    NOT_CONTAINS = "not_contains"
    IN = "in"
    NOT_IN = "not_in"
    BETWEEN = "between"
    REGEX = "regex"
    IS_NULL = "is_null"
    IS_NOT_NULL = "is_not_null"


@dataclass
class RuleCondition:
    """Represents a condition in a rule"""
    field: str
    operator: RuleOperator
    value: Any
    case_sensitive: bool = True
    
    def evaluate(self, context: Dict[str, Any]) -> bool:
        """Evaluate condition against context"""
        field_value = self._get_nested_value(context, self.field)
        
        if self.operator == RuleOperator.IS_NULL:
            return field_value is None
        elif self.operator == RuleOperator.IS_NOT_NULL:
            return field_value is not None
        
        if field_value is None:
            return False
        
        # Handle string comparisons
        if isinstance(field_value, str) and not self.case_sensitive:
            field_value = field_value.lower()
            if isinstance(self.value, str):
                compare_value = self.value.lower()
            else:
                compare_value = self.value
        else:
            compare_value = self.value
        
        # Evaluate based on operator
        if self.operator == RuleOperator.EQUALS:
            return field_value == compare_value
        elif self.operator == RuleOperator.NOT_EQUALS:
            return field_value != compare_value
        elif self.operator == RuleOperator.GREATER_THAN:
            return field_value > compare_value
        elif self.operator == RuleOperator.LESS_THAN:
            return field_value < compare_value
        elif self.operator == RuleOperator.GREATER_EQUAL:
            return field_value >= compare_value
        elif self.operator == RuleOperator.LESS_EQUAL:
            return field_value <= compare_value
        elif self.operator == RuleOperator.CONTAINS:
            return compare_value in str(field_value)
        elif self.operator == RuleOperator.NOT_CONTAINS:
            return compare_value not in str(field_value)
        elif self.operator == RuleOperator.IN:
            return field_value in compare_value
        elif self.operator == RuleOperator.NOT_IN:
            return field_value not in compare_value
        elif self.operator == RuleOperator.BETWEEN:
            if isinstance(compare_value, (list, tuple)) and len(compare_value) == 2:
                return compare_value[0] <= field_value <= compare_value[1]
        elif self.operator == RuleOperator.REGEX:
            return bool(re.match(compare_value, str(field_value)))
        
        return False
    
    def _get_nested_value(self, obj: Dict, path: str) -> Any:
        """Get value from nested dictionary using dot notation"""
        keys = path.split('.')
        value = obj
        for key in keys:
            if isinstance(value, dict):
                value = value.get(key)
            else:
                return None
        return value


@dataclass
class RuleAction:
    """Represents an action to take when rule matches"""
    type: str  # set_field, call_function, send_notification, etc.
    target: str  # field name or function name
    value: Any = None  # value to set or parameters
    
    async def execute(self, context: Dict[str, Any], engine: 'RuleEngine') -> Any:
        """Execute the action"""
        if self.type == "set_field":
            # Set a field value
            self._set_nested_value(context, self.target, self.value)
            return context
        elif self.type == "call_function":
            # Call a registered function
            if hasattr(engine, 'functions') and self.target in engine.functions:
                func = engine.functions[self.target]
                return await func(context, self.value)
        elif self.type == "send_notification":
            # Send a notification
            logger.info(f"Notification: {self.value}")
            return {"notification_sent": True, "message": self.value}
        elif self.type == "reject":
            # Reject the operation
            raise ValueError(f"Rule violation: {self.value}")
        elif self.type == "log":
            # Log the event
            logger.info(f"Rule action log: {self.value}")
            return {"logged": True}
        
        return None
    
    def _set_nested_value(self, obj: Dict, path: str, value: Any):
        """Set value in nested dictionary using dot notation"""
        keys = path.split('.')
        for key in keys[:-1]:
            if key not in obj:
                obj[key] = {}
            obj = obj[key]
        obj[keys[-1]] = value


@dataclass
class BusinessRule:
    """Represents a business rule"""
    id: str
    name: str
    description: str
    type: RuleType
    priority: RulePriority = RulePriority.MEDIUM
    conditions: List[RuleCondition] = field(default_factory=list)
    actions: List[RuleAction] = field(default_factory=list)
    enabled: bool = True
    tags: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def matches(self, context: Dict[str, Any], mode: str = "all") -> bool:
        """Check if rule conditions match the context"""
        if not self.enabled:
            return False
        
        if not self.conditions:
            return True
        
        if mode == "all":
            # All conditions must match
            return all(condition.evaluate(context) for condition in self.conditions)
        elif mode == "any":
            # Any condition must match
            return any(condition.evaluate(context) for condition in self.conditions)
        else:
            raise ValueError(f"Invalid mode: {mode}")
    
    async def execute_actions(self, context: Dict[str, Any], engine: 'RuleEngine') -> List[Any]:
        """Execute all actions for this rule"""
        results = []
        for action in self.actions:
            try:
                result = await action.execute(context, engine)
                results.append(result)
            except Exception as e:
                logger.error(f"Error executing action {action.type}: {e}")
                results.append({"error": str(e)})
        return results


class RuleEngine:
    """Main rule engine that evaluates and executes business rules"""
    
    def __init__(self):
        self.rules: List[BusinessRule] = []
        self.functions: Dict[str, Callable] = {}
        self.rule_sets: Dict[str, List[BusinessRule]] = {}
        
        # Register built-in functions
        self._register_builtin_functions()
    
    def _register_builtin_functions(self):
        """Register built-in rule functions"""
        self.functions["validate_appointment"] = self._validate_appointment
        self.functions["check_doctor_availability"] = self._check_doctor_availability
        self.functions["calculate_price"] = self._calculate_price
        self.functions["check_room_conflict"] = self._check_room_conflict
        self.functions["validate_insurance"] = self._validate_insurance
    
    def add_rule(self, rule: BusinessRule):
        """Add a rule to the engine"""
        self.rules.append(rule)
        
        # Add to rule sets by type
        rule_type = rule.type.value
        if rule_type not in self.rule_sets:
            self.rule_sets[rule_type] = []
        self.rule_sets[rule_type].append(rule)
        
        # Sort rules by priority
        self.rules.sort(key=lambda r: r.priority.value, reverse=True)
        for rule_set in self.rule_sets.values():
            rule_set.sort(key=lambda r: r.priority.value, reverse=True)
    
    def remove_rule(self, rule_id: str):
        """Remove a rule by ID"""
        self.rules = [r for r in self.rules if r.id != rule_id]
        for rule_set in self.rule_sets.values():
            rule_set[:] = [r for r in rule_set if r.id != rule_id]
    
    def register_function(self, name: str, func: Callable):
        """Register a custom function for rule actions"""
        self.functions[name] = func
    
    async def evaluate(
        self, 
        context: Dict[str, Any], 
        rule_type: Optional[RuleType] = None,
        tags: Optional[List[str]] = None,
        stop_on_first_match: bool = False
    ) -> List[Dict[str, Any]]:
        """
        Evaluate rules against context and execute actions
        
        Args:
            context: Data context to evaluate rules against
            rule_type: Optional filter by rule type
            tags: Optional filter by tags
            stop_on_first_match: Stop after first matching rule
            
        Returns:
            List of results from executed rules
        """
        results = []
        
        # Get rules to evaluate
        if rule_type and rule_type.value in self.rule_sets:
            rules_to_evaluate = self.rule_sets[rule_type.value]
        else:
            rules_to_evaluate = self.rules
        
        # Filter by tags if specified
        if tags:
            rules_to_evaluate = [
                r for r in rules_to_evaluate 
                if any(tag in r.tags for tag in tags)
            ]
        
        # Evaluate rules
        for rule in rules_to_evaluate:
            try:
                if rule.matches(context):
                    logger.debug(f"Rule matched: {rule.name}")
                    action_results = await rule.execute_actions(context, self)
                    
                    results.append({
                        "rule_id": rule.id,
                        "rule_name": rule.name,
                        "matched": True,
                        "results": action_results,
                        "context": context
                    })
                    
                    if stop_on_first_match:
                        break
                        
            except Exception as e:
                logger.error(f"Error evaluating rule {rule.id}: {e}")
                results.append({
                    "rule_id": rule.id,
                    "rule_name": rule.name,
                    "error": str(e)
                })
        
        return results
    
    async def validate(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """
        Validate context against all validation rules
        
        Returns:
            Dictionary with validation results
        """
        validation_results = await self.evaluate(
            context, 
            rule_type=RuleType.VALIDATION
        )
        
        errors = []
        warnings = []
        
        for result in validation_results:
            if "error" in result:
                errors.append({
                    "rule": result["rule_name"],
                    "error": result["error"]
                })
            elif result.get("matched"):
                for action_result in result.get("results", []):
                    if isinstance(action_result, dict):
                        if "error" in action_result:
                            errors.append({
                                "rule": result["rule_name"],
                                "error": action_result["error"]
                            })
                        elif "warning" in action_result:
                            warnings.append({
                                "rule": result["rule_name"],
                                "warning": action_result["warning"]
                            })
        
        return {
            "valid": len(errors) == 0,
            "errors": errors,
            "warnings": warnings,
            "rules_evaluated": len(validation_results)
        }
    
    # Built-in rule functions
    async def _validate_appointment(self, context: Dict[str, Any], params: Any) -> Dict:
        """Validate appointment scheduling rules"""
        appointment = context.get("appointment", {})
        
        # Check appointment time constraints
        start_time = appointment.get("start_time")
        if start_time:
            hour = datetime.fromisoformat(start_time).hour
            if hour < 8 or hour >= 18:
                return {"error": "Appointments must be between 8 AM and 6 PM"}
        
        # Check minimum duration
        duration = appointment.get("duration_minutes", 0)
        if duration < 15:
            return {"error": "Appointments must be at least 15 minutes"}
        
        return {"valid": True}
    
    async def _check_doctor_availability(self, context: Dict[str, Any], params: Any) -> Dict:
        """Check if doctor is available"""
        doctor_id = context.get("doctor_id")
        date = context.get("date")
        time = context.get("time")
        
        # This would normally check against the database
        # For demo, we'll return a mock result
        return {
            "available": True,
            "doctor_id": doctor_id,
            "date": date,
            "time": time
        }
    
    async def _calculate_price(self, context: Dict[str, Any], params: Any) -> Dict:
        """Calculate price based on rules"""
        service = context.get("service", {})
        base_price = service.get("base_price", 0)
        
        # Apply discounts or surcharges
        if context.get("is_new_patient"):
            base_price *= 1.2  # 20% surcharge for new patients
        
        if context.get("insurance_coverage"):
            base_price *= (1 - context.get("insurance_coverage", 0))
        
        return {
            "calculated_price": round(base_price, 2),
            "currency": "USD"
        }
    
    async def _check_room_conflict(self, context: Dict[str, Any], params: Any) -> Dict:
        """Check for room conflicts"""
        room_id = context.get("room_id")
        date = context.get("date")
        time = context.get("time")
        
        # This would check against the database
        return {
            "conflict": False,
            "room_id": room_id
        }
    
    async def _validate_insurance(self, context: Dict[str, Any], params: Any) -> Dict:
        """Validate insurance coverage"""
        insurance = context.get("insurance", {})
        
        if not insurance.get("provider"):
            return {"error": "Insurance provider required"}
        
        if not insurance.get("policy_number"):
            return {"error": "Policy number required"}
        
        return {
            "valid": True,
            "coverage_percentage": 0.8  # Mock 80% coverage
        }
    
    def export_rules(self) -> List[Dict]:
        """Export rules as JSON-serializable format"""
        return [
            {
                "id": rule.id,
                "name": rule.name,
                "description": rule.description,
                "type": rule.type.value,
                "priority": rule.priority.value,
                "conditions": [
                    {
                        "field": cond.field,
                        "operator": cond.operator.value,
                        "value": cond.value,
                        "case_sensitive": cond.case_sensitive
                    }
                    for cond in rule.conditions
                ],
                "actions": [
                    {
                        "type": action.type,
                        "target": action.target,
                        "value": action.value
                    }
                    for action in rule.actions
                ],
                "enabled": rule.enabled,
                "tags": rule.tags,
                "metadata": rule.metadata
            }
            for rule in self.rules
        ]
    
    def import_rules(self, rules_data: List[Dict]):
        """Import rules from JSON format"""
        for rule_data in rules_data:
            conditions = [
                RuleCondition(
                    field=cond["field"],
                    operator=RuleOperator(cond["operator"]),
                    value=cond["value"],
                    case_sensitive=cond.get("case_sensitive", True)
                )
                for cond in rule_data.get("conditions", [])
            ]
            
            actions = [
                RuleAction(
                    type=action["type"],
                    target=action["target"],
                    value=action.get("value")
                )
                for action in rule_data.get("actions", [])
            ]
            
            rule = BusinessRule(
                id=rule_data["id"],
                name=rule_data["name"],
                description=rule_data.get("description", ""),
                type=RuleType(rule_data["type"]),
                priority=RulePriority(rule_data.get("priority", 50)),
                conditions=conditions,
                actions=actions,
                enabled=rule_data.get("enabled", True),
                tags=rule_data.get("tags", []),
                metadata=rule_data.get("metadata", {})
            )
            
            self.add_rule(rule)


# Factory function to create pre-configured rule engine
def create_healthcare_rule_engine() -> RuleEngine:
    """Create rule engine with healthcare-specific rules"""
    engine = RuleEngine()
    
    # Add appointment validation rule
    engine.add_rule(BusinessRule(
        id="apt-001",
        name="Appointment Time Validation",
        description="Ensure appointments are during business hours",
        type=RuleType.VALIDATION,
        priority=RulePriority.HIGH,
        conditions=[
            RuleCondition(
                field="appointment.time",
                operator=RuleOperator.IS_NOT_NULL,
                value=None
            )
        ],
        actions=[
            RuleAction(
                type="call_function",
                target="validate_appointment",
                value=None
            )
        ],
        tags=["appointment", "scheduling"]
    ))
    
    # Add double-booking prevention rule
    engine.add_rule(BusinessRule(
        id="apt-002",
        name="Prevent Double Booking",
        description="Prevent scheduling conflicts for doctors",
        type=RuleType.CONSTRAINT,
        priority=RulePriority.CRITICAL,
        conditions=[
            RuleCondition(
                field="action",
                operator=RuleOperator.EQUALS,
                value="create_appointment"
            )
        ],
        actions=[
            RuleAction(
                type="call_function",
                target="check_doctor_availability",
                value=None
            )
        ],
        tags=["appointment", "conflict"]
    ))
    
    # Add price calculation rule
    engine.add_rule(BusinessRule(
        id="price-001",
        name="Dynamic Pricing",
        description="Calculate service price based on factors",
        type=RuleType.CALCULATION,
        priority=RulePriority.MEDIUM,
        conditions=[
            RuleCondition(
                field="service.base_price",
                operator=RuleOperator.IS_NOT_NULL,
                value=None
            )
        ],
        actions=[
            RuleAction(
                type="call_function",
                target="calculate_price",
                value=None
            )
        ],
        tags=["pricing", "billing"]
    ))
    
    return engine