"""
Feature Flags for mem0-redis Epic Rollout

Provides centralized feature flag management for safe canary deployment:
- FAST_PATH_ENABLED: Enable fast-path routing for FAQ/price queries
- MEM0_READS_ENABLED: Enable mem0 memory reads
- MEM0_SHADOW_MODE: Run mem0 writes in shadow mode (no reads)
- CANARY_SAMPLE_RATE: Percentage of traffic for canary testing (0.0-1.0)
"""

import os
import logging
import random
from typing import Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class FeatureFlags:
    """Feature flag configuration"""
    fast_path_enabled: bool = False
    mem0_reads_enabled: bool = False
    mem0_shadow_mode: bool = False
    canary_sample_rate: float = 0.0

    @property
    def is_canary_active(self) -> bool:
        """Check if canary testing is active"""
        return self.canary_sample_rate > 0.0

    def should_process_with_canary(self) -> bool:
        """Determine if this request should use canary features"""
        if not self.is_canary_active:
            return False
        return random.random() < self.canary_sample_rate


def _parse_bool(value: str) -> bool:
    """Parse boolean from environment variable"""
    return value.lower() in ('true', '1', 'yes', 'on')


def _parse_float(value: str, default: float = 0.0) -> float:
    """Parse float from environment variable with validation"""
    try:
        val = float(value)
        return max(0.0, min(1.0, val))  # Clamp between 0.0 and 1.0
    except (ValueError, TypeError):
        return default


def load_feature_flags() -> FeatureFlags:
    """
    Load feature flags from environment variables

    Returns:
        FeatureFlags instance with current configuration
    """
    flags = FeatureFlags(
        fast_path_enabled=_parse_bool(os.getenv('FAST_PATH_ENABLED', 'false')),
        mem0_reads_enabled=_parse_bool(os.getenv('MEM0_READS_ENABLED', 'false')),
        mem0_shadow_mode=_parse_bool(os.getenv('MEM0_SHADOW_MODE', 'false')),
        canary_sample_rate=_parse_float(os.getenv('CANARY_SAMPLE_RATE', '0.0'))
    )

    logger.info(
        f"Feature flags loaded: "
        f"fast_path={flags.fast_path_enabled}, "
        f"mem0_reads={flags.mem0_reads_enabled}, "
        f"mem0_shadow={flags.mem0_shadow_mode}, "
        f"canary_rate={flags.canary_sample_rate}"
    )

    return flags


# Global instance (singleton pattern)
_feature_flags: Optional[FeatureFlags] = None


def get_feature_flags() -> FeatureFlags:
    """
    Get global feature flags instance (singleton)

    Returns:
        FeatureFlags instance
    """
    global _feature_flags
    if _feature_flags is None:
        _feature_flags = load_feature_flags()
    return _feature_flags


def reload_feature_flags():
    """
    Reload feature flags from environment (for testing/debugging)

    Use sparingly in production - flags are loaded once at startup
    """
    global _feature_flags
    _feature_flags = load_feature_flags()
    logger.info("Feature flags reloaded")


def is_fast_path_enabled() -> bool:
    """Check if fast-path routing is enabled"""
    return get_feature_flags().fast_path_enabled


def is_mem0_reads_enabled() -> bool:
    """Check if mem0 reads are enabled"""
    flags = get_feature_flags()
    # Only enable reads if not in shadow mode
    return flags.mem0_reads_enabled and not flags.mem0_shadow_mode


def is_mem0_writes_enabled() -> bool:
    """Check if mem0 writes are enabled (includes shadow mode)"""
    flags = get_feature_flags()
    # Writes enabled if either reads enabled OR shadow mode
    return flags.mem0_reads_enabled or flags.mem0_shadow_mode


def should_use_canary_features() -> bool:
    """Check if this request should use canary features"""
    return get_feature_flags().should_process_with_canary()
