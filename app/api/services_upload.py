"""
API endpoints for uploading and managing medical services price lists
"""

from fastapi import APIRouter, UploadFile, File, HTTPException, Depends, Form
from fastapi.responses import JSONResponse
from typing import List, Optional
import asyncpg
import os
import uuid
from datetime import datetime
import tempfile
from pathlib import Path
import logging

from ..services.grok_price_parser import GrokPriceListParser, FileType, ParsedService
from ..database import get_db_connection

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/services", tags=["services"])

@router.post("/upload-price-list")
async def upload_price_list(
    file: UploadFile = File(...),
    clinic_id: str = Form(...),
    auto_import: bool = Form(True),
    conn: asyncpg.Connection = Depends(get_db_connection)
):
    """
    Upload and parse a price list file (CSV, PDF, or image)
    """
    try:
        # Validate file type
        file_extension = Path(file.filename).suffix.lower()
        if file_extension not in ['.csv', '.pdf', '.jpg', '.jpeg', '.png', '.xlsx', '.xls']:
            raise HTTPException(400, f"Unsupported file type: {file_extension}")
        
        # Save uploaded file temporarily
        with tempfile.NamedTemporaryFile(delete=False, suffix=file_extension) as tmp_file:
            content = await file.read()
            tmp_file.write(content)
            tmp_path = tmp_file.name
        
        try:
            # Create upload record
            upload_id = str(uuid.uuid4())
            await conn.execute("""
                INSERT INTO healthcare.price_list_uploads 
                (id, clinic_id, file_name, file_type, upload_status)
                VALUES ($1, $2, $3, $4, 'processing')
            """, upload_id, clinic_id, file.filename, file_extension[1:])
            
            # Initialize parser
            parser = GrokPriceListParser()
            
            # Parse the file
            logger.info(f"Parsing price list file: {file.filename}")
            services = await parser.parse_file(tmp_path)
            
            # Update upload record with results
            await conn.execute("""
                UPDATE healthcare.price_list_uploads 
                SET items_found = $1, upload_status = 'completed', processed_at = NOW()
                WHERE id = $2
            """, len(services), upload_id)
            
            # Auto-import if requested
            imported_count = 0
            failed_count = 0
            errors = []
            
            if auto_import:
                for service in services:
                    try:
                        # Check if service already exists
                        exists = await conn.fetchval("""
                            SELECT id FROM healthcare.services 
                            WHERE clinic_id = $1 AND code = $2
                        """, uuid.UUID(clinic_id), service.code)
                        
                        if exists:
                            # Update existing service
                            await conn.execute("""
                                UPDATE healthcare.services 
                                SET name = $1, category = $2, base_price = $3,
                                    duration_minutes = $4, description = $5,
                                    is_multi_stage = $6, stage_config = $7,
                                    updated_at = NOW()
                                WHERE clinic_id = $8 AND code = $9
                            """, service.name, service.category, service.price,
                                service.duration_minutes, service.description,
                                service.is_multi_stage, 
                                json.dumps(service.stage_config) if service.stage_config else None,
                                uuid.UUID(clinic_id), service.code)
                        else:
                            # Insert new service
                            await conn.execute("""
                                INSERT INTO healthcare.services 
                                (id, clinic_id, code, name, category, base_price,
                                 duration_minutes, description, is_multi_stage, stage_config,
                                 currency, active)
                                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, true)
                            """, str(uuid.uuid4()), uuid.UUID(clinic_id), service.code,
                                service.name, service.category, service.price,
                                service.duration_minutes or 30, service.description,
                                service.is_multi_stage, 
                                json.dumps(service.stage_config) if service.stage_config else None,
                                service.currency)
                        
                        # If service has specialization, link it
                        if service.specialization:
                            spec_id = await conn.fetchval("""
                                SELECT id FROM healthcare.specializations 
                                WHERE code = $1
                            """, service.specialization.upper())
                            
                            if spec_id:
                                await conn.execute("""
                                    UPDATE healthcare.services 
                                    SET specialization_id = $1 
                                    WHERE clinic_id = $2 AND code = $3
                                """, spec_id, uuid.UUID(clinic_id), service.code)
                        
                        imported_count += 1
                        
                    except Exception as e:
                        failed_count += 1
                        errors.append({
                            "service": service.name,
                            "error": str(e)
                        })
                        logger.error(f"Failed to import service {service.name}: {e}")
                
                # Update upload record with import results
                await conn.execute("""
                    UPDATE healthcare.price_list_uploads 
                    SET items_imported = $1, items_failed = $2, error_log = $3
                    WHERE id = $4
                """, imported_count, failed_count, 
                    json.dumps(errors) if errors else None, upload_id)
            
            # Return results
            return JSONResponse({
                "success": True,
                "upload_id": upload_id,
                "items_found": len(services),
                "items_imported": imported_count,
                "items_failed": failed_count,
                "services": [
                    {
                        "code": s.code,
                        "name": s.name,
                        "category": s.category,
                        "price": s.price,
                        "currency": s.currency,
                        "duration_minutes": s.duration_minutes,
                        "specialization": s.specialization,
                        "is_multi_stage": s.is_multi_stage
                    } for s in services
                ],
                "errors": errors if errors else None
            })
            
        finally:
            # Clean up temporary file
            os.unlink(tmp_path)
            
    except Exception as e:
        logger.error(f"Error processing price list upload: {e}")
        
        # Update upload record with failure
        if 'upload_id' in locals():
            await conn.execute("""
                UPDATE healthcare.price_list_uploads 
                SET upload_status = 'failed', error_log = $1
                WHERE id = $2
            """, json.dumps({"error": str(e)}), upload_id)
        
        raise HTTPException(500, f"Failed to process price list: {str(e)}")

@router.get("/services/{clinic_id}")
async def get_clinic_services(
    clinic_id: str,
    category: Optional[str] = None,
    active_only: bool = True,
    conn: asyncpg.Connection = Depends(get_db_connection)
):
    """Get all services for a clinic, optionally filtered by category"""
    
    query = """
        SELECT s.*, 
               sp.name as specialization_name,
               sp.code as specialization_code
        FROM healthcare.services s
        LEFT JOIN healthcare.specializations sp ON s.specialization_id = sp.id
        WHERE s.clinic_id = $1
    """
    params = [uuid.UUID(clinic_id)]
    
    if active_only:
        query += " AND s.active = true"
    
    if category:
        query += " AND s.category = $2"
        params.append(category)
    
    query += " ORDER BY s.category, s.name"
    
    services = await conn.fetch(query, *params)
    
    return JSONResponse({
        "success": True,
        "services": [dict(s) for s in services]
    })

@router.get("/categories/{clinic_id}")
async def get_service_categories(
    clinic_id: str,
    conn: asyncpg.Connection = Depends(get_db_connection)
):
    """Get all unique service categories for a clinic"""
    
    categories = await conn.fetch("""
        SELECT DISTINCT category, COUNT(*) as service_count
        FROM healthcare.services 
        WHERE clinic_id = $1 AND active = true
        GROUP BY category
        ORDER BY category
    """, uuid.UUID(clinic_id))
    
    return JSONResponse({
        "success": True,
        "categories": [dict(c) for c in categories]
    })

@router.post("/parse-from-url")
async def parse_price_list_from_url(
    image_url: str = Form(...),
    clinic_id: str = Form(...)
):
    """Parse a price list from a web URL (for images hosted online)"""
    
    try:
        parser = GrokPriceListParser()
        services = await parser.parse_url(image_url)
        
        return JSONResponse({
            "success": True,
            "services_found": len(services),
            "services": [
                {
                    "code": s.code,
                    "name": s.name,
                    "category": s.category,
                    "price": s.price,
                    "currency": s.currency,
                    "duration_minutes": s.duration_minutes,
                    "specialization": s.specialization,
                    "is_multi_stage": s.is_multi_stage,
                    "stage_config": s.stage_config
                } for s in services
            ]
        })
        
    except Exception as e:
        logger.error(f"Error parsing price list from URL: {e}")
        raise HTTPException(500, f"Failed to parse price list: {str(e)}")

@router.get("/uploads/{clinic_id}")
async def get_price_list_uploads(
    clinic_id: str,
    conn: asyncpg.Connection = Depends(get_db_connection)
):
    """Get history of price list uploads for a clinic"""
    
    uploads = await conn.fetch("""
        SELECT * FROM healthcare.price_list_uploads
        WHERE clinic_id = $1
        ORDER BY created_at DESC
        LIMIT 20
    """, uuid.UUID(clinic_id))
    
    return JSONResponse({
        "success": True,
        "uploads": [dict(u) for u in uploads]
    })

import json  # Add this import at the top