"""
Message Processor with Redis, Pinecone, and Mem0 Integration
Handles WhatsApp messages from API server with AI processing
"""

import os
import json
import uuid
import redis
import asyncio
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List
from pydantic import BaseModel, Field
from pinecone import Pinecone, ServerlessSpec
from mem0 import Memory
from openai import OpenAI
from fastapi import HTTPException
from supabase import create_client, Client

# Initialize services
redis_client = redis.Redis(
    host=os.environ.get('REDIS_HOST', 'localhost'),
    port=int(os.environ.get('REDIS_PORT', 6379)),
    decode_responses=True,
    db=0
)

# Initialize Pinecone for vector search (only if API key is available)
try:
    pinecone_api_key = os.environ.get('PINECONE_API_KEY', '')
    if pinecone_api_key:
        pc = Pinecone(api_key=pinecone_api_key)
    else:
        pc = None
        print("Warning: PINECONE_API_KEY not set, Pinecone functionality disabled")
except Exception as e:
    pc = None
    print(f"Warning: Failed to initialize Pinecone: {e}")

# Initialize Mem0 for conversation memory (with proper config)
try:
    memory = Memory.from_config({
        "llm": {
            "provider": "openai",
            "config": {
                "model": "gpt-4o-mini",
                "temperature": 0.2,
                "max_tokens": 1500,
            }
        },
        "embedder": {
            "provider": "openai",
            "config": {
                "model": "text-embedding-3-small"
            }
        },
        "vector_store": {
            "provider": "pinecone",
            "config": {
                "api_key": os.environ.get('PINECONE_API_KEY'),
                "environment": os.environ.get('PINECONE_ENV', 'us-east1-gcp'),
                "collection_name": "clinic-memories"  # Changed from index_name
            }
        }
    })
except Exception as e:
    print(f"Warning: Failed to initialize mem0: {e}")
    memory = None

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

class RedisSessionManager:
    """Manages conversation sessions in Redis"""

    @staticmethod
    def get_session_key(phone: str, clinic_id: str) -> str:
        """Generate Redis key for session"""
        return f"session:{clinic_id}:{phone}"

    @staticmethod
    def get_or_create_session(phone: str, clinic_id: str) -> Dict[str, Any]:
        """Get existing session or create new one"""
        session_key = RedisSessionManager.get_session_key(phone, clinic_id)

        # Try to get existing session
        session_data = redis_client.get(session_key)

        if session_data:
            session = json.loads(session_data)
            # Update last activity
            session['last_activity'] = datetime.utcnow().isoformat()
        else:
            # Create new session
            session = {
                'id': str(uuid.uuid4()),
                'clinic_id': clinic_id,
                'phone': phone,
                'created_at': datetime.utcnow().isoformat(),
                'last_activity': datetime.utcnow().isoformat(),
                'messages': [],
                'context': {}
            }

        # Save session with 24 hour expiry
        redis_client.setex(
            session_key,
            86400,  # 24 hours
            json.dumps(session)
        )

        return session

    @staticmethod
    def add_message(session: Dict[str, Any], role: str, content: str) -> None:
        """Add message to session history"""
        session['messages'].append({
            'role': role,
            'content': content,
            'timestamp': datetime.utcnow().isoformat()
        })

        # Keep only last 20 messages
        if len(session['messages']) > 20:
            session['messages'] = session['messages'][-20:]

        # Save updated session
        session_key = RedisSessionManager.get_session_key(
            session['phone'],
            session['clinic_id']
        )
        redis_client.setex(
            session_key,
            86400,
            json.dumps(session)
        )

class PineconeKnowledgeBase:
    """Manages clinic knowledge base with Pinecone vector search"""

    def __init__(self, clinic_id: str):
        self.clinic_id = clinic_id
        # Ensure index name is lowercase with hyphens only
        safe_clinic_id = clinic_id.lower().replace('_', '-').replace(' ', '-')
        # Match the index name used by ingestion pipeline
        self.index_name = f"clinic-{safe_clinic_id}-kb"  # Changed from -knowledge to -kb
        self.index = None

        # Create index if Pinecone is available
        if pc:
            try:
                existing_indexes = [index.name for index in pc.list_indexes()]
                if self.index_name not in existing_indexes:
                    pc.create_index(
                        name=self.index_name,
                        dimension=1536,  # OpenAI embedding dimension
                        metric='cosine',
                        spec=ServerlessSpec(
                            cloud='aws',
                            region='us-east-1'
                        )
                    )
                self.index = pc.Index(self.index_name)
            except Exception as e:
                print(f"Warning: Failed to create/access Pinecone index: {e}")
                self.index = None

    async def search(self, query: str, top_k: int = 3) -> List[str]:
        """Search knowledge base for relevant information"""
        if not self.index:
            print("Warning: Pinecone index not available, returning empty results")
            return []
            
        try:
            # Get embedding for query
            response = openai_client.embeddings.create(
                model="text-embedding-3-small",
                input=query
            )
            query_embedding = response.data[0].embedding

            # Search Pinecone
            results = self.index.query(
                vector=query_embedding,
                top_k=top_k,
                include_metadata=True
            )

            # Extract relevant text
            relevant_texts = []
            for match in results.matches:
                if match.score > 0.7:  # Relevance threshold
                    relevant_texts.append(match.metadata.get('text', ''))

            return relevant_texts
        except Exception as e:
            print(f"Warning: Search failed: {e}")
            return []
    
    async def search_by_category(
        self,
        query: str,
        category: str,
        top_k: int = 3
    ) -> List[Dict[str, Any]]:
        """Search within a specific knowledge category"""
        if not self.index:
            print("Warning: Pinecone index not available, returning empty results")
            return []
        
        try:
            # Get query embedding
            response = openai_client.embeddings.create(
                model="text-embedding-3-small",
                input=query
            )
            query_embedding = response.data[0].embedding
            
            # Search with category filter
            results = self.index.query(
                vector=query_embedding,
                top_k=top_k,
                include_metadata=True,
                filter={
                    "category": {"$eq": category},
                    "clinic_id": {"$eq": self.clinic_id}
                }
            )
            
            # Process results with source attribution
            relevant_docs = []
            for match in results.matches:
                if match.score > 0.7:  # Maintain threshold
                    relevant_docs.append({
                        'text': match.metadata.get('text', ''),
                        'category': match.metadata.get('category'),
                        'source': match.metadata.get('source', 'knowledge_base'),
                        'confidence': match.score,
                        'doc_id': match.metadata.get('doc_id')
                    })
            
            return relevant_docs
        except Exception as e:
            print(f"Warning: Category search failed: {e}")
            return []

class ConversationMemory:
    """Manages long-term conversation memory with Mem0"""

    @staticmethod
    async def remember(user_id: str, content: str, metadata: Dict[str, Any] = None):
        """Store important information in long-term memory"""
        if memory:
            memory.add(
                messages=content,
                user_id=user_id,
                metadata=metadata or {}
            )
        else:
            print("Warning: mem0 not available, skipping memory storage")

    @staticmethod
    async def recall(user_id: str, query: str = None) -> List[Dict]:
        """Retrieve relevant memories for user"""
        if not memory:
            print("Warning: mem0 not available, returning empty memories")
            return []
            
        if query:
            memories = memory.search(query=query, user_id=user_id, limit=5)
        else:
            memories = memory.get_all(user_id=user_id, limit=10)

        return memories

class MessageProcessor:
    """Main message processing logic with AI"""

    def __init__(self):
        self.session_manager = RedisSessionManager()

    async def process_message(self, request: MessageRequest) -> MessageResponse:
        """Process incoming WhatsApp message with AI"""

        # 1. Get or create session
        session = self.session_manager.get_or_create_session(
            request.from_phone,
            request.clinic_id
        )

        # 2. Add user message to session
        self.session_manager.add_message(session, "user", request.body)

        # 3. Get user memories
        user_id = f"{request.clinic_id}:{request.from_phone}"
        memories = await ConversationMemory.recall(user_id)

        # 4. Search knowledge base
        kb = PineconeKnowledgeBase(request.clinic_id)
        relevant_info = await kb.search(request.body)

        # 5. Build context for AI
        context = self._build_context(
            session=session,
            memories=memories,
            knowledge=relevant_info,
            clinic_name=request.clinic_name
        )

        # 6. Generate AI response
        ai_response = await self._generate_response(
            user_message=request.body,
            context=context,
            session_history=session['messages'][-10:]  # Last 10 messages
        )

        # 7. Store response in session
        self.session_manager.add_message(session, "assistant", ai_response)

        # 8. Extract and store important information
        await self._extract_and_store_info(
            user_id=user_id,
            user_message=request.body,
            ai_response=ai_response
        )

        # 9. Log to database for audit
        await self._log_conversation(
            session_id=session['id'],
            clinic_id=request.clinic_id,
            user_message=request.body,
            ai_response=ai_response
        )

        return MessageResponse(
            message=ai_response,
            session_id=session['id'],
            status="success",
            metadata={
                "memories_used": len(memories),
                "knowledge_used": len(relevant_info)
            }
        )

    def _build_context(
        self,
        session: Dict,
        memories: List[Dict],
        knowledge: List[str],
        clinic_name: str
    ) -> str:
        """Build context string for AI"""
        context_parts = [
            f"Eres un asistente virtual de {clinic_name}.",
            "Eres amable, profesional y servicial.",
            ""
        ]

        if memories:
            context_parts.append("Información recordada del paciente:")
            for mem in memories[:3]:
                context_parts.append(f"- {mem.get('memory', '')}")
            context_parts.append("")

        if knowledge:
            context_parts.append("Información relevante de la clínica:")
            for info in knowledge[:2]:
                context_parts.append(f"- {info}")
            context_parts.append("")

        context_parts.append("Responde en español de manera concisa y útil.")

        return "\n".join(context_parts)

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

        # Add conversation history
        for msg in session_history:
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
                max_tokens=500
            )

            return response.choices[0].message.content
        except Exception as e:
            print(f"Error generating AI response: {e}")
            return "Lo siento, estoy teniendo problemas técnicos. Por favor, llame a la clínica directamente."

    async def _extract_and_store_info(
        self,
        user_id: str,
        user_message: str,
        ai_response: str
    ):
        """Extract and store important information in Mem0"""

        # Use AI to extract important information
        extraction_prompt = f"""
        De la siguiente conversación, extrae información importante sobre el paciente
        que debería recordarse para futuras interacciones:

        Usuario: {user_message}
        Asistente: {ai_response}

        Extrae solo hechos importantes como preferencias, condiciones médicas, citas, etc.
        Responde con una lista de puntos concisos o "NADA" si no hay información importante.
        """

        try:
            response = openai_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": extraction_prompt}],
                temperature=0.3,
                max_tokens=200
            )

            extracted_info = response.choices[0].message.content

            if extracted_info and extracted_info.strip() != "NADA":
                await ConversationMemory.remember(
                    user_id=user_id,
                    content=extracted_info,
                    metadata={
                        "source": "whatsapp_conversation",
                        "timestamp": datetime.utcnow().isoformat()
                    }
                )
        except Exception as e:
            print(f"Error extracting information: {e}")

    async def _log_conversation(
        self,
        session_id: str,
        clinic_id: str,
        user_message: str,
        ai_response: str
    ):
        """Log conversation to database for audit"""
        try:
            # Log to Supabase
            await supabase.table('core.conversation_logs').insert({
                'session_id': session_id,
                'organization_id': clinic_id,
                'user_message': user_message,
                'ai_response': ai_response,
                'channel': 'whatsapp',
                'created_at': datetime.utcnow().isoformat()
            }).execute()
        except Exception as e:
            print(f"Error logging conversation: {e}")

# FastAPI endpoint handler
async def handle_process_message(request: MessageRequest) -> MessageResponse:
    """Main endpoint handler for processing messages"""
    processor = MessageProcessor()
    return await processor.process_message(request)
