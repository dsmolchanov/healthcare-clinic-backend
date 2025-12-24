"""
Centralized Logging Configuration

Provides consistent logging setup across the application.
When running in containers (Docker/Kubernetes/Fly.io), timestamps are omitted
from the Python log formatter since container runtimes add their own timestamps.

Usage:
    from app.utils.logging_config import configure_logging
    configure_logging()
"""
import os
import sys
import logging

# Detect container environment
IS_CONTAINERIZED = bool(
    os.environ.get('FLY_APP_NAME') or  # Fly.io
    os.environ.get('KUBERNETES_SERVICE_HOST') or  # Kubernetes
    os.path.exists('/.dockerenv')  # Docker
)

# Log format without timestamp for containers (runtime adds it)
CONTAINER_FORMAT = "[%(name)s] %(levelname)s: %(message)s"

# Log format with timestamp for local development
LOCAL_FORMAT = "[%(asctime)s] [%(name)s] %(levelname)s: %(message)s"

# Date format for local development
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def configure_logging(level: int = logging.INFO, force: bool = False) -> None:
    """
    Configure logging for the application.

    Args:
        level: Logging level (default: INFO)
        force: Force reconfiguration even if already configured
    """
    root_logger = logging.getLogger()

    # Avoid reconfiguring if already set up (unless forced)
    if root_logger.handlers and not force:
        return

    # Clear existing handlers if forcing reconfiguration
    if force:
        for handler in root_logger.handlers[:]:
            root_logger.removeHandler(handler)

    # Choose format based on environment
    log_format = CONTAINER_FORMAT if IS_CONTAINERIZED else LOCAL_FORMAT
    datefmt = None if IS_CONTAINERIZED else DATE_FORMAT

    # Create handler
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(level)

    # Create formatter
    formatter = logging.Formatter(log_format, datefmt=datefmt)
    handler.setFormatter(formatter)

    # Configure root logger
    root_logger.setLevel(level)
    root_logger.addHandler(handler)

    # Suppress noisy third-party loggers
    logging.getLogger('httpx').setLevel(logging.WARNING)
    logging.getLogger('httpcore').setLevel(logging.WARNING)
    logging.getLogger('hpack').setLevel(logging.WARNING)
    logging.getLogger('googleapiclient.discovery').setLevel(logging.WARNING)
    logging.getLogger('google.auth.transport.requests').setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """
    Get a logger with the given name.

    Args:
        name: Logger name (typically __name__)

    Returns:
        Configured logger instance
    """
    return logging.getLogger(name)
