"""Onboarding Readiness API
Provides endpoints for checking clinic readiness for activation and activating clinics.
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List
import logging
from app.database import get_healthcare_client, get_main_client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/onboarding", tags=["onboarding-readiness"])


class ReadinessCheck(BaseModel):
    ready: bool
    missing: List[str]
    warnings: List[str]
    clinic_id: str


class ActivationRequest(BaseModel):
    clinic_id: str


@router.get("/readiness")
async def check_readiness(clinic_id: str) -> ReadinessCheck:
    """Check if clinic meets minimum requirements for activation."""
    supabase = get_healthcare_client()
    missing = []
    warnings = []

    # Check clinic exists and get basic info
    try:
        clinic = supabase.table("clinics").select("*").eq("id", clinic_id).single().execute()
    except Exception as e:
        logger.error(f"Failed to fetch clinic {clinic_id}: {e}")
        raise HTTPException(status_code=404, detail="Clinic not found")

    if not clinic.data:
        raise HTTPException(status_code=404, detail="Clinic not found")

    clinic_data = clinic.data

    # Required checks
    if not clinic_data.get("business_hours") or clinic_data["business_hours"] == {}:
        missing.append("business_hours")

    if not clinic_data.get("timezone"):
        missing.append("timezone")

    # Check for at least one doctor
    try:
        doctors = supabase.table("doctors").select("id").eq("clinic_id", clinic_id).execute()
        if not doctors.data or len(doctors.data) < 1:
            missing.append("provider")
    except Exception as e:
        logger.warning(f"Failed to check doctors for clinic {clinic_id}: {e}")
        missing.append("provider")

    # Check for at least one service with price
    try:
        services = supabase.table("services").select("id, base_price").eq("clinic_id", clinic_id).execute()
        if not services.data or len(services.data) < 1:
            missing.append("service")
        elif all(s.get("base_price") is None for s in services.data):
            warnings.append("no_service_prices")
    except Exception as e:
        logger.warning(f"Failed to check services for clinic {clinic_id}: {e}")
        missing.append("service")

    # Check for WhatsApp or fallback channel (warning only, not blocking)
    try:
        org_id = clinic_data.get("organization_id")
        if org_id:
            integrations = supabase.table("integrations").select("*").eq(
                "organization_id", org_id
            ).eq("type", "whatsapp").eq("enabled", True).execute()

            if not integrations.data or len(integrations.data) < 1:
                warnings.append("whatsapp_not_connected")
    except Exception as e:
        logger.warning(f"Failed to check WhatsApp integration for clinic {clinic_id}: {e}")
        warnings.append("whatsapp_not_connected")

    # Check for FAQs (warning only)
    try:
        # FAQs are in public schema
        public_client = get_main_client()
        faqs = public_client.table("faqs").select("id").eq("clinic_id", clinic_id).execute()
        if not faqs.data or len(faqs.data) < 5:
            warnings.append("few_faqs")
    except Exception as e:
        logger.warning(f"Failed to check FAQs for clinic {clinic_id}: {e}")
        warnings.append("few_faqs")

    return ReadinessCheck(
        ready=len(missing) == 0,
        missing=missing,
        warnings=warnings,
        clinic_id=clinic_id
    )


@router.post("/activate")
async def activate_clinic(request: ActivationRequest):
    """Activate clinic if all requirements are met."""
    supabase = get_healthcare_client()

    # First check readiness
    readiness = await check_readiness(request.clinic_id)

    if not readiness.ready:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "Clinic not ready for activation",
                "missing": readiness.missing
            }
        )

    # Update clinic status to active
    try:
        result = supabase.table("clinics").update({
            "status": "active"
        }).eq("id", request.clinic_id).execute()

        if not result.data:
            raise HTTPException(status_code=500, detail="Failed to activate clinic")

        logger.info(f"Clinic {request.clinic_id} activated successfully")

        return {
            "success": True,
            "clinic_id": request.clinic_id,
            "status": "active",
            "warnings": readiness.warnings
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to activate clinic {request.clinic_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to activate clinic")
