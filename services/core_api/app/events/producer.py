"""
Event producer.

Publishes domain events to in-process subscribers, and to Kafka when a broker
is configured. Kafka is genuinely optional: with no broker configured the bus
runs in-process and says so, rather than spending four seconds failing to
reach a broker nobody asked for.

The singleton used to be constructed with the default bootstrap server
``localhost:9092`` and never read KAFKA_BOOTSTRAP_SERVERS at all, so the
broker configured in docker-compose was ignored and every deployment silently
fell back to in-process delivery while logging a connection error.
"""
import asyncio
import json
from typing import Any, Awaitable, Callable, Coroutine, Dict, List, Optional, Set

from shared.events import EventEnvelope
from shared.observability import logger

try:
    from aiokafka import AIOKafkaProducer
except ImportError:  # pragma: no cover - optional dependency
    AIOKafkaProducer = None


class EventProducer:
    def __init__(self, bootstrap_servers: Optional[str] = None) -> None:
        self.bootstrap_servers = bootstrap_servers
        self._producer = None
        self._local_subscribers: List[Callable[[str, Dict[str, Any]], Coroutine[Any, Any, None]]] = []
        # Tasks are retained until they finish. Without a strong reference a
        # bare asyncio.create_task is garbage-collectable mid-flight, and any
        # exception it raises is swallowed.
        self._pending: Set[asyncio.Task] = set()

    async def start(self) -> None:
        if self.bootstrap_servers is None:
            from services.core_api.app.config import settings

            self.bootstrap_servers = settings.kafka_bootstrap_servers

        if not self.bootstrap_servers:
            logger.info("event_bus_in_process", reason="no KAFKA_BOOTSTRAP_SERVERS configured")
            return
        if AIOKafkaProducer is None:
            logger.warn("event_bus_in_process", reason="aiokafka not installed")
            return

        try:
            self._producer = AIOKafkaProducer(
                bootstrap_servers=self.bootstrap_servers,
                value_serializer=lambda v: json.dumps(v).encode("utf-8"),
            )
            await self._producer.start()
            logger.info("kafka_producer_started", servers=self.bootstrap_servers)
        except Exception as exc:
            logger.warn(
                "kafka_unavailable_running_in_memory",
                servers=self.bootstrap_servers,
                error=str(exc),
            )
            self._producer = None

    def register_local_subscriber(
        self, callback: Callable[[str, Dict[str, Any]], Coroutine[Any, Any, None]]
    ) -> None:
        self._local_subscribers.append(callback)

    def _spawn(self, coro: Coroutine[Any, Any, None], topic: str) -> None:
        task = asyncio.create_task(coro)
        self._pending.add(task)

        def _done(finished: asyncio.Task) -> None:
            self._pending.discard(finished)
            if finished.cancelled():
                return
            exc = finished.exception()
            if exc is not None:
                logger.error("local_subscriber_failed", topic=topic, error=str(exc))

        task.add_done_callback(_done)

    async def publish(self, topic: str, payload: Dict[str, Any]) -> None:
        envelope = (
            payload
            if "eventType" in payload
            else EventEnvelope(eventType=topic, producer="core-api", payload=payload).to_dict()
        )

        if self._producer:
            try:
                await self._producer.send_and_wait(topic, envelope)
            except Exception as exc:
                logger.error("kafka_publish_failed", topic=topic, error=str(exc))

        for subscriber in self._local_subscribers:
            self._spawn(subscriber(topic, envelope), topic)

    async def drain(self, timeout: float = 5.0) -> None:
        """Wait for in-flight subscriber work. Used by tests and shutdown."""
        if not self._pending:
            return
        await asyncio.wait(set(self._pending), timeout=timeout)

    async def stop(self) -> None:
        await self.drain()
        if self._producer:
            try:
                await self._producer.stop()
                logger.info("kafka_producer_stopped")
            except Exception as exc:
                logger.error("kafka_stop_error", error=str(exc))


event_producer = EventProducer()
