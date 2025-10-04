"""
Mock Grok LLM Client for Local Testing
This is a simplified version for local testing without actual Grok API
"""

from enum import Enum
from typing import Optional, Dict, Any
import logging
import random

logger = logging.getLogger(__name__)


class LLMProvider(Enum):
    """Available LLM providers"""
    GROK = "grok"
    OPENAI = "openai"
    MOCK = "mock"


class UniversalLLMClient:
    """
    Universal LLM client with primary and fallback providers
    Mock implementation for local testing
    """

    def __init__(
        self,
        primary_provider: LLMProvider = LLMProvider.MOCK,
        fallback_provider: Optional[LLMProvider] = LLMProvider.MOCK,
        ab_test_enabled: bool = False,
        grok_percentage: float = 0.3
    ):
        """
        Initialize LLM client

        Args:
            primary_provider: Primary LLM provider
            fallback_provider: Fallback provider for failures
            ab_test_enabled: Enable A/B testing
            grok_percentage: Percentage of traffic for Grok
        """
        self.primary_provider = primary_provider
        self.fallback_provider = fallback_provider
        self.ab_test_enabled = ab_test_enabled
        self.grok_percentage = grok_percentage

        logger.info(f"Initialized Universal LLM Client (Mock) - Primary: {primary_provider}")

    async def generate(self, prompt: str, **kwargs) -> str:
        """
        Generate response from LLM (mock implementation)

        Args:
            prompt: Input prompt
            **kwargs: Additional parameters

        Returns:
            Mock response for testing
        """
        logger.debug(f"Mock LLM generation for prompt: {prompt[:100]}...")

        # Mock responses based on common prompts
        if "appointment" in prompt.lower():
            return "I can help you schedule an appointment. We have availability next Tuesday at 2 PM or Thursday at 10 AM. Which would work better for you?"
        elif "insurance" in prompt.lower():
            return "I can verify your insurance coverage. Please provide your insurance provider name and member ID."
        elif "emergency" in prompt.lower():
            return "This appears to be an emergency. Please call 911 or go to your nearest emergency room immediately."
        else:
            return "I understand your inquiry. How can I assist you further with your healthcare needs?"

    async def complete(self, messages: list, **kwargs) -> Dict[str, Any]:
        """
        Complete chat conversation (mock implementation)

        Args:
            messages: Chat history
            **kwargs: Additional parameters

        Returns:
            Mock completion response
        """
        logger.debug(f"Mock chat completion with {len(messages)} messages")

        # Get last user message
        last_message = ""
        for msg in reversed(messages):
            if msg.get("role") == "user":
                last_message = msg.get("content", "")
                break

        response = await self.generate(last_message)

        return {
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": response
                }
            }],
            "provider": "mock",
            "model": "mock-model"
        }

    def get_provider(self) -> str:
        """Get current provider name"""
        if self.ab_test_enabled and random.random() < self.grok_percentage:
            return "grok"
        return str(self.primary_provider.value)


# For compatibility
GrokClient = UniversalLLMClient