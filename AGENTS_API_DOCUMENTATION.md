# Agents API Documentation

## Overview

The Agents API provides endpoints for managing the multi-agent system. All agents data is stored in the `core` schema and accessed via RPC functions to bypass RLS permissions.

**Base URL**: `https://healthcare-clinic-backend.fly.dev/apps/voice-api/agents`

## Endpoints

### 1. Get All Agents for Organization

Fetches all active agents for a specific organization.

**Endpoint**: `GET /apps/voice-api/agents/organization/{organization_id}`

**Parameters**:
- `organization_id` (path, required): Organization UUID
- `agent_type` (query, optional): Filter by agent type (e.g., "receptionist", "appointment_specialist")

**Response**: Array of Agent objects

**Example Request**:
```bash
GET /apps/voice-api/agents/organization/4e8ddba1-ad52-4613-9a03-ec64636b3f6c
```

**Example Response**:
```json
[
  {
    "id": "44da20c9-166a-4dab-b752-a4c5098526b4",
    "organization_id": "4e8ddba1-ad52-4613-9a03-ec64636b3f6c",
    "name": "Shtern Dental Assistant",
    "type": "receptionist",
    "description": "Main orchestrator agent for Shtern Dental Clinic",
    "parent_agent_id": null,
    "configuration": {
      "system_prompt": "You are the assistant for Shtern Dental Clinic...",
      "language": "ru",
      "tone": "friendly-professional",
      "business_hours": {
        "timezone": "Asia/Jerusalem",
        "schedule": {
          "sunday": {"open": "09:00", "close": "19:00"}
        }
      }
    },
    "langgraph_config": {
      "orchestrator_type": "healthcare",
      "base_template": "HealthcareLangGraph",
      "enable_checkpointing": true,
      "enable_memory": true,
      "enable_rag": true,
      "compliance_mode": "hipaa"
    },
    "delegation_config": [
      {
        "intent": "appointment",
        "delegate_to_type": "appointment_specialist",
        "preserve_context": true
      }
    ],
    "quick_ack_config": {
      "enabled": true,
      "delay_ms": 500,
      "messages": {
        "ru": ["Секунду...", "Минутку...", "Сейчас помогу..."],
        "en": ["One moment...", "Just a second..."],
        "he": ["רגע אחד...", "רק רגע..."]
      },
      "randomize": true
    },
    "capabilities": ["triage", "delegation", "emergency_detection", "phi_protection"],
    "tools": [
      {"tool_id": "whatsapp_tool", "enabled": true}
    ],
    "is_active": true,
    "created_at": "2025-09-30T10:30:00Z",
    "updated_at": "2025-09-30T10:30:00Z"
  },
  {
    "id": "34f82fd6-175b-4413-9300-4f3815de8600",
    "organization_id": "4e8ddba1-ad52-4613-9a03-ec64636b3f6c",
    "name": "Shtern Appointment Specialist",
    "type": "appointment_specialist",
    "description": "Specialist agent for appointment booking",
    "parent_agent_id": "44da20c9-166a-4dab-b752-a4c5098526b4",
    "configuration": {
      "system_prompt": "You are an appointment booking specialist...",
      "language": "ru"
    },
    "langgraph_config": {
      "orchestrator_type": "general",
      "base_template": "BaseLangGraphOrchestrator"
    },
    "delegation_config": [],
    "quick_ack_config": {
      "enabled": true,
      "delay_ms": 500,
      "messages": {
        "ru": ["Проверяю доступное время..."],
        "en": ["Checking available times..."],
        "he": ["בודק זמנים פנויים..."]
      }
    },
    "capabilities": ["calendar_integration", "availability_check", "booking"],
    "tools": [
      {"tool_id": "calendar_tool", "enabled": true}
    ],
    "is_active": true,
    "created_at": "2025-09-30T10:31:00Z",
    "updated_at": "2025-09-30T10:31:00Z"
  }
]
```

### 2. Get Single Agent

Fetches a specific agent by ID.

**Endpoint**: `GET /apps/voice-api/agents/{agent_id}`

**Parameters**:
- `agent_id` (path, required): Agent UUID

**Response**: Single Agent object

**Example Request**:
```bash
GET /apps/voice-api/agents/44da20c9-166a-4dab-b752-a4c5098526b4
```

### 3. Get Child Agents

Fetches all child specialist agents for a parent orchestrator.

**Endpoint**: `GET /apps/voice-api/agents/{agent_id}/children`

**Parameters**:
- `agent_id` (path, required): Parent agent UUID

**Response**: Array of Agent objects

**Example Request**:
```bash
GET /apps/voice-api/agents/44da20c9-166a-4dab-b752-a4c5098526b4/children
```

**Example Response**:
```json
[
  {
    "id": "34f82fd6-175b-4413-9300-4f3815de8600",
    "name": "Shtern Appointment Specialist",
    "type": "appointment_specialist",
    "parent_agent_id": "44da20c9-166a-4dab-b752-a4c5098526b4",
    ...
  }
]
```

### 4. Get Agent Templates

Fetches all available agent templates from the marketplace.

**Endpoint**: `GET /apps/voice-api/agents/templates/all`

**Parameters**: None

**Response**: Array of Template objects

**Example Response**:
```json
[
  {
    "id": "...",
    "name": "Healthcare Receptionist Orchestrator",
    "slug": "healthcare-receptionist-orchestrator",
    "industry": "healthcare",
    "use_case": "patient-intake",
    "description": "Main orchestrator agent...",
    "base_config": {...},
    "is_official": true,
    "is_featured": true
  },
  {
    "id": "...",
    "name": "Appointment Booking Specialist",
    "slug": "appointment-booking-specialist",
    "industry": "healthcare",
    "use_case": "appointment-booking",
    ...
  }
]
```

## Frontend Integration Example

### React/TypeScript

```typescript
// types.ts
export interface Agent {
  id: string;
  organization_id: string;
  name: string;
  type: string;
  description?: string;
  parent_agent_id?: string;
  configuration: Record<string, any>;
  langgraph_config: {
    orchestrator_type: string;
    base_template: string;
    enable_checkpointing?: boolean;
    enable_memory?: boolean;
    enable_rag?: boolean;
    compliance_mode?: string;
  };
  delegation_config: Array<{
    intent: string;
    delegate_to_type?: string;
    escalate_to?: string;
    preserve_context?: boolean;
  }>;
  quick_ack_config: {
    enabled: boolean;
    delay_ms?: number;
    messages?: Record<string, string[]>;
    randomize?: boolean;
  };
  capabilities: string[];
  tools: Array<Record<string, any>>;
  is_active: boolean;
  created_at?: string;
  updated_at?: string;
}

// api.ts
export async function getAgentsForOrganization(
  organizationId: string
): Promise<Agent[]> {
  const response = await fetch(
    `https://healthcare-clinic-backend.fly.dev/apps/voice-api/agents/organization/${organizationId}`
  );

  if (!response.ok) {
    throw new Error(`Failed to fetch agents: ${response.statusText}`);
  }

  return response.json();
}

// AgentsList.tsx
import { useEffect, useState } from 'react';
import { getAgentsForOrganization } from './api';
import type { Agent } from './types';

export function AgentsList({ organizationId }: { organizationId: string }) {
  const [agents, setAgents] = useState<Agent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    async function fetchAgents() {
      try {
        setLoading(true);
        const data = await getAgentsForOrganization(organizationId);
        setAgents(data);
        setError(null);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to fetch agents');
      } finally {
        setLoading(false);
      }
    }

    fetchAgents();
  }, [organizationId]);

  if (loading) return <div>Loading agents...</div>;
  if (error) return <div>Error: {error}</div>;

  return (
    <div>
      <h2>Agents ({agents.length})</h2>
      {agents.map((agent) => (
        <div key={agent.id}>
          <h3>{agent.name}</h3>
          <p>Type: {agent.type}</p>
          <p>Template: {agent.langgraph_config.base_template}</p>
          <p>Capabilities: {agent.capabilities.join(', ')}</p>
          {agent.quick_ack_config.enabled && (
            <p>Quick Ack: Enabled ({Object.keys(agent.quick_ack_config.messages || {}).length} languages)</p>
          )}
        </div>
      ))}
    </div>
  );
}
```

## Agent Types

- `receptionist` - Main orchestrator that handles initial contact and routing
- `appointment_specialist` - Specialist for appointment booking/rescheduling
- `insurance_specialist` - Specialist for insurance verification
- `billing_specialist` - Specialist for billing inquiries
- `technical_specialist` - Specialist for technical support

## Quick Ack Configuration

The `quick_ack_config` allows agents to send immediate acknowledgment messages while processing:

```json
{
  "enabled": true,
  "delay_ms": 500,
  "messages": {
    "en": ["One moment...", "Just a second...", "Processing..."],
    "ru": ["Секунду...", "Минутку...", "Обрабатываю..."],
    "he": ["רגע אחד...", "רק רגע...", "מעבד..."]
  },
  "randomize": true
}
```

- `enabled`: Whether quick ack is enabled
- `delay_ms`: Delay before sending ack (default: 500ms)
- `messages`: Language-specific messages (ISO 639-1 codes)
- `randomize`: If true, randomly select from message array

## Delegation Configuration

The `delegation_config` defines when an orchestrator should delegate to specialists:

```json
[
  {
    "intent": "appointment",
    "delegate_to_type": "appointment_specialist",
    "preserve_context": true
  },
  {
    "intent": "emergency",
    "escalate_to": "human",
    "notify": ["clinic_admin"],
    "priority": "critical"
  }
]
```

## Error Handling

All endpoints return standard HTTP status codes:

- `200 OK` - Success
- `404 Not Found` - Agent not found
- `500 Internal Server Error` - Server error

Error response format:
```json
{
  "detail": "Error message description"
}
```

## Rate Limiting

No rate limiting is currently enforced, but consider implementing client-side debouncing for frequent requests.

## Authentication

Currently, the API uses service role authentication from the backend. Frontend should route through the backend rather than calling Supabase RPC functions directly.

## Notes

- All agents are scoped to organizations (multi-tenant)
- Agents in `core` schema require RPC functions for access
- Frontend should never query `core.agents` table directly
- Always use organization_id from authenticated user context
- Child agents have `parent_agent_id` set to their orchestrator's ID

## Testing

Test the API with curl:

```bash
# Get all agents for organization
curl https://healthcare-clinic-backend.fly.dev/apps/voice-api/agents/organization/4e8ddba1-ad52-4613-9a03-ec64636b3f6c

# Get single agent
curl https://healthcare-clinic-backend.fly.dev/apps/voice-api/agents/44da20c9-166a-4dab-b752-a4c5098526b4

# Get templates
curl https://healthcare-clinic-backend.fly.dev/apps/voice-api/agents/templates/all
```