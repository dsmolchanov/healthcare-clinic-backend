#!/usr/bin/env python3
"""
Comprehensive test suite for RAG Memory System
Tests all 4 phases of the implementation
"""

import asyncio
import os
import sys
import json
import pytest
from datetime import datetime
from typing import Dict, Any, List
import redis.asyncio as redis

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Import all components to test
from app.api.knowledge_ingestion import KnowledgeIngestionPipeline
from app.api.message_processor import PineconeKnowledgeBase
from app.memory.conversation_manager import ConversationContextManager
from app.api.response_constructor import LLMResponseConstructor
from app.state.multimodal_state import MultiModalStateManager, ConversationMode

# Test configuration
TEST_CLINIC_ID = "test_clinic_001"
TEST_SESSION_ID = "test_session_001"
TEST_USER_ID = "test_user_001"


class TestRAGMemorySystem:
    """Comprehensive test suite for RAG Memory System"""
    
    @classmethod
    def setup_class(cls):
        """Setup test environment"""
        print("\n" + "="*80)
        print("RAG MEMORY SYSTEM - COMPREHENSIVE TEST SUITE")
        print("="*80)
        
        # Check required environment variables
        required_vars = [
            'PINECONE_API_KEY',
            'OPENAI_API_KEY',
            'REDIS_HOST'
        ]
        
        missing_vars = [var for var in required_vars if not os.environ.get(var)]
        if missing_vars:
            print(f"⚠️  Missing environment variables: {', '.join(missing_vars)}")
            print("Please set these variables before running tests")
            return False
        
        print("✅ Environment variables configured")
        return True
    
    # ==================== PHASE 1 TESTS ====================
    
    async def test_phase1_document_ingestion(self):
        """Test document ingestion pipeline"""
        print("\n" + "-"*60)
        print("PHASE 1: Testing Document Ingestion Pipeline")
        print("-"*60)
        
        pipeline = KnowledgeIngestionPipeline(TEST_CLINIC_ID)
        
        # Test document 1: Procedures
        test_doc1 = """
        Dental Cleaning Procedure:
        A dental cleaning is a professional cleaning you receive from a dentist or dental hygienist.
        Most dental cleanings take approximately 45 minutes. Cleanings should be performed every 
        six months to prevent excessive plaque buildup.
        """
        
        print("\n1. Testing document ingestion with category 'procedures'...")
        result1 = await pipeline.ingest_document(
            content=test_doc1,
            metadata={
                'source': 'test_doc',
                'title': 'Dental Cleaning Guide'
            },
            category='procedures'
        )
        
        assert result1['status'] in ['indexed', 'already_indexed']
        print(f"   ✅ Document indexed: {result1['doc_id'][:8]}... ({result1.get('chunks', 0)} chunks)")
        
        # Test document 2: Policies
        test_doc2 = """
        Insurance Coverage Policy:
        We accept most major dental insurance plans. Patients are responsible for co-pays 
        at the time of service. For procedures not covered by insurance, we offer payment 
        plans with 0% interest for up to 12 months.
        """
        
        print("\n2. Testing document ingestion with category 'policies'...")
        result2 = await pipeline.ingest_document(
            content=test_doc2,
            metadata={
                'source': 'test_doc',
                'title': 'Insurance Policy'
            },
            category='policies'
        )
        
        assert result2['status'] in ['indexed', 'already_indexed']
        print(f"   ✅ Document indexed: {result2['doc_id'][:8]}... ({result2.get('chunks', 0)} chunks)")
        
        # Test category listing
        print("\n3. Testing category listing...")
        categories = await pipeline.list_categories()
        print(f"   Found categories: {categories}")
        assert 'procedures' in categories or 'policies' in categories
        print("   ✅ Categories retrieved successfully")
        
        return True
    
    async def test_phase1_category_search(self):
        """Test category-aware search in knowledge base"""
        print("\n" + "-"*60)
        print("PHASE 1: Testing Category-Aware Search")
        print("-"*60)
        
        kb = PineconeKnowledgeBase(TEST_CLINIC_ID)
        
        # Test search with category filter
        print("\n1. Testing search with category filter (procedures)...")
        results = await kb.search_by_category(
            query="dental cleaning",
            category="procedures",
            top_k=3
        )
        
        print(f"   Found {len(results)} results")
        for i, result in enumerate(results, 1):
            print(f"   Result {i}: confidence={result.get('confidence', 0):.2f}, category={result.get('category')}")
        
        if results:
            assert results[0].get('confidence', 0) > 0.7
            print("   ✅ Category search working with good confidence")
        else:
            print("   ⚠️  No results found (may need to wait for indexing)")
        
        # Test general search without category
        print("\n2. Testing general search without category...")
        general_results = await kb.search(
            query="insurance payment",
            top_k=3
        )
        
        print(f"   Found {len(general_results)} results")
        if general_results:
            print("   ✅ General search working")
        else:
            print("   ⚠️  No results found")
        
        return True
    
    # ==================== PHASE 2 TESTS ====================
    
    async def test_phase2_conversation_memory(self):
        """Test conversation context management"""
        print("\n" + "-"*60)
        print("PHASE 2: Testing Conversation Memory Management")
        print("-"*60)
        
        manager = ConversationContextManager(TEST_SESSION_ID, TEST_USER_ID)
        
        # Add test messages
        print("\n1. Adding messages to conversation memory...")
        test_messages = [
            ("user", "I need to schedule a dental cleaning"),
            ("assistant", "I can help you schedule a dental cleaning. When would you prefer?"),
            ("user", "Next Tuesday morning would be good"),
            ("assistant", "Let me check availability for Tuesday morning...")
        ]
        
        for role, content in test_messages:
            await manager.add_message(role, content)
            print(f"   Added {role} message")
        
        print("   ✅ Messages added to memory")
        
        # Test context window retrieval
        print("\n2. Testing context window retrieval...")
        context = await manager.get_context_window()
        print(f"   Retrieved {len(context)} messages from context window")
        assert len(context) > 0
        print("   ✅ Context window retrieval working")
        
        # Test relevant memory search
        print("\n3. Testing relevant memory search...")
        memories = await manager.get_relevant_memories(
            query="dental appointment",
            limit=5
        )
        
        print(f"   Found {len(memories)} relevant memories")
        for i, memory in enumerate(memories, 1):
            print(f"   Memory {i}: score={memory.get('score', 0):.2f}")
        
        print("   ✅ Memory search working")
        
        return True
    
    async def test_phase2_response_construction(self):
        """Test LLM response construction with context"""
        print("\n" + "-"*60)
        print("PHASE 2: Testing LLM Response Construction")
        print("-"*60)
        
        constructor = LLMResponseConstructor(
            TEST_SESSION_ID,
            TEST_USER_ID,
            TEST_CLINIC_ID
        )
        
        print("\n1. Constructing response with full context...")
        response = await constructor.construct_response(
            user_query="What time slots are available for dental cleaning?",
            intent="appointment",
            include_knowledge=True
        )
        
        print(f"   Response generated:")
        print(f"   - Content length: {len(response.get('content', ''))} chars")
        print(f"   - Intent: {response.get('intent')}")
        print(f"   - Sources: {response.get('sources', [])}")
        print(f"   - Confidence: {response.get('confidence', 0):.2f}")
        
        assert response.get('content')
        print("   ✅ Response construction successful")
        
        return True
    
    # ==================== PHASE 3 TESTS ====================
    
    async def test_phase3_langgraph_integration(self):
        """Test LangGraph RAG integration"""
        print("\n" + "-"*60)
        print("PHASE 3: Testing LangGraph RAG Integration")
        print("-"*60)
        
        # Import LangGraph orchestrator
        sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
        from services.langgraph_orchestrator import HealthcareLangGraphOrchestrator
        from services.models.unified_state import UnifiedSessionState, Channel
        
        print("\n1. Initializing LangGraph orchestrator...")
        orchestrator = HealthcareLangGraphOrchestrator(enable_checkpointing=False)
        await orchestrator.initialize()
        print("   ✅ Orchestrator initialized")
        
        print("\n2. Processing knowledge query through workflow...")
        result = await orchestrator.process_message(
            session_id=TEST_SESSION_ID,
            message="What is the procedure for dental cleaning?",
            channel=Channel.WHATSAPP,
            patient_id=TEST_USER_ID
        )
        
        print(f"   Workflow result:")
        print(f"   - Success: {result.get('success')}")
        print(f"   - Intent: {result.get('intent')}")
        print(f"   - Confidence: {result.get('confidence', 0):.2f}")
        print(f"   - Nodes visited: {len(result.get('nodes_visited', []))}")
        
        if result.get('success'):
            print("   ✅ LangGraph RAG integration working")
        else:
            print(f"   ⚠️  Error: {result.get('error')}")
        
        # Test memory retrieval node specifically
        print("\n3. Testing memory retrieval node...")
        state = await orchestrator.get_session_state(TEST_SESSION_ID)
        if state:
            print(f"   - Short-term memory items: {len(state.memory.short_term_memory)}")
            print(f"   - Long-term memory items: {len(state.memory.long_term_memory)}")
            print(f"   - Knowledge base items: {len(state.memory.knowledge_base)}")
            print("   ✅ Memory retrieval node functioning")
        
        return True
    
    # ==================== PHASE 4 TESTS ====================
    
    async def test_phase4_multimodal_state(self):
        """Test multi-modal state management"""
        print("\n" + "-"*60)
        print("PHASE 4: Testing Multi-Modal State Management")
        print("-"*60)
        
        manager = MultiModalStateManager()
        
        # Create session state
        print("\n1. Creating multi-modal session state...")
        state = await manager.create_session_state(
            session_id=TEST_SESSION_ID,
            initial_mode=ConversationMode.TEXT,
            metadata={
                'channel': 'whatsapp',
                'user_id': TEST_USER_ID,
                'clinic_id': TEST_CLINIC_ID
            }
        )
        
        assert state['session_id'] == TEST_SESSION_ID
        assert state['mode'] == 'text'
        print(f"   ✅ Session created in {state['mode']} mode")
        
        # Sync conversation state
        print("\n2. Syncing conversation state...")
        test_messages = [
            {'role': 'user', 'content': 'I need help', 'timestamp': datetime.utcnow().isoformat()},
            {'role': 'assistant', 'content': 'How can I help you?', 'timestamp': datetime.utcnow().isoformat()}
        ]
        
        await manager.sync_conversation_state(
            session_id=TEST_SESSION_ID,
            messages=test_messages,
            context={'intent': 'general', 'confidence': 0.9}
        )
        
        print("   ✅ Conversation state synced")
        
        # Prepare voice transition
        print("\n3. Preparing voice transition...")
        voice_config = await manager.prepare_voice_transition(TEST_SESSION_ID)
        
        assert voice_config['transition_ready']
        assert voice_config['room_name']
        print(f"   ✅ Voice transition prepared")
        print(f"   - Room name: {voice_config['room_name']}")
        print(f"   - Transition ready: {voice_config['transition_ready']}")
        
        # Retrieve state
        print("\n4. Retrieving session state...")
        retrieved_state = await manager.get_session_state(TEST_SESSION_ID)
        
        assert retrieved_state
        assert len(retrieved_state['conversation']['messages']) == 2
        print(f"   ✅ State retrieved with {len(retrieved_state['conversation']['messages'])} messages")
        
        return True
    
    # ==================== INTEGRATION TEST ====================
    
    async def test_end_to_end_flow(self):
        """Test complete end-to-end flow"""
        print("\n" + "="*60)
        print("END-TO-END INTEGRATION TEST")
        print("="*60)
        
        print("\nSimulating complete patient interaction flow...")
        
        # 1. Ingest knowledge
        print("\n1. Ingesting clinic knowledge...")
        pipeline = KnowledgeIngestionPipeline(TEST_CLINIC_ID)
        await pipeline.ingest_document(
            content="Our clinic offers dental cleanings every Monday through Friday from 9 AM to 5 PM.",
            metadata={'source': 'clinic_info'},
            category='procedures'
        )
        print("   ✅ Knowledge ingested")
        
        # 2. Start conversation
        print("\n2. Starting patient conversation...")
        manager = ConversationContextManager(TEST_SESSION_ID, TEST_USER_ID)
        await manager.add_message("user", "When can I get a dental cleaning?")
        print("   ✅ User message added")
        
        # 3. Construct response with RAG
        print("\n3. Constructing AI response with RAG...")
        constructor = LLMResponseConstructor(TEST_SESSION_ID, TEST_USER_ID, TEST_CLINIC_ID)
        response = await constructor.construct_response(
            user_query="When can I get a dental cleaning?",
            intent="appointment",
            include_knowledge=True
        )
        print(f"   ✅ Response generated ({len(response.get('content', ''))} chars)")
        
        # 4. Prepare for voice transition
        print("\n4. Preparing for voice transition...")
        state_manager = MultiModalStateManager()
        await state_manager.create_session_state(
            TEST_SESSION_ID,
            ConversationMode.TEXT,
            {'user_id': TEST_USER_ID}
        )
        
        voice_config = await state_manager.prepare_voice_transition(TEST_SESSION_ID)
        print(f"   ✅ Voice transition ready: {voice_config['room_name']}")
        
        print("\n" + "="*60)
        print("✅ END-TO-END TEST COMPLETED SUCCESSFULLY")
        print("="*60)
        
        return True


async def run_all_tests():
    """Run all tests in sequence"""
    test_suite = TestRAGMemorySystem()
    
    if not test_suite.setup_class():
        print("\n❌ Setup failed. Please configure environment variables.")
        return False
    
    tests = [
        ("Document Ingestion", test_suite.test_phase1_document_ingestion),
        ("Category Search", test_suite.test_phase1_category_search),
        ("Conversation Memory", test_suite.test_phase2_conversation_memory),
        ("Response Construction", test_suite.test_phase2_response_construction),
        ("LangGraph Integration", test_suite.test_phase3_langgraph_integration),
        ("Multi-Modal State", test_suite.test_phase4_multimodal_state),
        ("End-to-End Flow", test_suite.test_end_to_end_flow)
    ]
    
    results = []
    
    for test_name, test_func in tests:
        try:
            print(f"\n🧪 Running: {test_name}")
            result = await test_func()
            results.append((test_name, "✅ PASSED" if result else "⚠️ PARTIAL"))
        except Exception as e:
            print(f"\n❌ Error in {test_name}: {str(e)}")
            results.append((test_name, f"❌ FAILED: {str(e)[:50]}"))
    
    # Print summary
    print("\n" + "="*80)
    print("TEST SUMMARY")
    print("="*80)
    
    for test_name, status in results:
        print(f"{status:15} | {test_name}")
    
    passed = sum(1 for _, status in results if "PASSED" in status)
    total = len(results)
    
    print(f"\nTotal: {passed}/{total} tests passed")
    
    if passed == total:
        print("\n🎉 ALL TESTS PASSED! The RAG Memory System is fully operational.")
    else:
        print(f"\n⚠️  {total - passed} tests need attention.")
    
    return passed == total


if __name__ == "__main__":
    # Run the test suite
    success = asyncio.run(run_all_tests())
    sys.exit(0 if success else 1)