"""
Tier Registry - Resolves semantic tiers to actual models.

Precedence (highest to lowest):
1. Active experiment (if session/user assigned to treatment)
2. Environment variable (TIER_{NAME}_MODEL) - PANIC BUTTON
3. Clinic-specific DB mapping
4. Global DB mapping
5. Code default (DEFAULT_TIER_MODELS)

The ENV override is ABOVE DB so on-call can fix bad DB configs immediately
via `fly secrets set TIER_ROUTING_MODEL=gpt-5-mini` without DB access.
"""

import os
import time
import logging
import hashlib
from typing import Optional, Dict, Any, List, Tuple
from dataclasses import dataclass

from app.services.llm.tiers import ModelTier, DEFAULT_TIER_MODELS, DEFAULT_TIER_PROVIDERS

logger = logging.getLogger(__name__)


@dataclass
class TierResolution:
    """Result of tier resolution with full context."""
    tier: ModelTier
    model_name: str
    provider: str
    source: str  # 'experiment', 'env', 'clinic', 'global', 'default'
    experiment_id: Optional[str] = None
    variant: Optional[str] = None
    clinic_id: Optional[str] = None


class TierRegistry:
    """
    Central registry for tier→model resolution.

    Implements in-memory caching to avoid N+1 DB queries.
    Cache TTL: 60 seconds (configurable).
    """

    def __init__(self, supabase_client, capability_matrix=None):
        self.supabase = supabase_client
        self.capability_matrix = capability_matrix  # For model validation

        # Caching to avoid per-request DB hits
        self._mappings_cache: Dict[str, Tuple[Any, float]] = {}  # key -> (data, timestamp)
        self._experiments_cache: List[Dict] = []
        self._experiments_cache_time: float = 0
        self._cache_ttl = 60  # seconds

    async def resolve(
        self,
        tier: ModelTier,
        clinic_id: Optional[str] = None,
        session_id: Optional[str] = None,
        user_id: Optional[str] = None  # Prefer user_id for cross-device consistency
    ) -> TierResolution:
        """
        Resolve a tier to an actual model.

        Args:
            tier: The semantic tier (e.g., ModelTier.ROUTING)
            clinic_id: Optional clinic for clinic-specific overrides
            session_id: Optional session for sticky experiment assignment
            user_id: Optional user ID (preferred over session_id for cross-device)

        Returns:
            TierResolution with model details and source
        """
        tier_str = tier.value if isinstance(tier, ModelTier) else tier

        # Use user_id if available (better cross-device consistency), else session_id
        sticky_id = user_id or session_id

        # 1. Check for active experiments (highest priority)
        if sticky_id:
            experiment_resolution = await self._check_experiments_cached(
                tier_str, clinic_id, sticky_id
            )
            if experiment_resolution:
                return await self._validate_and_return(experiment_resolution)

        # 2. Check environment variable (PANIC BUTTON - before DB!)
        env_var = f"TIER_{tier_str.upper()}_MODEL"
        env_model = os.environ.get(env_var)
        if env_model:
            # Only use _infer_provider for env vars (no DB provider column)
            provider = self._infer_provider(env_model)
            logger.warning(f"ENV OVERRIDE for {tier_str}: {env_model} (source=env)")
            return await self._validate_and_return(TierResolution(
                tier=tier,
                model_name=env_model,
                provider=provider,
                source='env'
            ))

        # 3. Check clinic-specific DB mapping
        if clinic_id:
            clinic_mapping = await self._get_clinic_mapping_cached(tier_str, clinic_id)
            if clinic_mapping:
                return await self._validate_and_return(TierResolution(
                    tier=tier,
                    model_name=clinic_mapping['model_name'],
                    provider=clinic_mapping['provider'],  # Use DB provider column
                    source='clinic',
                    clinic_id=clinic_id
                ))

        # 4. Check global DB mapping
        global_mapping = await self._get_global_mapping_cached(tier_str)
        if global_mapping:
            return await self._validate_and_return(TierResolution(
                tier=tier,
                model_name=global_mapping['model_name'],
                provider=global_mapping['provider'],  # Use DB provider column
                source='global'
            ))

        # 5. Fall back to code default
        default_model = DEFAULT_TIER_MODELS.get(tier, DEFAULT_TIER_MODELS[ModelTier.TOOL_CALLING])
        provider = DEFAULT_TIER_PROVIDERS.get(tier, "openai")
        logger.info(f"Using code default for {tier_str}: {default_model}")
        return TierResolution(
            tier=tier,
            model_name=default_model,
            provider=provider,
            source='default'
        )

    async def _validate_and_return(self, resolution: TierResolution) -> TierResolution:
        """Validate model against CapabilityMatrix if available."""
        if self.capability_matrix:
            try:
                # Verify model exists and is active
                capability = await self.capability_matrix.load_model(resolution.model_name)
                if not capability:
                    logger.critical(
                        f"Model {resolution.model_name} not found in CapabilityMatrix! "
                        f"Falling back to default for tier {resolution.tier}"
                    )
                    default_model = DEFAULT_TIER_MODELS.get(resolution.tier, DEFAULT_TIER_MODELS[ModelTier.TOOL_CALLING])
                    return TierResolution(
                        tier=resolution.tier,
                        model_name=default_model,
                        provider=DEFAULT_TIER_PROVIDERS.get(resolution.tier, "openai"),
                        source='default'
                    )
            except Exception as e:
                logger.warning(f"Could not validate model {resolution.model_name}: {e}")
        return resolution

    async def _check_experiments_cached(
        self,
        tier: str,
        clinic_id: Optional[str],
        sticky_id: str
    ) -> Optional[TierResolution]:
        """
        Check if session/user is assigned to an active experiment.

        Uses in-memory cache to avoid per-request DB queries.
        Cache refreshes every 60 seconds.
        """
        try:
            # Refresh cache if stale
            now = time.time()
            if now - self._experiments_cache_time > self._cache_ttl:
                await self._refresh_experiments_cache()

            # Search cached experiments
            for experiment in self._experiments_cache:
                if experiment['tier'] != tier:
                    continue

                # Check if experiment applies to this clinic
                if experiment['scope'] == 'clinic':
                    clinic_ids = experiment.get('clinic_ids') or []
                    if clinic_id not in clinic_ids:
                        continue

                # Determine variant via sticky hash
                variant = self._assign_variant(
                    experiment['id'],
                    sticky_id,
                    experiment['variants']
                )

                if variant:
                    variant_config = experiment['variants'][variant]
                    # Use provider from variant config if specified, else infer
                    provider = variant_config.get('provider') or self._infer_provider(variant_config['model'])
                    return TierResolution(
                        tier=ModelTier(tier),
                        model_name=variant_config['model'],
                        provider=provider,
                        source='experiment',
                        experiment_id=str(experiment['id']),
                        variant=variant,
                        clinic_id=clinic_id
                    )

            return None

        except Exception as e:
            logger.warning(f"Error checking experiments: {e}")
            return None

    async def _refresh_experiments_cache(self):
        """Refresh the experiments cache from DB."""
        try:
            result = self.supabase.schema('public').table('model_experiments')\
                .select('*')\
                .eq('status', 'running')\
                .execute()

            self._experiments_cache = result.data or []
            self._experiments_cache_time = time.time()
            logger.debug(f"Refreshed experiments cache: {len(self._experiments_cache)} running")
        except Exception as e:
            logger.error(f"Failed to refresh experiments cache: {e}")
            # Keep stale cache rather than failing

    def _assign_variant(
        self,
        experiment_id: str,
        session_id: str,
        variants: Dict[str, Any]
    ) -> Optional[str]:
        """
        Assign session to experiment variant using sticky hash.

        Same session always gets same variant for consistent UX.
        """
        # Create deterministic hash
        hash_input = f"{experiment_id}:{session_id}"
        hash_value = int(hashlib.sha256(hash_input.encode()).hexdigest(), 16)
        bucket = hash_value % 100

        # Assign based on cumulative weights
        cumulative = 0
        for variant_name, config in variants.items():
            cumulative += config.get('weight', 0)
            if bucket < cumulative:
                return variant_name

        return None

    async def _get_clinic_mapping_cached(self, tier: str, clinic_id: str) -> Optional[Dict]:
        """Get clinic-specific tier mapping with caching."""
        cache_key = f"clinic:{clinic_id}:{tier}"
        cached = self._get_from_cache(cache_key)
        if cached is not None:
            return cached

        try:
            result = self.supabase.schema('public').table('model_tier_mappings')\
                .select('*')\
                .eq('scope', 'clinic')\
                .eq('clinic_id', clinic_id)\
                .eq('tier', tier)\
                .eq('is_enabled', True)\
                .order('priority', desc=True)\
                .limit(1)\
                .execute()

            data = result.data[0] if result.data else None
            self._set_cache(cache_key, data)
            return data
        except Exception as e:
            logger.warning(f"Error getting clinic mapping: {e}")
            return None

    async def _get_global_mapping_cached(self, tier: str) -> Optional[Dict]:
        """Get global tier mapping with caching."""
        cache_key = f"global:{tier}"
        cached = self._get_from_cache(cache_key)
        if cached is not None:
            return cached

        try:
            result = self.supabase.schema('public').table('model_tier_mappings')\
                .select('*')\
                .eq('scope', 'global')\
                .eq('tier', tier)\
                .eq('is_enabled', True)\
                .is_('clinic_id', 'null')\
                .order('priority', desc=True)\
                .limit(1)\
                .execute()

            data = result.data[0] if result.data else None
            self._set_cache(cache_key, data)
            return data
        except Exception as e:
            logger.warning(f"Error getting global mapping: {e}")
            return None

    def _get_from_cache(self, key: str) -> Optional[Any]:
        """Get value from cache if not expired."""
        if key in self._mappings_cache:
            data, timestamp = self._mappings_cache[key]
            if time.time() - timestamp < self._cache_ttl:
                return data
            del self._mappings_cache[key]
        return None

    def _set_cache(self, key: str, data: Any):
        """Set value in cache with current timestamp."""
        self._mappings_cache[key] = (data, time.time())

    def _infer_provider(self, model_name: str) -> str:
        """Infer provider from model name."""
        model_lower = model_name.lower()
        if 'gpt' in model_lower or 'o1' in model_lower:
            return 'openai'
        elif 'gemini' in model_lower:
            return 'google'
        elif 'claude' in model_lower:
            return 'anthropic'
        elif 'glm' in model_lower:
            return 'glm'
        elif 'deepseek' in model_lower:
            return 'deepseek'
        else:
            return 'openai'  # Default assumption


# Singleton instance
_tier_registry: Optional[TierRegistry] = None


async def get_tier_registry() -> TierRegistry:
    """Get or create singleton TierRegistry instance."""
    global _tier_registry

    if _tier_registry is None:
        from app.db.supabase_client import get_supabase_client
        from app.services.llm.capability_matrix import CapabilityMatrix

        supabase = get_supabase_client()
        capability_matrix = CapabilityMatrix(supabase)
        _tier_registry = TierRegistry(supabase, capability_matrix)
        logger.info("TierRegistry singleton created with CapabilityMatrix validation")

    return _tier_registry


async def warmup_tier_registry():
    """
    Pre-populate TierRegistry caches on startup.

    Call this during app initialization to avoid cold-start latency.
    """
    registry = await get_tier_registry()

    # Pre-load experiments cache
    await registry._refresh_experiments_cache()

    # Pre-load global mappings for all tiers
    for tier in ModelTier:
        await registry._get_global_mapping_cached(tier.value)

    logger.info("TierRegistry warmup complete")
