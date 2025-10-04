#!/usr/bin/env python3
"""
Test RAG Memory System with real services using environment variables from clinics/.env
"""

import asyncio
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables from clinics/.env
env_path = Path(__file__).parent.parent / '.env'
if env_path.exists():
    print(f"Loading environment from: {env_path}")
    load_dotenv(env_path, override=False)  # Don't override existing vars
    
    # Manually set the correct Pinecone API key (first occurrence)
    with open(env_path, 'r') as f:
        for line in f:
            if line.startswith('PINECONE_API_KEY=') and 'pcsk_' in line:
                key = line.split('=', 1)[1].strip()
                if key and key != '':
                    os.environ['PINECONE_API_KEY'] = key
                    print(f"Loaded PINECONE_API_KEY: {key[:20]}...")
                    break
else:
    print(f"Warning: .env file not found at {env_path}")

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent))

# Now import our test modules
from tests.test_rag_memory_system import TestRAGMemorySystem


async def run_real_tests():
    """Run tests with real services"""
    print("\n" + "="*80)
    print("RAG MEMORY SYSTEM - TESTING WITH REAL SERVICES")
    print("="*80)
    
    # Check if we have the required environment variables
    required_vars = {
        'PINECONE_API_KEY': os.environ.get('PINECONE_API_KEY', '')[:20] + '...' if os.environ.get('PINECONE_API_KEY') else 'NOT SET',
        'OPENAI_API_KEY': os.environ.get('OPENAI_API_KEY', '')[:20] + '...' if os.environ.get('OPENAI_API_KEY') else 'NOT SET',
        'REDIS_URL': os.environ.get('REDIS_URL', 'redis://localhost:6379')
    }
    
    print("\nEnvironment Variables:")
    for var, value in required_vars.items():
        print(f"  {var}: {value}")
    
    # Set REDIS_HOST from REDIS_URL if not already set
    if not os.environ.get('REDIS_HOST') and os.environ.get('REDIS_URL'):
        redis_url = os.environ.get('REDIS_URL', 'redis://localhost:6379')
        # Parse redis://localhost:6379 to get host
        if redis_url.startswith('redis://'):
            host = redis_url.replace('redis://', '').split(':')[0]
            os.environ['REDIS_HOST'] = host
            print(f"  REDIS_HOST: {host} (extracted from REDIS_URL)")
    
    # Initialize test suite
    test_suite = TestRAGMemorySystem()
    
    # Check setup
    if not test_suite.setup_class():
        print("\n❌ Setup failed. Check environment variables above.")
        return False
    
    print("\n✅ Environment configured successfully!")
    
    # Run selected tests
    tests_to_run = [
        ("Phase 1: Document Ingestion", test_suite.test_phase1_document_ingestion),
        ("Phase 1: Category Search", test_suite.test_phase1_category_search),
        ("Phase 2: Conversation Memory", test_suite.test_phase2_conversation_memory),
        ("Phase 2: Response Construction", test_suite.test_phase2_response_construction),
        ("Phase 4: Multi-Modal State", test_suite.test_phase4_multimodal_state),
        ("End-to-End Integration", test_suite.test_end_to_end_flow)
    ]
    
    results = []
    
    for test_name, test_func in tests_to_run:
        try:
            print(f"\n🧪 Running: {test_name}")
            print("-" * 60)
            result = await test_func()
            results.append((test_name, "✅ PASSED" if result else "⚠️ PARTIAL"))
        except Exception as e:
            print(f"\n❌ Error in {test_name}: {str(e)}")
            import traceback
            traceback.print_exc()
            results.append((test_name, f"❌ FAILED: {str(e)[:100]}"))
    
    # Print summary
    print("\n" + "="*80)
    print("REAL SERVICES TEST SUMMARY")
    print("="*80)
    
    for test_name, status in results:
        print(f"{status:15} | {test_name}")
    
    passed = sum(1 for _, status in results if "PASSED" in status)
    total = len(results)
    
    print(f"\nTotal: {passed}/{total} tests passed")
    
    if passed == total:
        print("\n🎉 ALL TESTS PASSED WITH REAL SERVICES!")
        print("\nThe RAG Memory System is fully operational with:")
        print("  ✅ Pinecone vector database")
        print("  ✅ OpenAI embeddings and LLM")
        print("  ✅ Redis session management")
        print("  ✅ mem0 conversation memory")
    else:
        failed = total - passed
        print(f"\n⚠️  {failed} test(s) need attention.")
        print("\nTroubleshooting:")
        print("  1. Check if Redis is running: redis-cli ping")
        print("  2. Verify Pinecone API key is valid")
        print("  3. Check OpenAI API key has sufficient credits")
        print("  4. Review error messages above")
    
    return passed == total


if __name__ == "__main__":
    success = asyncio.run(run_real_tests())
    sys.exit(0 if success else 1)