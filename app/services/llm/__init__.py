"""LLM factory and provider adapters for multi-LLM support"""

from app.services.llm.base_adapter import (
    LLMProvider,
    ToolCall,
    LLMResponse,
    ModelCapability,
    LLMAdapter
)
from app.services.llm.capability_matrix import CapabilityMatrix
from app.services.llm.llm_factory import LLMFactory, get_llm_factory
from app.services.llm.tiers import ModelTier, DEFAULT_TIER_MODELS, DEFAULT_TIER_PROVIDERS
from app.services.llm.tier_registry import TierRegistry, TierResolution, get_tier_registry, warmup_tier_registry

__all__ = [
    'LLMProvider',
    'ToolCall',
    'LLMResponse',
    'ModelCapability',
    'LLMAdapter',
    'CapabilityMatrix',
    'LLMFactory',
    'get_llm_factory',
    # Tier abstraction (Phase 1)
    'ModelTier',
    'DEFAULT_TIER_MODELS',
    'DEFAULT_TIER_PROVIDERS',
    # Tier registry (Phase 2)
    'TierRegistry',
    'TierResolution',
    'get_tier_registry',
    'warmup_tier_registry',
]
