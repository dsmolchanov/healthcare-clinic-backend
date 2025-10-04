"""
Agents API
Provides endpoints for managing multi-agent system
"""
from fastapi import APIRouter, HTTPException, Query
from typing import Optional, List, Dict, Any
import logging
from pydantic import BaseModel
from app.services.agent_service import get_agent_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/agents", tags=["agents"])


class AgentResponse(BaseModel):
    """Agent response model for frontend"""
    id: str
    organization_id: str
    name: str
    type: str
    description: Optional[str] = None
    parent_agent_id: Optional[str] = None
    configuration: Dict[str, Any] = {}
    langgraph_config: Dict[str, Any] = {}
    delegation_config: List[Dict[str, Any]] = []
    quick_ack_config: Dict[str, Any] = {}
    capabilities: List[str] = []
    tools: List[Dict[str, Any]] = []
    is_active: bool = True
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


@router.get("/organization/{organization_id}")
async def get_agents_for_organization(
    organization_id: str,
    agent_type: Optional[str] = Query(None, description="Filter by agent type")
) -> List[AgentResponse]:
    """
    Get all agents for an organization

    Args:
        organization_id: Organization UUID
        agent_type: Optional filter by agent type (receptionist, appointment_specialist, etc.)

    Returns:
        List of agents
    """
    try:
        agent_service = get_agent_service()

        # If agent_type is specified, get only that type
        if agent_type:
            agent = await agent_service.get_agent_for_organization(
                organization_id=organization_id,
                agent_type=agent_type
            )

            if not agent:
                return []

            return [AgentResponse(
                id=agent.id,
                organization_id=agent.organization_id,
                name=agent.name,
                type=agent.type,
                description=agent.configuration.get("description"),
                parent_agent_id=agent.parent_agent_id,
                configuration=agent.configuration,
                langgraph_config=agent.langgraph_config,
                delegation_config=agent.delegation_config,
                quick_ack_config=agent.quick_ack_config,
                capabilities=agent.capabilities,
                tools=agent.tools,
                is_active=agent.is_active
            )]

        # Otherwise, get all agents using RPC
        from app.db.supabase_client import get_supabase_client
        supabase = get_supabase_client()

        result = supabase.rpc('get_agents_for_organization', {'org_id': organization_id}).execute()

        if not result.data:
            logger.info(f"No agents found for organization {organization_id}")
            return []

        # Convert to response models
        agents = []
        for agent_data in result.data:
            agents.append(AgentResponse(
                id=agent_data['id'],
                organization_id=agent_data['organization_id'],
                name=agent_data['name'],
                type=agent_data['type'],
                description=agent_data.get('description'),
                parent_agent_id=agent_data.get('parent_agent_id'),
                configuration=agent_data.get('configuration', {}),
                langgraph_config=agent_data.get('langgraph_config', {}),
                delegation_config=agent_data.get('delegation_config', []),
                quick_ack_config=agent_data.get('quick_ack_config', {}),
                capabilities=agent_data.get('capabilities', []),
                tools=agent_data.get('tools', []),
                is_active=agent_data.get('is_active', True),
                created_at=agent_data.get('created_at'),
                updated_at=agent_data.get('updated_at')
            ))

        logger.info(f"Found {len(agents)} agents for organization {organization_id}")
        return agents

    except Exception as e:
        logger.error(f"Failed to fetch agents for organization {organization_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch agents: {str(e)}")


@router.get("/{agent_id}")
async def get_agent(agent_id: str) -> AgentResponse:
    """
    Get agent by ID

    Args:
        agent_id: Agent UUID

    Returns:
        Agent details
    """
    try:
        agent_service = get_agent_service()
        agent = await agent_service.get_agent_by_id(agent_id)

        if not agent:
            raise HTTPException(status_code=404, detail=f"Agent not found: {agent_id}")

        return AgentResponse(
            id=agent.id,
            organization_id=agent.organization_id,
            name=agent.name,
            type=agent.type,
            description=agent.configuration.get("description"),
            parent_agent_id=agent.parent_agent_id,
            configuration=agent.configuration,
            langgraph_config=agent.langgraph_config,
            delegation_config=agent.delegation_config,
            quick_ack_config=agent.quick_ack_config,
            capabilities=agent.capabilities,
            tools=agent.tools,
            is_active=agent.is_active
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to fetch agent {agent_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch agent: {str(e)}")


@router.get("/{agent_id}/children")
async def get_child_agents(agent_id: str) -> List[AgentResponse]:
    """
    Get child specialist agents for a parent orchestrator

    Args:
        agent_id: Parent agent UUID

    Returns:
        List of child agents
    """
    try:
        agent_service = get_agent_service()
        children = await agent_service.get_child_agents(agent_id)

        if not children:
            return []

        return [AgentResponse(
            id=agent.id,
            organization_id=agent.organization_id,
            name=agent.name,
            type=agent.type,
            description=agent.configuration.get("description"),
            parent_agent_id=agent.parent_agent_id,
            configuration=agent.configuration,
            langgraph_config=agent.langgraph_config,
            delegation_config=agent.delegation_config,
            quick_ack_config=agent.quick_ack_config,
            capabilities=agent.capabilities,
            tools=agent.tools,
            is_active=agent.is_active
        ) for agent in children]

    except Exception as e:
        logger.error(f"Failed to fetch child agents for {agent_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch child agents: {str(e)}")


@router.get("/templates/all")
async def get_agent_templates() -> List[Dict[str, Any]]:
    """
    Get all available agent templates

    Returns:
        List of agent templates
    """
    try:
        from app.db.supabase_client import get_supabase_client
        supabase = get_supabase_client()

        result = supabase.rpc('get_agent_templates').execute()

        if not result.data:
            return []

        logger.info(f"Found {len(result.data)} agent templates")
        return result.data

    except Exception as e:
        logger.error(f"Failed to fetch agent templates: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch templates: {str(e)}")


# ============================================================================
# CREATE, UPDATE, DELETE Operations
# ============================================================================

class CreateAgentRequest(BaseModel):
    """Request model for creating an agent"""
    organization_id: str
    name: str
    type: str
    description: Optional[str] = None
    template_id: Optional[str] = None
    parent_agent_id: Optional[str] = None
    configuration: Dict[str, Any] = {}
    langgraph_config: Dict[str, Any] = {}
    delegation_config: List[Dict[str, Any]] = []
    quick_ack_config: Dict[str, Any] = {}
    capabilities: List[str] = []
    tools: List[Dict[str, Any]] = []
    voice_config: Dict[str, Any] = {}
    industry_config: Dict[str, Any] = {}


class UpdateAgentRequest(BaseModel):
    """Request model for updating an agent"""
    name: Optional[str] = None
    description: Optional[str] = None
    configuration: Optional[Dict[str, Any]] = None
    langgraph_config: Optional[Dict[str, Any]] = None
    delegation_config: Optional[List[Dict[str, Any]]] = None
    quick_ack_config: Optional[Dict[str, Any]] = None
    capabilities: Optional[List[str]] = None
    tools: Optional[List[Dict[str, Any]]] = None
    voice_config: Optional[Dict[str, Any]] = None
    industry_config: Optional[Dict[str, Any]] = None
    is_active: Optional[bool] = None


@router.post("/create")
async def create_agent(request: CreateAgentRequest) -> Dict[str, Any]:
    """
    Create a new agent

    Args:
        request: Agent creation parameters

    Returns:
        Created agent details
    """
    try:
        from app.db.supabase_client import get_supabase_client
        supabase = get_supabase_client()

        # Call RPC function
        result = supabase.rpc('create_agent', {
            'p_organization_id': request.organization_id,
            'p_name': request.name,
            'p_type': request.type,
            'p_description': request.description,
            'p_template_id': request.template_id,
            'p_parent_agent_id': request.parent_agent_id,
            'p_configuration': request.configuration,
            'p_langgraph_config': request.langgraph_config,
            'p_delegation_config': request.delegation_config,
            'p_quick_ack_config': request.quick_ack_config,
            'p_capabilities': request.capabilities,
            'p_tools': request.tools,
            'p_voice_config': request.voice_config,
            'p_industry_config': request.industry_config
        }).execute()

        if not result.data:
            raise HTTPException(status_code=500, detail="Failed to create agent")

        response = result.data[0] if isinstance(result.data, list) else result.data

        if not response.get('success'):
            error_code = response.get('error_code', 'UNKNOWN')
            error_msg = response.get('error', 'Failed to create agent')

            if error_code == 'ORG_NOT_FOUND':
                raise HTTPException(status_code=404, detail=error_msg)
            elif error_code == 'DUPLICATE_NAME':
                raise HTTPException(status_code=409, detail=error_msg)
            else:
                raise HTTPException(status_code=400, detail=error_msg)

        logger.info(f"Created agent: {response.get('agent_id')}")
        return response

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to create agent: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to create agent: {str(e)}")


@router.put("/{agent_id}")
@router.patch("/{agent_id}")
async def update_agent(agent_id: str, request: UpdateAgentRequest) -> Dict[str, Any]:
    """
    Update an existing agent

    Args:
        agent_id: Agent UUID
        request: Fields to update (only provided fields will be updated)

    Returns:
        Updated agent details
    """
    try:
        from app.db.supabase_client import get_supabase_client
        supabase = get_supabase_client()

        # Call RPC function
        result = supabase.rpc('update_agent', {
            'p_agent_id': agent_id,
            'p_name': request.name,
            'p_description': request.description,
            'p_configuration': request.configuration,
            'p_langgraph_config': request.langgraph_config,
            'p_delegation_config': request.delegation_config,
            'p_quick_ack_config': request.quick_ack_config,
            'p_capabilities': request.capabilities,
            'p_tools': request.tools,
            'p_voice_config': request.voice_config,
            'p_industry_config': request.industry_config,
            'p_is_active': request.is_active
        }).execute()

        if not result.data:
            raise HTTPException(status_code=404, detail="Agent not found")

        response = result.data[0] if isinstance(result.data, list) else result.data

        if not response.get('success'):
            error_code = response.get('error_code', 'UNKNOWN')
            error_msg = response.get('error', 'Failed to update agent')

            if error_code == 'AGENT_NOT_FOUND':
                raise HTTPException(status_code=404, detail=error_msg)
            elif error_code == 'DUPLICATE_NAME':
                raise HTTPException(status_code=409, detail=error_msg)
            else:
                raise HTTPException(status_code=400, detail=error_msg)

        logger.info(f"Updated agent: {agent_id}")
        return response

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update agent {agent_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to update agent: {str(e)}")


@router.delete("/{agent_id}")
async def delete_agent(
    agent_id: str,
    hard_delete: bool = Query(False, description="Permanently delete (true) or deactivate (false)"),
    cascade: bool = Query(False, description="Delete child agents as well")
) -> Dict[str, Any]:
    """
    Delete or deactivate an agent

    Args:
        agent_id: Agent UUID
        hard_delete: If true, permanently delete; if false, deactivate (soft delete)
        cascade: If true, also delete child agents

    Returns:
        Deletion result
    """
    try:
        from app.db.supabase_client import get_supabase_client
        supabase = get_supabase_client()

        # Choose RPC function based on cascade option
        if cascade:
            result = supabase.rpc('delete_agent_cascade', {
                'p_agent_id': agent_id,
                'p_hard_delete': hard_delete
            }).execute()
        else:
            result = supabase.rpc('delete_agent', {
                'p_agent_id': agent_id,
                'p_hard_delete': hard_delete
            }).execute()

        if not result.data:
            raise HTTPException(status_code=404, detail="Agent not found")

        response = result.data[0] if isinstance(result.data, list) else result.data

        if not response.get('success'):
            error_code = response.get('error_code', 'UNKNOWN')
            error_msg = response.get('error', 'Failed to delete agent')

            if error_code == 'AGENT_NOT_FOUND':
                raise HTTPException(status_code=404, detail=error_msg)
            elif error_code == 'HAS_CHILDREN':
                raise HTTPException(
                    status_code=409,
                    detail=f"{error_msg} Use cascade=true to delete children as well."
                )
            else:
                raise HTTPException(status_code=400, detail=error_msg)

        logger.info(f"Deleted agent: {agent_id} (hard={hard_delete}, cascade={cascade})")
        return response

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to delete agent {agent_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to delete agent: {str(e)}")


@router.post("/{agent_id}/activate")
async def activate_agent(agent_id: str) -> Dict[str, Any]:
    """Activate an agent"""
    try:
        from app.db.supabase_client import get_supabase_client
        supabase = get_supabase_client()

        result = supabase.rpc('set_agent_active', {
            'p_agent_id': agent_id,
            'p_is_active': True
        }).execute()

        if not result.data:
            raise HTTPException(status_code=404, detail="Agent not found")

        response = result.data[0] if isinstance(result.data, list) else result.data

        if not response.get('success'):
            raise HTTPException(status_code=404, detail=response.get('error', 'Agent not found'))

        return response

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to activate agent {agent_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{agent_id}/deactivate")
async def deactivate_agent(agent_id: str) -> Dict[str, Any]:
    """Deactivate an agent"""
    try:
        from app.db.supabase_client import get_supabase_client
        supabase = get_supabase_client()

        result = supabase.rpc('set_agent_active', {
            'p_agent_id': agent_id,
            'p_is_active': False
        }).execute()

        if not result.data:
            raise HTTPException(status_code=404, detail="Agent not found")

        response = result.data[0] if isinstance(result.data, list) else result.data

        if not response.get('success'):
            raise HTTPException(status_code=404, detail=response.get('error', 'Agent not found'))

        return response

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to deactivate agent {agent_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{agent_id}/clone")
async def clone_agent_endpoint(
    agent_id: str,
    new_name: str = Query(..., description="Name for the cloned agent"),
    organization_id: Optional[str] = Query(None, description="Target organization (defaults to source org)")
) -> Dict[str, Any]:
    """
    Clone an agent with all its configuration

    Args:
        agent_id: Source agent UUID
        new_name: Name for the cloned agent
        organization_id: Optional target organization ID

    Returns:
        Cloned agent details
    """
    try:
        from app.db.supabase_client import get_supabase_client
        supabase = get_supabase_client()

        result = supabase.rpc('clone_agent', {
            'p_agent_id': agent_id,
            'p_new_name': new_name,
            'p_organization_id': organization_id
        }).execute()

        if not result.data:
            raise HTTPException(status_code=404, detail="Source agent not found")

        response = result.data[0] if isinstance(result.data, list) else result.data

        if not response.get('success'):
            error_code = response.get('error_code', 'UNKNOWN')
            error_msg = response.get('error', 'Failed to clone agent')

            if error_code == 'AGENT_NOT_FOUND':
                raise HTTPException(status_code=404, detail=error_msg)
            else:
                raise HTTPException(status_code=400, detail=error_msg)

        logger.info(f"Cloned agent {agent_id} to {response.get('agent_id')}")
        return response

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to clone agent {agent_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to clone agent: {str(e)}")