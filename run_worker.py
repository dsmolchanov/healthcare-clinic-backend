#!/usr/bin/env python3
"""
Standalone WhatsApp Queue Worker
Runs independently from the FastAPI web server

Usage:
    python run_worker.py [instance_name]

Environment Variables:
    INSTANCE_NAME - WhatsApp instance to process (optional if passed as arg)
    REDIS_URL - Redis connection URL
    EVOLUTION_API_URL - Evolution API base URL
    EVOLUTION_API_KEY - Evolution API key
"""
import asyncio
import os
import sys
import signal
import logging
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent))

# Load environment variables from .env if it exists
env_file = Path(__file__).parent / '.env'
if env_file.exists():
    print(f"Loading environment from {env_file}")
    with open(env_file, 'r') as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                if key not in os.environ:
                    os.environ[key] = value.strip('"').strip("'")

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Import worker after environment is loaded
from app.services.whatsapp_queue import WhatsAppWorker

# Global worker instance for signal handling
worker_instance = None


def signal_handler(signum, frame):
    """Handle graceful shutdown on SIGINT/SIGTERM"""
    logger.info(f"Received signal {signum}, shutting down gracefully...")
    if worker_instance:
        asyncio.create_task(worker_instance.stop())


async def main():
    """Main entry point for standalone worker"""
    global worker_instance

    # Get instance name from command line or environment
    if len(sys.argv) > 1:
        instance_name = sys.argv[1]
    else:
        # Try to auto-detect active instance from database
        instance_name = os.getenv("INSTANCE_NAME")

        if not instance_name:
            logger.info("No INSTANCE_NAME set, attempting auto-detection from database...")
            try:
                from app.database import get_supabase_client
                supabase = get_supabase_client()

                # Get the most recent active Evolution instance
                result = supabase.table('whatsapp_instances') \
                    .select('instance_name') \
                    .eq('status', 'connected') \
                    .order('updated_at', desc=True) \
                    .limit(1) \
                    .execute()

                if result.data and len(result.data) > 0:
                    instance_name = result.data[0]['instance_name']
                    logger.info(f"✅ Auto-detected instance: {instance_name}")
                else:
                    # Fallback to hardcoded default
                    instance_name = "clinic-4e8ddba1-ad52-4613-9a03-ec64636b3f6c-1759348170665"
                    logger.warning(f"No active instances found, using fallback: {instance_name}")
            except Exception as e:
                logger.error(f"Failed to auto-detect instance: {e}")
                # Fallback to hardcoded default
                instance_name = "clinic-4e8ddba1-ad52-4613-9a03-ec64636b3f6c-1759348170665"
                logger.warning(f"Using fallback instance: {instance_name}")

    logger.info("="*80)
    logger.info("WhatsApp Queue Worker Starting")
    logger.info("="*80)
    logger.info(f"Instance: {instance_name}")
    # Only log protocol and host, not credentials
    redis_url = os.getenv('REDIS_URL', 'NOT SET')
    if redis_url == 'NOT SET':
        logger.error("Redis URL: NOT SET")
    else:
        # Strip credentials: redis://user:pass@host:port → host:port
        redis_info = redis_url.split('@')[-1] if '@' in redis_url else 'configured'
        logger.info(f"Redis URL: {redis_info}")
    logger.info(f"Evolution API: {os.getenv('EVOLUTION_SERVER_URL', 'NOT SET')}")
    logger.info(f"Evolution API Key: {'SET' if os.getenv('EVOLUTION_API_KEY') else 'NOT SET'}")
    logger.info("="*80)

    # Validate required environment variables
    required_vars = ['REDIS_URL', 'EVOLUTION_SERVER_URL', 'EVOLUTION_API_KEY']
    missing_vars = [var for var in required_vars if not os.getenv(var)]

    if missing_vars:
        logger.error(f"❌ Missing required environment variables: {', '.join(missing_vars)}")
        logger.error("Set these in .env file or environment before starting worker")
        logger.error("Available env vars: " + ", ".join(sorted([k for k in os.environ.keys() if not k.startswith('_')])))
        sys.exit(1)

    logger.info("✅ All required environment variables are set")

    try:
        # Create and start worker
        logger.info("Initializing worker...")
        logger.info(f"Python path: {sys.path}")
        logger.info(f"Working directory: {os.getcwd()}")

        try:
            worker = WhatsAppWorker(instance=instance_name)
            worker_instance = worker
            logger.info("✅ Worker object created successfully")
        except Exception as e:
            logger.error(f"❌ Failed to create WhatsAppWorker: {e}", exc_info=True)
            sys.exit(1)

        # Register signal handlers for graceful shutdown
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

        logger.info("✅ Worker initialized successfully")
        logger.info(f"Consumer name: {worker.consumer_name}")
        logger.info("Starting worker loop (press Ctrl+C to stop)...")
        logger.info("="*80)

        # Run worker (blocks until stopped)
        try:
            await worker.run()
        except Exception as e:
            logger.error(f"❌ Worker loop crashed: {e}", exc_info=True)
            raise

    except KeyboardInterrupt:
        logger.info("\n📛 Keyboard interrupt received")
    except Exception as e:
        logger.error(f"❌ Worker error: {e}", exc_info=True)
        sys.exit(1)
    finally:
        if worker_instance:
            logger.info("Stopping worker...")
            await worker_instance.stop()
            logger.info("✅ Worker stopped cleanly")
            logger.info(f"Final stats - Processed: {worker_instance.processed_count}, Failed: {worker_instance.failed_count}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Worker terminated by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)