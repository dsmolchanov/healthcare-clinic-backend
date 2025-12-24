"""
Base LangGraph Orchestrator
General-purpose conversation orchestrator without domain-specific logic
Provides foundation for all specialized orchestrators
"""

from langgraph.graph import StateGraph, END
from typing import TypedDict, Optional, Dict, Any, List, Annotated
import logging
import asyncio
from datetime import datetime
from enum import Enum


def last_value(existing: Any, new: Any) -> Any:
    """Reducer that keeps the last (newest) value - used for scalar fields in LangGraph state."""
    return new


def merge_list(existing: List, new: List) -> List:
    """Reducer that appends new items to existing list - used for audit trails etc."""
    if existing is None:
        return new or []
    if new is None:
        return existing
    return existing + new


def bounded_merge_list(max_length: int = 50):
    """
    Factory for bounded list merge reducer.
    Keeps only the most recent `max_length` items.

    Args:
        max_length: Maximum number of items to retain (default 50)

    Returns:
        Reducer function that merges and truncates lists
    """
    def reducer(existing: List, new: List) -> List:
        if existing is None:
            existing = []
        if new is None:
            new = []
        combined = existing + new
        # Keep only the most recent items
        if len(combined) > max_length:
            return combined[-max_length:]
        return combined
    return reducer


logger = logging.getLogger(__name__)


class ComplianceMode(Enum):
    """Supported compliance modes"""
    NONE = "none"
    HIPAA = "hipaa"
    GDPR = "gdpr"
    LFPDPPP = "lfpdppp"  # Mexican data protection law


class BaseConversationState(TypedDict):
    """
    Base state for all conversation workflows.

    Uses Annotated types with reducers to handle LangGraph checkpointing properly.
    Scalar fields use last_value reducer (keep newest), list fields use merge_list.
    """
    # Core conversation data - scalar fields use last_value reducer
    session_id: Annotated[str, last_value]
    message: Annotated[str, last_value]
    context: Annotated[dict, last_value]
    intent: Annotated[Optional[str], last_value]
    response: Annotated[Optional[str], last_value]
    metadata: Annotated[dict, last_value]

    # Memory and knowledge - can be replaced each turn
    memories: Annotated[Optional[List[dict]], last_value]
    knowledge: Annotated[Optional[List[dict]], last_value]

    # Workflow control - scalar fields
    error: Annotated[Optional[str], last_value]
    should_end: Annotated[bool, last_value]
    next_node: Annotated[Optional[str], last_value]

    # Compliance tracking - mode is scalar, lists bounded to prevent memory growth
    compliance_mode: Annotated[Optional[str], last_value]
    compliance_checks: Annotated[List[dict], bounded_merge_list(50)]
    audit_trail: Annotated[List[dict], bounded_merge_list(50)]


class BaseLangGraphOrchestrator:
    """
    Base orchestrator for conversation workflows
    Provides common nodes and patterns for all use cases
    """

    def __init__(
        self,
        compliance_mode: Optional[ComplianceMode] = None,
        enable_memory: bool = True,
        enable_rag: bool = True,
        enable_checkpointing: bool = True,
        enable_llm: bool = True,
        llm_model: Optional[str] = None,
        supabase_client = None,
        agent_config: Optional[Dict[str, Any]] = None,
    ):
        """
        Initialize the base orchestrator

        Args:
            compliance_mode: Optional compliance framework to enforce
            enable_memory: Enable memory retrieval/storage
            enable_rag: Enable RAG knowledge retrieval
            enable_checkpointing: Enable LangGraph checkpointing
            enable_llm: Enable LLM for response generation
            llm_model: Specific LLM model to use (e.g., 'glm-4.5', 'gpt-5-mini')
            supabase_client: Supabase client for database operations
            agent_config: Agent configuration with LLM settings
        """
        self.compliance_mode = compliance_mode
        self.enable_memory = enable_memory
        self.enable_rag = enable_rag
        self.enable_checkpointing = enable_checkpointing
        self.agent_config = agent_config or {}
        self.supabase = supabase_client

        # Initialize LLM factory if enabled
        self.llm_factory = None
        self.primary_model = None
        self.fallback_model = None

        if enable_llm:
            try:
                # Import LLM factory
                from app.services.llm.llm_factory import LLMFactory

                # Create factory
                self.llm_factory = LLMFactory(supabase_client)

                # Load LLM configuration from agent config or use defaults
                llm_settings = self.agent_config.get('llm_settings', {})
                self.primary_model = llm_model or llm_settings.get('primary_model', 'gpt-5-nano')
                self.fallback_model = llm_settings.get('fallback_model', 'gpt-5-nano')
                self.temperature = llm_settings.get('temperature', 0.7)
                self.max_tokens = llm_settings.get('max_tokens', 500)

                logger.info(f"Initialized LLM factory with primary model: {self.primary_model}")

            except Exception as e:
                logger.warning(f"Failed to initialize LLM factory: {e}")
                self.llm_factory = None

        # Build the workflow graph
        self.graph = self._build_graph()

        # Checkpointing is initialized asynchronously
        # Call await _init_checkpointer() before first use
        self.checkpointer = None
        self._enable_checkpointing = enable_checkpointing

        # Compile without checkpointer initially
        # Will recompile with checkpointer after async init
        self.compiled_graph = self.graph.compile()

    def _build_graph(self) -> StateGraph:
        """
        Build the base workflow graph

        Returns:
            Compiled StateGraph with core nodes
        """
        workflow = StateGraph(BaseConversationState)

        # Core nodes - always present
        workflow.add_node("entry", self.entry_node)
        workflow.add_node("intent_classify", self.intent_classify_node)
        workflow.add_node("process", self.process_node)
        workflow.add_node("generate_response", self.generate_response_node)
        workflow.add_node("exit", self.exit_node)

        # Optional memory/RAG nodes
        if self.enable_memory:
            workflow.add_node("memory_retrieve", self.memory_retrieve_node)
            workflow.add_node("memory_store", self.memory_store_node)

        if self.enable_rag:
            workflow.add_node("knowledge_retrieve", self.knowledge_retrieve_node)

        # Add compliance layer conditionally
        if self.compliance_mode and self.compliance_mode != ComplianceMode.NONE:
            workflow.add_node("compliance_check", self.compliance_check_node)
            workflow.add_node("compliance_audit", self.compliance_audit_node)

            # Route through compliance first
            workflow.add_edge("entry", "compliance_check")
            workflow.add_conditional_edges(
                "compliance_check",
                self.compliance_router,
                {
                    "continue": "intent_classify",
                    "block": "exit"
                }
            )
        else:
            workflow.add_edge("entry", "intent_classify")

        # Intent routing - subclasses can override _add_intent_routing() for custom routing
        self._add_intent_routing(workflow)

        if self.enable_memory:
            workflow.add_edge("memory_retrieve", "knowledge_retrieve" if self.enable_rag else "process")

        if self.enable_rag:
            workflow.add_edge("knowledge_retrieve", "process")

        workflow.add_edge("process", "generate_response")

        # Determine the next node after generate_response
        # Priority: memory_store -> compliance_audit -> exit
        if self.enable_memory:
            workflow.add_edge("generate_response", "memory_store")
            if self.compliance_mode and self.compliance_mode != ComplianceMode.NONE:
                workflow.add_edge("memory_store", "compliance_audit")
                workflow.add_edge("compliance_audit", "exit")
            else:
                workflow.add_edge("memory_store", "exit")
        elif self.compliance_mode and self.compliance_mode != ComplianceMode.NONE:
            # No memory, but compliance is enabled - route directly to audit
            workflow.add_edge("generate_response", "compliance_audit")
            workflow.add_edge("compliance_audit", "exit")
        else:
            # No memory, no compliance - go straight to exit
            workflow.add_edge("generate_response", "exit")

        workflow.add_edge("exit", END)

        # Set entry point
        workflow.set_entry_point("entry")

        return workflow

    async def _init_checkpointer(self) -> None:
        """
        Initialize checkpointer asynchronously.

        Call this method before first process() call. Recompiles graph with checkpointer.
        Uses Postgres checkpointer in production, MemorySaver in development.
        """
        if not self._enable_checkpointing:
            return

        if self.checkpointer is not None:
            return  # Already initialized

        try:
            from app.services.orchestrator.checkpointer import get_checkpointer
            self.checkpointer = await get_checkpointer()
            self.compiled_graph = self.graph.compile(checkpointer=self.checkpointer)
            logger.info("Graph recompiled with checkpointer")
        except Exception as e:
            logger.warning(f"Failed to initialize checkpointer: {e}. Continuing without checkpointing.")
            self._enable_checkpointing = False

    def _add_intent_routing(self, workflow: StateGraph) -> None:
        """
        Add routing edges from intent_classify node.

        Subclasses can override this to add conditional routing based on intent.
        Default implementation goes directly to memory_retrieve or process.
        """
        workflow.add_edge("intent_classify", "memory_retrieve" if self.enable_memory else "process")

    # Core node implementations

    async def entry_node(self, state: BaseConversationState) -> BaseConversationState:
        """Entry point for all conversations"""
        logger.debug(f"Entry node - session: {state['session_id']}")

        # Initialize audit trail
        if 'audit_trail' not in state:
            state['audit_trail'] = []

        state['audit_trail'].append({
            "node": "entry",
            "timestamp": datetime.utcnow().isoformat(),
            "message_length": len(state.get('message', ''))
        })

        return state

    async def intent_classify_node(self, state: BaseConversationState) -> BaseConversationState:
        """Classify user intent from message"""
        logger.debug(f"Intent classification - session: {state['session_id']}")

        # Basic intent classification (override in subclasses for LLM-based)
        message_lower = state['message'].lower()

        if any(word in message_lower for word in ['appointment', 'schedule', 'book']):
            state['intent'] = 'appointment'
        elif any(word in message_lower for word in ['cancel', 'reschedule']):
            state['intent'] = 'modification'
        elif any(word in message_lower for word in ['help', 'question', 'what', 'how']):
            state['intent'] = 'inquiry'
        else:
            state['intent'] = 'general'

        state['audit_trail'].append({
            "node": "intent_classify",
            "timestamp": datetime.utcnow().isoformat(),
            "intent": state['intent']
        })

        return state

    async def memory_retrieve_node(self, state: BaseConversationState) -> BaseConversationState:
        """Retrieve relevant memories for context"""
        logger.debug(f"Memory retrieval - session: {state['session_id']}")

        try:
            # Import universal memory system
            from app.services.universal_rag import UniversalMemory

            # Initialize memory with user context
            user_id = state.get('metadata', {}).get('user_id') or state['session_id']
            memory = UniversalMemory(user_id=user_id)

            # Search for relevant memories
            query = state.get('message', '')
            memories = await memory.get_context(
                query=query,
                session_id=state['session_id'],
                max_memories=3
            )

            state['memories'] = memories if memories else []
            logger.info(f"Retrieved {len(state['memories'])} memories for session {state['session_id']}")

        except Exception as e:
            logger.warning(f"Memory retrieval failed: {e}, using empty memories")
            state['memories'] = []

        state['audit_trail'].append({
            "node": "memory_retrieve",
            "timestamp": datetime.utcnow().isoformat(),
            "memories_retrieved": len(state['memories'])
        })

        return state

    async def knowledge_retrieve_node(self, state: BaseConversationState) -> BaseConversationState:
        """Retrieve relevant knowledge from RAG"""
        logger.debug(f"Knowledge retrieval - session: {state['session_id']}")

        try:
            # Import universal RAG system
            from app.services.universal_rag import UniversalRAG

            # Initialize RAG with namespace (e.g., clinic_id)
            namespace = state.get('metadata', {}).get('clinic_id') or 'default'
            rag = UniversalRAG(namespace=namespace)

            # Get context for the query
            query = state.get('message', '')
            knowledge_context = await rag.get_context(
                query=query,
                session_id=state['session_id'],
                max_tokens=1000
            )

            state['knowledge'] = knowledge_context if knowledge_context else []
            logger.info(f"Retrieved knowledge context for session {state['session_id']}")

        except Exception as e:
            logger.warning(f"Knowledge retrieval failed: {e}, using empty knowledge")
            state['knowledge'] = []

        state['audit_trail'].append({
            "node": "knowledge_retrieve",
            "timestamp": datetime.utcnow().isoformat(),
            "knowledge_retrieved": len(str(state['knowledge']))
        })

        return state

    async def process_node(self, state: BaseConversationState) -> BaseConversationState:
        """Main processing logic - can use LLM for understanding"""
        logger.debug(f"Processing - session: {state['session_id']}")

        # Use LLM factory if available for better understanding
        if self.llm_factory:
            try:
                # Prepare context from pipeline-provided data
                context_parts = []
                pipeline_ctx = state.get('context', {})

                # Clinic profile for business context
                clinic_profile = pipeline_ctx.get('clinic_profile', {})
                if clinic_profile:
                    clinic_name = clinic_profile.get('name', 'Clinic')
                    context_parts.append(f"Clinic: {clinic_name}")
                    if clinic_profile.get('business_hours'):
                        context_parts.append(f"Hours: {clinic_profile['business_hours']}")

                # Available doctors (summarized)
                clinic_doctors = pipeline_ctx.get('clinic_doctors', [])
                if clinic_doctors:
                    doctor_names = [d.get('name', d.get('full_name', 'Doctor')) for d in clinic_doctors[:5]]
                    context_parts.append(f"Available doctors: {', '.join(doctor_names)}")

                # Available services (summarized with prices)
                clinic_services = pipeline_ctx.get('clinic_services', [])
                if clinic_services:
                    svc_summary = [f"{s.get('name', 'Service')} (${s.get('price', 'N/A')})" for s in clinic_services[:10]]
                    context_parts.append(f"Services: {', '.join(svc_summary)}")

                # FAQs for common questions
                clinic_faqs = pipeline_ctx.get('clinic_faqs', [])
                if clinic_faqs:
                    faq_summary = "; ".join([f"Q: {f.get('question', '')[:50]}..." for f in clinic_faqs[:3]])
                    context_parts.append(f"Common FAQs: {faq_summary}")

                # Patient context
                patient_profile = pipeline_ctx.get('patient_profile', {})
                if patient_profile:
                    patient_name = patient_profile.get('name', patient_profile.get('first_name', ''))
                    if patient_name:
                        context_parts.append(f"Patient: {patient_name}")

                # Legacy memory/knowledge context
                if state.get('memories'):
                    context_parts.append(f"Previous context: {str(state['memories'])[:300]}")
                if state.get('knowledge'):
                    context_parts.append(f"Relevant knowledge: {str(state['knowledge'])[:300]}")

                context = "\n".join(context_parts) if context_parts else ""

                # Generate response using LLM factory
                # Check for system prompt override in metadata
                system_prompt_override = state.get('metadata', {}).get('system_prompt_override')

                if system_prompt_override:
                    system_prompt = system_prompt_override
                else:
                    # Try to use PromptComposer with DB templates for sophisticated prompts
                    try:
                        from app.prompts.composer import PromptComposer
                        from app.api.pipeline.context import PipelineContext

                        # Build a minimal PipelineContext from state for the composer
                        # This allows reuse of DB-backed prompt templates
                        clinic_id = state.get('metadata', {}).get('clinic_id')
                        if clinic_id and pipeline_ctx:
                            composer = PromptComposer(use_db_templates=True)
                            # Create a mock context with needed fields
                            mock_ctx = type('MockCtx', (), {
                                'clinic_profile': clinic_profile,
                                'clinic_name': clinic_profile.get('name', 'Clinic'),
                                'effective_clinic_id': clinic_id,
                                'from_phone': state.get('metadata', {}).get('phone_number', ''),
                                'profile': patient_profile,
                                'conversation_state': None,
                                'session_messages': pipeline_ctx.get('conversation_history', []),
                                'previous_session_summary': None,
                                'additional_context': None,
                                'constraints': None,
                                'narrowing_instruction': None,
                            })()
                            system_prompt = await composer.compose_async(mock_ctx, include_booking_policy=True)
                            logger.debug(f"[LangGraph] Using DB-backed prompt composer for clinic {clinic_id}")
                        else:
                            raise ValueError("No clinic_id for prompt composer")
                    except Exception as e:
                        logger.debug(f"[LangGraph] Falling back to inline prompt: {e}")
                        # Fallback to inline prompt with context
                        system_prompt = (
                            "You are a helpful healthcare assistant. Use the provided context to give accurate, "
                            "relevant responses. Be concise and professional.\n\n"
                            f"Context:\n{context}" if context else
                            "You are a helpful healthcare assistant. Be concise and professional."
                        )

                # Build messages for factory
                messages = [
                    {"role": "system", "content": system_prompt},
                ]

                # Include conversation history from pipeline context
                conversation_history = state.get('context', {}).get('conversation_history', [])
                if conversation_history:
                    for msg in conversation_history[-10:]:  # Last 10 messages to avoid token overflow
                        role = msg.get('role', 'user')
                        content = msg.get('content', msg.get('text', ''))
                        if role in ('user', 'assistant') and content:
                            messages.append({"role": role, "content": content})

                # Add current message
                messages.append({"role": "user", "content": state['message']})

                # Generate using factory
                response = await self.llm_factory.generate(
                    messages=messages,
                    model=self.primary_model,
                    temperature=self.temperature,
                    max_tokens=self.max_tokens
                )

                state['response'] = response.content
                state['metadata']['llm_provider'] = response.provider
                state['metadata']['llm_model'] = response.model
                state['metadata']['llm_latency_ms'] = response.latency_ms
                state['metadata']['llm_input_tokens'] = response.usage['input_tokens']
                state['metadata']['llm_output_tokens'] = response.usage['output_tokens']

            except Exception as e:
                logger.warning(f"LLM processing failed: {e}, using fallback")
                state['response'] = f"Processing message with intent: {state.get('intent', 'unknown')}"
        else:
            # Base implementation - echo back
            state['response'] = f"Processing message with intent: {state.get('intent', 'unknown')}"

        state['audit_trail'].append({
            "node": "process",
            "timestamp": datetime.utcnow().isoformat(),
            "llm_used": self.llm_factory is not None
        })

        return state

    async def generate_response_node(self, state: BaseConversationState) -> BaseConversationState:
        """
        Pure passthrough node - response is already generated in process_node.
        This node exists for graph structure consistency (audit trail, future hooks).
        """
        logger.debug(f"generate_response_node passthrough - session: {state['session_id']}")

        state['audit_trail'].append({
            "node": "generate_response",
            "timestamp": datetime.utcnow().isoformat(),
            "response_length": len(state.get('response', '') or ''),
            "source": "passthrough"
        })

        return state

    async def memory_store_node(self, state: BaseConversationState) -> BaseConversationState:
        """Store conversation in memory"""
        logger.debug(f"Memory storage - session: {state['session_id']}")

        try:
            # Import universal memory system
            from app.services.universal_rag import UniversalMemory

            # Initialize memory with user context
            user_id = state.get('metadata', {}).get('user_id') or state['session_id']
            memory = UniversalMemory(user_id=user_id)

            # Store the conversation turn
            conversation_content = f"User: {state.get('message', '')}\nAssistant: {state.get('response', '')}"
            metadata = {
                "session_id": state['session_id'],
                "intent": state.get('intent'),
                "timestamp": datetime.utcnow().isoformat()
            }

            result = await memory.add(
                content=conversation_content,
                metadata=metadata,
                session_id=state['session_id']
            )

            logger.info(f"Stored conversation in memory: {result}")

        except Exception as e:
            logger.warning(f"Memory storage failed: {e}")

        state['audit_trail'].append({
            "node": "memory_store",
            "timestamp": datetime.utcnow().isoformat()
        })

        return state

    async def compliance_check_node(self, state: BaseConversationState) -> BaseConversationState:
        """Check compliance requirements before processing"""
        logger.debug(f"Compliance check - mode: {self.compliance_mode}")

        state['compliance_checks'] = []

        # Basic compliance checks based on mode
        if self.compliance_mode == ComplianceMode.HIPAA:
            # Check for PHI patterns
            state['compliance_checks'].append({
                "type": "phi_check",
                "passed": True,  # Placeholder
                "timestamp": datetime.utcnow().isoformat()
            })
        elif self.compliance_mode == ComplianceMode.GDPR:
            # Check for consent
            state['compliance_checks'].append({
                "type": "consent_check",
                "passed": True,  # Placeholder
                "timestamp": datetime.utcnow().isoformat()
            })

        state['audit_trail'].append({
            "node": "compliance_check",
            "timestamp": datetime.utcnow().isoformat(),
            "mode": self.compliance_mode.value if self.compliance_mode else "none",
            "checks": len(state['compliance_checks'])
        })

        return state

    async def compliance_audit_node(self, state: BaseConversationState) -> BaseConversationState:
        """Create audit log for compliance"""
        logger.debug(f"Compliance audit - session: {state['session_id']}")

        # Placeholder - integrate with immutable audit logger

        state['audit_trail'].append({
            "node": "compliance_audit",
            "timestamp": datetime.utcnow().isoformat()
        })

        return state

    async def exit_node(self, state: BaseConversationState) -> BaseConversationState:
        """Clean exit point"""
        logger.debug(f"Exit node - session: {state['session_id']}")

        state['should_end'] = True

        state['audit_trail'].append({
            "node": "exit",
            "timestamp": datetime.utcnow().isoformat(),
            "total_nodes": len(state['audit_trail'])
        })

        return state

    def compliance_router(self, state: BaseConversationState) -> str:
        """Route based on compliance check results"""
        if state.get('compliance_checks'):
            # Check if all compliance checks passed
            all_passed = all(check.get('passed', False) for check in state['compliance_checks'])
            return "continue" if all_passed else "block"
        return "continue"

    async def process(
        self,
        message: str,
        session_id: str,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Process a message through the workflow

        Args:
            message: User message to process
            session_id: Unique session identifier
            metadata: Optional metadata

        Returns:
            Processed state with response
        """
        initial_state = BaseConversationState(
            session_id=session_id,
            message=message,
            context={},
            intent=None,
            response=None,
            metadata=metadata or {},
            memories=None,
            knowledge=None,
            error=None,
            should_end=False,
            next_node=None,
            compliance_mode=self.compliance_mode.value if self.compliance_mode else None,
            compliance_checks=[],
            audit_trail=[]
        )

        try:
            # Initialize checkpointer on first call (if enabled)
            await self._init_checkpointer()

            # Run the graph
            if self._enable_checkpointing and self.checkpointer is not None:
                result = await self.compiled_graph.ainvoke(
                    initial_state,
                    {"configurable": {"thread_id": session_id}}
                )
            else:
                result = await self.compiled_graph.ainvoke(initial_state)

            return result

        except Exception as e:
            logger.error(f"Error processing message: {e}")
            return {
                "session_id": session_id,
                "response": "I encountered an error processing your message. Please try again.",
                "error": str(e)
            }


# Example usage
if __name__ == "__main__":
    async def test_orchestrator():
        from app.db import get_supabase_client

        # Get Supabase client
        supabase = get_supabase_client()

        # Create orchestrator without compliance
        orchestrator = BaseLangGraphOrchestrator(
            compliance_mode=None,
            enable_memory=True,
            enable_rag=True,
            supabase_client=supabase
        )

        # Process a message
        result = await orchestrator.process(
            message="I need to schedule an appointment",
            session_id="test_session_123",
            metadata={"channel": "whatsapp"}
        )

        print(f"Response: {result.get('response')}")
        print(f"Intent: {result.get('intent')}")
        print(f"Audit trail: {len(result.get('audit_trail', []))} nodes")
        print(f"LLM Model: {result.get('metadata', {}).get('llm_model')}")

    # Run test
    asyncio.run(test_orchestrator())