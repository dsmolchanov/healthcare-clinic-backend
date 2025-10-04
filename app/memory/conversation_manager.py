# clinics/backend/app/memory/conversation_manager.py
"""Manages dynamic conversation memory for LLM response construction"""

import os
import json
import redis.asyncio as redis
from datetime import datetime
from typing import List, Dict, Any, Optional
from mem0 import Memory
from openai import OpenAI


class ConversationContextManager:
    """Manages dynamic conversation memory for LLM response construction"""
    
    def __init__(self, session_id: str, user_id: str):
        self.session_id = session_id
        self.user_id = user_id
        self.redis_client = redis.Redis(
            host=os.environ.get('REDIS_HOST', 'localhost'),
            decode_responses=True
        )
        self.mem0 = self._init_mem0()
        
        # Configuration
        self.context_window_size = 10  # Last N messages
        self.summary_threshold = 20  # Summarize after N messages
        
    def _init_mem0(self):
        """Initialize mem0 with existing configuration"""
        try:
            return Memory.from_config({
                "llm": {
                    "provider": "openai",
                    "config": {"model": "gpt-4o-mini", "temperature": 0.2}
                },
                "embedder": {
                    "provider": "openai",
                    "config": {"model": "text-embedding-3-small"}
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
            return None
    
    async def add_message(
        self,
        role: str,
        content: str,
        metadata: Optional[Dict] = None
    ):
        """Add a message to conversation memory"""
        
        # Create message object
        message = {
            'role': role,
            'content': content,
            'timestamp': datetime.utcnow().isoformat(),
            'metadata': metadata or {}
        }
        
        # Store in Redis for immediate access
        key = f"chat:{self.session_id}:messages"
        await self.redis_client.rpush(key, json.dumps(message))
        
        # Store in mem0 for long-term memory (if available)
        if self.mem0:
            self.mem0.add(
                messages=[message],
                user_id=self.user_id,
                metadata={
                    'session_id': self.session_id,
                    'timestamp': message['timestamp']
                }
            )
        
        # Check if summarization needed
        message_count = await self.redis_client.llen(key)
        if message_count >= self.summary_threshold:
            await self._summarize_old_messages()
    
    async def get_context_window(self) -> List[Dict]:
        """Get recent messages for context"""
        
        key = f"chat:{self.session_id}:messages"
        
        # Get last N messages from Redis
        messages = await self.redis_client.lrange(
            key,
            -self.context_window_size,
            -1
        )
        
        return [json.loads(msg) for msg in messages]
    
    async def get_relevant_memories(
        self,
        query: str,
        limit: int = 5
    ) -> List[Dict]:
        """Retrieve relevant memories for the query"""
        
        # Search mem0 for relevant context (if available)
        if not self.mem0:
            return []
            
        memories = self.mem0.search(
            query=query,
            user_id=self.user_id,
            limit=limit
        )
        
        # Score and rank memories
        scored_memories = []
        for memory in memories:
            score = self._calculate_relevance_score(memory, query)
            if score > 0.5:  # Relevance threshold
                scored_memories.append({
                    'content': memory.get('memory', ''),
                    'score': score,
                    'timestamp': memory.get('created_at'),
                    'metadata': memory.get('metadata', {})
                })
        
        # Sort by score
        scored_memories.sort(key=lambda x: x['score'], reverse=True)
        
        return scored_memories
    
    def _calculate_relevance_score(
        self,
        memory: Dict,
        query: str
    ) -> float:
        """Calculate relevance score for a memory"""
        
        # Simple scoring based on recency and similarity
        # In production, use more sophisticated scoring
        base_score = memory.get('score', 0.5)
        
        # Boost recent memories
        created_at = memory.get('created_at')
        if created_at:
            age_hours = (datetime.utcnow() - datetime.fromisoformat(created_at)).total_seconds() / 3600
            recency_boost = max(0, 1 - (age_hours / 24))  # Decay over 24 hours
            base_score = base_score * 0.7 + recency_boost * 0.3
        
        return min(1.0, base_score)
    
    async def _summarize_old_messages(self):
        """Summarize old messages to save context space"""
        
        key = f"chat:{self.session_id}:messages"
        summary_key = f"chat:{self.session_id}:summaries"
        
        # Get messages to summarize
        all_messages = await self.redis_client.lrange(key, 0, -1)
        
        if len(all_messages) <= self.context_window_size:
            return
        
        # Get messages to summarize (older than context window)
        to_summarize = all_messages[:-self.context_window_size]
        
        # Create summary using LLM
        client = OpenAI()
        
        conversation = "\n".join([
            f"{json.loads(msg)['role']}: {json.loads(msg)['content']}"
            for msg in to_summarize
        ])
        
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": "Summarize this conversation, preserving key facts and context:"
                },
                {
                    "role": "user",
                    "content": conversation
                }
            ],
            temperature=0.3,
            max_tokens=200
        )
        
        summary = response.choices[0].message.content
        
        # Store summary
        await self.redis_client.rpush(summary_key, json.dumps({
            'summary': summary,
            'message_count': len(to_summarize),
            'timestamp': datetime.utcnow().isoformat()
        }))
        
        # Remove summarized messages from main list
        await self.redis_client.ltrim(key, len(to_summarize), -1)