"""
Voice services for unified LangGraph processing.

This module provides adapters for integrating LangGraph with voice services
like LiveKit Agents, enabling unified voice + text on the same graph.

Usage:
    # For direct streaming (SSE, WebSocket, etc.)
    from app.services.voice import LangGraphVoiceAdapter
    adapter = LangGraphVoiceAdapter(clinic_id="clinic123")
    async for chunk in adapter.stream_response(message, session_id, metadata):
        yield chunk

    # For LiveKit integration (when livekit-agents is installed)
    from app.services.voice import LangGraphLLM
    llm = LangGraphLLM(clinic_id="clinic123")
"""

from .langgraph_adapter import LangGraphVoiceAdapter, StreamingResponse, create_voice_adapter

# LiveKit LLM is optional - only available when livekit-agents is installed
try:
    from .livekit_llm import LangGraphLLM, LangGraphLLMStream, LIVEKIT_AVAILABLE
except ImportError:
    LangGraphLLM = None
    LangGraphLLMStream = None
    LIVEKIT_AVAILABLE = False

__all__ = [
    "LangGraphVoiceAdapter",
    "StreamingResponse",
    "create_voice_adapter",
    "LangGraphLLM",
    "LangGraphLLMStream",
    "LIVEKIT_AVAILABLE",
]
