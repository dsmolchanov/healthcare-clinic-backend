"""
Persistent Conversation Memory System
Combines Supabase for storage and mem0 for intelligent memory management
"""

import os
import json
import uuid
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional, TYPE_CHECKING, Set
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
        self._mem0_write_queue: Optional[asyncio.Queue] = None
        self._mem0_worker_task: Optional[asyncio.Task] = None
        self._mem0_warmup_clinics: Set[str] = set()

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

    def _build_mem0_user_key(self, phone: str, clinic_id: Optional[str]) -> str:
        """Create a stable mem0 user key scoped by clinic for multi-tenant isolation."""
        clinic_part = (clinic_id or "global").strip() or "global"
        return f"{clinic_part}:{phone}"

    def _candidate_mem0_user_ids(self, phone: str, clinic_id: Optional[str]) -> List[str]:
        """Return legacy + scoped mem0 user IDs so we can read both new and old data."""
        phone_clean = phone
        candidates = []

        if clinic_id:
            candidates.append(self._build_mem0_user_key(phone_clean, clinic_id))

        # Legacy identifiers (pre multi-tenant) - keep for backward compatibility
        candidates.append(phone_clean)
        candidates.append(self._build_mem0_user_key(phone_clean, None))

        # Deduplicate while preserving order
        deduped = []
        seen = set()
        for candidate in candidates:
            if candidate not in seen:
                deduped.append(candidate)
                seen.add(candidate)
        return deduped

    def _get_mem0_queue(self) -> asyncio.Queue:
        """Lazy create the mem0 write queue."""
        if self._mem0_write_queue is None:
            self._mem0_write_queue = asyncio.Queue(maxsize=512)
        return self._mem0_write_queue

    async def _ensure_mem0_worker(self):
        """Make sure the background mem0 writer is running."""
        if self._mem0_worker_task and not self._mem0_worker_task.done():
            return

        loop = asyncio.get_running_loop()
        queue = self._get_mem0_queue()
        self._mem0_worker_task = loop.create_task(self._mem0_writer_loop(queue))
        logger.info("Started mem0 background writer task")

    async def _mem0_writer_loop(self, queue: asyncio.Queue):
        """Background worker that flushes mem0 operations off the critical path."""
        while True:
            job = await queue.get()
            try:
                job_type = job.get('type')
                if job_type == 'message':
                    await self._process_mem0_message_job(job)
                elif job_type == 'turn':
                    await self._process_mem0_turn_job(job)
                elif job_type == 'warmup':
                    await self._process_mem0_warmup_job(job)
                else:
                    logger.warning(f"Unknown mem0 job type: {job_type}")
            except Exception as exc:
                logger.error(f"mem0 worker job failed: {exc}", exc_info=True)
            finally:
                queue.task_done()

    async def _process_mem0_message_job(self, job: Dict[str, Any]):
        """Persist a single message to mem0 and backfill Supabase metadata."""
        message_id = job.get('message_id')
        phone_number = job.get('phone_number', '')
        clinic_id = job.get('clinic_id')
        content = job.get('content', '')
        base_metadata = dict(job.get('metadata') or {})
        session_uuid = job.get('session_uuid')
        external_session_id = job.get('external_session_id')
        role = job.get('role')

        mem0_metadata = {
            'role': role,
            'session_id': session_uuid,
            'external_session_id': external_session_id,
            'timestamp': datetime.utcnow().isoformat(),
            'clinic_id': clinic_id,
            **base_metadata
        }

        result = await self.add_mem0_memory(
            phone_number=phone_number,
            content=content,
            metadata=mem0_metadata,
            clinic_id=clinic_id
        )

        if not message_id or not result or not result.get('summary'):
            return

        updated_metadata = dict(base_metadata)
        updated_metadata['mem0_summary'] = result['summary']

        if result.get('memory_id'):
            updated_metadata['mem0_id'] = result['memory_id']

        await self._update_message_metadata(message_id, updated_metadata)

    async def _process_mem0_turn_job(self, job: Dict[str, Any]):
        """Store aggregated conversation turn in mem0 without touching Supabase."""
        phone_number = job.get('phone_number', '')
        clinic_id = job.get('clinic_id')
        content = job.get('content', '')
        metadata = job.get('metadata') or {}

        await self.add_mem0_memory(
            phone_number=phone_number,
            content=content,
            metadata=metadata,
            clinic_id=clinic_id
        )

    async def _process_mem0_warmup_job(self, job: Dict[str, Any]):
        """Touch the vector index so mem0 is hot before real traffic arrives."""
        clinic_id = job.get('clinic_id')
        phone_number = job.get('phone_number', 'warmup')

        if not self.mem0_available or not self.memory:
            return

        user_key = self._build_mem0_user_key(phone_number.replace("@s.whatsapp.net", ""), clinic_id)

        try:
            loop = asyncio.get_running_loop()
            await asyncio.wait_for(
                loop.run_in_executor(
                    None,
                    lambda: self.memory.get_all(user_id=user_key, limit=1)
                ),
                timeout=MEM0_TIMEOUT_MS / 1000.0
            )
            logger.info(f"mem0 warmup complete for clinic {clinic_id or 'global'}")
        except asyncio.TimeoutError:
            logger.warning(f"mem0 warmup timed out for clinic {clinic_id}")
        except Exception as exc:
            logger.warning(f"mem0 warmup failed for clinic {clinic_id}: {exc}")

    async def _update_message_metadata(self, message_id: str, metadata: Dict[str, Any]):
        """Persist updated metadata back to Supabase in a worker thread."""

        def _update():
            return (
                self.supabase
                .table('conversation_messages')
                .update({'metadata': metadata})
                .eq('id', message_id)
                .execute()
            )

        try:
            await asyncio.to_thread(_update)
            logger.debug(f"Updated metadata for message {message_id}")
        except Exception as exc:
            logger.warning(f"Failed to update metadata for message {message_id}: {exc}")

    async def _enqueue_mem0_job(self, job: Dict[str, Any]):
        """Submit a mem0 job to be processed asynchronously."""
        self._ensure_mem0_initialized()

        if not self.mem0_available or not self.memory:
            return

        queue = self._get_mem0_queue()
        await self._ensure_mem0_worker()

        try:
            queue.put_nowait(job)
        except asyncio.QueueFull:
            logger.warning("mem0 write queue is full, dropping job")

    async def _schedule_mem0_warmup(self, clinic_id: Optional[str], phone_number: str):
        """Kick off a mem0 warmup for a clinic once per process."""
        if not clinic_id or clinic_id in self._mem0_warmup_clinics:
            return

        self._mem0_warmup_clinics.add(clinic_id)

        await self._enqueue_mem0_job({
            'type': 'warmup',
            'clinic_id': clinic_id,
            'phone_number': phone_number
        })

    async def schedule_mem0_message_update(
        self,
        *,
        message_id: Optional[str],
        phone_number: str,
        clinic_id: Optional[str],
        content: str,
        metadata: Dict[str, Any],
        session_uuid: str,
        role: str,
        external_session_id: Optional[str] = None
    ):
        """Public helper to queue a mem0 add + metadata backfill for an existing message."""

        if not message_id:
            return

        job = {
            'type': 'message',
            'clinic_id': clinic_id,
            'phone_number': phone_number,
            'content': content,
            'metadata': dict(metadata or {}),
            'message_id': message_id,
            'session_uuid': session_uuid,
            'external_session_id': external_session_id,
            'role': role
        }

        await self._enqueue_mem0_job(job)

    async def add_mem0_memory(
        self,
        phone_number: str,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
        clinic_id: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """Store a memory in mem0 and return summary metadata."""

        self._ensure_mem0_initialized()

        if not self.mem0_available or not self.memory:
            return None

        clean_phone = phone_number.replace("@s.whatsapp.net", "")
        resolved_clinic = clinic_id or (metadata or {}).get('clinic_id')
        metadata_payload = dict(metadata or {})
        if resolved_clinic:
            metadata_payload.setdefault('clinic_id', resolved_clinic)

        user_candidates = self._candidate_mem0_user_ids(clean_phone, resolved_clinic)
        target_user_id = user_candidates[0]

        try:
            loop = asyncio.get_event_loop()
            result = await asyncio.wait_for(
                loop.run_in_executor(
                    None,
                    lambda: self.memory.add(
                        content,
                        user_id=target_user_id,
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
                        'source': 'whatsapp',
                        'clinic_id': clinic_id
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
        clean_phone = phone_number.replace("@s.whatsapp.net", "")

        async def _store_with_timeout():
            """Internal helper with timeout protection"""
            try:
                # Store the external key for debugging
                external_session_id = session_id
                session: Optional[Dict[str, Any]] = None

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
                clinic_id = base_metadata.get('clinic_id')

                if not clinic_id and session and isinstance(session, dict):
                    session_meta = session.get('metadata') or {}
                    clinic_id = session_meta.get('clinic_id') or session.get('clinic_id')

                if not clinic_id:
                    try:
                        lookup = (
                            self.supabase
                            .table('conversation_sessions')
                            .select('metadata')
                            .eq('id', actual_session_uuid)
                            .single()
                            .execute()
                        )
                        if lookup.data:
                            session_meta = lookup.data.get('metadata') or {}
                            clinic_id = session_meta.get('clinic_id')
                    except Exception as exc:
                        logger.debug(f"Unable to resolve clinic_id for session {actual_session_uuid}: {exc}")

                if clinic_id:
                    base_metadata.setdefault('clinic_id', clinic_id)

                base_metadata.setdefault('from_number', clean_phone)

                # Warm vector index once per clinic to avoid cold-start latency
                await self._schedule_mem0_warmup(clinic_id, clean_phone)

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

                if message_id_container[0]:
                    asyncio.create_task(
                        self.schedule_mem0_message_update(
                            message_id=message_id_container[0],
                            phone_number=phone_number,
                            clinic_id=clinic_id,
                            content=content,
                            metadata=dict(base_metadata),
                            session_uuid=actual_session_uuid,
                            role=role,
                            external_session_id=external_session_id
                        )
                    )

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

        # Combine user and assistant into conversational context
        conversation_turn = f"User: {user_message}\nAssistant: {assistant_response}"
        clinic_id = (metadata or {}).get('clinic_id') if metadata else None

        await self._enqueue_mem0_job({
            'type': 'turn',
            'clinic_id': clinic_id,
            'phone_number': phone_number,
            'content': conversation_turn,
            'metadata': {
                'session_id': session_id,
                'timestamp': datetime.utcnow().isoformat(),
                'turn_type': 'conversation',
                **(metadata or {})
            }
        })

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

    async def _fetch_cached_mem0_summaries(
        self,
        phone_number: str,
        clinic_id: Optional[str],
        limit: int
    ) -> List[str]:
        """Fetch recent mem0 summaries from Supabase metadata cache."""

        def _query():
            query = self.supabase.table('conversation_messages').select('metadata, created_at')
            query = query.eq('metadata->>from_number', phone_number)

            if clinic_id:
                query = query.eq('metadata->>clinic_id', clinic_id)

            query = query.filter('metadata->>mem0_summary', 'not.is', 'null')
            query = query.order('created_at', desc=True).limit(limit)
            response = query.execute()
            return response.data or []

        try:
            rows = await asyncio.to_thread(_query)
            summaries: List[str] = []
            for row in rows:
                metadata = row.get('metadata') or {}
                summary = metadata.get('mem0_summary')
                if summary:
                    summaries.append(summary)
            return summaries
        except Exception as exc:
            logger.debug(f"Cached mem0 summary fetch failed: {exc}")
            return []

    async def get_memory_context(
        self,
        phone_number: str,
        clinic_id: Optional[str] = None,
        query: Optional[str] = None,
        limit: int = 5
    ) -> List[str]:
        """Get memory context for a phone number with detailed logging"""

        clean_phone = phone_number.replace("@s.whatsapp.net", "")

        cached_summaries = await self._fetch_cached_mem0_summaries(
            phone_number=clean_phone,
            clinic_id=clinic_id,
            limit=limit * 3
        )

        if query:
            lowered = query.lower()
            cached_filtered = [item for item in cached_summaries if lowered in item.lower()]
        else:
            cached_filtered = cached_summaries

        context: List[str] = []
        for summary in cached_filtered:
            context.append(summary)
            if len(context) >= limit:
                return context

        # Lazy initialize mem0 only if we still need more context
        self._ensure_mem0_initialized()

        if not self.mem0_available or not self.memory:
            return context

        remaining = max(limit - len(context), 0)
        if remaining == 0:
            return context

        try:
            logger.info(
                "Querying mem0 for user: %s (clinic: %s, query: %s)",
                clean_phone[:8] + "***",
                clinic_id or 'global',
                (query[:50] if query else 'None')
            )

            loop = asyncio.get_event_loop()

            for user_id in self._candidate_mem0_user_ids(clean_phone, clinic_id):
                try:
                    if query:
                        memories = await asyncio.wait_for(
                            loop.run_in_executor(
                                None,
                                lambda: self.memory.search(query, user_id=user_id, limit=remaining)
                            ),
                            timeout=MEM0_TIMEOUT_MS / 1000.0
                        )
                    else:
                        memories = await asyncio.wait_for(
                            loop.run_in_executor(
                                None,
                                lambda: self.memory.get_all(user_id=user_id, limit=remaining)
                            ),
                            timeout=MEM0_TIMEOUT_MS / 1000.0
                        )
                except asyncio.TimeoutError:
                    logger.warning("mem0 search timed out for user %s", user_id)
                    continue
                except Exception as exc:
                    logger.debug(f"mem0 search failed for user {user_id}: {exc}")
                    continue

                for memory in memories:
                    if isinstance(memory, dict):
                        memory_text = memory.get('memory') or memory.get('content') or ''
                    else:
                        memory_text = str(memory)

                    if not memory_text:
                        continue

                    if memory_text in context:
                        continue

                    context.append(memory_text)
                    if len(context) >= limit:
                        break

                if len(context) >= limit:
                    break

            return context

        except Exception as exc:
            logger.error(f"❌ Failed to get mem0 context: {exc}", exc_info=True)
            return context
    
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
        phone_number: str,
        clinic_id: Optional[str] = None
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
            # Get mem0 memories about user preferences (prefers cached ones when available)
            memories: List[Any] = []
            self._ensure_mem0_initialized()

            if self.mem0_available and self.memory:
                loop = asyncio.get_event_loop()

                for user_id in self._candidate_mem0_user_ids(clean_phone, clinic_id):
                    try:
                        memories = await asyncio.wait_for(
                            loop.run_in_executor(
                                None,
                                lambda uid=user_id: self.memory.get_all(user_id=uid, limit=20)
                            ),
                            timeout=MEM0_TIMEOUT_MS / 1000.0
                        )
                    except (asyncio.TimeoutError, Exception):
                        continue

                    if memories:
                        break

                for memory in memories or []:
                    memory_text = memory.get('memory', '') if isinstance(memory, dict) else str(memory)

                    lowered = memory_text.lower()

                    # Extract preferences from memories
                    if 'prefers' in lowered or 'likes' in lowered:
                        preferences['appointment_preferences'].append(memory_text)

                    if 'language' in lowered or 'speaks' in lowered:
                        preferences['language'] = memory_text

                    if 'name is' in lowered or 'call me' in lowered:
                        preferences['preferred_name'] = memory_text

            # Also check recent messages for language detection
            history = await self.get_conversation_history(phone_number, clinic_id or '', limit=5)
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
