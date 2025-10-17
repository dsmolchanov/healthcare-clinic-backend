"""
Graceful Shutdown Handler

Ensures clean shutdown of services during deployments:
- Stops accepting new requests
- Completes in-flight requests
- Closes database connections
- Flushes logs and metrics
- Releases resources

Prevents mid-conversation disruptions during deployments.
"""

import signal
import logging
import asyncio
import sys
from typing import List, Callable, Optional, Awaitable
from datetime import datetime

logger = logging.getLogger(__name__)


class GracefulShutdownHandler:
    """
    Handles graceful shutdown of services

    Usage:
        shutdown_handler = GracefulShutdownHandler(
            shutdown_timeout=30,  # Max time to wait for shutdown
            service_name="WhatsApp Worker"
        )

        # Register cleanup functions
        shutdown_handler.register(worker.stop)
        shutdown_handler.register(redis.close)
        shutdown_handler.register(db.disconnect)

        # Start listening for signals
        shutdown_handler.setup()

        # In your main loop
        while not shutdown_handler.should_shutdown():
            await do_work()
    """

    def __init__(self, shutdown_timeout: int = 30, service_name: str = "Service"):
        self.shutdown_timeout = shutdown_timeout
        self.service_name = service_name
        self.shutdown_requested = False
        self.cleanup_functions: List[Callable] = []
        self.async_cleanup_functions: List[Callable[[], Awaitable[None]]] = []
        self.shutdown_start_time: Optional[datetime] = None

    def register(self, cleanup_fn: Callable):
        """
        Register a cleanup function to be called on shutdown

        Args:
            cleanup_fn: Synchronous cleanup function
        """
        self.cleanup_functions.append(cleanup_fn)
        logger.debug(f"Registered cleanup function: {cleanup_fn.__name__}")

    def register_async(self, cleanup_fn: Callable[[], Awaitable[None]]):
        """
        Register an async cleanup function to be called on shutdown

        Args:
            cleanup_fn: Asynchronous cleanup function
        """
        self.async_cleanup_functions.append(cleanup_fn)
        logger.debug(f"Registered async cleanup function: {cleanup_fn.__name__}")

    def setup(self):
        """
        Setup signal handlers for graceful shutdown

        Listens for:
        - SIGTERM (deployment, Docker stop)
        - SIGINT (Ctrl+C)
        - SIGHUP (reload config)
        """
        signal.signal(signal.SIGTERM, self._handle_signal)
        signal.signal(signal.SIGINT, self._handle_signal)
        signal.signal(signal.SIGHUP, self._handle_signal)

        logger.info(f"✅ {self.service_name}: Graceful shutdown handler configured")

    def _handle_signal(self, signum, frame):
        """Handle shutdown signals"""
        signal_name = signal.Signals(signum).name
        logger.warning(f"🛑 {self.service_name}: Received {signal_name}, initiating graceful shutdown...")

        self.shutdown_requested = True
        self.shutdown_start_time = datetime.utcnow()

        # Run cleanup in async context if available
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # Schedule cleanup as a task
                loop.create_task(self._execute_cleanup())
            else:
                # Run synchronous cleanup only
                self._execute_sync_cleanup()
                sys.exit(0)
        except RuntimeError:
            # No event loop available, run sync cleanup
            self._execute_sync_cleanup()
            sys.exit(0)

    def should_shutdown(self) -> bool:
        """Check if shutdown has been requested"""
        return self.shutdown_requested

    def _execute_sync_cleanup(self):
        """Execute synchronous cleanup functions"""
        logger.info(f"🧹 {self.service_name}: Running synchronous cleanup ({len(self.cleanup_functions)} functions)...")

        for cleanup_fn in self.cleanup_functions:
            try:
                logger.debug(f"Calling cleanup: {cleanup_fn.__name__}")
                cleanup_fn()
                logger.debug(f"✅ Completed: {cleanup_fn.__name__}")
            except Exception as e:
                logger.error(f"❌ Cleanup failed for {cleanup_fn.__name__}: {e}")

        logger.info(f"✅ {self.service_name}: Synchronous cleanup complete")

    async def _execute_cleanup(self):
        """Execute all cleanup functions (sync and async)"""
        logger.info(
            f"🧹 {self.service_name}: Running cleanup "
            f"({len(self.cleanup_functions)} sync, {len(self.async_cleanup_functions)} async)..."
        )

        # Run sync cleanup first
        self._execute_sync_cleanup()

        # Run async cleanup with timeout
        if self.async_cleanup_functions:
            try:
                await asyncio.wait_for(
                    self._run_async_cleanup(),
                    timeout=self.shutdown_timeout
                )
            except asyncio.TimeoutError:
                logger.error(
                    f"⏱️ {self.service_name}: Async cleanup timed out after {self.shutdown_timeout}s"
                )

        # Calculate shutdown time
        if self.shutdown_start_time:
            shutdown_duration = (datetime.utcnow() - self.shutdown_start_time).total_seconds()
            logger.info(f"✅ {self.service_name}: Graceful shutdown complete in {shutdown_duration:.2f}s")

        # Exit gracefully
        sys.exit(0)

    async def _run_async_cleanup(self):
        """Run all async cleanup functions"""
        for cleanup_fn in self.async_cleanup_functions:
            try:
                logger.debug(f"Calling async cleanup: {cleanup_fn.__name__}")
                await cleanup_fn()
                logger.debug(f"✅ Completed: {cleanup_fn.__name__}")
            except Exception as e:
                logger.error(f"❌ Async cleanup failed for {cleanup_fn.__name__}: {e}")


class RequestDrainHandler:
    """
    Handles draining of in-flight requests during shutdown

    Usage:
        drain_handler = RequestDrainHandler(max_drain_time=20)

        # In request handler
        if not drain_handler.can_accept_requests():
            return {"error": "Service shutting down"}

        with drain_handler.track_request():
            await process_request()
    """

    def __init__(self, max_drain_time: int = 20):
        self.max_drain_time = max_drain_time
        self.draining = False
        self.drain_start_time: Optional[datetime] = None
        self.active_requests = 0

    def start_draining(self):
        """Start draining mode - stop accepting new requests"""
        self.draining = True
        self.drain_start_time = datetime.utcnow()
        logger.warning(f"🚫 Started request draining (timeout: {self.max_drain_time}s)")

    def can_accept_requests(self) -> bool:
        """Check if service can accept new requests"""
        return not self.draining

    async def wait_for_completion(self):
        """Wait for all in-flight requests to complete"""
        if not self.draining:
            return

        logger.info(f"⏳ Waiting for {self.active_requests} in-flight requests to complete...")

        start_time = datetime.utcnow()

        while self.active_requests > 0:
            # Check timeout
            elapsed = (datetime.utcnow() - start_time).total_seconds()
            if elapsed > self.max_drain_time:
                logger.warning(
                    f"⏱️ Request drain timeout after {self.max_drain_time}s, "
                    f"{self.active_requests} requests still active"
                )
                break

            # Wait briefly
            await asyncio.sleep(0.5)

        if self.active_requests == 0:
            drain_duration = (datetime.utcnow() - start_time).total_seconds()
            logger.info(f"✅ All requests completed in {drain_duration:.2f}s")
        else:
            logger.warning(f"⚠️ Forcing shutdown with {self.active_requests} active requests")

    class RequestContext:
        """Context manager for tracking active requests"""

        def __init__(self, handler: 'RequestDrainHandler'):
            self.handler = handler

        def __enter__(self):
            self.handler.active_requests += 1
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            self.handler.active_requests -= 1

    def track_request(self):
        """Track an active request"""
        return self.RequestContext(self)


# Global instances for easy access
_shutdown_handler: Optional[GracefulShutdownHandler] = None
_drain_handler: Optional[RequestDrainHandler] = None


def get_shutdown_handler(
    shutdown_timeout: int = 30,
    service_name: str = "Service"
) -> GracefulShutdownHandler:
    """Get or create global shutdown handler"""
    global _shutdown_handler
    if _shutdown_handler is None:
        _shutdown_handler = GracefulShutdownHandler(shutdown_timeout, service_name)
    return _shutdown_handler


def get_drain_handler(max_drain_time: int = 20) -> RequestDrainHandler:
    """Get or create global drain handler"""
    global _drain_handler
    if _drain_handler is None:
        _drain_handler = RequestDrainHandler(max_drain_time)
    return _drain_handler
