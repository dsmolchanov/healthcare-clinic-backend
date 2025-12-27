"""
Tier Mappings API - CRUD endpoints for model tier configurations.

Allows UI to view and modify which models are used for each semantic tier.
"""

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import Optional, List
import logging

from app.db.supabase_client import get_supabase_client
from app.services.llm.tiers import ModelTier, DEFAULT_TIER_MODELS, DEFAULT_TIER_PROVIDERS

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tier-mappings", tags=["tier-mappings"])


class TierMappingResponse(BaseModel):
    """Response model for a single tier mapping."""
    tier: str
    model_name: str
    provider: str
    source: str  # 'database' or 'default'
    is_enabled: bool = True
    notes: Optional[str] = None
    id: Optional[str] = None


class TierMappingsListResponse(BaseModel):
    """Response model for all tier mappings."""
    mappings: List[TierMappingResponse]
    clinic_id: Optional[str] = None
    scope: str


class UpdateTierMappingRequest(BaseModel):
    """Request model for updating a tier mapping."""
    tier: str
    provider: str
    model_name: str
    notes: Optional[str] = None


class AvailableModel(BaseModel):
    """Model available for selection."""
    provider: str
    model_name: str
    display_name: str


@router.get("", response_model=TierMappingsListResponse)
async def get_tier_mappings(
    clinic_id: Optional[str] = Query(None, description="Clinic ID for clinic-specific mappings"),
    scope: str = Query("global", description="Scope: 'global' or 'clinic'")
):
    """
    Get all tier mappings for a given scope.

    Returns database mappings if they exist, otherwise falls back to code defaults.
    """
    supabase = get_supabase_client()
    mappings: List[TierMappingResponse] = []

    try:
        # Build query based on scope
        query = supabase.schema('public').table('model_tier_mappings')\
            .select('*')\
            .eq('is_enabled', True)

        if scope == 'clinic' and clinic_id:
            query = query.eq('scope', 'clinic').eq('clinic_id', clinic_id)
        else:
            query = query.eq('scope', 'global').is_('clinic_id', 'null')

        result = query.execute()
        db_mappings = {m['tier']: m for m in (result.data or [])}

        # Build response with all tiers
        for tier in ModelTier:
            tier_value = tier.value

            if tier_value in db_mappings:
                # Use database mapping
                db_map = db_mappings[tier_value]
                mappings.append(TierMappingResponse(
                    tier=tier_value,
                    model_name=db_map['model_name'],
                    provider=db_map['provider'],
                    source='database',
                    is_enabled=db_map['is_enabled'],
                    notes=db_map.get('notes'),
                    id=str(db_map['id'])
                ))
            else:
                # Fall back to code defaults
                mappings.append(TierMappingResponse(
                    tier=tier_value,
                    model_name=DEFAULT_TIER_MODELS[tier],
                    provider=DEFAULT_TIER_PROVIDERS[tier],
                    source='default',
                    is_enabled=True,
                    notes='Code default (no DB override)'
                ))

        return TierMappingsListResponse(
            mappings=mappings,
            clinic_id=clinic_id if scope == 'clinic' else None,
            scope=scope
        )

    except Exception as e:
        logger.error(f"Error fetching tier mappings: {e}")
        # Return code defaults on error
        for tier in ModelTier:
            mappings.append(TierMappingResponse(
                tier=tier.value,
                model_name=DEFAULT_TIER_MODELS[tier],
                provider=DEFAULT_TIER_PROVIDERS[tier],
                source='default',
                is_enabled=True,
                notes='Fallback due to DB error'
            ))
        return TierMappingsListResponse(mappings=mappings, scope=scope)


@router.put("/{tier}")
async def update_tier_mapping(
    tier: str,
    request: UpdateTierMappingRequest,
    clinic_id: Optional[str] = Query(None, description="Clinic ID for clinic-specific override")
):
    """
    Update or create a tier mapping.

    If clinic_id is provided, creates a clinic-specific override.
    Otherwise, updates the global mapping.
    """
    supabase = get_supabase_client()

    # Validate tier
    valid_tiers = [t.value for t in ModelTier]
    if tier not in valid_tiers:
        raise HTTPException(status_code=400, detail=f"Invalid tier: {tier}. Valid tiers: {valid_tiers}")

    try:
        scope = 'clinic' if clinic_id else 'global'

        # Check if mapping exists
        query = supabase.schema('public').table('model_tier_mappings')\
            .select('id')\
            .eq('tier', tier)\
            .eq('scope', scope)

        if clinic_id:
            query = query.eq('clinic_id', clinic_id)
        else:
            query = query.is_('clinic_id', 'null')

        existing = query.execute()

        mapping_data = {
            'tier': tier,
            'provider': request.provider,
            'model_name': request.model_name,
            'scope': scope,
            'clinic_id': clinic_id,
            'is_enabled': True,
            'notes': request.notes,
            'priority': 0
        }

        if existing.data:
            # Update existing
            result = supabase.schema('public').table('model_tier_mappings')\
                .update(mapping_data)\
                .eq('id', existing.data[0]['id'])\
                .execute()
            logger.info(f"Updated tier mapping: {tier} -> {request.model_name}")
        else:
            # Insert new
            result = supabase.schema('public').table('model_tier_mappings')\
                .insert(mapping_data)\
                .execute()
            logger.info(f"Created tier mapping: {tier} -> {request.model_name}")

        return {
            "success": True,
            "tier": tier,
            "model_name": request.model_name,
            "provider": request.provider,
            "scope": scope
        }

    except Exception as e:
        logger.error(f"Error updating tier mapping: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{tier}")
async def delete_tier_mapping(
    tier: str,
    clinic_id: Optional[str] = Query(None, description="Clinic ID for clinic-specific override")
):
    """
    Delete a tier mapping (reverts to default).

    Only deletes database overrides; cannot delete code defaults.
    """
    supabase = get_supabase_client()

    try:
        scope = 'clinic' if clinic_id else 'global'

        query = supabase.schema('public').table('model_tier_mappings')\
            .delete()\
            .eq('tier', tier)\
            .eq('scope', scope)

        if clinic_id:
            query = query.eq('clinic_id', clinic_id)
        else:
            query = query.is_('clinic_id', 'null')

        query.execute()

        logger.info(f"Deleted tier mapping: {tier} (scope={scope})")
        return {"success": True, "tier": tier, "reverted_to_default": True}

    except Exception as e:
        logger.error(f"Error deleting tier mapping: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/available-models", response_model=List[AvailableModel])
async def get_available_models():
    """
    Get all available models that can be used for tier mappings.

    Fetches from the models table (same source as agent configuration).
    """
    supabase = get_supabase_client()

    try:
        result = supabase.schema('public').table('models')\
            .select('provider, model, display_name')\
            .eq('modality', 'llm')\
            .eq('is_active', True)\
            .execute()

        models = [
            AvailableModel(
                provider=m['provider'],
                model_name=m['model'],
                display_name=m['display_name'] or m['model']
            )
            for m in (result.data or [])
        ]

        return models

    except Exception as e:
        logger.error(f"Error fetching available models: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/tiers")
async def get_tier_definitions():
    """
    Get tier definitions with descriptions.

    Returns the semantic meaning of each tier for UI display.
    """
    tier_descriptions = {
        ModelTier.ROUTING: "Fast classification, routing decisions, simple yes/no",
        ModelTier.TOOL_CALLING: "Reliable function/tool calling with structured output",
        ModelTier.REASONING: "Complex analysis, multi-step reasoning, extraction",
        ModelTier.SUMMARIZATION: "Session summaries, context compression, memory",
        ModelTier.MULTIMODAL: "Image/PDF processing, vision tasks",
        ModelTier.VOICE: "Voice/realtime - latency-critical for voice agents",
    }

    return [
        {
            "tier": tier.value,
            "description": tier_descriptions.get(tier, ""),
            "default_model": DEFAULT_TIER_MODELS[tier],
            "default_provider": DEFAULT_TIER_PROVIDERS[tier]
        }
        for tier in ModelTier
    ]
