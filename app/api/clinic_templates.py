"""Clinic Templates API
Provides endpoints for applying clinic type templates to seed services and FAQs.
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Literal, List, Optional
import logging
from app.database import get_healthcare_client, get_main_client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/onboarding", tags=["clinic-templates"])


class TemplateRequest(BaseModel):
    clinic_id: str
    template_type: Literal["dental", "medical", "specialist", "custom"]
    specialty: Optional[str] = None  # For specialist type
    currency: str = "USD"
    language: str = "en"


class AddProviderRequest(BaseModel):
    clinic_id: str
    name: str
    email: Optional[str] = None
    specialization: Optional[str] = None
    phone: Optional[str] = None


# Template definitions for dental services
DENTAL_SERVICES: List[dict] = [
    {"name": "General Checkup", "category": "General", "description": "Comprehensive dental examination", "base_price": 150, "duration_minutes": 30},
    {"name": "Teeth Cleaning", "category": "Preventive", "description": "Professional dental cleaning", "base_price": 120, "duration_minutes": 45},
    {"name": "Cavity Filling", "category": "Restorative", "description": "Composite filling for cavities", "base_price": 200, "duration_minutes": 60},
    {"name": "Teeth Whitening", "category": "Cosmetic", "description": "Professional whitening treatment", "base_price": 500, "duration_minutes": 90},
    {"name": "Root Canal", "category": "Endodontics", "description": "Root canal therapy", "base_price": 1500, "duration_minutes": 120},
    {"name": "Tooth Extraction", "category": "Surgery", "description": "Simple tooth extraction", "base_price": 250, "duration_minutes": 45},
    {"name": "Dental Crown", "category": "Restorative", "description": "Porcelain crown installation", "base_price": 1200, "duration_minutes": 120},
    {"name": "X-Ray", "category": "Diagnostic", "description": "Dental X-ray imaging", "base_price": 75, "duration_minutes": 15},
    {"name": "Emergency Visit", "category": "General", "description": "Emergency dental care", "base_price": 200, "duration_minutes": 60},
    {"name": "Consultation", "category": "General", "description": "General consultation", "base_price": 75, "duration_minutes": 30},
]

# Template definitions for dental FAQs
DENTAL_FAQS: List[dict] = [
    {"question": "What are your office hours?", "answer": "Please check our business hours on the clinic page. We're happy to accommodate your schedule.", "category": "general", "priority": 95, "is_featured": True},
    {"question": "Do you accept walk-ins?", "answer": "We prefer scheduled appointments to ensure you receive the best care, but we do accommodate emergencies when possible.", "category": "general", "priority": 90},
    {"question": "What forms of payment do you accept?", "answer": "We accept cash, credit cards, and debit cards. Payment plans may be available for larger procedures.", "category": "pricing", "priority": 90},
    {"question": "How much does a cleaning cost?", "answer": "Our professional cleaning service starts at $120. Final cost may vary based on your specific needs.", "category": "pricing", "priority": 85},
    {"question": "How much does a checkup cost?", "answer": "A comprehensive dental checkup is $150. This includes examination and any necessary X-rays.", "category": "pricing", "priority": 85},
    {"question": "Do you offer payment plans?", "answer": "Yes, we offer payment plans for certain procedures. Please ask our staff for details.", "category": "pricing", "priority": 80},
    {"question": "What should I bring to my first visit?", "answer": "Please bring a valid ID, insurance card (if applicable), and any relevant medical/dental records.", "category": "policies", "priority": 85},
    {"question": "How do I reschedule my appointment?", "answer": "You can reschedule by calling our office or messaging us through WhatsApp. We ask for at least 24 hours notice.", "category": "policies", "priority": 80},
    {"question": "What is your cancellation policy?", "answer": "We request at least 24 hours notice for cancellations. Late cancellations may incur a fee.", "category": "policies", "priority": 80},
    {"question": "Do you take insurance?", "answer": "We work with many insurance providers. Please contact us with your insurance details to verify coverage.", "category": "general", "priority": 90},
]

# Medical clinic services template
MEDICAL_SERVICES: List[dict] = [
    {"name": "General Consultation", "category": "General", "description": "Standard medical consultation", "base_price": 100, "duration_minutes": 30},
    {"name": "Annual Physical", "category": "Preventive", "description": "Comprehensive annual health examination", "base_price": 200, "duration_minutes": 60},
    {"name": "Vaccination", "category": "Preventive", "description": "Standard vaccination administration", "base_price": 50, "duration_minutes": 15},
    {"name": "Blood Test", "category": "Diagnostic", "description": "Basic blood panel analysis", "base_price": 75, "duration_minutes": 15},
    {"name": "Follow-up Visit", "category": "General", "description": "Follow-up appointment", "base_price": 75, "duration_minutes": 20},
]

MEDICAL_FAQS: List[dict] = [
    {"question": "What are your office hours?", "answer": "Please check our business hours on the clinic page. We're happy to accommodate your schedule.", "category": "general", "priority": 95, "is_featured": True},
    {"question": "Do you accept walk-ins?", "answer": "We prefer scheduled appointments, but we do our best to accommodate urgent cases.", "category": "general", "priority": 90},
    {"question": "What forms of payment do you accept?", "answer": "We accept cash, credit cards, and most major insurance plans.", "category": "pricing", "priority": 90},
    {"question": "How much does a consultation cost?", "answer": "A general consultation is $100. Please contact us for specific pricing.", "category": "pricing", "priority": 85},
    {"question": "Do you take insurance?", "answer": "Yes, we work with many insurance providers. Please contact us to verify your coverage.", "category": "general", "priority": 90},
]


@router.post("/apply-template")
async def apply_template(request: TemplateRequest):
    """Apply a clinic type template to seed services and FAQs."""
    healthcare_client = get_healthcare_client()
    public_client = get_main_client()

    # Verify clinic exists and is in draft status
    try:
        clinic = healthcare_client.table("clinics").select("*").eq("id", request.clinic_id).single().execute()
    except Exception as e:
        logger.error(f"Failed to fetch clinic {request.clinic_id}: {e}")
        raise HTTPException(status_code=404, detail="Clinic not found")

    if not clinic.data:
        raise HTTPException(status_code=404, detail="Clinic not found")

    if clinic.data.get("status") == "active":
        raise HTTPException(status_code=400, detail="Cannot apply template to active clinic")

    services_seeded = 0
    faqs_seeded = 0

    # Select template based on type
    if request.template_type == "dental":
        services_template = DENTAL_SERVICES
        faqs_template = DENTAL_FAQS
    elif request.template_type == "medical":
        services_template = MEDICAL_SERVICES
        faqs_template = MEDICAL_FAQS
    elif request.template_type == "custom":
        # No seeding for custom, user adds manually
        services_template = []
        faqs_template = []
    else:
        # Specialist or other - use medical as base
        services_template = MEDICAL_SERVICES
        faqs_template = MEDICAL_FAQS

    # Seed services
    for svc in services_template:
        try:
            service_data = {
                "clinic_id": request.clinic_id,
                "name": svc["name"],
                "category": svc["category"],
                "description": svc["description"],
                "base_price": svc["base_price"],
                "duration_minutes": svc["duration_minutes"],
                "is_active": True
            }
            result = healthcare_client.table("services").insert(service_data).execute()
            if result.data:
                services_seeded += 1
        except Exception as e:
            # Log but continue - might be duplicate
            logger.warning(f"Failed to seed service {svc['name']}: {e}")

    # Seed FAQs (in public schema)
    for faq in faqs_template:
        try:
            faq_data = {
                "clinic_id": request.clinic_id,
                "question": faq["question"],
                "answer": faq["answer"],
                "category": faq["category"],
                "language": request.language,
                "priority": faq.get("priority", 75),
                "is_featured": faq.get("is_featured", False),
                "is_active": True
            }
            result = public_client.table("faqs").insert(faq_data).execute()
            if result.data:
                faqs_seeded += 1
        except Exception as e:
            # Log but continue - might be duplicate
            logger.warning(f"Failed to seed FAQ {faq['question'][:30]}...: {e}")

    # Update clinic with currency and language
    try:
        healthcare_client.table("clinics").update({
            "currency": request.currency,
            "primary_language": request.language
        }).eq("id", request.clinic_id).execute()
    except Exception as e:
        logger.warning(f"Failed to update clinic currency/language: {e}")

    return {
        "success": True,
        "template_type": request.template_type,
        "services_seeded": services_seeded,
        "faqs_seeded": faqs_seeded
    }


@router.get("/templates")
async def list_templates():
    """List available clinic templates."""
    return {
        "templates": [
            {
                "type": "dental",
                "title": "Dental Clinic",
                "description": "Dental services, cleanings, procedures",
                "services_count": len(DENTAL_SERVICES),
                "faqs_count": len(DENTAL_FAQS)
            },
            {
                "type": "medical",
                "title": "Medical Clinic",
                "description": "General medicine, consultations",
                "services_count": len(MEDICAL_SERVICES),
                "faqs_count": len(MEDICAL_FAQS)
            },
            {
                "type": "specialist",
                "title": "Specialist",
                "description": "Dermatology, Cardiology, etc.",
                "services_count": 0,
                "faqs_count": 0
            },
            {
                "type": "custom",
                "title": "Custom",
                "description": "Start from scratch",
                "services_count": 0,
                "faqs_count": 0
            }
        ]
    }


@router.post("/add-provider")
async def add_provider(request: AddProviderRequest):
    """Add a provider/doctor to a clinic during onboarding."""
    healthcare_client = get_healthcare_client()

    # Verify clinic exists
    try:
        clinic = healthcare_client.table("clinics").select("id, name").eq("id", request.clinic_id).single().execute()
    except Exception as e:
        logger.error(f"Failed to fetch clinic {request.clinic_id}: {e}")
        raise HTTPException(status_code=404, detail="Clinic not found")

    if not clinic.data:
        raise HTTPException(status_code=404, detail="Clinic not found")

    # Create the doctor
    try:
        # Generate default working hours (9 AM - 6 PM weekdays)
        default_working_hours = {
            "monday": {"start": "09:00", "end": "18:00"},
            "tuesday": {"start": "09:00", "end": "18:00"},
            "wednesday": {"start": "09:00", "end": "18:00"},
            "thursday": {"start": "09:00", "end": "18:00"},
            "friday": {"start": "09:00", "end": "17:00"},
            "saturday": None,
            "sunday": None
        }

        doctor_data = {
            "clinic_id": request.clinic_id,
            "name": request.name,
            "email": request.email,
            "phone": request.phone,
            "specialization": request.specialization,
            "working_hours": default_working_hours,
            "active": True
        }

        result = healthcare_client.table("doctors").insert(doctor_data).execute()

        if not result.data:
            raise HTTPException(status_code=500, detail="Failed to create provider")

        doctor = result.data[0]
        logger.info(f"Created provider {request.name} for clinic {request.clinic_id}")

        return {
            "success": True,
            "doctor_id": doctor.get("id"),
            "name": request.name
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to create provider for clinic {request.clinic_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to create provider: {str(e)}")
