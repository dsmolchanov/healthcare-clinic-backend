"""
Model Tiers - Semantic categories for LLM usage.

Instead of hardcoding models like "gpt-4o-mini", code specifies
intent via tiers. The TierRegistry resolves tiers to actual models.

IMPORTANT: These tier names are stored in the database as strings.
Renaming a tier requires a data migration. Treat these as stable API values.
"""

from enum import StrEnum
from typing import Dict


class ModelTier(StrEnum):
    """
    Semantic model tiers based on use case, not provider.

    WARNING: These string values are stored in DB. Do not rename casually.
    """

    # Fast, cheap - classification, routing, simple decisions
    # Typical: gpt-4o-mini, gemini-2.0-flash, claude-3-haiku
    ROUTING = "routing"

    # Reliable function calling, structured output
    # Typical: gpt-4o-mini (stable), gpt-5-mini
    TOOL_CALLING = "tool_calling"

    # Complex analysis, extraction, multi-step reasoning
    # Typical: gpt-4o, claude-3.5-sonnet, gemini-2.5-pro
    REASONING = "reasoning"

    # Session summaries, context compression, memory
    # Typical: gpt-4o-mini, gemini-flash
    SUMMARIZATION = "summarization"

    # Image/PDF processing, vision tasks
    # Typical: gpt-4o, gemini-2.0-flash, claude-3.5-sonnet
    MULTIMODAL = "multimodal"

    # Voice/realtime - latency-critical for voice agents
    # Typical: gpt-4o-mini-realtime, gemini-2.0-flash
    # Reserved for future voice worker migration
    VOICE = "voice"


# Default tier→model mappings (fallback when DB unavailable)
# These are the "safety net" - guaranteed to work without DB
# CRITICAL: Use only KNOWN STABLE models here, not hypothetical ones
DEFAULT_TIER_MODELS: Dict[ModelTier, str] = {
    ModelTier.ROUTING: "gpt-5-mini",              # Fast, stable (released Aug 2025)
    ModelTier.TOOL_CALLING: "gpt-5-mini",         # Reliable tool calling
    ModelTier.REASONING: "gemini-3-flash-preview",    # Complex analysis
    ModelTier.SUMMARIZATION: "gpt-5-mini",        # Cost-effective
    ModelTier.MULTIMODAL: "gemini-3-flash-preview",   # Vision support
    ModelTier.VOICE: "gpt-5-mini",                # Low latency (placeholder)
}

# Default providers for code defaults (used when DB unavailable)
DEFAULT_TIER_PROVIDERS: Dict[ModelTier, str] = {
    ModelTier.ROUTING: "openai",
    ModelTier.TOOL_CALLING: "openai",
    ModelTier.REASONING: "google",
    ModelTier.SUMMARIZATION: "openai",
    ModelTier.MULTIMODAL: "google",
    ModelTier.VOICE: "openai",
}
