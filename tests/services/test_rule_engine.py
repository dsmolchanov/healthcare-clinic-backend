"""
Tests for Business Rules Engine
"""

import pytest
import asyncio
from datetime import datetime
from app.services.rule_engine import (
    RuleEngine,
    BusinessRule,
    RuleCondition,
    RuleAction,
    RuleType,
    RulePriority,
    RuleOperator,
    create_healthcare_rule_engine
)


class TestRuleCondition:
    """Test rule condition evaluation"""
    
    def test_equals_operator(self):
        """Test equals operator"""
        condition = RuleCondition(
            field="status",
            operator=RuleOperator.EQUALS,
            value="confirmed"
        )
        
        assert condition.evaluate({"status": "confirmed"}) == True
        assert condition.evaluate({"status": "pending"}) == False
        assert condition.evaluate({}) == False
    
    def test_not_equals_operator(self):
        """Test not equals operator"""
        condition = RuleCondition(
            field="status",
            operator=RuleOperator.NOT_EQUALS,
            value="cancelled"
        )
        
        assert condition.evaluate({"status": "confirmed"}) == True
        assert condition.evaluate({"status": "cancelled"}) == False
    
    def test_greater_than_operator(self):
        """Test greater than operator"""
        condition = RuleCondition(
            field="price",
            operator=RuleOperator.GREATER_THAN,
            value=100
        )
        
        assert condition.evaluate({"price": 150}) == True
        assert condition.evaluate({"price": 100}) == False
        assert condition.evaluate({"price": 50}) == False
    
    def test_less_than_operator(self):
        """Test less than operator"""
        condition = RuleCondition(
            field="age",
            operator=RuleOperator.LESS_THAN,
            value=18
        )
        
        assert condition.evaluate({"age": 16}) == True
        assert condition.evaluate({"age": 18}) == False
        assert condition.evaluate({"age": 25}) == False
    
    def test_between_operator(self):
        """Test between operator"""
        condition = RuleCondition(
            field="hour",
            operator=RuleOperator.BETWEEN,
            value=[9, 17]
        )
        
        assert condition.evaluate({"hour": 10}) == True
        assert condition.evaluate({"hour": 9}) == True
        assert condition.evaluate({"hour": 17}) == True
        assert condition.evaluate({"hour": 8}) == False
        assert condition.evaluate({"hour": 18}) == False
    
    def test_contains_operator(self):
        """Test contains operator"""
        condition = RuleCondition(
            field="description",
            operator=RuleOperator.CONTAINS,
            value="urgent"
        )
        
        assert condition.evaluate({"description": "This is an urgent request"}) == True
        assert condition.evaluate({"description": "Normal request"}) == False
    
    def test_in_operator(self):
        """Test in operator"""
        condition = RuleCondition(
            field="status",
            operator=RuleOperator.IN,
            value=["confirmed", "pending"]
        )
        
        assert condition.evaluate({"status": "confirmed"}) == True
        assert condition.evaluate({"status": "pending"}) == True
        assert condition.evaluate({"status": "cancelled"}) == False
    
    def test_regex_operator(self):
        """Test regex operator"""
        condition = RuleCondition(
            field="email",
            operator=RuleOperator.REGEX,
            value=r"^[\w\.-]+@[\w\.-]+\.\w+$"
        )
        
        assert condition.evaluate({"email": "test@example.com"}) == True
        assert condition.evaluate({"email": "invalid-email"}) == False
    
    def test_is_null_operator(self):
        """Test is_null operator"""
        condition = RuleCondition(
            field="notes",
            operator=RuleOperator.IS_NULL,
            value=None
        )
        
        assert condition.evaluate({"notes": None}) == True
        assert condition.evaluate({}) == True
        assert condition.evaluate({"notes": "Some notes"}) == False
    
    def test_is_not_null_operator(self):
        """Test is_not_null operator"""
        condition = RuleCondition(
            field="doctor_id",
            operator=RuleOperator.IS_NOT_NULL,
            value=None
        )
        
        assert condition.evaluate({"doctor_id": "doc123"}) == True
        assert condition.evaluate({"doctor_id": None}) == False
        assert condition.evaluate({}) == False
    
    def test_nested_field_access(self):
        """Test accessing nested fields with dot notation"""
        condition = RuleCondition(
            field="appointment.status",
            operator=RuleOperator.EQUALS,
            value="confirmed"
        )
        
        context = {
            "appointment": {
                "status": "confirmed",
                "date": "2024-01-15"
            }
        }
        
        assert condition.evaluate(context) == True
        assert condition.evaluate({"appointment": {"status": "pending"}}) == False
    
    def test_case_insensitive_comparison(self):
        """Test case-insensitive string comparison"""
        condition = RuleCondition(
            field="name",
            operator=RuleOperator.EQUALS,
            value="john doe",
            case_sensitive=False
        )
        
        assert condition.evaluate({"name": "John Doe"}) == True
        assert condition.evaluate({"name": "JOHN DOE"}) == True
        assert condition.evaluate({"name": "john doe"}) == True
        assert condition.evaluate({"name": "jane doe"}) == False


class TestRuleAction:
    """Test rule actions"""
    
    @pytest.mark.asyncio
    async def test_set_field_action(self):
        """Test setting a field value"""
        action = RuleAction(
            type="set_field",
            target="status",
            value="approved"
        )
        
        context = {"status": "pending"}
        engine = RuleEngine()
        
        result = await action.execute(context, engine)
        assert result["status"] == "approved"
    
    @pytest.mark.asyncio
    async def test_set_nested_field_action(self):
        """Test setting a nested field value"""
        action = RuleAction(
            type="set_field",
            target="appointment.status",
            value="confirmed"
        )
        
        context = {}
        engine = RuleEngine()
        
        result = await action.execute(context, engine)
        assert result["appointment"]["status"] == "confirmed"
    
    @pytest.mark.asyncio
    async def test_call_function_action(self):
        """Test calling a registered function"""
        engine = RuleEngine()
        
        # Register a test function
        async def test_function(context, params):
            return {"result": "success", "context": context}
        
        engine.register_function("test_function", test_function)
        
        action = RuleAction(
            type="call_function",
            target="test_function",
            value={"param1": "value1"}
        )
        
        context = {"data": "test"}
        result = await action.execute(context, engine)
        
        assert result["result"] == "success"
        assert result["context"] == context
    
    @pytest.mark.asyncio
    async def test_reject_action(self):
        """Test reject action that raises an error"""
        action = RuleAction(
            type="reject",
            target="",
            value="Validation failed"
        )
        
        context = {}
        engine = RuleEngine()
        
        with pytest.raises(ValueError, match="Rule violation: Validation failed"):
            await action.execute(context, engine)
    
    @pytest.mark.asyncio
    async def test_log_action(self):
        """Test log action"""
        action = RuleAction(
            type="log",
            target="",
            value="Test log message"
        )
        
        context = {}
        engine = RuleEngine()
        
        result = await action.execute(context, engine)
        assert result["logged"] == True


class TestBusinessRule:
    """Test business rule evaluation"""
    
    def test_rule_matches_all_conditions(self):
        """Test rule matching with all conditions mode"""
        rule = BusinessRule(
            id="test-001",
            name="Test Rule",
            description="Test rule description",
            type=RuleType.VALIDATION,
            conditions=[
                RuleCondition("status", RuleOperator.EQUALS, "pending"),
                RuleCondition("price", RuleOperator.GREATER_THAN, 100)
            ]
        )
        
        # Both conditions match
        assert rule.matches({"status": "pending", "price": 150}) == True
        
        # Only one condition matches
        assert rule.matches({"status": "pending", "price": 50}) == False
        assert rule.matches({"status": "confirmed", "price": 150}) == False
        
        # No conditions match
        assert rule.matches({"status": "confirmed", "price": 50}) == False
    
    def test_rule_matches_any_conditions(self):
        """Test rule matching with any conditions mode"""
        rule = BusinessRule(
            id="test-002",
            name="Test Rule",
            description="Test rule description",
            type=RuleType.VALIDATION,
            conditions=[
                RuleCondition("status", RuleOperator.EQUALS, "urgent"),
                RuleCondition("price", RuleOperator.GREATER_THAN, 500)
            ]
        )
        
        # Both conditions match
        assert rule.matches({"status": "urgent", "price": 600}, mode="any") == True
        
        # Only one condition matches
        assert rule.matches({"status": "urgent", "price": 100}, mode="any") == True
        assert rule.matches({"status": "normal", "price": 600}, mode="any") == True
        
        # No conditions match
        assert rule.matches({"status": "normal", "price": 100}, mode="any") == False
    
    def test_disabled_rule_never_matches(self):
        """Test that disabled rules never match"""
        rule = BusinessRule(
            id="test-003",
            name="Disabled Rule",
            description="This rule is disabled",
            type=RuleType.VALIDATION,
            enabled=False,
            conditions=[
                RuleCondition("status", RuleOperator.EQUALS, "test")
            ]
        )
        
        assert rule.matches({"status": "test"}) == False
    
    def test_rule_with_no_conditions_always_matches(self):
        """Test that rules with no conditions always match"""
        rule = BusinessRule(
            id="test-004",
            name="No Conditions Rule",
            description="Rule with no conditions",
            type=RuleType.WORKFLOW,
            conditions=[]
        )
        
        assert rule.matches({}) == True
        assert rule.matches({"any": "data"}) == True
    
    @pytest.mark.asyncio
    async def test_execute_actions(self):
        """Test executing rule actions"""
        rule = BusinessRule(
            id="test-005",
            name="Action Rule",
            description="Rule with actions",
            type=RuleType.WORKFLOW,
            actions=[
                RuleAction("set_field", "status", "processed"),
                RuleAction("set_field", "processed_at", "2024-01-15")
            ]
        )
        
        context = {"status": "pending"}
        engine = RuleEngine()
        
        results = await rule.execute_actions(context, engine)
        
        assert len(results) == 2
        assert context["status"] == "processed"
        assert context["processed_at"] == "2024-01-15"


class TestRuleEngine:
    """Test rule engine functionality"""
    
    def test_add_and_remove_rules(self):
        """Test adding and removing rules"""
        engine = RuleEngine()
        
        rule1 = BusinessRule(
            id="test-001",
            name="Rule 1",
            description="First rule",
            type=RuleType.VALIDATION
        )
        
        rule2 = BusinessRule(
            id="test-002",
            name="Rule 2",
            description="Second rule",
            type=RuleType.WORKFLOW
        )
        
        # Add rules
        engine.add_rule(rule1)
        engine.add_rule(rule2)
        
        assert len(engine.rules) == 2
        assert len(engine.rule_sets[RuleType.VALIDATION.value]) == 1
        assert len(engine.rule_sets[RuleType.WORKFLOW.value]) == 1
        
        # Remove a rule
        engine.remove_rule("test-001")
        
        assert len(engine.rules) == 1
        assert len(engine.rule_sets[RuleType.VALIDATION.value]) == 0
        assert len(engine.rule_sets[RuleType.WORKFLOW.value]) == 1
    
    def test_rules_sorted_by_priority(self):
        """Test that rules are sorted by priority"""
        engine = RuleEngine()
        
        low_priority = BusinessRule(
            id="low",
            name="Low Priority",
            description="",
            type=RuleType.VALIDATION,
            priority=RulePriority.LOW
        )
        
        high_priority = BusinessRule(
            id="high",
            name="High Priority",
            description="",
            type=RuleType.VALIDATION,
            priority=RulePriority.HIGH
        )
        
        critical_priority = BusinessRule(
            id="critical",
            name="Critical Priority",
            description="",
            type=RuleType.VALIDATION,
            priority=RulePriority.CRITICAL
        )
        
        # Add in random order
        engine.add_rule(low_priority)
        engine.add_rule(critical_priority)
        engine.add_rule(high_priority)
        
        # Check they are sorted by priority (highest first)
        assert engine.rules[0].id == "critical"
        assert engine.rules[1].id == "high"
        assert engine.rules[2].id == "low"
    
    @pytest.mark.asyncio
    async def test_evaluate_rules(self):
        """Test evaluating rules against context"""
        engine = RuleEngine()
        
        rule1 = BusinessRule(
            id="rule-001",
            name="Price Check",
            description="Check if price is valid",
            type=RuleType.VALIDATION,
            conditions=[
                RuleCondition("price", RuleOperator.GREATER_THAN, 0)
            ],
            actions=[
                RuleAction("set_field", "price_valid", True)
            ]
        )
        
        rule2 = BusinessRule(
            id="rule-002",
            name="Status Check",
            description="Check status",
            type=RuleType.VALIDATION,
            conditions=[
                RuleCondition("status", RuleOperator.EQUALS, "pending")
            ],
            actions=[
                RuleAction("set_field", "needs_review", True)
            ]
        )
        
        engine.add_rule(rule1)
        engine.add_rule(rule2)
        
        context = {"price": 100, "status": "pending"}
        results = await engine.evaluate(context, rule_type=RuleType.VALIDATION)
        
        assert len(results) == 2
        assert results[0]["matched"] == True
        assert results[1]["matched"] == True
        assert context["price_valid"] == True
        assert context["needs_review"] == True
    
    @pytest.mark.asyncio
    async def test_evaluate_with_stop_on_first_match(self):
        """Test stopping evaluation after first match"""
        engine = RuleEngine()
        
        rule1 = BusinessRule(
            id="rule-001",
            name="First Rule",
            description="",
            type=RuleType.VALIDATION,
            priority=RulePriority.HIGH,
            conditions=[],  # Always matches
            actions=[]
        )
        
        rule2 = BusinessRule(
            id="rule-002",
            name="Second Rule",
            description="",
            type=RuleType.VALIDATION,
            priority=RulePriority.LOW,
            conditions=[],  # Always matches
            actions=[]
        )
        
        engine.add_rule(rule1)
        engine.add_rule(rule2)
        
        results = await engine.evaluate({}, stop_on_first_match=True)
        
        assert len(results) == 1
        assert results[0]["rule_id"] == "rule-001"
    
    @pytest.mark.asyncio
    async def test_evaluate_with_tag_filter(self):
        """Test evaluating only rules with specific tags"""
        engine = RuleEngine()
        
        rule1 = BusinessRule(
            id="rule-001",
            name="Appointment Rule",
            description="",
            type=RuleType.VALIDATION,
            tags=["appointment", "scheduling"]
        )
        
        rule2 = BusinessRule(
            id="rule-002",
            name="Billing Rule",
            description="",
            type=RuleType.VALIDATION,
            tags=["billing", "payment"]
        )
        
        engine.add_rule(rule1)
        engine.add_rule(rule2)
        
        # Filter by appointment tag
        results = await engine.evaluate({}, tags=["appointment"])
        assert len(results) == 1
        assert results[0]["rule_id"] == "rule-001"
        
        # Filter by billing tag
        results = await engine.evaluate({}, tags=["billing"])
        assert len(results) == 1
        assert results[0]["rule_id"] == "rule-002"
    
    @pytest.mark.asyncio
    async def test_validate_method(self):
        """Test the validate convenience method"""
        engine = RuleEngine()
        
        # Add a rule that will fail
        rule1 = BusinessRule(
            id="rule-001",
            name="Required Field",
            description="",
            type=RuleType.VALIDATION,
            conditions=[
                RuleCondition("required_field", RuleOperator.IS_NULL, None)
            ],
            actions=[
                RuleAction("reject", "", "Required field is missing")
            ]
        )
        
        engine.add_rule(rule1)
        
        # Test validation failure
        result = await engine.validate({"other_field": "value"})
        
        assert result["valid"] == False
        assert len(result["errors"]) == 1
        assert "Required field is missing" in str(result["errors"][0])
        
        # Test validation success
        result = await engine.validate({"required_field": "present"})
        
        assert result["valid"] == True
        assert len(result["errors"]) == 0
    
    def test_export_import_rules(self):
        """Test exporting and importing rules"""
        engine1 = RuleEngine()
        
        rule = BusinessRule(
            id="test-001",
            name="Test Rule",
            description="Test description",
            type=RuleType.VALIDATION,
            priority=RulePriority.HIGH,
            conditions=[
                RuleCondition("field", RuleOperator.EQUALS, "value")
            ],
            actions=[
                RuleAction("set_field", "target", "new_value")
            ],
            tags=["test", "validation"],
            metadata={"custom": "data"}
        )
        
        engine1.add_rule(rule)
        
        # Export rules
        exported = engine1.export_rules()
        
        assert len(exported) == 1
        assert exported[0]["id"] == "test-001"
        assert exported[0]["name"] == "Test Rule"
        
        # Import into new engine
        engine2 = RuleEngine()
        engine2.import_rules(exported)
        
        assert len(engine2.rules) == 1
        imported_rule = engine2.rules[0]
        
        assert imported_rule.id == rule.id
        assert imported_rule.name == rule.name
        assert imported_rule.description == rule.description
        assert imported_rule.type == rule.type
        assert imported_rule.priority == rule.priority
        assert len(imported_rule.conditions) == 1
        assert len(imported_rule.actions) == 1
        assert imported_rule.tags == rule.tags
        assert imported_rule.metadata == rule.metadata


class TestHealthcareRuleEngine:
    """Test healthcare-specific rule engine"""
    
    @pytest.mark.asyncio
    async def test_appointment_validation(self):
        """Test appointment time validation"""
        engine = create_healthcare_rule_engine()
        
        # Test valid appointment time
        context = {
            "appointment": {
                "time": "10:00",
                "start_time": "2024-01-15T10:00:00",
                "duration_minutes": 30
            },
            "action": "create_appointment"
        }
        
        results = await engine.evaluate(context, rule_type=RuleType.VALIDATION)
        
        # Should pass validation
        for result in results:
            if "error" in str(result):
                assert False, f"Validation should pass: {result}"
        
        # Test invalid appointment time (too early)
        context = {
            "appointment": {
                "time": "06:00",
                "start_time": "2024-01-15T06:00:00",
                "duration_minutes": 30
            }
        }
        
        results = await engine.evaluate(context, rule_type=RuleType.VALIDATION)
        
        # Should have validation error
        has_error = False
        for result in results:
            if "results" in result:
                for action_result in result["results"]:
                    if isinstance(action_result, dict) and "error" in action_result:
                        has_error = True
                        assert "between 8 AM and 6 PM" in action_result["error"]
        
        assert has_error, "Should have validation error for early appointment"
    
    @pytest.mark.asyncio
    async def test_price_calculation(self):
        """Test dynamic price calculation"""
        engine = create_healthcare_rule_engine()
        
        context = {
            "service": {
                "base_price": 100
            },
            "is_new_patient": True,
            "insurance_coverage": 0.2
        }
        
        results = await engine.evaluate(context, rule_type=RuleType.CALCULATION)
        
        # Find the price calculation result
        for result in results:
            if result.get("rule_name") == "Dynamic Pricing":
                for action_result in result["results"]:
                    if isinstance(action_result, dict) and "calculated_price" in action_result:
                        # Base price 100 * 1.2 (new patient) * 0.8 (20% insurance) = 96
                        assert action_result["calculated_price"] == 96.0
                        assert action_result["currency"] == "USD"
    
    @pytest.mark.asyncio
    async def test_doctor_availability_check(self):
        """Test doctor availability checking"""
        engine = create_healthcare_rule_engine()
        
        context = {
            "action": "create_appointment",
            "doctor_id": "doc-123",
            "date": "2024-01-15",
            "time": "10:00"
        }
        
        results = await engine.evaluate(context, rule_type=RuleType.CONSTRAINT)
        
        # Should check doctor availability
        for result in results:
            if result.get("rule_name") == "Prevent Double Booking":
                assert result["matched"] == True
                for action_result in result["results"]:
                    if isinstance(action_result, dict) and "available" in action_result:
                        assert action_result["available"] == True
                        assert action_result["doctor_id"] == "doc-123"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])