# Widget-to-Queue Integration Guide

## Overview

This document describes how to integrate the PlainTalk chat widget with the WhatsApp queue-worker system to provide a unified message processing pipeline for both WhatsApp and web widget channels.

## Current Architecture

### Widget Frontend (`/plaintalk/widget`)
- **Technology**: React 18 + TypeScript + Shadow DOM
- **Entry Point**: `src/components/ChatInterface.tsx`
- **Current Endpoint**: `GET /apps/voice-api/widget-chat?body={message}`
- **Default API URL**: `https://healthcare-clinic-backend.fly.dev`

### Backend (`/clinics/backend`)
- **Current Implementation**: Direct AI processing (synchronous)
- **Response Time**: 5-9 seconds (blocking)
- **Location**: `app/main.py:1071-1171`

### WhatsApp Queue System
- **Queue**: Redis Streams (`wa:{instance}:stream`)
- **Worker**: Separate process with XAUTOCLAIM
- **Pattern**: Async processing with rate limiting

## Problem Statement

**Current Widget Behavior**:
1. User sends message → Frontend waits
2. Backend processes with AI (5-9s)
3. Frontend displays response

**Issues**:
- Long wait time (5-9s) with no feedback
- Frontend blocked during processing
- No queuing or retry logic
- Different architecture than WhatsApp (not unified)

**Desired Behavior**:
1. User sends message → Immediate ack (< 100ms)
2. Message queued to Redis
3. Worker processes in background
4. Response sent via WebSocket/SSE
5. Frontend displays response in real-time

## Proposed Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    UNIFIED MESSAGE PIPELINE                      │
└─────────────────────────────────────────────────────────────────┘

Widget User                                    WhatsApp User
    ↓                                                 ↓
WebSocket/SSE                                  Evolution API
    ↓                                                 ↓
POST /apps/voice-api/widget/send                    POST /webhooks/evolution/
    ↓                                                 ↓
    └──────────────────┬──────────────────────────────┘
                       ↓
            Unified Message Handler
                       ↓
         Redis Stream: messages:stream
           (Channel-agnostic queue)
                       ↓
              Worker Process
        (XAUTOCLAIM + XREADGROUP)
                       ↓
            AI Processing (5-9s)
                       ↓
         ┌─────────────┴─────────────┐
         ↓                           ↓
   Evolution API              WebSocket/SSE
   (WhatsApp)                  (Widget)
         ↓                           ↓
    WhatsApp User               Widget User
```

## Implementation Options

### Option 1: Queue-Based (Recommended) ✅

**Architecture**: Widget messages go through the same queue as WhatsApp

**Pros**:
- ✅ Unified processing pipeline
- ✅ Same retry logic and error handling
- ✅ Rate limiting across all channels
- ✅ Consistent monitoring and debugging
- ✅ Easy to add more channels (SMS, Telegram, etc.)

**Cons**:
- Requires WebSocket/SSE for real-time responses
- More complex than direct processing

**Implementation**:
1. Create widget-specific stream: `widget:{session_id}:stream`
2. Queue message to Redis on POST
3. Worker picks up via XAUTOCLAIM
4. Worker sends response via WebSocket/SSE

### Option 2: Direct Processing with Queue Fallback

**Architecture**: Widget tries direct, falls back to queue on timeout

**Pros**:
- Fast responses for simple queries
- Graceful degradation

**Cons**:
- Two code paths to maintain
- Inconsistent behavior

### Option 3: Immediate Ack + Polling (Simplest)

**Architecture**: Return 202 Accepted, client polls for response

**Pros**:
- ✅ No WebSocket complexity
- ✅ Works with existing REST API
- ✅ Simple to implement

**Cons**:
- Polling overhead
- Not real-time

## Recommended Implementation (Option 1)

### Phase 1: Add Widget to Existing Queue

Reuse the existing WhatsApp queue infrastructure for widget messages.

#### Backend Changes

**1. Create Widget Message Endpoint** (`app/apps/voice-api/widget_websocket.py`)

```python
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from app.services.whatsapp_queue import enqueue_message
import json

router = APIRouter()

# Store active WebSocket connections
active_connections: dict[str, WebSocket] = {}

@router.websocket("/ws/widget/{session_id}")
async def widget_websocket(websocket: WebSocket, session_id: str):
    """WebSocket endpoint for widget real-time communication"""
    await websocket.accept()
    active_connections[session_id] = websocket

    try:
        while True:
            # Receive message from widget
            data = await websocket.receive_text()
            message = json.loads(data)

            # Queue message (reuse WhatsApp queue)
            message_id = f"widget_{session_id}_{int(time.time())}"
            await enqueue_message(
                instance=f"widget_{session_id}",
                to_number=session_id,  # Use session_id as identifier
                text=message['text'],
                message_id=message_id,
                metadata={
                    "channel": "widget",
                    "session_id": session_id,
                    "user_id": message.get('user_id'),
                    "clinic_id": message.get('clinic_id')
                }
            )

            # Send immediate ack
            await websocket.send_json({
                "type": "ack",
                "message_id": message_id,
                "status": "queued"
            })

    except WebSocketDisconnect:
        del active_connections[session_id]
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        if session_id in active_connections:
            del active_connections[session_id]

async def send_widget_response(session_id: str, text: str, message_id: str):
    """Send response back to widget via WebSocket"""
    if session_id in active_connections:
        try:
            await active_connections[session_id].send_json({
                "type": "message",
                "text": text,
                "message_id": message_id,
                "timestamp": datetime.utcnow().isoformat()
            })
        except Exception as e:
            logger.error(f"Failed to send widget response: {e}")
```

**2. Modify Worker to Handle Widget Messages** (`app/services/whatsapp_queue/worker.py`)

```python
async def send_response(self, instance: str, to: str, text: str, metadata: dict):
    """Send response via appropriate channel"""

    channel = metadata.get("channel", "whatsapp")

    if channel == "widget":
        # Send via WebSocket
        session_id = metadata.get("session_id")
        message_id = metadata.get("message_id")
        await send_widget_response(session_id, text, message_id)
        return True

    elif channel == "whatsapp":
        # Send via Evolution API (existing code)
        return await send_text(instance, to, text)

    else:
        logger.warning(f"Unknown channel: {channel}")
        return False
```

**3. Register WebSocket Router** (`app/main.py`)

```python
from app.api.widget_websocket import router as widget_ws_router

app.include_router(widget_ws_router, tags=["widget"])
```

#### Frontend Changes

**1. Add WebSocket Support** (`plaintalk/widget/src/hooks/useWebSocket.ts`)

```typescript
import { useEffect, useRef, useState } from 'react';

interface UseWebSocketOptions {
  url: string;
  sessionId: string;
  onMessage: (message: any) => void;
  onError?: (error: Event) => void;
}

export const useWebSocket = ({
  url,
  sessionId,
  onMessage,
  onError
}: UseWebSocketOptions) => {
  const wsRef = useRef<WebSocket | null>(null);
  const [isConnected, setIsConnected] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const wsUrl = `${url}/ws/widget/${sessionId}`;
    const ws = new WebSocket(wsUrl.replace('https', 'wss').replace('http', 'ws'));

    ws.onopen = () => {
      console.log('WebSocket connected');
      setIsConnected(true);
      setError(null);
    };

    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        onMessage(data);
      } catch (err) {
        console.error('Failed to parse WebSocket message:', err);
      }
    };

    ws.onerror = (event) => {
      console.error('WebSocket error:', event);
      setError('Connection error');
      onError?.(event);
    };

    ws.onclose = () => {
      console.log('WebSocket disconnected');
      setIsConnected(false);
    };

    wsRef.current = ws;

    return () => {
      ws.close();
    };
  }, [url, sessionId]);

  const sendMessage = (message: any) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(message));
      return true;
    }
    return false;
  };

  return { isConnected, sendMessage, error };
};
```

**2. Update ChatInterface Component** (`plaintalk/widget/src/components/ChatInterface.tsx`)

```typescript
import { useWebSocket } from '../hooks/useWebSocket';

const ChatInterface: React.FC<ChatInterfaceProps> = ({
  agentId,
  apiUrl = 'https://healthcare-clinic-backend.fly.dev',
  ...props
}) => {
  const [messages, setMessages] = useState<Message[]>([]);
  const [sessionId] = useState(`widget_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`);

  // WebSocket connection
  const { isConnected, sendMessage: wsSend, error: wsError } = useWebSocket({
    url: apiUrl,
    sessionId,
    onMessage: (data) => {
      if (data.type === 'message') {
        // Add agent response
        const agentMessage: Message = {
          id: data.message_id,
          text: data.text,
          sender: 'agent',
          timestamp: new Date(data.timestamp)
        };
        setMessages(prev => [...prev, agentMessage]);
        setIsTyping(false);
      } else if (data.type === 'ack') {
        // Message queued successfully
        console.log('Message queued:', data.message_id);
      }
    },
    onError: (err) => {
      console.error('WebSocket error:', err);
      setError('Connection lost. Please refresh.');
    }
  });

  const sendMessage = async (text: string) => {
    if (!text.trim() || !isConnected) return;

    // Add user message immediately
    const userMessage: Message = {
      id: `msg_${Date.now()}`,
      text: text.trim(),
      sender: 'user',
      timestamp: new Date()
    };

    setMessages(prev => [...prev, userMessage]);
    setIsTyping(true);
    setError(null);

    // Send via WebSocket
    const sent = wsSend({
      text: text.trim(),
      clinic_id: clinicId,
      user_id: sessionId
    });

    if (!sent) {
      setError('Failed to send message. Please try again.');
      setIsTyping(false);
    }
  };

  return (
    // ... existing JSX with isConnected status
  );
};
```

### Phase 2: Optimize with Immediate Ack Pattern

Once WebSocket is working, add immediate acknowledgment:

```python
@router.websocket("/ws/widget/{session_id}")
async def widget_websocket(websocket: WebSocket, session_id: str):
    # ... existing code ...

    # Send typing indicator immediately
    await websocket.send_json({
        "type": "typing",
        "status": true
    })

    # Queue for AI processing
    await enqueue_message(...)

    # Worker will send actual response when ready
```

### Phase 3: Add Fallback for REST API

Keep the existing `/apps/voice-api/widget-chat` endpoint for backwards compatibility:

```python
@app.get("/apps/voice-api/widget-chat")
async def widget_chat_get(body: str = "", session_id: str = ""):
    """Legacy REST endpoint - still works but slower"""

    # Option 1: Direct processing (current behavior)
    if should_use_direct_processing():
        response = await process_message_directly(body)
        return {"message": response}

    # Option 2: Queue with polling
    message_id = await enqueue_message(...)
    return {
        "message_id": message_id,
        "status": "queued",
        "poll_url": f"/apps/voice-api/widget-poll/{message_id}"
    }
```

## Configuration

### Environment Variables

Add to backend `.env`:
```bash
# Widget configuration
WIDGET_USE_WEBSOCKET=true          # Enable WebSocket mode
WIDGET_FALLBACK_REST=true          # Keep REST API for fallback
WIDGET_MAX_CONNECTIONS=1000        # Max concurrent WebSocket connections
```

### Widget HTML Attributes

```html
<plaintalk-widget
  agent-id="your-agent-id"
  api-url="https://healthcare-clinic-backend.fly.dev"
  clinic-id="3e411ecb-3411-4add-91e2-8fa897310cb0"
  use-websocket="true"
  modes="voice,text"
  default-mode="text"
></plaintalk-widget>
```

## Testing

### 1. Test WebSocket Connection

```javascript
// Browser console
const ws = new WebSocket('wss://healthcare-clinic-backend.fly.dev/ws/widget/test123');
ws.onopen = () => console.log('Connected');
ws.onmessage = (e) => console.log('Received:', e.data);
ws.send(JSON.stringify({ text: 'Hello' }));
```

### 2. Test Queue Integration

```bash
# Check Redis for widget messages
redis-cli XLEN widget:test123:stream

# Check worker logs
fly logs --app healthcare-clinic-backend | grep widget
```

### 3. Test End-to-End

```bash
# Open test page
open plaintalk/widget/example-chat.html

# Send message
# Should see immediate ack (<100ms)
# Then agent response (5-9s via WebSocket)
```

## Monitoring

### Key Metrics

```python
# Add to worker stats
{
    "widget_messages_processed": counter,
    "widget_websocket_connections": gauge,
    "widget_response_latency": histogram
}
```

### Health Check

```bash
curl https://healthcare-clinic-backend.fly.dev/admin/streams/health | jq '.widget_stats'
```

## Migration Path

### Step 1: Add WebSocket Support (Week 1)
- [ ] Implement WebSocket endpoint
- [ ] Update worker to handle widget messages
- [ ] Test with example-chat.html

### Step 2: Update Widget Frontend (Week 1)
- [ ] Add useWebSocket hook
- [ ] Update ChatInterface to use WebSocket
- [ ] Add fallback to REST API
- [ ] Deploy to Vercel

### Step 3: Enable in Production (Week 2)
- [ ] Set WIDGET_USE_WEBSOCKET=true
- [ ] Monitor queue depth and latency
- [ ] Gradual rollout (10% → 50% → 100%)

### Step 4: Deprecate Old Endpoint (Week 3)
- [ ] Keep REST API for 30 days
- [ ] Log warnings for REST API usage
- [ ] Remove after migration complete

## Benefits

### For Users
- ✅ Instant feedback (< 100ms ack)
- ✅ Real-time responses (no page reload)
- ✅ Better error handling (retry logic)
- ✅ Consistent experience across channels

### For Operations
- ✅ Unified monitoring dashboard
- ✅ Single queue to manage
- ✅ Same retry/error logic everywhere
- ✅ Easy to add new channels

### For Development
- ✅ Single codebase for all channels
- ✅ Test once, works everywhere
- ✅ Easier to add features (typing indicators, read receipts)

## Alternative: Server-Sent Events (SSE)

If WebSocket is too complex, use SSE:

```python
from fastapi.responses import StreamingResponse

@app.post("/apps/voice-api/widget-stream")
async def widget_stream(request: Request):
    async def event_generator():
        # Send immediate ack
        yield f"data: {json.dumps({'type': 'ack'})}\n\n"

        # Queue message
        message_id = await enqueue_message(...)

        # Wait for response (with timeout)
        response = await wait_for_response(message_id, timeout=30)

        # Send response
        yield f"data: {json.dumps({'type': 'message', 'text': response})}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")
```

**Frontend**:
```typescript
const eventSource = new EventSource(`${apiUrl}/apps/voice-api/widget-stream`);
eventSource.onmessage = (event) => {
  const data = JSON.parse(event.data);
  if (data.type === 'message') {
    addMessage(data.text);
  }
};
```

## Security Considerations

1. **Authentication**: Add JWT tokens for WebSocket auth
2. **Rate Limiting**: Limit messages per session (10 msg/min)
3. **Session Timeout**: Close inactive WebSockets after 30min
4. **Input Validation**: Sanitize all user input
5. **CORS**: Configure proper CORS headers

## Support & Resources

- **Widget Documentation**: `/plaintalk/widget/README-CHAT.md`
- **Queue Documentation**: `/apps/healthcare-backend/WHATSAPP_QUEUE_IMPLEMENTATION.md`
- **WebSocket Docs**: https://fastapi.tiangolo.com/advanced/websockets/
- **React WebSocket**: https://github.com/robtaussig/react-use-websocket

---

**Status**: Design Complete
**Next Step**: Implement WebSocket endpoint
**Estimated Time**: 1-2 days for full implementation