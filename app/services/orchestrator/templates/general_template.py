"""
General LangGraph Template
Simple conversation orchestrator without compliance requirements
Suitable for general chatbots, customer service, and informational agents
"""

import sys
import os

from ..base_langgraph import BaseLangGraphOrchestrator, BaseConversationState, ComplianceMode
from typing import Optional, Dict, Any, List
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


class GeneralConversationState(BaseConversationState):
    """General conversation state with minimal fields"""
    # Conversation flow
    conversation_type: Optional[str]
    user_sentiment: Optional[str]
    topic: Optional[str]

    # Response formatting
    response_style: str  # formal, casual, friendly
    include_suggestions: bool


class GeneralLangGraph(BaseLangGraphOrchestrator):
    """
    General-purpose LangGraph orchestrator
    Simple workflow without compliance overhead
    """

    def __init__(
        self,
        llm_client: Optional[Any] = None,
        response_style: str = "friendly",
        enable_suggestions: bool = True
    ):
        """
        Initialize general orchestrator

        Args:
            llm_client: LLM client for response generation
            response_style: Default response style (formal/casual/friendly)
            enable_suggestions: Whether to include suggested follow-ups
        """
        # No compliance needed for general use
        super().__init__(
            compliance_mode=None,
            enable_memory=True,
            enable_rag=True,
            enable_checkpointing=False  # Lighter weight without checkpointing
        )

        self.llm_client = llm_client
        self.response_style = response_style
        self.enable_suggestions = enable_suggestions

    async def intent_classify_node(self, state: GeneralConversationState) -> GeneralConversationState:
        """Enhanced intent classification for general conversations"""
        logger.debug(f"Intent classification - session: {state['session_id']}")

        message_lower = state['message'].lower()

        # Classify conversation type
        if any(word in message_lower for word in ['help', 'support', 'problem', 'issue']):
            state['conversation_type'] = 'support'
            state['intent'] = 'help_request'
        elif any(word in message_lower for word in ['buy', 'purchase', 'price', 'cost']):
            state['conversation_type'] = 'sales'
            state['intent'] = 'purchase_inquiry'
        elif any(word in message_lower for word in ['how', 'what', 'when', 'where', 'why']):
            state['conversation_type'] = 'informational'
            state['intent'] = 'question'
        elif any(word in message_lower for word in ['thank', 'thanks', 'bye', 'goodbye']):
            state['conversation_type'] = 'closing'
            state['intent'] = 'farewell'
        else:
            state['conversation_type'] = 'general'
            state['intent'] = 'chat'

        # Simple sentiment detection
        if any(word in message_lower for word in ['angry', 'frustrated', 'upset', 'terrible']):
            state['user_sentiment'] = 'negative'
        elif any(word in message_lower for word in ['happy', 'great', 'excellent', 'wonderful']):
            state['user_sentiment'] = 'positive'
        else:
            state['user_sentiment'] = 'neutral'

        # Extract topic (simplified)
        if 'product' in message_lower:
            state['topic'] = 'product'
        elif 'service' in message_lower:
            state['topic'] = 'service'
        elif 'account' in message_lower:
            state['topic'] = 'account'
        else:
            state['topic'] = 'general'

        state['audit_trail'].append({
            "node": "intent_classify",
            "timestamp": datetime.utcnow().isoformat(),
            "intent": state['intent'],
            "conversation_type": state['conversation_type'],
            "sentiment": state['user_sentiment']
        })

        return state

    async def process_node(self, state: GeneralConversationState) -> GeneralConversationState:
        """Process message and generate appropriate response"""
        logger.debug(f"Processing - session: {state['session_id']}")

        # Use memories and knowledge if available
        context_parts = []

        if state.get('memories'):
            context_parts.append(f"Previous context: {state['memories'][:3]}")  # Last 3 memories

        if state.get('knowledge'):
            context_parts.append(f"Relevant info: {state['knowledge'][:2]}")  # Top 2 results

        context = "\n".join(context_parts) if context_parts else "No additional context"

        # Generate response based on intent and context
        if self.llm_client:
            # Use LLM for response generation
            prompt = f"""
            User message: {state['message']}
            Intent: {state['intent']}
            Sentiment: {state['user_sentiment']}
            Context: {context}
            Style: {self.response_style}

            Generate a helpful response.
            """

            try:
                response = await self.llm_client.generate(prompt)
                state['response'] = response
            except Exception as e:
                logger.error(f"LLM generation failed: {e}")
                state['response'] = self._get_fallback_response(state)
        else:
            # Use template-based responses
            state['response'] = self._get_fallback_response(state)

        state['audit_trail'].append({
            "node": "process",
            "timestamp": datetime.utcnow().isoformat(),
            "used_llm": bool(self.llm_client)
        })

        return state

    async def generate_response_node(self, state: GeneralConversationState) -> GeneralConversationState:
        """Format final response with suggestions if enabled"""
        logger.debug(f"Response generation - session: {state['session_id']}")

        # Add suggestions based on conversation type
        if self.enable_suggestions and state.get('conversation_type'):
            suggestions = self._get_suggestions(state['conversation_type'])
            if suggestions:
                state['response'] += f"\n\n{suggestions}"

        # Adjust tone based on sentiment
        if state.get('user_sentiment') == 'negative':
            # Add empathy for negative sentiment
            state['response'] = f"I understand your concern. {state['response']}"

        state['audit_trail'].append({
            "node": "generate_response",
            "timestamp": datetime.utcnow().isoformat(),
            "response_length": len(state['response']),
            "included_suggestions": self.enable_suggestions
        })

        return state

    def _get_fallback_response(self, state: GeneralConversationState) -> str:
        """Generate template-based fallback response"""
        responses = {
            'help_request': "I'm here to help! Could you please provide more details about what you need assistance with?",
            'purchase_inquiry': "I'd be happy to help you with product information. What specific item are you interested in?",
            'question': "That's a great question. Let me help you with that information.",
            'farewell': "Thank you for chatting with me! Have a wonderful day!",
            'chat': "I understand. Please tell me more about what you're looking for."
        }

        return responses.get(state.get('intent', 'chat'), "How can I assist you today?")

    def _get_suggestions(self, conversation_type: str) -> str:
        """Get suggested follow-up actions"""
        suggestions = {
            'support': "💡 You might also want to: Check our FAQ | Contact support team | View tutorials",
            'sales': "💡 You might also like: Browse catalog | Compare products | Check current offers",
            'informational': "💡 Related topics: Documentation | Video guides | Community forum",
            'closing': "",  # No suggestions for farewell
            'general': "💡 I can help with: Product info | Technical support | Account questions"
        }

        return suggestions.get(conversation_type, "")

    async def process(
        self,
        message: str,
        session_id: str,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Process general conversation

        Args:
            message: User message
            session_id: Session ID
            metadata: Optional metadata

        Returns:
            Processed state with response
        """
        # Create general-specific initial state
        initial_state = GeneralConversationState(
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
            compliance_mode=None,
            compliance_checks=[],
            audit_trail=[],
            conversation_type=None,
            user_sentiment=None,
            topic=None,
            response_style=self.response_style,
            include_suggestions=self.enable_suggestions
        )

        # Process through parent class
        try:
            result = await self.compiled_graph.ainvoke(initial_state)
            return result
        except Exception as e:
            logger.error(f"Error processing message: {e}")
            return {
                "session_id": session_id,
                "response": "I apologize, but I encountered an error. Please try again.",
                "error": str(e)
            }


# Example usage
if __name__ == "__main__":
    import asyncio

    async def test_general():
        # Create general orchestrator
        orchestrator = GeneralLangGraph(
            llm_client=None,  # Would use actual LLM client
            response_style="friendly",
            enable_suggestions=True
        )

        # Test support request
        result = await orchestrator.process(
            message="I'm having trouble with my account login",
            session_id="user_123"
        )

        print(f"Response: {result.get('response')}")
        print(f"Intent: {result.get('intent')}")
        print(f"Conversation type: {result.get('conversation_type')}")
        print(f"Sentiment: {result.get('user_sentiment')}")

        # Test purchase inquiry
        purchase_result = await orchestrator.process(
            message="What's the price of your premium subscription?",
            session_id="user_456"
        )

        print(f"\nPurchase Response: {purchase_result.get('response')}")

        # Test farewell
        farewell_result = await orchestrator.process(
            message="Thanks for your help, goodbye!",
            session_id="user_789"
        )

        print(f"\nFarewell Response: {farewell_result.get('response')}")

    asyncio.run(test_general())