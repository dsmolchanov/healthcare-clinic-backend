"""
API endpoints for multimodal bulk data upload and parsing
Uses OpenAI GPT-4 for intelligent entity discovery and extraction
"""

from fastapi import APIRouter, UploadFile, File, HTTPException, Form
from fastapi.responses import JSONResponse
from typing import Dict, Any
import uuid
from datetime import datetime
import logging
import json

from ..services.openai_multimodal_parser import (
    OpenAIMultimodalParser,
    FieldMapping,
    DiscoveryResult,
    ImportResult
)
# Fallback to original if needed
from ..services.grok_multimodal_parser import GrokMultimodalParser

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/bulk-upload", tags=["bulk-upload"])

# Store discovery results temporarily (in production, use Redis)
discovery_cache: Dict[str, Any] = {}

@router.post("/discover")
async def discover_entities(
    file: UploadFile = File(...),
    clinic_id: str = Form(...)
):
    """
    Phase 1: Analyze uploaded file and discover entities
    Returns field mappings for user validation
    """
    logger.info(f"[DEBUG] Discovery endpoint received clinic_id: {clinic_id}")
    try:
        # Validate file size (max 10MB)
        file_content = await file.read()
        if len(file_content) > 10 * 1024 * 1024:
            raise HTTPException(400, "File size exceeds 10MB limit")

        # Initialize parser - try Grok first, then OpenAI as fallback
        try:
            parser = GrokMultimodalParser()
            logger.info("Using Grok parser for multimodal upload")
        except Exception as e:
            logger.warning(f"Grok parser failed to initialize: {e}, falling back to OpenAI")
            parser = OpenAIMultimodalParser()

        # Discover entities
        logger.info(f"Discovering entities in file: {file.filename}")
        discovery_result = await parser.discover_entities(
            file_content,
            file.content_type,
            file.filename
        )

        # Generate session ID for this discovery
        session_id = str(uuid.uuid4())

        # Cache the discovery result and file content
        discovery_cache[session_id] = {
            "discovery": discovery_result,
            "file_content": file_content,
            "mime_type": file.content_type,
            "filename": file.filename,
            "clinic_id": clinic_id,
            "created_at": datetime.utcnow().isoformat()
        }

        # Clean up old cache entries (older than 1 hour)
        current_time = datetime.utcnow()
        to_remove = []
        for key, value in discovery_cache.items():
            if key != session_id:
                created = datetime.fromisoformat(value["created_at"])
                if (current_time - created).seconds > 3600:
                    to_remove.append(key)
        for key in to_remove:
            del discovery_cache[key]

        # Return discovery results
        return JSONResponse({
            "success": True,
            "session_id": session_id,
            "filename": file.filename,
            "discovered_entities": [
                {
                    "field_name": entity.field_name,
                    "sample_values": entity.sample_values,
                    "data_type": entity.data_type,
                    "occurrence_count": entity.occurrence_count,
                    "suggested_table": entity.suggested_table,
                    "suggested_field": entity.suggested_field,
                    "confidence": entity.confidence,
                    "metadata": entity.metadata
                }
                for entity in discovery_result.detected_entities
            ],
            "summary": discovery_result.summary,
            "warnings": discovery_result.warnings or []
        })

    except Exception as e:
        logger.error(f"Entity discovery failed: {e}")
        raise HTTPException(500, f"Discovery failed: {str(e)}")

@router.post("/validate-mappings")
async def validate_mappings(
    session_id: str = Form(...),
    mappings: str = Form(...)  # JSON string of mappings
):
    """
    Validate user-confirmed field mappings before import
    Returns preview of data to be imported
    """
    try:
        # Retrieve cached discovery
        if session_id not in discovery_cache:
            raise HTTPException(404, "Session not found or expired")

        cache_data = discovery_cache[session_id]

        # Parse mappings
        mapping_list = []
        mappings_data = json.loads(mappings)
        for mapping in mappings_data:
            if mapping.get("target_table") and mapping.get("target_field"):
                mapping_list.append(FieldMapping(
                    original_field=mapping["original_field"],
                    target_table=mapping["target_table"],
                    target_field=mapping["target_field"],
                    data_type=mapping.get("data_type", "string"),
                    transformation=mapping.get("transformation")
                ))

        if not mapping_list:
            raise HTTPException(400, "No valid mappings provided")

        # Group by table for preview
        tables_preview = {}
        for mapping in mapping_list:
            if mapping.target_table not in tables_preview:
                tables_preview[mapping.target_table] = {
                    "fields": [],
                    "record_count": 0,
                    "sample_row": {}  # Add sample row preview
                }
            tables_preview[mapping.target_table]["fields"].append({
                "original": mapping.original_field,
                "target": mapping.target_field,
                "type": mapping.data_type
            })

        # Estimate record counts and build sample row from discovery
        for entity in cache_data["discovery"].detected_entities:
            for mapping in mapping_list:
                if entity.field_name == mapping.original_field:
                    table = mapping.target_table
                    if table in tables_preview:
                        tables_preview[table]["record_count"] = max(
                            tables_preview[table]["record_count"],
                            entity.occurrence_count
                        )
                        # Add first sample value to preview row
                        if entity.sample_values and len(entity.sample_values) > 0:
                            tables_preview[table]["sample_row"][mapping.target_field] = entity.sample_values[0]

        return JSONResponse({
            "success": True,
            "session_id": session_id,
            "tables_preview": tables_preview,
            "total_mappings": len(mapping_list)
        })

    except json.JSONDecodeError:
        raise HTTPException(400, "Invalid mappings format")
    except Exception as e:
        logger.error(f"Mapping validation failed: {e}")
        raise HTTPException(500, f"Validation failed: {str(e)}")

@router.post("/import")
async def import_data(
    session_id: str = Form(...),
    mappings: str = Form(...)  # JSON string of mappings
):
    """
    Phase 2: Parse data with validated mappings and import to database
    """
    try:
        # Import the fixed version
        from .multimodal_upload_fixed import import_data_fixed

        # Retrieve cached data
        if session_id not in discovery_cache:
            raise HTTPException(404, "Session not found or expired")

        cache_data = discovery_cache[session_id]

        # Use the fixed import function
        result = await import_data_fixed(session_id, mappings, cache_data)

        # Clean up cache after successful import
        if result.get("success"):
            del discovery_cache[session_id]

        return JSONResponse(result)

    except Exception as e:
        logger.error(f"Import failed: {e}")
        raise HTTPException(500, f"Import failed: {str(e)}")

@router.get("/session/{session_id}")
async def get_session_info(session_id: str):
    """Get information about a discovery session"""

    if session_id not in discovery_cache:
        raise HTTPException(404, "Session not found or expired")

    cache_data = discovery_cache[session_id]

    return JSONResponse({
        "success": True,
        "session_id": session_id,
        "filename": cache_data["filename"],
        "created_at": cache_data["created_at"],
        "entity_count": len(cache_data["discovery"].detected_entities),
        "summary": cache_data["discovery"].summary
    })

@router.delete("/session/{session_id}")
async def cancel_session(session_id: str):
    """Cancel and clean up a discovery session"""

    if session_id in discovery_cache:
        del discovery_cache[session_id]

    return JSONResponse({
        "success": True,
        "message": "Session cancelled"
    })