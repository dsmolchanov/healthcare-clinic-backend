"""
Base LangGraph Orchestrator
General-purpose conversation orchestrator without domain-specific logic
Provides foundation for all specialized orchestrators
"""

from langgraph.graph import StateGraph, END
from typing import TypedDict, Optional, Dict, Any, List
import logging
import asyncio
from datetime import datetime
from enum import Enum

logger = logging.getLogger(__name__)


class ComplianceMode(Enum):
    """Supported compliance modes"""
    NONE = "none"
    HIPAA = "hipaa"
    GDPR = "gdpr"
    LFPDPPP = "lfpdppp"  # Mexican data protection law


class BaseConversationState(TypedDict):
    """Base state for all conversation workflows"""
    # Core conversation data
    session_id: str
    message: str
    context: dict
    intent: Optional[str]
    response: Optional[str]
    metadata: dict

    # Memory and knowledge
    memories: Optional[List[dict]]
    knowledge: Optional[List[dict]]

    # Workflow control
    error: Optional[str]
    should_end: bool
    next_node: Optional[str]

    # Compliance tracking
    compliance_mode: Optional[str]
    compliance_checks: List[dict]
    audit_trail: List[dict]


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

        # Compile with optional checkpointing
        if enable_checkpointing:
            from langgraph.checkpoint.memory import MemorySaver
            self.checkpointer = MemorySaver()
            self.compiled_graph = self.graph.compile(checkpointer=self.checkpointer)
        else:
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

        # Standard flow
        workflow.add_edge("intent_classify", "memory_retrieve" if self.enable_memory else "process")

        if self.enable_memory:
            workflow.add_edge("memory_retrieve", "knowledge_retrieve" if self.enable_rag else "process")

        if self.enable_rag:
            workflow.add_edge("knowledge_retrieve", "process")

        workflow.add_edge("process", "generate_response")
        workflow.add_edge("generate_response", "memory_store" if self.enable_memory else "exit")

        if self.enable_memory:
            workflow.add_edge("memory_store", "compliance_audit" if self.compliance_mode else "exit")

        if self.compliance_mode and self.compliance_mode != ComplianceMode.NONE:
            workflow.add_edge("compliance_audit", "exit")

        workflow.add_edge("exit", END)

        # Set entry point
        workflow.set_entry_point("entry")

        return workflow

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
                # Prepare context
                context_parts = []
                if state.get('memories'):
                    context_parts.append(f"Previous context: {state['memories'][:500]}")
                if state.get('knowledge'):
                    context_parts.append(f"Relevant knowledge: {state['knowledge'][:500]}")

                context = "\n".join(context_parts) if context_parts else ""

                # Generate response using LLM factory
                # Check for system prompt override in metadata
                system_prompt_override = state.get('metadata', {}).get('system_prompt_override')

                if system_prompt_override:
                    system_prompt = system_prompt_override
                else:
                    system_prompt = (
                        "You are a helpful assistant. Use the provided context to give accurate, "
                        "relevant responses. Be concise and professional."
                    )

                if context:
                    system_prompt += f"\n\nContext:\n{context}"

                # Build messages for factory
                messages = [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": state['message']}
                ]

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
        """Generate or enhance final response to user"""
        logger.debug(f"Response generation - session: {state['session_id']}")

        # Enhance response with LLM if needed and available
        if not state.get('response'):
            if self.llm_factory:
                try:
                    messages = [
                        {"role": "system", "content": "You are a helpful assistant. Provide a clear, concise response."},
                        {"role": "user", "content": f"Generate a helpful response to: {state['message']}"}
                    ]

                    response = await self.llm_factory.generate(
                        messages=messages,
                        model=self.primary_model,
                        temperature=0.7,
                        max_tokens=300
                    )
                    state['response'] = response.content
                except Exception as e:
                    logger.warning(f"LLM response generation failed: {e}")
                    state['response'] = "I understand your message. How can I help you?"
            else:
                state['response'] = "I understand your message. How can I help you?"

        state['audit_trail'].append({
            "node": "generate_response",
            "timestamp": datetime.utcnow().isoformat(),
            "response_length": len(state['response'])
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
            # Run the graph
            if self.enable_checkpointing:
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