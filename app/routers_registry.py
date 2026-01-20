"""
Router registry for healthcare backend.

Centralizes all API router registrations for cleaner main.py.
Routers are grouped by domain/functionality.
"""
import logging
from fastapi import FastAPI

logger = logging.getLogger(__name__)


def register_core_routers(app: FastAPI):
    """Register core API routers."""
    from app.api import quick_onboarding_rpc
    from app.api import multimodal_upload
    from app.api import services_upload
    from app.api import permissions_api
    from app.api import invitations_api
    from app.api import sales_invitations_api

    app.include_router(quick_onboarding_rpc.router)
    app.include_router(multimodal_upload.router)
    app.include_router(services_upload.router)
    app.include_router(permissions_api.router)
    app.include_router(invitations_api.router)
    app.include_router(sales_invitations_api.router)


def register_onboarding_routers(app: FastAPI):
    """Register onboarding and templates routers."""
    from app.api import onboarding_readiness
    from app.api import clinic_templates
    from app.api import agent_simulation
    from app.api import analytics_events

    app.include_router(onboarding_readiness.router)
    app.include_router(clinic_templates.router)
    app.include_router(agent_simulation.router)
    app.include_router(analytics_events.router)


def register_webhook_routers(app: FastAPI):
    """Register webhook routers."""
    from app.api import webhooks
    from app.api import whatsapp_webhook
    from app.api import evolution_webhook
    from app.webhooks import calendar_webhooks
    from app.webhooks import appointment_sync_webhook

    app.include_router(webhooks.router)
    app.include_router(whatsapp_webhook.router)
    app.include_router(evolution_webhook.router)
    app.include_router(calendar_webhooks.router)
    app.include_router(appointment_sync_webhook.router)


def register_integration_routers(app: FastAPI):
    """Register integration routers."""
    from app.api import integrations_routes
    from app.api import evolution_mock
    from app.api import calendar_demo

    app.include_router(integrations_routes.router)
    app.include_router(calendar_demo.router)
    app.include_router(evolution_mock.router)


def register_scheduling_routers(app: FastAPI):
    """Register scheduling and appointment routers."""
    from app.api import scheduling_routes
    from app.api import smart_scheduling_api
    from app.api import appointments_api
    from app.api import calendar_management
    from app.api import calendar_sync

    app.include_router(scheduling_routes.router)
    app.include_router(smart_scheduling_api.router)
    app.include_router(appointments_api.router)
    app.include_router(calendar_management.router)
    app.include_router(calendar_sync.router)


def register_rule_engine_routers(app: FastAPI):
    """Register rule engine routers."""
    from app.api import rule_authoring_api
    from app.api import scheduling_rule_chat_api
    from app.api import medical_director

    app.include_router(rule_authoring_api.router)
    app.include_router(scheduling_rule_chat_api.router)
    app.include_router(medical_director.router)


def register_resource_routers(app: FastAPI):
    """Register resource and healthcare routers."""
    from app.api import resources_api
    from app.api import healthcare_api
    from app.api import price_list_api
    from app.api import maintenance_routes

    app.include_router(resources_api.router)
    app.include_router(healthcare_api.router)
    app.include_router(price_list_api.router)
    app.include_router(maintenance_routes.router)


def register_admin_routers(app: FastAPI):
    """Register admin and stream routers."""
    from app.api import admin_streams
    from app.api import agents_api
    from app.api import memory_health
    from app.api import system_health

    app.include_router(admin_streams.router)
    app.include_router(agents_api.router)
    app.include_router(memory_health.router)
    app.include_router(system_health.router)


def register_billing_routers(app: FastAPI):
    """Register billing and subscription routers."""
    from app.api import billing_routes

    app.include_router(billing_routes.router)
    app.include_router(billing_routes.webhooks_router)


def register_config_routers(app: FastAPI):
    """Register configuration and prompt routers."""
    from app.api import prompt_routes
    from app.api import tier_mappings_api

    app.include_router(prompt_routes.router)
    app.include_router(tier_mappings_api.router)


def register_realtime_routers(app: FastAPI):
    """Register real-time and WebSocket routers."""
    from app.api import websocket_api

    app.include_router(websocket_api.router)


def register_compliance_routers(app: FastAPI):
    """Register HIPAA compliance and metrics routers."""
    from app.api import hipaa_compliance_api
    from app.api import metrics_endpoint

    app.include_router(hipaa_compliance_api.router)
    app.include_router(metrics_endpoint.router)


def register_hitl_routers(app: FastAPI):
    """Register HITL (Human-in-the-Loop) routers."""
    from app.api import hitl_router
    from app.api import message_plan_api

    app.include_router(hitl_router.router)
    app.include_router(message_plan_api.router)


def register_langgraph_router(app: FastAPI):
    """Register LangGraph service router with graceful fallback."""
    try:
        from app.api import langgraph_service
        app.include_router(langgraph_service.router)
        logger.info("✅ LangGraph service routes loaded for dual-lane routing")
    except ImportError as e:
        logger.warning(f"❌ LangGraph service module not available: {e}")


def register_all_routers(app: FastAPI):
    """Register all routers with the application."""
    register_core_routers(app)
    register_onboarding_routers(app)
    register_webhook_routers(app)
    register_integration_routers(app)
    register_scheduling_routers(app)
    register_rule_engine_routers(app)
    register_resource_routers(app)
    register_admin_routers(app)
    register_billing_routers(app)
    register_config_routers(app)
    register_realtime_routers(app)
    register_compliance_routers(app)
    register_hitl_routers(app)
    register_langgraph_router(app)

    logger.info("✅ All routers registered")
