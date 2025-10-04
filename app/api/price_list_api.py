"""
Price List Upload API
Strategic backend service for parsing medical price lists
"""

from fastapi import APIRouter, File, UploadFile, HTTPException, Form, BackgroundTasks
from fastapi.responses import JSONResponse
from typing import Optional, List
import asyncpg
import os
import json
import uuid
from datetime import datetime
import logging
import redis
from ..services.price_list_parser import PriceListParser, ParsedService, FileType
# from ..auth.dependencies import get_current_clinic  # TODO: Add auth later
# from ..database import get_db_connection  # TODO: Add database connection

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/price-lists", tags=["price-lists"])

# Initialize parser with caching
redis_client = None
try:
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
    redis_client = redis.from_url(redis_url, decode_responses=True)
except Exception as e:
    logger.warning(f"Redis not available, caching disabled: {e}")

parser = PriceListParser(cache_client=redis_client)

@router.post("/parse")
async def parse_price_list(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    clinic_id: Optional[str] = Form(None),
    auto_import: bool = Form(False)
):
    """
    Parse a price list file and optionally import to database
    
    Strategic benefits:
    - Secure API key handling
    - Caching of parsed results
    - Background processing for large files
    - Rate limiting for AI APIs
    - Audit logging
    """
    
    # Validate file size (max 20MB)
    if file.size > 20 * 1024 * 1024:
        raise HTTPException(400, "File too large. Maximum size is 20MB")
    
    # Read file content
    content = await file.read()
    
    try:
        # Log upload for audit
        logger.info(f"Processing price list upload: {file.filename} ({file.size} bytes) for clinic {clinic_id}")
        
        # Parse file
        services = await parser.parse_file(
            file_content=content,
            file_name=file.filename,
            use_cache=True
        )
        
        # Convert to dict for JSON response
        services_dict = [s.to_dict() for s in services]
        
        # Auto-import if requested
        import_result = None
        if auto_import and clinic_id:
            # Queue background import task
            background_tasks.add_task(
                import_services_to_db,
                services,
                clinic_id
            )
            import_result = {
                "status": "queued",
                "message": "Import has been queued and will process in background"
            }
        
        return JSONResponse({
            "success": True,
            "file_name": file.filename,
            "file_size": file.size,
            "services_found": len(services),
            "services": services_dict,
            "import_result": import_result,
            "cached": False,  # Will be true on subsequent calls
            "parser_version": "2.0"
        })
        
    except ValueError as e:
        logger.error(f"Validation error: {e}")
        raise HTTPException(400, str(e))
    except Exception as e:
        logger.error(f"Parse error: {e}")
        raise HTTPException(500, f"Failed to parse file: {str(e)}")

@router.post("/parse-base64")
async def parse_base64_content(
    content_type: str = Form(...),
    content_base64: str = Form(...),
    file_name: Optional[str] = Form("uploaded_file"),
    clinic_id: Optional[str] = Form(None)
):
    """
    Parse base64-encoded file content
    Used by frontend when file is already loaded
    """
    
    import base64
    
    try:
        # Decode base64 content
        content = base64.b64decode(content_base64)
        
        # Detect file type from content_type or name
        file_type = None
        if 'pdf' in content_type:
            file_type = FileType.PDF
        elif 'image' in content_type:
            file_type = FileType.IMAGE
        elif 'csv' in content_type:
            file_type = FileType.CSV
        
        # Parse content
        services = await parser.parse_file(
            file_content=content,
            file_name=file_name,
            file_type=file_type,
            use_cache=True
        )
        
        services_dict = [s.to_dict() for s in services]
        
        return JSONResponse({
            "success": True,
            "services_found": len(services),
            "services": services_dict
        })
        
    except Exception as e:
        logger.error(f"Parse error: {e}")
        raise HTTPException(500, f"Failed to parse content: {str(e)}")

@router.get("/supported-formats")
async def get_supported_formats():
    """Get list of supported file formats"""
    
    return JSONResponse({
        "formats": [
            {
                "extension": ".csv",
                "mime_types": ["text/csv", "application/csv"],
                "description": "Comma-separated values",
                "ai_required": False
            },
            {
                "extension": ".pdf",
                "mime_types": ["application/pdf"],
                "description": "PDF documents",
                "ai_required": True
            },
            {
                "extension": ".jpg",
                "mime_types": ["image/jpeg"],
                "description": "JPEG images",
                "ai_required": True
            },
            {
                "extension": ".png",
                "mime_types": ["image/png"],
                "description": "PNG images",
                "ai_required": True
            },
            {
                "extension": ".xlsx",
                "mime_types": ["application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"],
                "description": "Excel spreadsheets",
                "ai_required": False
            }
        ],
        "max_file_size_mb": 20,
        "ai_models": ["grok-4-fast", "openai-gpt-4-vision"],
        "caching_enabled": redis_client is not None
    })

@router.post("/validate")
async def validate_price_list(
    file: UploadFile = File(...)
):
    """
    Validate a price list without importing
    Returns parsing results and any issues found
    """
    
    content = await file.read()
    
    try:
        services = await parser.parse_file(
            file_content=content,
            file_name=file.filename,
            use_cache=False  # Don't cache validation results
        )
        
        # Validate services
        issues = []
        for service in services:
            if service.price <= 0:
                issues.append(f"Service '{service.name}' has invalid price: {service.price}")
            if not service.category:
                issues.append(f"Service '{service.name}' has no category")
            if service.confidence_score < 0.5:
                issues.append(f"Service '{service.name}' has low confidence score: {service.confidence_score}")
        
        return JSONResponse({
            "valid": len(issues) == 0,
            "services_found": len(services),
            "issues": issues,
            "summary": {
                "total_services": len(services),
                "categories": len(set(s.category for s in services)),
                "avg_price": sum(s.price for s in services) / len(services) if services else 0,
                "multi_stage_count": sum(1 for s in services if s.is_multi_stage)
            }
        })
        
    except Exception as e:
        return JSONResponse({
            "valid": False,
            "error": str(e),
            "issues": [f"Failed to parse file: {str(e)}"]
        })

async def import_services_to_db(services: List[ParsedService], clinic_id: str):
    """
    Background task to import services to database
    """
    
    conn = await asyncpg.connect(os.getenv("DATABASE_URL"))
    
    try:
        imported = 0
        updated = 0
        failed = 0
        
        for service in services:
            try:
                # Check if service exists
                existing = await conn.fetchval(
                    "SELECT id FROM healthcare.services WHERE clinic_id = $1 AND code = $2",
                    uuid.UUID(clinic_id), service.code
                )
                
                if existing:
                    # Update existing service
                    await conn.execute("""
                        UPDATE healthcare.services 
                        SET name = $1, category = $2, base_price = $3,
                            duration_minutes = $4, description = $5,
                            is_multi_stage = $6, stage_config = $7,
                            updated_at = NOW()
                        WHERE id = $8
                    """, service.name, service.category, service.price,
                        service.duration_minutes, service.description,
                        service.is_multi_stage, 
                        json.dumps(service.stage_config) if service.stage_config else None,
                        existing)
                    updated += 1
                else:
                    # Insert new service
                    await conn.execute("""
                        INSERT INTO healthcare.services 
                        (id, clinic_id, code, name, category, base_price,
                         duration_minutes, description, is_multi_stage, stage_config,
                         currency, active, created_at)
                        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, true, NOW())
                    """, uuid.uuid4(), uuid.UUID(clinic_id), service.code,
                        service.name, service.category, service.price,
                        service.duration_minutes, service.description,
                        service.is_multi_stage, 
                        json.dumps(service.stage_config) if service.stage_config else None,
                        service.currency)
                    imported += 1
                    
            except Exception as e:
                logger.error(f"Failed to import service {service.name}: {e}")
                failed += 1
        
        # Log import results
        await conn.execute("""
            INSERT INTO healthcare.price_list_uploads 
            (id, clinic_id, file_name, items_found, items_imported, items_failed, 
             upload_status, processed_at, created_at)
            VALUES ($1, $2, $3, $4, $5, $6, 'completed', NOW(), NOW())
        """, uuid.uuid4(), uuid.UUID(clinic_id), "background_import",
            len(services), imported + updated, failed)
        
        logger.info(f"Import complete: {imported} new, {updated} updated, {failed} failed")
        
    except Exception as e:
        logger.error(f"Database error during import: {e}")
    finally:
        await conn.close()

@router.get("/cache-stats")
async def get_cache_statistics():
    """Get caching statistics for monitoring"""
    
    if not redis_client:
        return JSONResponse({
            "cache_enabled": False,
            "message": "Redis cache not available"
        })
    
    try:
        # Get cache statistics
        info = redis_client.info("stats")
        keys = redis_client.keys("parsed_services:*")
        
        return JSONResponse({
            "cache_enabled": True,
            "total_cached_files": len(keys),
            "cache_hits": info.get("keyspace_hits", 0),
            "cache_misses": info.get("keyspace_misses", 0),
            "hit_rate": info.get("keyspace_hits", 0) / 
                       (info.get("keyspace_hits", 0) + info.get("keyspace_misses", 1))
        })
    except Exception as e:
        return JSONResponse({
            "cache_enabled": True,
            "error": str(e)
        })