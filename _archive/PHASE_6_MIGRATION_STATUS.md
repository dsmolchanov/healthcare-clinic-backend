# Phase 6: Direct Call Migration - Status Report

**Date**: 2025-10-02
**Status**: Partially Complete (3 of 19 files migrated)

## Overview

Phase 6 involves migrating 40+ direct `OpenAI()` instantiations to use the unified LLM factory pattern. This ensures all LLM calls go through the factory for consistent routing, metrics, and multi-provider support.

## Files Identified for Migration

Total files with direct OpenAI imports: **19 files**

### Priority 1: Core Message Processing (COMPLETED ✅)
1. ✅ `app/apps/voice-api/response_constructor.py` - LLM factory injected via constructor
2. ✅ `app/apps/voice-api/whatsapp_webhook.py` - Using factory with lazy initialization
3. ✅ `app/apps/voice-api/multilingual_message_processor.py` - Factory getter added (embeddings deferred)

### Priority 2: Supporting Services (PENDING)
4. ⏳ `app/services/followup_scheduler.py` - Needs factory integration
5. ⏳ `app/services/openai_multimodal_parser.py` - Needs factory integration
6. ⏳ `app/memory/conversation_manager.py` - Needs factory integration
7. ⏳ `app/twilio_handler.py` - Needs factory integration

### Priority 3: Knowledge Base & RAG (DEFERRED - Embeddings)
These files use OpenAI primarily for embeddings, not chat completions:
- `app/apps/voice-api/knowledge_ingestion_fixed.py`
- `app/apps/voice-api/rag_cache.py`
- `app/apps/voice-api/knowledge_ingestion.py`
- `app/apps/voice-api/enhanced_knowledge_ingestion.py`
- `app/apps/voice-api/structured_data_embedder.py`
- `app/apps/voice-api/improved_knowledge_base.py`

**Note**: Embeddings migration will be addressed in a future phase when we add embedding support to the factory.

### Priority 4: Alternative Processors (LOW PRIORITY)
- `app/apps/voice-api/simple_message_processor.py`
- `app/apps/voice-api/message_processor.py`
- `app/apps/voice-api/whatsapp_webhook_simple.py`
- `app/main.py` (has fallback OpenAI usage)

## Changes Made

### 1. response_constructor.py
```python
# BEFORE:
def __init__(self, session_id: str, user_id: str, clinic_id: str):
    self.openai_client = OpenAI()

# AFTER:
def __init__(self, session_id: str, user_id: str, clinic_id: str, llm_factory=None):
    self.llm_factory = llm_factory  # Inject factory
```

### 2. whatsapp_webhook.py
```python
# BEFORE:
openai_client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
response = await openai_client.chat.completions.create(...)

# AFTER:
async def get_llm_factory():
    global _llm_factory
    if _llm_factory is None:
        from app.services.llm.llm_factory import LLMFactory
        from app.db import get_supabase_client
        _llm_factory = LLMFactory(get_supabase_client())
    return _llm_factory

factory = await get_llm_factory()
response = await factory.generate(messages=[...])
```

### 3. multilingual_message_processor.py
```python
# BEFORE:
def get_openai_client() -> OpenAI:
    global _openai_client
    if _openai_client is None:
        _openai_client = OpenAI(api_key=api_key)
    return _openai_client

# AFTER:
async def get_llm_factory():
    from app.services.llm.llm_factory import LLMFactory
    global _llm_factory
    if _llm_factory is None:
        supabase = get_supabase_client()
        _llm_factory = LLMFactory(supabase)
    return _llm_factory
```

## Benefits of Migration

✅ **Centralized LLM Management**: All LLM calls go through one factory
✅ **Multi-Provider Support**: Can route to GLM-4.6, Gemini, or OpenAI
✅ **Cost Optimization**: Automatic routing to cheapest suitable model
✅ **Metrics Tracking**: Unified tracking of tokens, latency, and costs
✅ **Fallback Support**: Automatic fallback to secondary providers
✅ **Tool Calling**: Consistent tool calling across providers

## Remaining Work

### Immediate (Phase 6 completion):
1. Migrate `followup_scheduler.py` to use factory
2. Migrate `openai_multimodal_parser.py` to use factory
3. Migrate `conversation_manager.py` to use factory
4. Update all instantiation sites to pass factory

### Future (Phase 7 - Embeddings):
1. Add embeddings support to LLM factory
2. Migrate all knowledge base files to use factory for embeddings
3. Support multi-provider embeddings (OpenAI, Cohere, etc.)

### Testing Required:
- Integration tests for migrated files
- End-to-end WhatsApp message flow test
- Performance testing with GLM-4.6 vs OpenAI
- Cost comparison metrics

## Migration Pattern

For any remaining files, follow this pattern:

```python
# 1. Remove direct import
# OLD: from openai import OpenAI or AsyncOpenAI

# 2. Add factory getter (module level)
_llm_factory = None

async def get_llm_factory():
    from app.services.llm.llm_factory import LLMFactory
    from app.db import get_supabase_client

    global _llm_factory
    if _llm_factory is None:
        _llm_factory = LLMFactory(get_supabase_client())
    return _llm_factory

# 3. Update usage
# OLD:
client = OpenAI()
response = client.chat.completions.create(model="gpt-4", messages=[...])

# NEW:
factory = await get_llm_factory()
response = await factory.generate(messages=[...], temperature=0.7)
content = response.content
```

## Next Steps

1. Complete Priority 2 migrations (4 files)
2. Run comprehensive integration tests
3. Deploy to production with monitoring
4. Monitor metrics for cost savings
5. Plan Phase 7 for embeddings support

## Notes

- Phase 5 (LangGraph Integration) is **100% complete** ✅
- Phase 6 is **~16% complete** (3 of 19 files)
- Focus on high-traffic paths first (WhatsApp webhook ✅, LangGraph orchestrator ✅)
- Embeddings files can wait for dedicated embeddings factory support
