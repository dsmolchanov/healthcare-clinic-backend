"""Main entry point for the webhook server"""
import logging
import sys
import os
from pathlib import Path

# Load environment variables from .env file if it exists
env_file = Path(__file__).parent / '.env'
if env_file.exists():
    with open(env_file, 'r') as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                # Only set if not already in environment
                if key not in os.environ:
                    os.environ[key] = value.strip('"').strip("'")

# Configure logging before imports
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

logger.info("Starting webhook server...")
logger.info(f"Python version: {sys.version}")
logger.info(f"Working directory: {os.getcwd()}")
# Log if database URL is set (without exposing the actual URL)
if os.getenv('SUPABASE_DB_URL'):
    logger.info("SUPABASE_DB_URL is configured")
else:
    logger.warning("SUPABASE_DB_URL is not set")

try:
    # Import the correct app that uses RPC version
    from app.main import app
    logger.info("Successfully imported FastAPI app from app.main")
except Exception as e:
    logger.error(f"Failed to import app: {e}")
    raise

# Expose the app for uvicorn
__all__ = ['app']

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8080))
    logger.info(f"Starting server on port {port}")
    uvicorn.run(app, host="0.0.0.0", port=port)
