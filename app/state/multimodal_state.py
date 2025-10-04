# clinics/backend/app/state/multimodal_state.py
"""Manages conversation state across text and voice channels"""

import redis.asyncio as redis
from typing import Dict, Any, Optional, List
import json
import os
from datetime import datetime
from enum import Enum


class ConversationMode(Enum):
    TEXT = "text"
    VOICE = "voice"
    HYBRID = "hybrid"


class MultiModalStateManager:
    """Manages conversation state across text and voice channels"""
    
    def __init__(self):
        self.redis_client = redis.Redis(
            host=os.environ.get('REDIS_HOST', 'localhost'),
            decode_responses=True
        )
        self.pubsub = self.redis_client.pubsub()
        
    async def create_session_state(
        self,
        session_id: str,
        initial_mode: ConversationMode,
        metadata: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Create a new session state"""
        
        state = {
            'session_id': session_id,
            'mode': initial_mode.value,
            'created_at': datetime.utcnow().isoformat(),
            'last_activity': datetime.utcnow().isoformat(),
            'metadata': metadata,
            'conversation': {
                'messages': [],
                'context': {},
                'memory_refs': []
            },
            'voice_config': {
                'room_name': None,
                'participant_id': None,
                'audio_track': None
            },
            'text_config': {
                'channel': metadata.get('channel', 'web'),
                'user_identifier': metadata.get('user_id')
            },
            'transition_ready': True,
            'version': 1
        }
        
        # Store in Redis with expiry
        key = f"session:multimodal:{session_id}"
        await self.redis_client.setex(
            key,
            86400,  # 24 hour expiry
            json.dumps(state)
        )
        
        # Publish creation event
        await self.redis_client.publish(
            f"session:created",
            json.dumps({'session_id': session_id, 'mode': initial_mode.value})
        )
        
        return state
    
    async def prepare_voice_transition(
        self,
        session_id: str
    ) -> Dict[str, Any]:
        """Prepare state for text-to-voice transition"""
        
        key = f"session:multimodal:{session_id}"
        state = await self.get_session_state(session_id)
        
        if not state:
            raise ValueError(f"Session {session_id} not found")
        
        # Generate LiveKit room configuration
        room_name = f"voice_{session_id}_{int(datetime.utcnow().timestamp())}"
        
        # Update state for voice
        state['voice_config'] = {
            'room_name': room_name,
            'participant_id': f"participant_{session_id}",
            'audio_track': None,
            'prepared_at': datetime.utcnow().isoformat()
        }
        
        state['transition_ready'] = True
        state['last_activity'] = datetime.utcnow().isoformat()
        
        # Save updated state
        await self.redis_client.setex(
            key,
            86400,
            json.dumps(state)
        )
        
        # Publish transition event
        await self.redis_client.publish(
            f"session:transition:voice",
            json.dumps({
                'session_id': session_id,
                'room_name': room_name,
                'context': state['conversation']['context']
            })
        )
        
        return {
            'room_name': room_name,
            'session_state': state,
            'transition_ready': True
        }
    
    async def sync_conversation_state(
        self,
        session_id: str,
        messages: List[Dict],
        context: Dict[str, Any]
    ):
        """Sync conversation state between channels"""
        
        key = f"session:multimodal:{session_id}"
        state = await self.get_session_state(session_id)
        
        if not state:
            return
        
        # Update conversation
        state['conversation']['messages'].extend(messages)
        state['conversation']['context'].update(context)
        state['last_activity'] = datetime.utcnow().isoformat()
        
        # Keep only recent messages
        max_messages = 50
        if len(state['conversation']['messages']) > max_messages:
            state['conversation']['messages'] = state['conversation']['messages'][-max_messages:]
        
        # Save state
        await self.redis_client.setex(
            key,
            86400,
            json.dumps(state)
        )
        
        # Publish sync event
        await self.redis_client.publish(
            f"session:sync",
            json.dumps({
                'session_id': session_id,
                'message_count': len(messages),
                'mode': state['mode']
            })
        )
    
    async def get_session_state(
        self,
        session_id: str
    ) -> Optional[Dict[str, Any]]:
        """Get current session state"""
        
        key = f"session:multimodal:{session_id}"
        state_json = await self.redis_client.get(key)
        
        if state_json:
            return json.loads(state_json)
        return None
    
    async def subscribe_to_transitions(
        self,
        session_id: str
    ):
        """Subscribe to state transition events"""
        
        await self.pubsub.subscribe(
            f"session:transition:*",
            f"session:sync"
        )
        
        async for message in self.pubsub.listen():
            if message['type'] == 'message':
                data = json.loads(message['data'])
                if data.get('session_id') == session_id:
                    yield data
    
    async def cleanup_expired_sessions(self):
        """Clean up expired session states"""
        
        pattern = "session:multimodal:*"
        cursor = 0
        
        while True:
            cursor, keys = await self.redis_client.scan(
                cursor, 
                match=pattern,
                count=100
            )
            
            for key in keys:
                ttl = await self.redis_client.ttl(key)
                if ttl == -1:  # No expiry set
                    await self.redis_client.expire(key, 86400)
            
            if cursor == 0:
                break