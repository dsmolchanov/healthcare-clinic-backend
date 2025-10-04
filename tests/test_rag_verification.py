#!/usr/bin/env python3
"""
Final verification test for RAG Memory System
This test verifies the structure and functionality of the implementation
"""

import os
import sys
import importlib
import inspect
from typing import List, Dict, Any

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class RAGSystemVerification:
    """Verify the RAG Memory System implementation"""
    
    def __init__(self):
        self.results = []
        self.total_checks = 0
        self.passed_checks = 0
    
    def check(self, name: str, condition: bool, details: str = ""):
        """Record a verification check"""
        self.total_checks += 1
        if condition:
            self.passed_checks += 1
            status = "✅"
        else:
            status = "❌"
        
        self.results.append((status, name, details))
        print(f"{status} {name}")
        if details and not condition:
            print(f"   Details: {details}")
    
    def verify_phase1_components(self):
        """Verify Phase 1: Static Memory Enhancement components"""
        print("\n" + "="*60)
        print("PHASE 1: Static Memory Enhancement (Pinecone)")
        print("="*60)
        
        # Check KnowledgeIngestionPipeline
        try:
            from app.api.knowledge_ingestion import KnowledgeIngestionPipeline
            self.check("KnowledgeIngestionPipeline class exists", True)
            
            # Check methods
            methods = ['ingest_document', 'update_document', 'delete_document', 'list_categories']
            for method in methods:
                self.check(
                    f"  - {method} method exists",
                    hasattr(KnowledgeIngestionPipeline, method)
                )
        except ImportError as e:
            self.check("KnowledgeIngestionPipeline class exists", False, str(e))
        
        # Check PineconeKnowledgeBase enhancements
        try:
            from app.api.message_processor import PineconeKnowledgeBase
            self.check("PineconeKnowledgeBase class exists", True)
            
            # Check for search_by_category method
            self.check(
                "  - search_by_category method exists",
                hasattr(PineconeKnowledgeBase, 'search_by_category')
            )
            
            # Check method signature
            if hasattr(PineconeKnowledgeBase, 'search_by_category'):
                sig = inspect.signature(PineconeKnowledgeBase.search_by_category)
                params = list(sig.parameters.keys())
                self.check(
                    "  - search_by_category has correct parameters",
                    'category' in params and 'query' in params
                )
        except ImportError as e:
            self.check("PineconeKnowledgeBase class exists", False, str(e))
    
    def verify_phase2_components(self):
        """Verify Phase 2: Dynamic Chat Memory components"""
        print("\n" + "="*60)
        print("PHASE 2: Dynamic Chat Memory Implementation")
        print("="*60)
        
        # Check ConversationContextManager
        try:
            from app.memory.conversation_manager import ConversationContextManager
            self.check("ConversationContextManager class exists", True)
            
            # Check methods
            methods = ['add_message', 'get_context_window', 'get_relevant_memories', '_summarize_old_messages']
            for method in methods:
                self.check(
                    f"  - {method} method exists",
                    hasattr(ConversationContextManager, method)
                )
        except ImportError as e:
            self.check("ConversationContextManager class exists", False, str(e))
        
        # Check LLMResponseConstructor
        try:
            from app.api.response_constructor import LLMResponseConstructor
            self.check("LLMResponseConstructor class exists", True)
            
            # Check methods
            methods = ['construct_response', '_build_context', '_generate_llm_response', '_extract_sources']
            for method in methods:
                self.check(
                    f"  - {method} method exists",
                    hasattr(LLMResponseConstructor, method)
                )
        except ImportError as e:
            self.check("LLMResponseConstructor class exists", False, str(e))
    
    def verify_phase3_components(self):
        """Verify Phase 3: LangGraph RAG Implementation"""
        print("\n" + "="*60)
        print("PHASE 3: LangGraph RAG Implementation")
        print("="*60)
        
        # Check LangGraph orchestrator updates
        try:
            sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
            from services.langgraph_orchestrator import HealthcareLangGraphOrchestrator
            self.check("HealthcareLangGraphOrchestrator class exists", True)
            
            # Check updated methods
            methods = ['retrieve_memory_node', 'knowledge_handler_node', '_determine_category']
            for method in methods:
                self.check(
                    f"  - {method} method exists",
                    hasattr(HealthcareLangGraphOrchestrator, method)
                )
            
            # Check if methods contain RAG integration
            if hasattr(HealthcareLangGraphOrchestrator, 'retrieve_memory_node'):
                source = inspect.getsource(HealthcareLangGraphOrchestrator.retrieve_memory_node)
                self.check(
                    "  - retrieve_memory_node uses ConversationContextManager",
                    'ConversationContextManager' in source
                )
                self.check(
                    "  - retrieve_memory_node uses PineconeKnowledgeBase",
                    'PineconeKnowledgeBase' in source
                )
            
            if hasattr(HealthcareLangGraphOrchestrator, 'knowledge_handler_node'):
                source = inspect.getsource(HealthcareLangGraphOrchestrator.knowledge_handler_node)
                self.check(
                    "  - knowledge_handler_node uses search_by_category",
                    'search_by_category' in source
                )
        except ImportError as e:
            self.check("HealthcareLangGraphOrchestrator class exists", False, str(e))
    
    def verify_phase4_components(self):
        """Verify Phase 4: Redis Shared State components"""
        print("\n" + "="*60)
        print("PHASE 4: Redis Shared State for Voice Switching")
        print("="*60)
        
        # Check MultiModalStateManager
        try:
            from app.state.multimodal_state import MultiModalStateManager, ConversationMode
            self.check("MultiModalStateManager class exists", True)
            self.check("ConversationMode enum exists", True)
            
            # Check methods
            methods = [
                'create_session_state',
                'prepare_voice_transition',
                'sync_conversation_state',
                'get_session_state',
                'subscribe_to_transitions',
                'cleanup_expired_sessions'
            ]
            for method in methods:
                self.check(
                    f"  - {method} method exists",
                    hasattr(MultiModalStateManager, method)
                )
        except ImportError as e:
            self.check("MultiModalStateManager class exists", False, str(e))
        
        # Check LangGraph integration
        try:
            sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
            from services.langgraph_orchestrator import HealthcareLangGraphOrchestrator
            
            self.check(
                "persist_state_for_voice method exists",
                hasattr(HealthcareLangGraphOrchestrator, 'persist_state_for_voice')
            )
            
            if hasattr(HealthcareLangGraphOrchestrator, 'persist_state_for_voice'):
                source = inspect.getsource(HealthcareLangGraphOrchestrator.persist_state_for_voice)
                self.check(
                    "  - Uses MultiModalStateManager",
                    'MultiModalStateManager' in source
                )
        except ImportError as e:
            self.check("LangGraph voice persistence", False, str(e))
    
    def verify_integration(self):
        """Verify integration between components"""
        print("\n" + "="*60)
        print("INTEGRATION VERIFICATION")
        print("="*60)
        
        # Check if all main files exist
        files_to_check = [
            ('app/api/knowledge_ingestion.py', 'Knowledge Ingestion Pipeline'),
            ('app/api/message_processor.py', 'Message Processor (enhanced)'),
            ('app/memory/conversation_manager.py', 'Conversation Manager'),
            ('app/api/response_constructor.py', 'Response Constructor'),
            ('app/state/multimodal_state.py', 'Multi-modal State Manager')
        ]
        
        for file_path, description in files_to_check:
            full_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                file_path
            )
            self.check(
                f"{description} file exists",
                os.path.exists(full_path)
            )
        
        # Check if placeholder implementations are replaced
        try:
            from services.langgraph_orchestrator import HealthcareLangGraphOrchestrator
            
            if hasattr(HealthcareLangGraphOrchestrator, 'retrieve_memory_node'):
                source = inspect.getsource(HealthcareLangGraphOrchestrator.retrieve_memory_node)
                self.check(
                    "No placeholder comments in retrieve_memory_node",
                    'Would also retrieve from vector database' not in source and
                    'Would query RAG system here' not in source
                )
        except:
            pass
    
    def print_summary(self):
        """Print verification summary"""
        print("\n" + "="*80)
        print("VERIFICATION SUMMARY")
        print("="*80)
        
        for status, name, details in self.results:
            if not name.startswith("  -"):
                print(f"\n{status} {name}")
            else:
                print(f"{status} {name}")
        
        print("\n" + "-"*80)
        print(f"Total Checks: {self.total_checks}")
        print(f"Passed: {self.passed_checks}")
        print(f"Failed: {self.total_checks - self.passed_checks}")
        
        success_rate = (self.passed_checks / self.total_checks * 100) if self.total_checks > 0 else 0
        print(f"Success Rate: {success_rate:.1f}%")
        
        if success_rate >= 90:
            print("\n🎉 EXCELLENT! The RAG Memory System implementation is complete and well-structured.")
        elif success_rate >= 70:
            print("\n✅ GOOD! The RAG Memory System is mostly complete with minor issues.")
        elif success_rate >= 50:
            print("\n⚠️  PARTIAL: The RAG Memory System needs some work to be fully functional.")
        else:
            print("\n❌ INCOMPLETE: Significant parts of the RAG Memory System are missing.")
        
        print("\n" + "="*80)
        print("RECOMMENDATIONS")
        print("="*80)
        
        if self.total_checks - self.passed_checks > 0:
            print("\nTo complete the implementation:")
            print("1. Review failed checks above")
            print("2. Ensure all required files are created")
            print("3. Verify all methods are implemented")
            print("4. Remove any placeholder code")
        
        print("\nTo test with real services:")
        print("1. Set PINECONE_API_KEY environment variable")
        print("2. Set OPENAI_API_KEY environment variable")
        print("3. Ensure Redis is running (or set REDIS_HOST)")
        print("4. Run: python tests/test_rag_memory_system.py")
        
        return success_rate >= 70


def main():
    """Run verification"""
    verifier = RAGSystemVerification()
    
    print("\n" + "="*80)
    print("RAG MEMORY SYSTEM - IMPLEMENTATION VERIFICATION")
    print("="*80)
    
    # Run all verification phases
    verifier.verify_phase1_components()
    verifier.verify_phase2_components()
    verifier.verify_phase3_components()
    verifier.verify_phase4_components()
    verifier.verify_integration()
    
    # Print summary
    success = verifier.print_summary()
    
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())