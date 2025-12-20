"""
API routes for prompt template management.

Phase 2B-2 of Agentic Flow Architecture Refactor.
Enables UI editing of prompts without code deployments.
"""

import logging
from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any

from app.services.prompt_template_service import (
    get_prompt_template_service,
    VALID_COMPONENT_KEYS,
    COMPONENT_DESCRIPTIONS,
)
from app.prompts import DEFAULT_TEMPLATES

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/prompts", tags=["prompts"])


# =============================================================================
# Pydantic Models
# =============================================================================

class PromptTemplateCreate(BaseModel):
    """Request body for creating/updating a prompt template."""
    component_key: str = Field(
        ...,
        description="Component identifier (e.g., 'base_persona', 'booking_policy')"
    )
    content: str = Field(
        ...,
        description="Template content with placeholders like {clinic_name}"
    )
    description: Optional[str] = Field(
        None,
        description="Optional description/help text for this template"
    )


class PromptTemplateResponse(BaseModel):
    """Response for a single prompt template."""
    id: str
    component_key: str
    content: str
    description: Optional[str]
    is_active: bool
    version: int
    created_at: Optional[str]
    updated_at: Optional[str]


class PromptDefaultResponse(BaseModel):
    """Response for a default (Python constant) template."""
    component_key: str
    default_content: str
    description: str


class PromptStatusResponse(BaseModel):
    """Response showing template status (default vs custom)."""
    component_key: str
    description: str
    source: str  # 'default' or 'custom'
    version: Optional[int]
    content_preview: str  # First 100 chars


class PromptPreviewRequest(BaseModel):
    """Request body for previewing a composed prompt."""
    clinic_name: str = "Demo Clinic"
    services: List[str] = ["Cleaning", "Whitening", "Checkup"]
    doctors: List[Dict[str, str]] = [
        {"name": "Dr. Smith", "id": "doc1", "specialization": "General"}
    ]


class PromptPreviewResponse(BaseModel):
    """Response for prompt preview."""
    preview: str
    sources: Dict[str, str]  # {component_key: 'default' | 'custom'}
    total_length: int


# =============================================================================
# Endpoints
# =============================================================================

@router.get("/defaults", response_model=List[PromptDefaultResponse])
async def get_prompt_defaults():
    """
    Get all default prompt templates (Python constants).

    Returns the built-in defaults that are used when no clinic-specific
    override exists. Useful for UI to show what the defaults look like.
    """
    defaults = []
    for key in sorted(VALID_COMPONENT_KEYS):
        content = DEFAULT_TEMPLATES.get(key, '')
        defaults.append(PromptDefaultResponse(
            component_key=key,
            default_content=content,
            description=COMPONENT_DESCRIPTIONS.get(key, ''),
        ))
    return defaults


@router.get("/components")
async def get_component_info() -> Dict[str, Any]:
    """
    Get information about available prompt components.

    Returns component keys, descriptions, and available placeholders.
    """
    return {
        "components": [
            {
                "key": key,
                "description": COMPONENT_DESCRIPTIONS.get(key, ''),
                "has_default": key in DEFAULT_TEMPLATES,
            }
            for key in sorted(VALID_COMPONENT_KEYS)
        ],
        "placeholders": {
            "clinic_name": "Name of the clinic",
            "clinic_id": "UUID of the clinic",
            "clinic_location": "Clinic address/location",
            "services_text": "Comma-separated list of services",
            "doctors_text": "Formatted list of doctors",
            "weekday_hours": "Weekday business hours",
            "saturday_hours": "Saturday hours",
            "sunday_hours": "Sunday hours",
            "current_date": "Today's date (YYYY-MM-DD)",
            "current_day": "Day of week (e.g., Monday)",
            "current_time": "Current time (HH:MM)",
            "tomorrow_date": "Tomorrow's date",
            "tomorrow_day": "Tomorrow's day of week",
            "todays_hours": "Today's business hours",
            "from_phone": "Patient's phone number",
        }
    }


@router.get("/clinic/{clinic_id}", response_model=List[PromptTemplateResponse])
async def get_clinic_templates(
    clinic_id: str,
    include_inactive: bool = Query(False, description="Include deactivated templates")
):
    """
    Get all prompt templates for a clinic.

    Returns only active templates by default. Use include_inactive=true
    to see deleted templates (useful for admin/audit).
    """
    service = get_prompt_template_service()
    templates = await service.list_templates(clinic_id, include_inactive=include_inactive)

    return [
        PromptTemplateResponse(
            id=t['id'],
            component_key=t['component_key'],
            content=t['content'],
            description=t.get('description'),
            is_active=t['is_active'],
            version=t['version'],
            created_at=t.get('created_at'),
            updated_at=t.get('updated_at'),
        )
        for t in templates
    ]


@router.get("/clinic/{clinic_id}/status", response_model=List[PromptStatusResponse])
async def get_clinic_template_status(clinic_id: str):
    """
    Get the status of all prompt components for a clinic.

    Shows which components are using defaults vs custom templates.
    """
    service = get_prompt_template_service()
    custom_templates = await service.get_clinic_templates(clinic_id)

    statuses = []
    for key in sorted(VALID_COMPONENT_KEYS):
        if key in custom_templates:
            content = custom_templates[key]
            source = 'custom'
            # Get version from list_templates
            all_templates = await service.list_templates(clinic_id)
            version = next(
                (t['version'] for t in all_templates if t['component_key'] == key),
                1
            )
        else:
            content = DEFAULT_TEMPLATES.get(key, '')
            source = 'default'
            version = None

        statuses.append(PromptStatusResponse(
            component_key=key,
            description=COMPONENT_DESCRIPTIONS.get(key, ''),
            source=source,
            version=version,
            content_preview=content[:100] + ('...' if len(content) > 100 else ''),
        ))

    return statuses


@router.get("/clinic/{clinic_id}/{component_key}")
async def get_clinic_template(
    clinic_id: str,
    component_key: str
) -> Dict[str, Any]:
    """
    Get a specific template for a clinic.

    Returns the custom template if exists, otherwise the default.
    """
    if component_key not in VALID_COMPONENT_KEYS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid component_key. Must be one of: {', '.join(sorted(VALID_COMPONENT_KEYS))}"
        )

    service = get_prompt_template_service()
    template = await service.get_template(clinic_id, component_key)

    if template:
        return {
            "source": "custom",
            "component_key": component_key,
            "content": template['content'],
            "description": template.get('description'),
            "version": template['version'],
            "updated_at": template.get('updated_at'),
        }

    # Return default
    return {
        "source": "default",
        "component_key": component_key,
        "content": DEFAULT_TEMPLATES.get(component_key, ''),
        "description": COMPONENT_DESCRIPTIONS.get(component_key, ''),
        "version": None,
        "updated_at": None,
    }


@router.post("/clinic/{clinic_id}")
async def create_or_update_template(
    clinic_id: str,
    template: PromptTemplateCreate
):
    """
    Create or update a prompt template for a clinic.

    If a template already exists for this component, it will be updated.
    The version number is automatically incremented on content changes.
    """
    if template.component_key not in VALID_COMPONENT_KEYS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid component_key. Must be one of: {', '.join(sorted(VALID_COMPONENT_KEYS))}"
        )

    # Validate that content has required placeholders (basic check)
    default_content = DEFAULT_TEMPLATES.get(template.component_key, '')
    if default_content:
        # Extract placeholders from default
        import re
        default_placeholders = set(re.findall(r'\{(\w+)\}', default_content))
        content_placeholders = set(re.findall(r'\{(\w+)\}', template.content))

        # Warn if missing placeholders (but don't fail)
        missing = default_placeholders - content_placeholders
        if missing:
            logger.warning(
                f"Template for {template.component_key} is missing placeholders: {missing}"
            )

    service = get_prompt_template_service()
    success = await service.save_template(
        clinic_id=clinic_id,
        component_key=template.component_key,
        content=template.content,
        description=template.description,
    )

    if not success:
        raise HTTPException(status_code=500, detail="Failed to save template")

    logger.info(f"Saved template {template.component_key} for clinic {clinic_id}")
    return {"status": "ok", "message": f"Template '{template.component_key}' saved"}


@router.delete("/clinic/{clinic_id}/{component_key}")
async def delete_template(
    clinic_id: str,
    component_key: str
):
    """
    Delete (deactivate) a prompt template.

    The clinic will fall back to using the Python default for this component.
    The template is soft-deleted and can be restored by creating a new one.
    """
    if component_key not in VALID_COMPONENT_KEYS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid component_key. Must be one of: {', '.join(sorted(VALID_COMPONENT_KEYS))}"
        )

    service = get_prompt_template_service()
    success = await service.delete_template(clinic_id, component_key)

    if not success:
        raise HTTPException(status_code=500, detail="Failed to delete template")

    logger.info(f"Deleted template {component_key} for clinic {clinic_id}")
    return {"status": "ok", "message": f"Template '{component_key}' deleted, using default"}


@router.post("/clinic/{clinic_id}/preview", response_model=PromptPreviewResponse)
async def preview_composed_prompt(
    clinic_id: str,
    preview_data: Optional[PromptPreviewRequest] = None
):
    """
    Preview the fully composed prompt for a clinic.

    Uses sample data to render the prompt with current templates.
    Useful for testing before going live.
    """
    from app.prompts import PromptComposer
    from unittest.mock import MagicMock

    # Use provided data or defaults
    data = preview_data or PromptPreviewRequest()

    # Create mock context
    mock_ctx = MagicMock()
    mock_ctx.clinic_name = data.clinic_name
    mock_ctx.effective_clinic_id = clinic_id
    mock_ctx.from_phone = "+1234567890"
    mock_ctx.clinic_profile = {
        'services': data.services,
        'doctors': data.doctors,
        'business_hours': {
            'weekdays': '9:00 AM - 5:00 PM',
            'saturday': '10:00 AM - 2:00 PM',
            'sunday': 'Closed',
        },
        'location': 'Sample Location',
    }
    mock_ctx.profile = None
    mock_ctx.conversation_state = None
    mock_ctx.constraints = None
    mock_ctx.narrowing_instruction = None
    mock_ctx.session_messages = []
    mock_ctx.previous_session_summary = None
    mock_ctx.additional_context = ""

    # Compose with DB templates
    composer = PromptComposer(use_db_templates=True)
    preview = await composer.compose_async(mock_ctx)

    # Determine sources
    service = get_prompt_template_service()
    custom_templates = await service.get_clinic_templates(clinic_id)

    sources = {}
    for key in VALID_COMPONENT_KEYS:
        sources[key] = 'custom' if key in custom_templates else 'default'

    return PromptPreviewResponse(
        preview=preview,
        sources=sources,
        total_length=len(preview),
    )


@router.post("/clinic/{clinic_id}/invalidate-cache")
async def invalidate_cache(clinic_id: str):
    """
    Invalidate the template cache for a clinic.

    Forces the next request to reload templates from the database.
    Normally not needed as cache auto-invalidates on updates.
    """
    service = get_prompt_template_service()
    service.invalidate_cache(clinic_id)

    return {"status": "ok", "message": f"Cache invalidated for clinic {clinic_id}"}
