"""
Message request/response schemas - stable wire schema.
Follows SOTA pattern: small stable schema with orchestration evolving around it.
"""
from enum import Enum
from typing import Optional, Dict, Any

from pydantic import BaseModel, Field


class AgentState(str, Enum):
    """Agent state for UI signaling."""
    IDLE = "idle"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    ESCALATED = "escalated"
    PROCESSING = "processing"


class MessageRequest(BaseModel):
    """
    Incoming message request from webhook.

    This is the stable wire schema used across all entry points:
    - WhatsApp webhooks (Evolution API)
    - Voice (LiveKit)
    - API endpoints

    Changes to this schema should be backward compatible.
    """
    # Required fields
    from_phone: str
    to_phone: str
    body: str
    message_sid: str
    clinic_id: str
    clinic_name: str

    # Optional fields with defaults
    message_type: str = "text"
    media_url: Optional[str] = None
    channel: str = "whatsapp"
    profile_name: str = "Usuario"
    metadata: Dict[str, Any] = Field(default_factory=dict)

    # Extended fields for omnichannel support
    organization_id: Optional[str] = None
    instance_name: Optional[str] = None

    # Alias for compatibility
    @property
    def message(self) -> str:
        """Alias for body to support different naming conventions."""
        return self.body


class MessageResponse(BaseModel):
    """
    Outgoing message response.

    This is the stable wire schema returned by the message processor.
    UI clients can rely on these fields for state management.
    """
    # Core response
    message: str
    session_id: str = ""

    # Response metadata
    status: str = "success"
    detected_language: str = "unknown"

    # UI state signaling - allows frontend to show appropriate UI
    agent_state: AgentState = AgentState.IDLE

    # Extended metadata
    metadata: Dict[str, Any] = Field(default_factory=dict)

    # Alias for compatibility with different naming conventions
    @property
    def response(self) -> str:
        """Alias for message to support different naming conventions."""
        return self.message
