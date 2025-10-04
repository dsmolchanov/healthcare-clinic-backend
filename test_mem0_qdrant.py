#!/usr/bin/env python3
"""
Comprehensive test script for mem0 with Qdrant (local mode) + OpenAI
"""
import os
import sys
import asyncio
from datetime import datetime

# Load .env file
try:
    from dotenv import load_dotenv
    load_dotenv()
    print("✓ Loaded .env file")
except ImportError:
    print("⚠ python-dotenv not installed, using system env vars only")

# Test 1: Environment variables
print("=" * 80)
print("TEST 1: Environment Variables")
print("=" * 80)

openai_key = os.environ.get('OPENAI_API_KEY')
print(f"✓ OPENAI_API_KEY: {'SET' if openai_key else 'MISSING'}")
if openai_key:
    print(f"  Length: {len(openai_key)} chars")
    print(f"  Starts with: {openai_key[:10]}...")

# Test 2: Import mem0
print("\n" + "=" * 80)
print("TEST 2: Import mem0")
print("=" * 80)

try:
    from mem0 import Memory
    print("✓ mem0 imported successfully")
except ImportError as e:
    print(f"✗ Failed to import mem0: {e}")
    sys.exit(1)

# Test 3: Qdrant client
print("\n" + "=" * 80)
print("TEST 3: Qdrant Client")
print("=" * 80)

try:
    from qdrant_client import QdrantClient
    print("✓ qdrant-client imported successfully")
except ImportError as e:
    print(f"✗ Failed to import qdrant-client: {e}")
    sys.exit(1)

# Test 4: Create mem0 config
print("\n" + "=" * 80)
print("TEST 4: Create mem0 Configuration")
print("=" * 80)

config = {
    "vector_store": {
        "provider": "qdrant",
        "config": {
            "collection_name": "test_memories",
            "path": "./test_qdrant_data",
            "embedding_model_dims": 1536
        }
    },
    "llm": {
        "provider": "openai",
        "config": {
            "model": "gpt-4o-mini",
            "temperature": 0.2
        }
    },
    "embedder": {
        "provider": "openai",
        "config": {
            "model": "text-embedding-3-small"
        }
    },
    "version": "v1.1"
}

print("✓ Configuration created:")
print(f"  Vector Store: {config['vector_store']['provider']}")
print(f"  Collection: {config['vector_store']['config']['collection_name']}")
print(f"  Storage Path: {config['vector_store']['config']['path']}")
print(f"  LLM: {config['llm']['config']['model']}")
print(f"  Embedder: {config['embedder']['config']['model']}")

# Test 5: Initialize mem0
print("\n" + "=" * 80)
print("TEST 5: Initialize mem0 with Qdrant")
print("=" * 80)

try:
    print("Initializing mem0...")
    memory = Memory.from_config(config)
    print("✓ mem0 initialized successfully!")
    print(f"  Memory object: {memory}")
    print(f"  Type: {type(memory)}")
except Exception as e:
    print(f"✗ Failed to initialize mem0: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# Test 6: Add memories
print("\n" + "=" * 80)
print("TEST 6: Add Memories")
print("=" * 80)

test_memories = [
    {
        "user_id": "test_user_001",
        "content": "User prefers morning appointments between 9-11 AM",
        "metadata": {"type": "preference", "category": "scheduling"}
    },
    {
        "user_id": "test_user_001",
        "content": "Patient Dan Kopylivich - requested dental cleaning",
        "metadata": {"type": "request", "category": "service"}
    },
    {
        "user_id": "test_user_002",
        "content": "User allergic to penicillin",
        "metadata": {"type": "medical", "category": "allergy"}
    }
]

print(f"Adding {len(test_memories)} test memories...")
added_memories = []

for idx, mem_data in enumerate(test_memories, 1):
    try:
        result = memory.add(
            mem_data["content"],
            user_id=mem_data["user_id"],
            metadata=mem_data["metadata"]
        )
        print(f"✓ Memory {idx} added: {mem_data['content'][:50]}...")
        print(f"  Result: {result}")
        added_memories.append(result)
    except Exception as e:
        print(f"✗ Failed to add memory {idx}: {e}")
        import traceback
        traceback.print_exc()

# Test 7: Retrieve all memories for a user
print("\n" + "=" * 80)
print("TEST 7: Retrieve All Memories")
print("=" * 80)

try:
    print("Retrieving memories for test_user_001...")
    memories = memory.get_all(user_id="test_user_001", limit=10)
    print(f"✓ Retrieved {len(memories)} memories")
    
    for idx, mem in enumerate(memories, 1):
        if isinstance(mem, dict):
            print(f"\n  Memory {idx}:")
            print(f"    ID: {mem.get('id', 'N/A')}")
            print(f"    Content: {mem.get('memory', mem.get('text', 'N/A'))}")
            print(f"    User: {mem.get('user_id', 'N/A')}")
        else:
            print(f"  Memory {idx}: {mem}")
            
except Exception as e:
    print(f"✗ Failed to retrieve memories: {e}")
    import traceback
    traceback.print_exc()

# Test 8: Search memories
print("\n" + "=" * 80)
print("TEST 8: Search Memories (Semantic)")
print("=" * 80)

search_queries = [
    ("test_user_001", "What time does the patient prefer?"),
    ("test_user_001", "Who is Dan?"),
    ("test_user_002", "Any medical conditions?")
]

for user_id, query in search_queries:
    try:
        print(f"\nSearching for '{query}' (user: {user_id})...")
        results = memory.search(query, user_id=user_id, limit=3)
        print(f"✓ Found {len(results)} results:")
        
        for idx, result in enumerate(results, 1):
            if isinstance(result, dict):
                score = result.get('score', 0)
                content = result.get('memory', result.get('text', 'N/A'))
                print(f"  {idx}. [Score: {score:.3f}] {content[:60]}...")
            else:
                print(f"  {idx}. {result}")
                
    except Exception as e:
        print(f"✗ Search failed: {e}")
        import traceback
        traceback.print_exc()

# Test 9: Check storage on disk
print("\n" + "=" * 80)
print("TEST 9: Verify Qdrant Data on Disk")
print("=" * 80)

import os
storage_path = config['vector_store']['config']['path']

if os.path.exists(storage_path):
    print(f"✓ Storage path exists: {storage_path}")
    
    # List contents
    try:
        for root, dirs, files in os.walk(storage_path):
            level = root.replace(storage_path, '').count(os.sep)
            indent = ' ' * 2 * level
            print(f'{indent}{os.path.basename(root)}/')
            subindent = ' ' * 2 * (level + 1)
            for file in files[:10]:  # Limit to first 10 files per dir
                print(f'{subindent}{file}')
            if len(files) > 10:
                print(f'{subindent}... and {len(files) - 10} more files')
    except Exception as e:
        print(f"  Warning: Could not list directory: {e}")
else:
    print(f"✗ Storage path does not exist: {storage_path}")

# Test 10: Async context retrieval (like in production)
print("\n" + "=" * 80)
print("TEST 10: Async Context Retrieval (Production Simulation)")
print("=" * 80)

async def test_async_retrieval():
    """Simulate production async retrieval"""
    try:
        # Import the actual production function
        from app.memory.conversation_memory import get_memory_manager
        
        print("Getting memory manager...")
        manager = get_memory_manager()
        
        print(f"Memory manager initialized: {manager.mem0_available}")
        
        if manager.mem0_available:
            print("✓ mem0 is available in manager")
            
            # Test retrieval
            phone_number = "test_user_001"
            query = "What are the patient preferences?"
            
            print(f"\nRetrieving context for: {phone_number}")
            print(f"Query: {query}")
            
            memories = await manager.get_memory_context(
                phone_number=phone_number,
                query=query,
                limit=3
            )
            
            print(f"✓ Retrieved {len(memories)} memory items:")
            for idx, mem in enumerate(memories, 1):
                print(f"  {idx}. {mem[:80]}...")
        else:
            print("✗ mem0 is NOT available in manager")
            print("  This indicates initialization failed in conversation_memory.py")
            
    except Exception as e:
        print(f"✗ Async test failed: {e}")
        import traceback
        traceback.print_exc()

try:
    asyncio.run(test_async_retrieval())
except Exception as e:
    print(f"✗ Could not run async test: {e}")

# Summary
print("\n" + "=" * 80)
print("TEST SUMMARY")
print("=" * 80)
print(f"Timestamp: {datetime.now().isoformat()}")
print(f"mem0 Version: {Memory.__module__ if hasattr(Memory, '__module__') else 'unknown'}")
print(f"Storage Path: {storage_path}")
print(f"Collection: {config['vector_store']['config']['collection_name']}")
print("\n✅ All tests completed!")
print("\nNext steps:")
print("  1. Review any errors above")
print("  2. If all passed, deploy to production")
print("  3. Test with actual WhatsApp messages")
