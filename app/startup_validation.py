"""
Startup validation for required environment variables.
Fails fast if critical configuration is missing or insecure.

Uses Pydantic Settings for centralized, testable validation.
"""
import os
import sys
import logging
from pydantic import field_validator
from pydantic_settings import BaseSettings

logger = logging.getLogger(__name__)

# Minimum entropy for security keys (32 hex chars = 128 bits)
MIN_SECRET_LENGTH = 32


class EnvironmentSettings(BaseSettings):
    """Validated environment configuration."""

    # Required secrets
    SUPABASE_URL: str
    SUPABASE_SERVICE_ROLE_KEY: str
    OPENAI_API_KEY: str

    # Encryption keys (no defaults allowed)
    MASTER_ENCRYPTION_SECRET: str
    ENCRYPTION_SALT: str

    # JWT (required for auth)
    JWT_SECRET: str = ""  # Optional initially, required in Phase 5

    # Redis (required for sessions/cache)
    REDIS_URL: str = "redis://localhost:6379"

    # Environment indicator
    ENVIRONMENT: str = "development"

    @field_validator('MASTER_ENCRYPTION_SECRET', 'ENCRYPTION_SALT')
    @classmethod
    def validate_secret_strength(cls, v: str, info) -> str:
        """Ensure secrets have minimum entropy."""
        if v in ("change-this-in-production", "change-this-salt", ""):
            raise ValueError(
                f"{info.field_name} cannot use insecure default. "
                f"Generate with: python -c \"import secrets; print(secrets.token_hex(32))\""
            )
        if len(v) < MIN_SECRET_LENGTH:
            raise ValueError(
                f"{info.field_name} must be at least {MIN_SECRET_LENGTH} chars for security"
            )
        return v

    @field_validator('REDIS_URL')
    @classmethod
    def validate_redis_not_localhost_in_prod(cls, v: str, info) -> str:
        """Ensure Redis is not localhost in production."""
        env = os.getenv("ENVIRONMENT", "development")
        if "localhost" in v and env == "production":
            raise ValueError("REDIS_URL cannot point to localhost in production")
        return v

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",  # Allow extra env vars without validation errors
    }


def validate_environment() -> bool:
    """
    Validate environment configuration at startup.
    Returns True if valid, logs errors and returns False otherwise.

    Note: Never log actual secret values, only variable names.
    """
    try:
        settings = EnvironmentSettings()
        logger.info("Environment validation passed")
        return True
    except Exception as e:
        logger.error(f"Startup validation failed: {e}")
        return False


def validate_or_exit():
    """Validate environment or exit with error code."""
    if not validate_environment():
        logger.critical("Application cannot start with invalid configuration")
        sys.exit(1)


# Singleton settings instance for use throughout the app
_settings: EnvironmentSettings | None = None


def get_settings() -> EnvironmentSettings:
    """Get validated settings singleton."""
    global _settings
    if _settings is None:
        _settings = EnvironmentSettings()
    return _settings
