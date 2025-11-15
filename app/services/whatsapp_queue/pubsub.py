"""
Redis Pub/Sub for Worker Instance Notifications

Enables push-based instance discovery for multi-instance worker.
When integrations are created/disabled, notifications are published
to Redis channels that workers subscribe to.
"""
import json
from typing import Callable, Optional
from redis import Redis

from .config import (
    INSTANCE_ADDED_CHANNEL,
    INSTANCE_REMOVED_CHANNEL,
    logger
)
from .queue import get_redis_client


class InstanceNotifier:
    """Publish instance change events to workers"""

    def __init__(self, redis_client: Optional[Redis] = None):
        """
        Initialize instance notifier

        Args:
            redis_client: Optional Redis client (creates new if not provided)
        """
        self.redis = redis_client or get_redis_client()

    def notify_added(self, instance_name: str, organization_id: str):
        """
        Notify workers that a new instance was added

        Args:
            instance_name: WhatsApp instance name
            organization_id: Organization UUID
        """
        payload = json.dumps({
            "instance_name": instance_name,
            "organization_id": organization_id,
            "action": "added"
        })

        try:
            subscribers = self.redis.publish(INSTANCE_ADDED_CHANNEL, payload)
            logger.info(f"📢 Notified {subscribers} worker(s) about new instance: {instance_name}")
        except Exception as e:
            logger.error(f"Failed to publish instance added notification: {e}")

    def notify_removed(self, instance_name: str, organization_id: str):
        """
        Notify workers that an instance was removed/disabled

        Args:
            instance_name: WhatsApp instance name
            organization_id: Organization UUID
        """
        payload = json.dumps({
            "instance_name": instance_name,
            "organization_id": organization_id,
            "action": "removed"
        })

        try:
            subscribers = self.redis.publish(INSTANCE_REMOVED_CHANNEL, payload)
            logger.info(f"📢 Notified {subscribers} worker(s) about removed instance: {instance_name}")
        except Exception as e:
            logger.error(f"Failed to publish instance removed notification: {e}")


class InstanceSubscriber:
    """Subscribe to instance change events"""

    def __init__(
        self,
        on_added: Callable[[str, str], None],
        on_removed: Callable[[str, str], None],
        redis_client: Optional[Redis] = None
    ):
        """
        Initialize subscriber

        Args:
            on_added: Callback for instance added (instance_name, org_id)
            on_removed: Callback for instance removed (instance_name, org_id)
            redis_client: Optional Redis client
        """
        self.redis = redis_client or get_redis_client()
        self.on_added = on_added
        self.on_removed = on_removed
        self.pubsub = None
        self.running = False

    def start(self):
        """
        Start listening for instance changes (blocking call)

        This method blocks until stop() is called. Should be run in a
        separate thread from the main worker loop.
        """
        self.pubsub = self.redis.pubsub()
        self.pubsub.subscribe(INSTANCE_ADDED_CHANNEL, INSTANCE_REMOVED_CHANNEL)
        self.running = True

        logger.info(f"🎧 Subscribed to instance change notifications")

        for message in self.pubsub.listen():
            if not self.running:
                break

            # Skip non-message types (subscribe confirmations, etc.)
            if message['type'] != 'message':
                continue

            try:
                # Parse JSON payload
                data = json.loads(message['data'])
                instance_name = data['instance_name']
                org_id = data['organization_id']

                # Route to appropriate callback based on channel
                channel = message['channel']
                if isinstance(channel, bytes):
                    channel = channel.decode('utf-8')

                if channel == INSTANCE_ADDED_CHANNEL:
                    logger.debug(f"Received instance added: {instance_name}")
                    self.on_added(instance_name, org_id)
                elif channel == INSTANCE_REMOVED_CHANNEL:
                    logger.debug(f"Received instance removed: {instance_name}")
                    self.on_removed(instance_name, org_id)

            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse instance notification: {e}")
            except KeyError as e:
                logger.error(f"Missing required field in notification: {e}")
            except Exception as e:
                logger.error(f"Error processing instance notification: {e}", exc_info=True)

    def stop(self):
        """Stop listening and close subscription"""
        logger.info("Stopping instance subscriber...")
        self.running = False

        if self.pubsub:
            try:
                self.pubsub.unsubscribe()
                self.pubsub.close()
                logger.info("✅ Instance subscriber stopped")
            except Exception as e:
                logger.error(f"Error stopping subscriber: {e}")
