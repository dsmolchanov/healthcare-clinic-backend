"""
FastAPI application factory.

Creates and configures the FastAPI application with:
- OpenAPI documentation with security schemes
- CORS middleware
- Rate limiting middleware
- HIPAA audit middleware
"""
import logging
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.startup import lifespan
from app.middleware.rate_limiter import webhook_limiter

logger = logging.getLogger(__name__)


def create_openapi_schema(app: FastAPI):
    """Generate custom OpenAPI schema with security schemes."""
    def custom_openapi():
        if app.openapi_schema:
            return app.openapi_schema

        from fastapi.openapi.utils import get_openapi

        openapi_schema = get_openapi(
            title=app.title,
            version=app.version,
            description=app.description,
            routes=app.routes,
        )

        # Add security schemes
        openapi_schema["components"]["securitySchemes"] = {
            "bearerAuth": {
                "type": "http",
                "scheme": "bearer",
                "bearerFormat": "JWT",
                "description": "Enter your JWT token"
            }
        }

        # Add tags for better organization
        openapi_schema["tags"] = [
            {"name": "health", "description": "Health check endpoints"},
            {"name": "messages", "description": "Message processing endpoints"},
            {"name": "webhooks", "description": "Webhook handlers for external services"},
            {"name": "appointments", "description": "Appointment management"},
            {"name": "integrations", "description": "External service integrations"},
        ]

        app.openapi_schema = openapi_schema
        return app.openapi_schema

    return custom_openapi


def configure_cors(app: FastAPI):
    """Configure CORS middleware for production frontend."""
    origins = [
        "https://plaintalk.io",
        "https://www.plaintalk.io",
        "http://localhost:3000",
        "http://localhost:5173",
        "http://localhost:5174",
        "*"  # Allow all origins as fallback
    ]

    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["*"],
        expose_headers=["*"],
    )


def configure_rate_limiting(app: FastAPI):
    """Add rate limiting middleware for webhook endpoints."""
    @app.middleware("http")
    async def rate_limit_middleware(request: Request, call_next):
        if request.url.path.startswith("/webhooks/"):
            return await webhook_limiter(request, call_next)
        return await call_next(request)


def configure_audit_middleware(app: FastAPI):
    """Add HIPAA audit middleware for PHI access logging."""
    from app.security.audit_middleware import HIPAAAuditMiddleware
    app.add_middleware(HIPAAAuditMiddleware)


def create_app() -> FastAPI:
    """
    Create and configure the FastAPI application.

    Returns:
        Configured FastAPI application instance
    """
    app = FastAPI(
        title="Healthcare Clinics Backend",
        description="""
Voice agent platform for healthcare clinics.

## Features
- WhatsApp appointment booking
- Multi-channel message processing
- Calendar integration
- HIPAA-compliant audit logging

## Authentication
Protected endpoints require Bearer token authentication.
Use the Authorize button above to set your JWT token.
""",
        version="1.0.0",
        lifespan=lifespan,
        redirect_slashes=False,  # Prevent HTTP redirects from HTTPS requests
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )

    # Configure custom OpenAPI schema
    app.openapi = create_openapi_schema(app)

    # Configure middleware
    configure_cors(app)
    configure_rate_limiting(app)
    configure_audit_middleware(app)

    return app
