"""
Event Producer for Core API (Phase 2.2).
Publishes state changes to Kafka and local asynchronous subscribers.
"""
import json
import asyncio
from typing import Dict, Any, List, Callable, Awaitable
from shared.events import EventEnvelope
from shared.observability import logger

try:
    from aiokafka import AIOKafkaProducer
except ImportError:
    AIOKafkaProducer = None


class EventProducer:
    def __init__(self, bootstrap_servers: str = "localhost:9092"):
        self.bootstrap_servers = bootstrap_servers
        self._producer = None
        self._local_subscribers: List[Callable[[str, Dict[str, Any]], Awaitable[None]]] = []

    async def start(self):
        if AIOKafkaProducer:
            try:
                self._producer = AIOKafkaProducer(
                    bootstrap_servers=self.bootstrap_servers,
                    value_serializer=lambda v: json.dumps(v).encode("utf-8"),
                )
                await self._producer.start()
                logger.info("kafka_producer_started", servers=self.bootstrap_servers)
            except Exception as e:
                logger.warn("kafka_unavailable_running_in_memory", error=str(e))
                self._producer = None
        else:
            logger.info("aiokafka_not_installed_using_local_bus")

    def register_local_subscriber(
        self, callback: Callable[[str, Dict[str, Any]], Awaitable[None]]
    ):
        """Allows internal decoupled services to react to events in single-process or test mode."""
        self._local_subscribers.append(callback)

    async def publish(self, topic: str, payload: Dict[str, Any]):
        envelope = (
            payload
            if "eventType" in payload
            else EventEnvelope(eventType=topic, producer="core-api", payload=payload).to_dict()
        )

        # 1. Publish to Kafka if connected
        if self._producer:
            try:
                await self._producer.send_and_wait(topic, envelope)
                logger.info("event_published_kafka", topic=topic)
            except Exception as e:
                logger.error("kafka_publish_failed", topic=topic, error=str(e))

        # 2. Dispatch to local subscribers
        for sub in self._local_subscribers:
            try:
                asyncio.create_task(sub(topic, envelope))
            except Exception as e:
                logger.error("local_subscriber_error", topic=topic, error=str(e))

    async def stop(self):
        if self._producer:
            try:
                await self._producer.stop()
                logger.info("kafka_producer_stopped")
            except Exception as e:
                logger.error("kafka_stop_error", error=str(e))


event_producer = EventProducer()
