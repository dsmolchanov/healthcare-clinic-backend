from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, List, AsyncIterator
from pydantic import BaseModel
from enum import Enum


class LLMProvider(str, Enum):
    """Supported LLM providers"""
    GLM = "glm"
    GOOGLE = "google"
    OPENAI = "openai"
    CEREBRAS = "cerebras"


class ToolCall(BaseModel):
    """Normalized tool call structure"""
    id: str  # Unique ID for correlation
    name: str
    arguments: Dict[str, Any]


class LLMResponse(BaseModel):
    """Normalized LLM response"""
    content: Optional[str]
    tool_calls: List[ToolCall] = []
    provider: str
    model: str
    usage: Dict[str, int]  # {input_tokens, output_tokens, total_tokens}
    latency_ms: int
    ttft_ms: Optional[int] = None


class ModelCapability(BaseModel):
    """Model capability metadata from database"""
    provider: str
    model_name: str
    display_name: str

    # Pricing
    input_price_per_1m: float
    output_price_per_1m: float

    # Performance
    max_input_tokens: int
    max_output_tokens: int
    avg_output_speed_tokens_per_sec: Optional[float] = None
    avg_ttft_seconds: Optional[float] = None
    p95_latency_ms: Optional[int] = None

    # Capabilities
    supports_streaming: bool
    supports_tool_calling: bool
    tool_calling_success_rate: Optional[float]
    supports_parallel_tools: bool
    supports_json_mode: bool
    supports_structured_output: bool
    supports_thinking_mode: bool

    # Provider config
    api_endpoint: Optional[str]
    requires_api_key_env_var: str
    base_url_override: Optional[str]


class LLMAdapter(ABC):
    """Abstract base class for LLM provider adapters"""

    def __init__(self, capability: ModelCapability):
        self.capability = capability
        self.provider = capability.provider
        self.model = capability.model_name

    @abstractmethod
    async def generate(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        **kwargs
    ) -> LLMResponse:
        """Generate a response without tools"""
        pass

    @abstractmethod
    async def generate_with_tools(
        self,
        messages: List[Dict[str, str]],
        tools: List[Dict[str, Any]],
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        **kwargs
    ) -> LLMResponse:
        """Generate a response with tool calling"""
        pass

    @abstractmethod
    async def stream(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        **kwargs
    ) -> AsyncIterator[str]:
        """Stream response chunks"""
        pass

    @abstractmethod
    def sanitize_parameters(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Remove unsupported parameters for this provider"""
        pass

    @abstractmethod
    def normalize_tool_calls(self, response: Any) -> List[ToolCall]:
        """Normalize provider-specific tool calls to common format"""
        pass
