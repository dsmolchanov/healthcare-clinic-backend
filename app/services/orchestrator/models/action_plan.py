"""
Typed action plans for Plan-then-Execute pattern.

Per Opinion 4, Section 3.2:
"Plan-then-Execute patterns yield better predictability and security than purely reactive agents."

Usage:
    # Planner node produces plan
    plan = ActionPlan(
        goal="Book dental cleaning for patient",
        steps=[
            PlanStep(
                action=ActionType.CHECK_AVAILABILITY,
                tool_name="check_availability",
                arguments={"doctor_id": "doc123", "date": "2024-01-15"},
                description="Check doctor availability"
            ),
            PlanStep(
                action=ActionType.BOOK_APPOINTMENT,
                tool_name="book_appointment",
                arguments={"doctor_id": "doc123", "slot_id": "..."},
                requires_confirmation=True,
                description="Book the appointment"
            ),
        ],
        requires_human_confirmation=True,
        estimated_steps=2
    )

    # Executor node executes plan step by step
    result = await execute_plan(plan, tools)
"""
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from enum import Enum
from datetime import datetime, timezone


class ActionType(str, Enum):
    """Types of actions the planner can propose."""
    CHECK_AVAILABILITY = "check_availability"
    BOOK_APPOINTMENT = "book_appointment"
    CANCEL_APPOINTMENT = "cancel_appointment"
    RESCHEDULE_APPOINTMENT = "reschedule_appointment"
    GET_PATIENT_INFO = "get_patient_info"
    QUERY_PRICES = "query_prices"
    QUERY_SERVICES = "query_services"
    QUERY_DOCTORS = "query_doctors"


class PlanStep(BaseModel):
    """Single step in an action plan."""
    action: ActionType
    tool_name: str
    arguments: Dict[str, Any] = Field(default_factory=dict)
    requires_confirmation: bool = False
    description: str

    def to_execution_dict(self) -> Dict[str, Any]:
        """Convert step to execution format."""
        return {
            "action": self.action.value,
            "tool_name": self.tool_name,
            "arguments": self.arguments,
            "requires_confirmation": self.requires_confirmation,
            "description": self.description,
        }


class ActionPlan(BaseModel):
    """
    Typed plan produced by planner_agent.
    Executed step-by-step by executor_node.

    Features:
    - Goal-oriented: Clear statement of what we're trying to accomplish
    - Step-by-step: Ordered list of tool calls
    - Confirmation-aware: Tracks which steps need human approval
    - Auditable: Plan can be logged and reviewed
    """
    goal: str
    steps: List[PlanStep]
    requires_human_confirmation: bool = False
    estimated_steps: int = 0

    # Metadata for tracking
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    plan_id: Optional[str] = None

    def get_confirmation_steps(self) -> List[PlanStep]:
        """Get steps that require human confirmation."""
        return [step for step in self.steps if step.requires_confirmation]

    def has_write_operations(self) -> bool:
        """Check if plan contains write operations."""
        write_actions = {
            ActionType.BOOK_APPOINTMENT,
            ActionType.CANCEL_APPOINTMENT,
            ActionType.RESCHEDULE_APPOINTMENT,
        }
        return any(step.action in write_actions for step in self.steps)

    def to_summary(self) -> str:
        """Generate human-readable summary of the plan."""
        lines = [f"Goal: {self.goal}"]
        for i, step in enumerate(self.steps, 1):
            confirm_marker = " [requires confirmation]" if step.requires_confirmation else ""
            lines.append(f"  {i}. {step.description}{confirm_marker}")
        return "\n".join(lines)


class PlanExecutionResult(BaseModel):
    """Result of plan execution."""
    success: bool
    completed_steps: List[str] = Field(default_factory=list)
    failed_step: Optional[str] = None
    error: Optional[str] = None
    outputs: Dict[str, Any] = Field(default_factory=dict)

    # Execution metadata
    execution_time_ms: Optional[float] = None
    retries: int = 0

    def to_context(self) -> Dict[str, Any]:
        """Convert result to context dictionary for graph state."""
        return {
            "plan_success": self.success,
            "plan_completed_steps": self.completed_steps,
            "plan_failed_step": self.failed_step,
            "plan_error": self.error,
            "plan_outputs": self.outputs,
        }
