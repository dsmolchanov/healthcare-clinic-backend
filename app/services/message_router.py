"""
Message Router for Dual-Lane Architecture
Routes text messages directly to LangGraph, voice through LiveKit
Achieves <500ms text response times by bypassing LiveKit for text
"""

import logging
import time
from enum import Enum
from typing import Dict, Any, Optional, Tuple, TypeVar, Callable
import aiohttp
import asyncio
from datetime import datetime

# Phase 8: Import fast-path intent router
from app.services.intent_router import IntentRouter, Intent

# Phase 9: Import direct function-calling lane
from app.services.direct_lane.tool_intent_classifier import ToolIntentClassifier
from app.services.direct_lane.direct_tool_executor import DirectToolExecutor
from app.database import get_supabase

logger = logging.getLogger(__name__)

T = TypeVar('T')


async def with_budget(coro: Callable[[], T], budget_ms: int, fallback: T) -> T:
    """
    Execute coroutine with time budget, return fallback if exceeded.

    Args:
        coro: Async function to execute
        budget_ms: Time budget in milliseconds
        fallback: Value to return if timeout

    Returns:
        Result from coro or fallback
    """
    try:
        return await asyncio.wait_for(coro, timeout=budget_ms / 1000.0)
    except asyncio.TimeoutError:
        logger.warning(f"Stage exceeded budget of {budget_ms}ms, using fallback")
        return fallback


class MessageType(Enum):
    """Types of messages to route"""
    TEXT = "text"
    VOICE = "voice"
    VOICE_NOTE = "voice_note"
    IMAGE = "image"


class MessageSource(Enum):
    """Sources of incoming messages"""
    WHATSAPP = "whatsapp"
    WEB = "web"
    SMS = "sms"
    LIVEKIT = "livekit"
    TELEGRAM = "telegram"
    API = "api"


class RoutingDecision(Enum):
    """Routing decisions for messages"""
    LANGGRAPH_DIRECT = "langgraph_direct"  # Direct to LangGraph (fast lane)
    LIVEKIT_VOICE = "livekit_voice"        # Through LiveKit for voice
    HYBRID = "hybrid"                       # Both paths (special cases)


class MessageRouter:
    """
    Dual-lane message router for optimized text/voice handling
    Routes text directly to LangGraph for <500ms responses
    """

    def __init__(
        self,
        langgraph_url: str = None,
        livekit_api_url: str = "http://localhost:8787",
        enable_metrics: bool = True
    ):
        """
        Initialize the message router

        Args:
            langgraph_url: URL for LangGraph service (None = use direct processing)
            livekit_api_url: URL for LiveKit API
            enable_metrics: Whether to track routing metrics
        """
        # Default to direct processing if not specified
        import os
        if langgraph_url is None:
            # Use environment variable or default to direct processing
            # "direct" triggers immediate fallback to in-process handling
            langgraph_url = os.getenv("LANGGRAPH_URL", "direct")

        self.langgraph_url = langgraph_url
        self.livekit_api_url = livekit_api_url
        self.enable_metrics = enable_metrics
        self.use_direct_processing = langgraph_url == "direct"  # Flag for direct mode

        # Phase 8: Initialize fast-path intent router
        self.intent_router = IntentRouter()
        logger.info("✅ Fast-path intent router initialized")

        # Phase 9: Initialize direct function-calling lane
        self.enable_direct_lane = os.getenv("ENABLE_DIRECT_LANE", "true").lower() == "true"  # Default ON
        self.direct_lane_confidence_threshold = float(os.getenv("DIRECT_LANE_CONFIDENCE_THRESHOLD", "0.8"))
        if self.enable_direct_lane:
            self.tool_intent_classifier = ToolIntentClassifier()
            logger.info("✅ Direct function-calling lane initialized (confidence threshold: {})".format(
                self.direct_lane_confidence_threshold
            ))
        else:
            self.tool_intent_classifier = None
            logger.info("⚠️ Direct function-calling lane disabled (set ENABLE_DIRECT_LANE=true to enable)")

        # Performance metrics
        self.metrics = {
            "text_latencies": [],
            "voice_latencies": [],
            "routing_decisions": {},
            "fast_path_hits": 0,  # Phase 8: Track fast-path usage
            "direct_lane_hits": 0,  # Phase 9: Track direct lane usage
            "direct_lane_fallbacks": 0,  # Phase 9: Track fallbacks to LangGraph
            "total_messages": 0
        }

    def determine_route(
        self,
        message_type: MessageType,
        source: MessageSource,
        context: Optional[Dict[str, Any]] = None
    ) -> RoutingDecision:
        """
        Determine the optimal route for a message

        Args:
            message_type: Type of message
            source: Source channel
            context: Optional context (active voice call, etc.)

        Returns:
            Routing decision
        """
        # Text messages from non-LiveKit sources go directly to LangGraph
        if message_type == MessageType.TEXT and source != MessageSource.LIVEKIT:
            return RoutingDecision.LANGGRAPH_DIRECT

        # Voice messages always go through LiveKit
        if message_type in [MessageType.VOICE, MessageType.VOICE_NOTE]:
            return RoutingDecision.LIVEKIT_VOICE

        # LiveKit-originated text stays in LiveKit (maintains context)
        if source == MessageSource.LIVEKIT:
            return RoutingDecision.LIVEKIT_VOICE

        # Check for active voice session
        if context and context.get("active_voice_session"):
            return RoutingDecision.HYBRID

        # Default text messages to fast lane
        return RoutingDecision.LANGGRAPH_DIRECT

    async def route_to_langgraph(
        self,
        session_id: str,
        message: str,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Tuple[Dict[str, Any], float]:
        """
        Route message directly to LangGraph for fast processing
        Falls back to direct processing if LangGraph is unavailable

        Args:
            session_id: Unique session identifier
            message: Text message to process
            metadata: Optional metadata

        Returns:
            Tuple of (response, latency_ms)
        """
        start_time = time.perf_counter()

        # If direct processing is enabled, skip HTTP call
        if self.use_direct_processing:
            logger.info("Using direct processing (LangGraph URL set to 'direct')")
            return await self._direct_process(session_id, message, metadata, start_time)

        try:
            async with aiohttp.ClientSession() as session:
                # Determine if we should use healthcare orchestrator
                use_healthcare = metadata.get("clinic_id") is not None

                payload = {
                    "session_id": session_id,
                    "text": message,
                    "metadata": metadata or {},
                    "use_healthcare": use_healthcare,
                    "enable_rag": True,  # Always enable RAG for knowledge
                    "enable_memory": True  # Enable memory for context
                }

                logger.debug(f"Routing to LangGraph: session={session_id}, healthcare={use_healthcare}")

                async with session.post(
                    f"{self.langgraph_url}/langgraph/process",  # Updated endpoint
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=2.0)  # 2 second timeout
                ) as response:
                    result = await response.json()

                    latency_ms = (time.perf_counter() - start_time) * 1000

                    if self.enable_metrics:
                        self.metrics["text_latencies"].append(latency_ms)

                    logger.info(f"LangGraph response in {latency_ms:.2f}ms")

                    return result, latency_ms

        except asyncio.TimeoutError:
            latency_ms = (time.perf_counter() - start_time) * 1000
            logger.error(f"LangGraph timeout after {latency_ms:.2f}ms - using direct processing fallback")
            # Fall back to direct processing
            return await self._direct_process(session_id, message, metadata, start_time)

        except Exception as e:
            latency_ms = (time.perf_counter() - start_time) * 1000
            logger.error(f"LangGraph error: {e} - using direct processing fallback")
            # Fall back to direct processing
            return await self._direct_process(session_id, message, metadata, start_time)

    async def _direct_process(
        self,
        session_id: str,
        message: str,
        metadata: Optional[Dict[str, Any]],
        start_time: float
    ) -> Tuple[Dict[str, Any], float]:
        """
        Direct message processing fallback when LangGraph is unavailable
        """
        try:
            from app.api.multilingual_message_processor import handle_process_message, MessageRequest

            # Build request
            request = MessageRequest(
                from_phone=metadata.get("from", "unknown"),
                to_phone=metadata.get("to", "clinic"),
                body=message,
                message_sid=session_id,
                clinic_id=metadata.get("clinic_id", "default"),
                clinic_name=metadata.get("clinic_name", "Clinic"),
                channel=metadata.get("channel", "whatsapp"),
                metadata=metadata or {}
            )

            # Process
            response = await handle_process_message(request)

            latency_ms = (time.perf_counter() - start_time) * 1000

            if self.enable_metrics:
                self.metrics["text_latencies"].append(latency_ms)

            logger.info(f"Direct processing response in {latency_ms:.2f}ms")

            return {
                "response": response.message,
                "session_id": response.session_id,
                "language": response.detected_language,
                "metadata": response.metadata
            }, latency_ms

        except Exception as e:
            latency_ms = (time.perf_counter() - start_time) * 1000
            logger.error(f"Direct processing error: {e}")
            return {
                "response": "Hello! I'm here to help you. What can I do for you today?",
                "error": str(e)
            }, latency_ms

    async def route_to_livekit(
        self,
        session_id: str,
        message: str,
        message_type: MessageType,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Tuple[Dict[str, Any], float]:
        """
        Route message through LiveKit for voice processing

        Args:
            session_id: Session ID
            message: Message content (text or audio URL)
            message_type: Type of message
            metadata: Optional metadata

        Returns:
            Tuple of (response, latency_ms)
        """
        start_time = time.perf_counter()

        try:
            async with aiohttp.ClientSession() as session:
                # Prepare LiveKit room request
                payload = {
                    "session_id": session_id,
                    "message_type": message_type.value,
                    "content": message,
                    "metadata": metadata or {}
                }

                logger.debug(f"Routing to LiveKit: session={session_id}, type={message_type.value}")

                async with session.post(
                    f"{self.livekit_api_url}/room/message",
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=10.0)  # Longer timeout for voice
                ) as response:
                    result = await response.json()

                    latency_ms = (time.perf_counter() - start_time) * 1000

                    if self.enable_metrics:
                        self.metrics["voice_latencies"].append(latency_ms)

                    logger.info(f"LiveKit response in {latency_ms:.2f}ms")

                    return result, latency_ms

        except Exception as e:
            latency_ms = (time.perf_counter() - start_time) * 1000
            logger.error(f"LiveKit error: {e}")
            return {
                "response": "Voice processing unavailable.",
                "error": str(e)
            }, latency_ms

    async def route_message(
        self,
        message: str,
        session_id: str,
        source: MessageSource,
        message_type: MessageType = MessageType.TEXT,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Main routing method - determines and executes optimal path

        Args:
            message: Message content
            session_id: Session identifier
            source: Source channel
            message_type: Type of message
            metadata: Optional metadata

        Returns:
            Processed response with routing metadata
        """
        # Track metrics
        if self.enable_metrics:
            self.metrics["total_messages"] += 1

        # Phase 8: FAST-PATH check for known intents (budget: 300-500ms)
        if message_type == MessageType.TEXT and isinstance(message, str):
            start_intent = time.time()
            intent = self.intent_router.detect_intent(message)

            if intent != Intent.UNKNOWN:
                logger.info(f"🚀 Fast-path activated for intent: {intent.value}")

                # Try fast-path handler
                fast_response = await self.intent_router.route_to_handler(
                    intent,
                    message={'body': message, 'from_phone': metadata.get('phone_number', 'unknown')},
                    context={'session_id': session_id, **(metadata or {})}
                )

                if fast_response:
                    intent_ms = (time.time() - start_intent) * 1000
                    logger.info(f"✅ Fast-path completed: {intent.value} in {intent_ms:.0f}ms")

                    if self.enable_metrics:
                        self.metrics["fast_path_hits"] += 1

                    return {
                        **fast_response,
                        "routing_path": "fast_path",
                        "intent": intent.value,
                        "latency_ms": intent_ms
                    }

        # Phase 9: DIRECT FUNCTION-CALLING LANE (budget: 700-800ms)
        if (self.enable_direct_lane and
            message_type == MessageType.TEXT and
            isinstance(message, str) and
            metadata.get("enable_direct_tools", True)):

            start_direct = time.time()

            # Classify intent with direct lane classifier
            tool_match = self.tool_intent_classifier.classify(message, context=metadata)

            # High confidence → execute directly (threshold: 0.8)
            if tool_match.confidence >= self.direct_lane_confidence_threshold:
                logger.info(
                    f"🔧 Direct lane activated: {tool_match.intent.value} "
                    f"(confidence: {tool_match.confidence:.2f})"
                )

                # Get supabase client
                try:
                    supabase_client = await get_supabase()
                    clinic_id = metadata.get("clinic_id")

                    if not clinic_id:
                        logger.warning("No clinic_id in metadata, falling back to LangGraph")
                    else:
                        # Execute tool directly
                        executor = DirectToolExecutor(
                            clinic_id=clinic_id,
                            supabase_client=supabase_client
                        )

                        result = await executor.execute_tool(tool_match, context=metadata)

                        if result.get("success"):
                            direct_ms = (time.time() - start_direct) * 1000
                            logger.info(
                                f"✅ Direct lane completed: {tool_match.intent.value} "
                                f"in {direct_ms:.0f}ms"
                            )

                            if self.enable_metrics:
                                self.metrics["direct_lane_hits"] += 1

                            return {
                                **result,
                                "routing_path": "direct_function_call",
                                "intent": tool_match.intent.value,
                                "confidence": tool_match.confidence,
                                "total_latency_ms": direct_ms
                            }

                        # If tool execution failed, fall through to LangGraph
                        logger.warning(
                            f"Direct tool failed, falling back to LangGraph: {result.get('error')}"
                        )
                        if self.enable_metrics:
                            self.metrics["direct_lane_fallbacks"] += 1

                except Exception as e:
                    logger.error(f"Direct lane error: {e}", exc_info=True)
                    if self.enable_metrics:
                        self.metrics["direct_lane_fallbacks"] += 1
                    # Fall through to LangGraph

        # Determine routing decision
        route = self.determine_route(message_type, source, metadata)

        # Track routing decision
        if self.enable_metrics:
            route_key = f"{source.value}_{message_type.value}"
            self.metrics["routing_decisions"][route_key] = \
                self.metrics["routing_decisions"].get(route_key, 0) + 1

        logger.info(f"Routing decision: {route.value} for {source.value}/{message_type.value}")

        # Execute routing
        if route == RoutingDecision.LANGGRAPH_DIRECT:
            response, latency = await self.route_to_langgraph(session_id, message, metadata)
            response["routing_path"] = "langgraph_direct"
            response["latency_ms"] = latency

        elif route == RoutingDecision.LIVEKIT_VOICE:
            response, latency = await self.route_to_livekit(
                session_id, message, message_type, metadata
            )
            response["routing_path"] = "livekit_voice"
            response["latency_ms"] = latency

        elif route == RoutingDecision.HYBRID:
            # Send to both paths (for mode transitions)
            tasks = [
                self.route_to_langgraph(session_id, message, metadata),
                self.route_to_livekit(session_id, message, message_type, metadata)
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            # Use LangGraph response if successful, LiveKit as fallback
            if not isinstance(results[0], Exception):
                response, latency = results[0]
                response["routing_path"] = "hybrid_langgraph"
            else:
                response, latency = results[1]
                response["routing_path"] = "hybrid_livekit"

            response["latency_ms"] = latency

        # Add routing metadata
        response["routed_at"] = datetime.utcnow().isoformat()
        response["source"] = source.value
        response["message_type"] = message_type.value

        # Log performance warning if over threshold
        if response.get("latency_ms", 0) > 500:
            logger.warning(f"High latency detected: {response['latency_ms']:.2f}ms on {route.value}")

        return response

    def get_metrics(self) -> Dict[str, Any]:
        """
        Get routing performance metrics

        Returns:
            Dictionary of metrics
        """
        if not self.enable_metrics:
            return {"metrics_enabled": False}

        # Calculate statistics
        text_avg = sum(self.metrics["text_latencies"]) / len(self.metrics["text_latencies"]) \
            if self.metrics["text_latencies"] else 0
        voice_avg = sum(self.metrics["voice_latencies"]) / len(self.metrics["voice_latencies"]) \
            if self.metrics["voice_latencies"] else 0

        # Calculate P95 latencies
        def calculate_p95(latencies):
            if not latencies:
                return 0
            sorted_latencies = sorted(latencies)
            index = int(len(sorted_latencies) * 0.95)
            return sorted_latencies[min(index, len(sorted_latencies) - 1)]

        return {
            "total_messages": self.metrics["total_messages"],
            "routing_decisions": self.metrics["routing_decisions"],
            "text_latency": {
                "avg_ms": text_avg,
                "p95_ms": calculate_p95(self.metrics["text_latencies"]),
                "count": len(self.metrics["text_latencies"])
            },
            "voice_latency": {
                "avg_ms": voice_avg,
                "p95_ms": calculate_p95(self.metrics["voice_latencies"]),
                "count": len(self.metrics["voice_latencies"])
            },
            "target_met": text_avg < 500 if text_avg > 0 else False
        }


# Global router instance
message_router = MessageRouter()


# Convenience function for Evolution webhook integration
async def route_evolution_message(
    message: str,
    from_number: str,
    instance_name: str,
    message_type: str = "text"
) -> Dict[str, Any]:
    """
    Route Evolution/WhatsApp message through dual-lane architecture

    Args:
        message: Message text
        from_number: Sender's phone number
        instance_name: WhatsApp instance
        message_type: Type of message (text/voice_note)

    Returns:
        Routed response
    """
    # Determine message type
    msg_type = MessageType.VOICE_NOTE if message_type == "voice_note" else MessageType.TEXT

    # Create session ID from phone number
    session_id = f"whatsapp_{from_number}_{instance_name}"

    # Route the message
    return await message_router.route_message(
        message=message,
        session_id=session_id,
        source=MessageSource.WHATSAPP,
        message_type=msg_type,
        metadata={
            "from_number": from_number,
            "instance_name": instance_name,
            "channel": "whatsapp"
        }
    )