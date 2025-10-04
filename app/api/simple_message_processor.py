"""
Simplified Message Processor for Quick Deployment
Handles WhatsApp messages with basic AI processing (no Redis/Pinecone/Mem0 for MVP)
"""

import os
import json
import uuid
from datetime import datetime
from typing import Dict, Any, Optional, List
from pydantic import BaseModel, Field
from openai import OpenAI
from supabase import create_client, Client

# Initialize OpenAI
openai_client = OpenAI(api_key=os.environ.get('OPENAI_API_KEY'))

# Initialize Supabase
supabase: Client = create_client(
    os.environ.get('SUPABASE_URL', ''),
    os.environ.get('SUPABASE_ANON_KEY', '')
)

class MessageRequest(BaseModel):
    """Request model for incoming WhatsApp messages"""
    from_phone: str
    to_phone: str
    body: str
    message_sid: str
    clinic_id: str
    clinic_name: str
    message_type: str = "text"
    media_url: Optional[str] = None
    channel: str = "whatsapp"
    profile_name: str = "Usuario"
    metadata: Dict[str, Any] = Field(default_factory=dict)

class MessageResponse(BaseModel):
    """Response model for processed messages"""
    message: str
    session_id: str
    status: str = "success"
    metadata: Dict[str, Any] = Field(default_factory=dict)

class SimpleMessageProcessor:
    """Simplified message processor for MVP"""

    def __init__(self):
        self.sessions = {}  # In-memory sessions for MVP

    async def process_message(self, request: MessageRequest) -> MessageResponse:
        """Process incoming WhatsApp message with AI"""

        # 1. Get or create simple session
        session_key = f"{request.clinic_id}:{request.from_phone}"
        if session_key not in self.sessions:
            self.sessions[session_key] = {
                'id': str(uuid.uuid4()),
                'messages': [],
                'created_at': datetime.utcnow().isoformat()
            }

        session = self.sessions[session_key]

        # 2. Add user message to session
        session['messages'].append({
            'role': 'user',
            'content': request.body
        })

        # Keep only last 10 messages
        if len(session['messages']) > 10:
            session['messages'] = session['messages'][-10:]

        # 3. Build context for AI
        context = f"""Eres un asistente virtual de {request.clinic_name}, una clínica dental en México.

Información de la clínica:
- Nombre: {request.clinic_name}
- Horario: Lunes a Viernes 9:00-19:00, Sábado 9:00-14:00
- Servicios: Limpieza dental, blanqueamiento, ortodoncia, implantes, emergencias
- Ubicación: Ciudad de México

Instrucciones:
1. Responde en español de manera amable y profesional
2. Si preguntan por citas, menciona que pueden agendar llamando a la clínica
3. Para emergencias, sugiere que llamen inmediatamente
4. Mantén las respuestas concisas (máximo 3 oraciones)
5. Si no sabes algo, sugiere que llamen para más información

Responde al siguiente mensaje del paciente:"""

        # 4. Generate AI response
        ai_response = await self._generate_response(
            user_message=request.body,
            context=context,
            session_history=session['messages'][:-1]  # Exclude current message
        )

        # 5. Store response in session
        session['messages'].append({
            'role': 'assistant',
            'content': ai_response
        })

        # 6. Log to database (optional for MVP)
        try:
            await self._log_conversation(
                session_id=session['id'],
                clinic_id=request.clinic_id,
                user_message=request.body,
                ai_response=ai_response
            )
        except Exception as e:
            print(f"Error logging to database: {e}")

        return MessageResponse(
            message=ai_response,
            session_id=session['id'],
            status="success",
            metadata={
                "message_count": len(session['messages'])
            }
        )

    async def _generate_response(
        self,
        user_message: str,
        context: str,
        session_history: List[Dict]
    ) -> str:
        """Generate AI response using OpenAI"""

        messages = [
            {"role": "system", "content": context}
        ]

        # Add recent conversation history
        for msg in session_history[-4:]:  # Last 4 messages
            messages.append({
                "role": msg['role'],
                "content": msg['content']
            })

        # Add current message
        messages.append({"role": "user", "content": user_message})

        try:
            response = openai_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=messages,
                temperature=0.7,
                max_tokens=300
            )

            return response.choices[0].message.content
        except Exception as e:
            print(f"Error generating AI response: {e}")
            # Fallback responses based on common queries
            user_lower = user_message.lower()

            if any(word in user_lower for word in ['cita', 'agendar', 'appointment']):
                return "Para agendar una cita, por favor llame a la clínica directamente. Nuestro personal le ayudará a encontrar el mejor horario disponible."
            elif any(word in user_lower for word in ['precio', 'costo', 'cuánto']):
                return "Los precios varían según el tratamiento. Le invitamos a visitarnos para una evaluación gratuita y presupuesto personalizado."
            elif any(word in user_lower for word in ['emergencia', 'dolor', 'urgente']):
                return "Para emergencias dentales, por favor llame a la clínica inmediatamente. Si es fuera de horario, acuda al servicio de urgencias más cercano."
            elif any(word in user_lower for word in ['horario', 'abierto', 'hora']):
                return "Nuestro horario es: Lunes a Viernes 9:00-19:00, Sábado 9:00-14:00. Domingos cerrado."
            else:
                return "Gracias por su mensaje. Un miembro de nuestro equipo le responderá pronto. Para asistencia inmediata, por favor llame a la clínica."

    async def _log_conversation(
        self,
        session_id: str,
        clinic_id: str,
        user_message: str,
        ai_response: str
    ):
        """Log conversation to database"""
        try:
            # Try to log to Supabase (may fail if table doesn't exist)
            result = await supabase.table('conversation_logs').insert({
                'session_id': session_id,
                'organization_id': clinic_id,
                'user_message': user_message,
                'ai_response': ai_response,
                'channel': 'whatsapp',
                'created_at': datetime.utcnow().isoformat()
            }).execute()
        except Exception as e:
            # If logging fails, just print error (non-critical for MVP)
            print(f"Could not log to database: {e}")

# FastAPI endpoint handler
async def handle_process_message(request: MessageRequest) -> MessageResponse:
    """Main endpoint handler for processing messages"""
    processor = SimpleMessageProcessor()
    return await processor.process_message(request)
