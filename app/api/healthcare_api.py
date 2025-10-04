"""
Healthcare API - Direct doctor specialties endpoints
Handles doctor specialty management for the frontend medical director dashboard
"""

import os
import logging
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from supabase import create_client, Client
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configure logging
logger = logging.getLogger(__name__)

# Initialize Supabase client
supabase_url = os.getenv("SUPABASE_URL")
supabase_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_ANON_KEY")

if not supabase_url or not supabase_key:
    raise ValueError("Supabase credentials are required")

try:
    from supabase.client import ClientOptions
    options = ClientOptions(
        schema='healthcare',
        auto_refresh_token=True,
        persist_session=False
    )
    supabase: Client = create_client(supabase_url, supabase_key, options=options)
except Exception as e:
    logger.error(f"Failed to connect to Supabase: {e}")
    raise

# Create router - this matches the frontend's expected URL pattern
router = APIRouter(prefix="/api/healthcare", tags=["healthcare"])

# ============================================================================
# PYDANTIC MODELS
# ============================================================================

class SpecialtyAssignmentUpdate(BaseModel):
    approval_status: Optional[str] = Field(None, pattern="^(pending|approved|rejected)$")
    assignment_confidence: Optional[float] = Field(None, ge=0.0, le=1.0)
    assignment_method: Optional[str] = None
    requires_approval: Optional[bool] = None
    approval_notes: Optional[str] = None

# ============================================================================
# DOCTOR SPECIALTIES ENDPOINTS
# ============================================================================

@router.patch("/doctor-specialties/{assignment_id}")
async def update_doctor_specialty_assignment(assignment_id: str, request: SpecialtyAssignmentUpdate):
    """Update a doctor specialty assignment"""
    try:
        # Check if assignment exists
        existing_result = supabase.table("doctor_specialties")\
            .select("*")\
            .eq("id", assignment_id)\
            .execute()

        if not existing_result.data:
            raise HTTPException(status_code=404, detail="Assignment not found")

        # Build update data from non-None fields
        update_data = {}
        for field, value in request.dict(exclude_none=True).items():
            update_data[field] = value

        # If approving, set approved_at timestamp
        if update_data.get("approval_status") == "approved":
            update_data["approved_at"] = datetime.utcnow().isoformat()

        if not update_data:
            raise HTTPException(status_code=400, detail="No fields to update")

        # Update the assignment
        result = supabase.table("doctor_specialties")\
            .update(update_data)\
            .eq("id", assignment_id)\
            .execute()

        if result.data:
            # If approved, trigger service eligibility derivation
            if update_data.get("approval_status") == "approved":
                try:
                    assignment = existing_result.data[0]
                    # Call auto-derive function
                    supabase.rpc("auto_derive_doctor_service_eligibility", {
                        "p_doctor_id": assignment["doctor_id"],
                        "p_confidence_threshold": 0.80
                    }).execute()
                except Exception as e:
                    logger.warning(f"Failed to auto-derive eligibilities: {e}")

            return {
                "success": True,
                "message": "Specialty assignment updated successfully",
                "data": result.data[0]
            }
        else:
            raise HTTPException(status_code=500, detail="Failed to update specialty assignment")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating assignment: {e}")
        raise HTTPException(status_code=500, detail="Failed to update specialty assignment")

@router.delete("/doctor-specialties/{assignment_id}")
async def delete_doctor_specialty_assignment(assignment_id: str):
    """Delete a doctor specialty assignment"""
    try:
        result = supabase.table("doctor_specialties")\
            .delete()\
            .eq("id", assignment_id)\
            .execute()

        if result.data:
            return {
                "success": True,
                "message": "Specialty assignment deleted successfully"
            }
        else:
            raise HTTPException(status_code=404, detail="Assignment not found")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting assignment: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete specialty assignment")

@router.get("/doctor-specialties/{assignment_id}")
async def get_doctor_specialty_assignment(assignment_id: str):
    """Get a specific doctor specialty assignment"""
    try:
        result = supabase.table("doctor_specialties")\
            .select("*, specialties(*), doctors(*)")\
            .eq("id", assignment_id)\
            .execute()

        if result.data:
            return result.data[0]
        else:
            raise HTTPException(status_code=404, detail="Assignment not found")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching assignment: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch specialty assignment")