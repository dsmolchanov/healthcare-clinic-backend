from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List, Optional
import logging
import os
import httpx

from ..database import get_healthcare_client, get_main_client

router = APIRouter(prefix="/api/agent", tags=["agent"])
logger = logging.getLogger(__name__)


class Message(BaseModel):
    role: str
    content: str


class SimulationRequest(BaseModel):
    clinic_id: str
    message: str
    conversation_history: Optional[List[Message]] = []


@router.post("/simulate")
async def simulate_agent(request: SimulationRequest):
    """Simulate AI agent response for testing during onboarding."""
    supabase = get_healthcare_client()

    # Get clinic data
    try:
        clinic = (
            supabase.table("clinics")
            .select("*")
            .eq("id", request.clinic_id)
            .single()
            .execute()
        )
    except Exception as e:
        logger.error(f"Failed to fetch clinic: {e}")
        raise HTTPException(status_code=404, detail="Clinic not found")

    if not clinic.data:
        raise HTTPException(status_code=404, detail="Clinic not found")

    clinic_data = clinic.data

    # Get services
    services = (
        supabase.table("services")
        .select("name, base_price, duration_minutes")
        .eq("clinic_id", request.clinic_id)
        .eq("is_active", True)
        .execute()
    )

    # Get FAQs (from public schema)
    public_client = get_main_client()
    faqs = (
        public_client.table("faqs")
        .select("question, answer")
        .eq("clinic_id", request.clinic_id)
        .eq("is_active", True)
        .limit(20)
        .execute()
    )

    # Build services list
    services_list = ""
    if services.data:
        for s in services.data:
            price = f"${s['base_price']}" if s.get("base_price") else "Price varies"
            duration = f"{s['duration_minutes']} min" if s.get("duration_minutes") else ""
            services_list += f"- {s['name']}: {price}"
            if duration:
                services_list += f" ({duration})"
            services_list += "\n"

    # Build FAQ list
    faq_list = ""
    if faqs.data:
        for f in faqs.data:
            faq_list += f"Q: {f['question']}\nA: {f['answer']}\n\n"

    # Build context
    business_hours = clinic_data.get("business_hours", {})
    hours_str = ""
    if isinstance(business_hours, dict):
        for day, hours in business_hours.items():
            hours_str += f"  {day.capitalize()}: {hours}\n"

    context = f"""You are an AI receptionist for {clinic_data['name']}.

Business Hours:
{hours_str if hours_str else "  Not specified"}

Phone: {clinic_data.get('phone', 'Not available')}

Services offered:
{services_list if services_list else "No services configured yet"}

Frequently Asked Questions:
{faq_list if faq_list else "No FAQs configured yet"}

Instructions:
- Respond helpfully and concisely
- If asked to book an appointment, explain that this is a test mode and real bookings will work once the clinic is activated
- Be friendly and professional
- If you don't know something specific, say so and suggest contacting the clinic directly"""

    try:
        # Use direct OpenAI API call for reliability
        openai_api_key = os.environ.get("OPENAI_API_KEY")
        if not openai_api_key:
            raise ValueError("OPENAI_API_KEY not configured")

        messages = [
            {"role": "system", "content": context},
            *[{"role": m.role, "content": m.content} for m in (request.conversation_history or [])],
            {"role": "user", "content": request.message},
        ]

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {openai_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "gpt-4o-mini",
                    "messages": messages,
                    "temperature": 0.7,
                    "max_tokens": 500,
                },
            )
            response.raise_for_status()
            data = response.json()

        content = data["choices"][0]["message"]["content"]
        return {"success": True, "response": content}

    except httpx.HTTPStatusError as e:
        logger.error(f"OpenAI API error: {e.response.text}")
        raise HTTPException(
            status_code=500, detail=f"AI service error: {e.response.status_code}"
        )
    except Exception as e:
        logger.error(f"Agent simulation failed: {e}")
        raise HTTPException(
            status_code=500, detail=f"Failed to generate response: {str(e)}"
        )
