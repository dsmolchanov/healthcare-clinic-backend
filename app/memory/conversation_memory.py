"""
Persistent Conversation Memory System
Combines Supabase for storage and mem0 for intelligent memory management
"""

import os
import json
import uuid
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional, TYPE_CHECKING
from supabase import create_client, Client
import logging
import asyncio

if TYPE_CHECKING:
    import asyncio
try:
    from mem0 import Memory
    MEM0_AVAILABLE = True
except ImportError:
    MEM0_AVAILABLE = False
    Memory = None

logger = logging.getLogger(__name__)

# Mem0 operation timeout (configurable via MEM0_TIMEOUT_MS, default 800ms)
# Enforce a floor of 800ms to prevent overly aggressive timeouts.
MEM0_TIMEOUT_MS = max(int(os.getenv("MEM0_TIMEOUT_MS", "800")), 800)

# Module-global in-flight deduplication map
_inflight: Dict[tuple, asyncio.Task] = {}

async def once(key: tuple, coro_factory):
    """
    De-duplicate concurrent calls with the same key within a process.
    Ensures only one RPC happens even under high concurrency.
    """
    task = _inflight.get(key)
    if task is None:
        task = asyncio.create_task(coro_factory())
        _inflight[key] = task
        def _done(_):
            _inflight.pop(key, None)
        task.add_done_callback(_done)
    return await task

class ConversationMemoryManager:
    """Manages persistent conversation memory with Supabase and mem0"""

    def __init__(self):
        # Initialize Supabase client
        self.supabase: Client = create_client(
            os.environ.get('SUPABASE_URL', ''),
            os.environ.get('SUPABASE_ANON_KEY', '')
        )

        # Session cache to avoid duplicate RPC calls
        # Maps phone_number -> {session_id, timestamp}
        self._session_cache: Dict[str, Dict[str, Any]] = {}
        self._cache_ttl_seconds = 300  # 5 minutes

        # Initialize mem0 for intelligent memory if available
        # Make it lazy to avoid blocking app startup
        self.memory = None
        self.mem0_available = False
        self._mem0_init_attempted = False

        if not MEM0_AVAILABLE:
            logger.info("mem0 not installed, using Supabase for memory storage")

    @staticmethod
    def _extract_mem0_summary(mem0_result: Any) -> tuple[Optional[str], Optional[str]]:
        """Extract summary text and id from mem0 add response."""
        if mem0_result is None:
            return None, None

        if isinstance(mem0_result, dict):
            summary = (
                mem0_result.get('text')
                or mem0_result.get('memory')
                or mem0_result.get('summary')
                or mem0_result.get('content')
            )
            memory_id = mem0_result.get('id') or mem0_result.get('memory_id')
            return summary, memory_id

        if isinstance(mem0_result, (list, tuple)):
            for item in mem0_result:
                summary, memory_id = ConversationMemoryManager._extract_mem0_summary(item)
                if summary:
                    return summary, memory_id
            return None, None

        # Unknown type – fallback to string conversion
        try:
            summary_str = str(mem0_result)
            return summary_str, None
        except Exception:
            return None, None

    def _ensure_mem0_initialized(self):
        """Lazy initialization of mem0 - only init on first use"""
        if self._mem0_init_attempted or not MEM0_AVAILABLE:
            return

        self._mem0_init_attempted = True
        try:
            self._init_mem0()
        except Exception as e:
            logger.error(f"Failed to initialize mem0: {e}", exc_info=True)
            self.memory = None
            self.mem0_available = False

    def _ensure_qdrant_path(self) -> str:
        """Ensure Qdrant storage path exists"""
        base_path = os.environ.get('QDRANT_PATH', '/app/qdrant_data')
        storage_path = os.path.join(base_path, 'storage')

        # Create directory if it doesn't exist
        os.makedirs(storage_path, exist_ok=True)

        logger.info(f"Qdrant storage path: {storage_path}")
        return storage_path

    def _init_mem0(self):
        """Initialize mem0 with Qdrant vector store (self-hosted, local mode)"""

        # Use Qdrant in local mode - no external service needed!
        # Data is stored locally in the filesystem for HIPAA compliance
        mem0_config = {
            "llm": {
                "provider": "openai",
                "config": {
                    "model": os.environ.get('MEM0_LLM_MODEL', 'gpt-4o-mini'),
                    "temperature": 0.2
                }
            },
            "embedder": {
                "provider": "openai",
                "config": {
                    "model": "text-embedding-3-small"
                }
            },
            "vector_store": {
                "provider": "qdrant",
                "config": {
                    "collection_name": "whatsapp_memories",
                    # Ensure directory exists
                    "path": self._ensure_qdrant_path(),
                    "embedding_model_dims": 1536
                }
            },
            "version": "v1.1"
        }

        try:
            logger.info("Initializing mem0 with Qdrant vector store (local mode)...")
            self.memory = Memory.from_config(mem0_config)
            self.mem0_available = True
            logger.info("✅ mem0 initialized successfully")

            # Test mem0 connectivity
            try:
                test_memories = self.memory.get_all(user_id="system_test", limit=1)
                logger.info(f"✅ mem0 connectivity test passed (found {len(test_memories)} memories)")
            except Exception as test_error:
                logger.warning(f"⚠️ mem0 connectivity test failed: {test_error}")

        except Exception as e:
            logger.error(f"❌ mem0 initialization failed: {e}", exc_info=True)
            self.memory = None
            self.mem0_available = False
            # Don't silently fail - raise if mem0 is critical
            if os.environ.get('MEM0_REQUIRED', 'false').lower() == 'true':
                raise

    async def add_mem0_memory(
        self,
        phone_number: str,
        content: str,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Optional[Dict[str, Any]]:
        """Store a memory in mem0 and return summary metadata."""

        self._ensure_mem0_initialized()

        if not self.mem0_available or not self.memory:
            return None

        clean_phone = phone_number.replace("@s.whatsapp.net", "")
        metadata_payload = dict(metadata or {})

        try:
            loop = asyncio.get_event_loop()
            result = await asyncio.wait_for(
                loop.run_in_executor(
                    None,
                    lambda: self.memory.add(
                        content,
                        user_id=clean_phone,
                        metadata=metadata_payload
                    )
                ),
                timeout=MEM0_TIMEOUT_MS / 1000.0
            )

            summary, memory_id = self._extract_mem0_summary(result)

            return {
                'summary': summary,
                'memory_id': memory_id,
                'raw': result
            }

        except asyncio.TimeoutError:
            logger.warning(
                f"⏱️ mem0 add timed out after {MEM0_TIMEOUT_MS}ms (non-critical)"
            )
            return None
        except Exception as e:
            logger.warning(f"Failed to store in mem0: {e}")
            return None

    async def get_or_create_session(
        self,
        phone_number: str,
        clinic_id: str,
        channel: str = "whatsapp"
    ) -> Dict[str, Any]:
        """Get existing session or create new one for phone number using atomic RPC (with caching + deduplication)"""

        # Clean phone number (remove @s.whatsapp.net if present)
        clean_phone = phone_number.replace("@s.whatsapp.net", "")
        cache_key = f"{clean_phone}_{clinic_id}_{channel}"

        # Check cache first
        cached = self._session_cache.get(cache_key)
        if cached:
            # Check if cache is still valid
            cache_age = (datetime.utcnow() - cached.get('cached_at', datetime.min)).total_seconds()
            if cache_age < self._cache_ttl_seconds:
                logger.debug(f"✅ Cache hit: session {cached['id']} (age: {cache_age:.1f}s)")
                return cached

        # Use in-flight deduplication to prevent concurrent calls
        dedup_key = ("session", clean_phone, clinic_id, channel)

        async def _fetch_session():
            try:
                # Use RPC function for atomic create-or-get (prevents FK constraint violations)
                result = self.supabase.rpc('create_or_get_session', {
                    'p_user': clean_phone,
                    'p_channel': channel,
                    'p_clinic': clinic_id,
                    'p_metadata': {
                        'phone_number': clean_phone,
                        'source': 'whatsapp'
                    }
                }).execute()

                if result.data:
                    session_id = result.data
                    logger.info(f"Got/created session {session_id} for {clean_phone}")

                    # Fetch full session details
                    session_result = self.supabase.table('conversation_sessions').select('*').eq(
                        'id', session_id
                    ).single().execute()

                    if session_result.data:
                        session_data = session_result.data
                        # Cache the result
                        session_data['cached_at'] = datetime.utcnow()
                        self._session_cache[cache_key] = session_data
                        return session_data

                    # Fallback if fetch fails
                    fallback = {
                        'id': session_id,
                        'user_identifier': clean_phone,
                        'channel_type': channel,
                        'metadata': {'clinic_id': clinic_id},
                        'cached_at': datetime.utcnow()
                    }
                    self._session_cache[cache_key] = fallback
                    return fallback

                # Should not reach here, but provide fallback
                raise Exception("RPC returned empty result")

            except Exception as e:
                logger.error(f"Error managing session for {clean_phone}: {e}")
                # Return a temporary session if database fails
                return {
                    'id': str(uuid.uuid4()),
                    'room_name': f"temp_{clean_phone}_{uuid.uuid4().hex[:8]}",
                    'user_identifier': clean_phone,
                    'metadata': {'clinic_id': clinic_id}
                }

        # Execute with deduplication
        return await once(dedup_key, _fetch_session)
    
    async def store_message(
        self,
        session_id: str,
        role: str,  # 'user' or 'assistant'
        content: str,
        phone_number: str,
        metadata: Optional[Dict] = None
    ) -> Optional[str]:
        """
        Store a message in the conversation history (fire-and-forget with timeout)

        Args:
            session_id: Can be either external key (whatsapp_xxx) or UUID.
                       Will be converted to proper UUID if needed.

        Returns:
            The generated message ID, or None if storage failed
        """
        import asyncio

        message_id_container = [None]  # Use list to capture ID from async function

        async def _store_with_timeout():
            """Internal helper with timeout protection"""
            try:
                # Store the external key for debugging
                external_session_id = session_id

                # Check if session_id is a UUID or external key
                try:
                    uuid.UUID(session_id)
                    # Already a valid UUID, use it directly
                    actual_session_uuid = session_id
                except ValueError:
                    # It's an external key, need to fetch/create the real session
                    logger.debug(f"Converting external session key to UUID: {session_id}")

                    # Extract info from external key (format: whatsapp_PHONE_INSTANCE)
                    clean_phone = phone_number.replace("@s.whatsapp.net", "")

                    # Get or create session using the phone number
                    clinic_id = metadata.get('clinic_id') if metadata else ''
                    session = await self.get_or_create_session(
                        phone_number=clean_phone,
                        clinic_id=clinic_id,
                        channel='whatsapp'
                    )

                    if session and 'id' in session:
                        actual_session_uuid = session['id']
                        logger.debug(f"Mapped external key {external_session_id} to UUID {actual_session_uuid}")
                    else:
                        logger.error(f"Failed to get/create session for {session_id}")
                        return

                msg_id = str(uuid.uuid4())
                message_id_container[0] = msg_id  # Capture the ID

                base_metadata = dict(metadata or {})

                mem0_result: Optional[Dict[str, Any]] = None
                if self.mem0_available and self.memory:
                    mem0_result = await self.add_mem0_memory(
                        phone_number=phone_number,
                        content=content,
                        metadata={
                            'role': role,
                            'session_id': actual_session_uuid,
                            'external_session_id': external_session_id,
                            'timestamp': datetime.utcnow().isoformat()
                        }
                    )

                if mem0_result and mem0_result.get('summary'):
                    base_metadata.setdefault('mem0_summary', mem0_result['summary'])
                    if mem0_result.get('memory_id'):
                        base_metadata.setdefault('mem0_id', mem0_result['memory_id'])

                # Store using new RPC (writes to healthcare.conversation_logs)
                result = self.supabase.rpc('log_message_with_metrics', {
                    'p_session_id': actual_session_uuid,
                    'p_role': role,
                    'p_content': content,
                    'p_metadata': base_metadata,
                    'p_log_platform_events': False  # No events for simple message store
                }).execute()

                # Extract message_id from RPC response
                if result.data:
                    msg_id = result.data.get('message_id')
                    message_id_container[0] = msg_id

                logger.debug(f"Stored {role} message for session UUID {actual_session_uuid} (external: {external_session_id})")

            except Exception as e:
                logger.error(f"Error storing message: {e}")

        # Fire-and-forget with timeout protection
        try:
            await asyncio.wait_for(_store_with_timeout(), timeout=1.5)
            return message_id_container[0]
        except asyncio.TimeoutError:
            logger.warning(f"Memory write timed out (>1.5s), continuing without blocking")
            return message_id_container[0]  # Return ID even if timed out (message might still get stored)
        except Exception as e:
            logger.warning(f"Memory write failed: {e}, continuing without blocking")
            return None

    async def store_conversation_turn(
        self,
        session_id: str,
        user_message: str,
        assistant_response: str,
        phone_number: str,
        metadata: Optional[Dict[str, Any]] = None
    ):
        """
        Store a complete conversation turn (user message + assistant response) in mem0
        This provides better context than storing individual messages
        """

        # Lazy initialize mem0 on first use
        self._ensure_mem0_initialized()

        if not self.mem0_available or not self.memory:
            logger.warning("mem0 not available, skipping memory storage")
            return

        clean_phone = phone_number.replace("@s.whatsapp.net", "")

        # Combine user and assistant into conversational context
        conversation_turn = f"User: {user_message}\nAssistant: {assistant_response}"

        try:
            logger.info(f"Storing conversation turn in mem0 for user: {clean_phone[:8]}***")

            # Run with timeout
            async def _add_turn():
                loop = asyncio.get_event_loop()
                return await loop.run_in_executor(
                    None,
                    lambda: self.memory.add(
                        conversation_turn,
                        user_id=clean_phone,
                        metadata={
                            'session_id': session_id,
                            'timestamp': datetime.utcnow().isoformat(),
                            'turn_type': 'conversation',
                            **(metadata or {})
                        }
                    )
                )

            result = await asyncio.wait_for(
                _add_turn(),
                timeout=MEM0_TIMEOUT_MS / 1000.0
            )

            logger.info(f"✅ Conversation turn stored in mem0: {result}")

        except asyncio.TimeoutError:
            logger.warning(f"⏱️ mem0 add conversation timed out after {MEM0_TIMEOUT_MS}ms (non-critical)")
        except Exception as e:
            logger.error(f"❌ Failed to store in mem0: {e}", exc_info=True)

    async def get_conversation_history(
        self,
        phone_number: str,
        clinic_id: str,
        limit: int = 10,
        include_all_sessions: bool = True
    ) -> List[Dict[str, Any]]:
        """Get conversation history for a phone number"""
        
        clean_phone = phone_number.replace("@s.whatsapp.net", "")
        
        try:
            if include_all_sessions:
                # Get all messages from all sessions for this phone number
                sessions_result = self.supabase.table('conversation_sessions').select('id').eq(
                    'user_identifier', clean_phone
                ).eq(
                    'metadata->>clinic_id', clinic_id
                ).execute()
                
                if not sessions_result.data:
                    return []
                
                session_ids = [s['id'] for s in sessions_result.data]
                
                # Get messages from all sessions
                messages_result = self.supabase.table('conversation_messages').select('*').in_(
                    'session_id', session_ids
                ).order(
                    'created_at', desc=False  # Oldest first
                ).limit(limit).execute()
                
            else:
                # Get only current session messages
                session = await self.get_or_create_session(phone_number, clinic_id)
                messages_result = self.supabase.table('conversation_messages').select('*').eq(
                    'session_id', session['id']
                ).order(
                    'created_at', desc=False
                ).limit(limit).execute()
            
            return messages_result.data if messages_result.data else []
            
        except Exception as e:
            logger.error(f"Error getting conversation history: {e}")
            return []
    
    async def get_memory_context(
        self,
        phone_number: str,
        query: Optional[str] = None,
        limit: int = 5
    ) -> List[str]:
        """Get memory context for a phone number with detailed logging"""

        # Lazy initialize mem0 on first use
        self._ensure_mem0_initialized()

        if not self.mem0_available or not self.memory:
            logger.warning("mem0 not available, returning empty context")
            return []

        clean_phone = phone_number.replace("@s.whatsapp.net", "")

        try:
            logger.info(f"Querying mem0 for user: {clean_phone[:8]}*** (query: {query[:50] if query else 'None'})")

            # Run with timeout
            async def _search_mem0():
                loop = asyncio.get_event_loop()
                if query:
                    return await loop.run_in_executor(
                        None,
                        lambda: self.memory.search(query, user_id=clean_phone, limit=limit)
                    )
                else:
                    return await loop.run_in_executor(
                        None,
                        lambda: self.memory.get_all(user_id=clean_phone, limit=limit)
                    )

            memories = await asyncio.wait_for(
                _search_mem0(),
                timeout=MEM0_TIMEOUT_MS / 1000.0
            )

            if query:
                logger.info(f"mem0 search returned {len(memories)} memories")
            else:
                logger.info(f"mem0 get_all returned {len(memories)} memories")

            # Extract memory texts
            context = []
            for idx, memory in enumerate(memories):
                if isinstance(memory, dict):
                    memory_text = memory.get('memory', '')
                    score = memory.get('score', 0)
                    context.append(memory_text)
                    logger.debug(f"  Memory {idx+1}: {memory_text[:100]}... (score: {score:.3f})")
                else:
                    context.append(str(memory))

            logger.info(f"✅ Returning {len(context)} memory items")
            return context

        except asyncio.TimeoutError:
            logger.warning(f"⏱️ mem0 search timed out after {MEM0_TIMEOUT_MS}ms, returning empty context")
            return []
        except Exception as e:
            logger.error(f"❌ Failed to get mem0 context: {e}", exc_info=True)
            return []  # Return empty but log the error prominently
    
    async def summarize_conversation(
        self,
        session_id: str,
        summary: str
    ):
        """Store conversation summary when session ends"""
        
        try:
            # Update session with summary
            self.supabase.table('conversation_sessions').update({
                'metadata': {
                    'summary': summary
                },
                'ended_at': datetime.utcnow().isoformat(),
                'updated_at': datetime.utcnow().isoformat()
            }).eq('id', session_id).execute()
            
            logger.info(f"Stored summary for session {session_id}")
            
        except Exception as e:
            logger.error(f"Error storing summary: {e}")
    
    async def get_user_preferences(
        self,
        phone_number: str
    ) -> Dict[str, Any]:
        """Get user preferences and important information from history"""
        
        clean_phone = phone_number.replace("@s.whatsapp.net", "")
        
        preferences = {
            'language': None,
            'preferred_name': None,
            'medical_history': [],
            'appointment_preferences': [],
            'communication_style': None
        }
        
        try:
            # Get mem0 memories about user preferences
            if self.mem0_available and self.memory:
                memories = self.memory.get_all(user_id=clean_phone, limit=20)
                
                for memory in memories:
                    memory_text = memory.get('memory', '') if isinstance(memory, dict) else str(memory)
                    
                    # Extract preferences from memories
                    if 'prefers' in memory_text.lower() or 'likes' in memory_text.lower():
                        preferences['appointment_preferences'].append(memory_text)
                    
                    if 'language' in memory_text.lower() or 'speaks' in memory_text.lower():
                        preferences['language'] = memory_text
                    
                    if 'name is' in memory_text.lower() or 'call me' in memory_text.lower():
                        preferences['preferred_name'] = memory_text
            
            # Also check recent messages for language detection
            history = await self.get_conversation_history(phone_number, '', limit=5)
            if history:
                # Simple language detection based on recent messages
                for msg in history:
                    if msg['role'] == 'user':
                        content = msg['content']
                        if any(char in content for char in 'абвгдеёжзийклмнопрстуфхцчшщъыьэюя'):
                            preferences['language'] = 'Russian'
                        elif any(char in content for char in 'áéíóúñ¿¡'):
                            preferences['language'] = 'Spanish'
                        # Add more language detection as needed
            
        except Exception as e:
            logger.warning(f"Error getting user preferences: {e}")
        
        return preferences

# Singleton instance
_memory_manager = None

def get_memory_manager() -> ConversationMemoryManager:
    """Get or create singleton memory manager instance"""
    global _memory_manager
    if _memory_manager is None:
        _memory_manager = ConversationMemoryManager()
    return _memory_manager
