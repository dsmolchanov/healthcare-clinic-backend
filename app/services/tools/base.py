from abc import ABC, abstractmethod
from typing import Any, Dict, Optional, List

class ToolHandler(ABC):
    """Abstract base class for tool handlers."""

    @property
    @abstractmethod
    def tool_name(self) -> str:
        """The name of the tool this handler executes."""
        pass

    @abstractmethod
    async def execute(self, args: Dict[str, Any], context: Dict[str, Any]) -> str:
        """
        Execute the tool with the given arguments and context.
        
        Args:
            args: The arguments provided by the LLM.
            context: Additional context needed for execution (e.g., clinic_id, session_history).
            
        Returns:
            A string representation of the tool result to be passed back to the LLM.
        """
        pass
