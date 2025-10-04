# clinics/backend/app/api/response_constructor.py
"""Constructs LLM responses with RAG context"""

from typing import List, Dict, Any, Optional
import os
import sys

# Add parent directory to path for imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.memory.conversation_manager import ConversationContextManager
from app.api.message_processor import PineconeKnowledgeBase


class LLMResponseConstructor:
    """Constructs LLM responses with RAG context"""

    def __init__(self, session_id: str, user_id: str, clinic_id: str, llm_factory=None):
        self.session_id = session_id
        self.user_id = user_id
        self.clinic_id = clinic_id
        self.context_manager = ConversationContextManager(session_id, user_id)
        self.knowledge_base = PineconeKnowledgeBase(clinic_id)
        self.llm_factory = llm_factory  # Inject factory instead of creating client
    
    async def construct_response(
        self,
        user_query: str,
        intent: str,
        include_knowledge: bool = True
    ) -> Dict[str, Any]:
        """Construct response with full context"""
        
        # 1. Get conversation context
        recent_messages = await self.context_manager.get_context_window()
        
        # 2. Get relevant memories
        memories = await self.context_manager.get_relevant_memories(user_query)
        
        # 3. Get static knowledge if needed
        knowledge = []
        if include_knowledge:
            knowledge = await self.knowledge_base.search(user_query, top_k=3)
        
        # 4. Build context for LLM
        context = self._build_context(
            recent_messages,
            memories,
            knowledge
        )
        
        # 5. Generate response
        response = await self._generate_llm_response(
            user_query,
            context,
            intent
        )
        
        # 6. Add response to memory
        await self.context_manager.add_message(
            role='assistant',
            content=response['content'],
            metadata={'intent': intent, 'sources': response.get('sources', [])}
        )
        
        return response
    
    def _build_context(
        self,
        messages: List[Dict],
        memories: List[Dict],
        knowledge: List[str]
    ) -> str:
        """Build context string for LLM"""
        
        context_parts = []
        
        # Add conversation history
        if messages:
            context_parts.append("Recent Conversation:")
            for msg in messages[-5:]:  # Last 5 messages
                context_parts.append(f"{msg['role']}: {msg['content']}")
        
        # Add relevant memories
        if memories:
            context_parts.append("\nRelevant Context:")
            for memory in memories[:3]:  # Top 3 memories
                context_parts.append(f"- {memory['content']} (confidence: {memory['score']:.2f})")
        
        # Add knowledge
        if knowledge:
            context_parts.append("\nRelevant Information:")
            for k in knowledge:
                context_parts.append(f"- {k}")
        
        return "\n".join(context_parts)
    
    async def _generate_llm_response(
        self,
        query: str,
        context: str,
        intent: str
    ) -> Dict[str, Any]:
        """Generate response using LLM factory with context"""

        # Build system prompt based on intent
        system_prompts = {
            'appointment': "You are a helpful medical assistant scheduling appointments.",
            'knowledge': "You are a knowledgeable medical information assistant.",
            'general': "You are a friendly and helpful medical clinic assistant."
        }

        messages = [
            {
                "role": "system",
                "content": f"{system_prompts.get(intent, system_prompts['general'])}\n\nContext:\n{context}"
            },
            {
                "role": "user",
                "content": query
            }
        ]

        # Use LLM factory if available, otherwise fallback to basic response
        if self.llm_factory:
            response = await self.llm_factory.generate(
                messages=messages,
                temperature=0.7,
                max_tokens=500
            )
            content = response.content
        else:
            # Fallback if factory not available
            content = f"I understand you're asking about: {query}. Please let me help you with that."

        return {
            'content': content,
            'intent': intent,
            'sources': self._extract_sources(context),
            'confidence': 0.9  # Calculate based on context quality
        }
    
    def _extract_sources(self, context: str) -> List[str]:
        """Extract source references from context"""
        sources = []
        
        # Look for knowledge base references
        if "Relevant Information:" in context:
            # Simple extraction - in production, use more sophisticated parsing
            info_section = context.split("Relevant Information:")[1]
            lines = info_section.split("\n")
            for line in lines:
                if line.strip().startswith("-"):
                    sources.append("knowledge_base")
                    break
        
        # Look for memory references
        if "Relevant Context:" in context:
            sources.append("conversation_memory")
        
        return list(set(sources))  # Return unique sources