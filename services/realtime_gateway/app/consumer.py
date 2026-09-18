"""
Real-Time Gateway Event Consumer (Phase 6).
Listens to Kafka event topics or internal queue and broadcasts incoming events to WebSockets.
"""
import json
import asyncio
from typing import Callable, Awaitable
from shared.observability import logger

try:
    from aiokafka import AIOKafkaConsumer
except ImportError:
    AIOKafkaConsumer = None

ALL_TOPICS = [
    "breakdown.detected",
    "match.proposed",
    "match.accepted",
    "escrow.state_changed",
    "truck.updated",
    "incident.breakdown_reported",
    "incident.triaging",
    "incident.matching",
    "incident.rescue_offered",
    "incident.rescue_accepted",
    "incident.driver_en_route",
    "incident.cargo_transfer",
    "incident.rescue_in_transit",
    "incident.delivered",
    "incident.verification",
    "incident.escrow_released",
    "incident.disputed",
    "incident.cancelled",
]


async def consume_all_events(
    bootstrap_servers: str,
    broadcast_callback: Callable[[dict], Awaitable[None]],
):
    if not AIOKafkaConsumer:
        logger.warn("aiokafka_not_available_realtime_gateway_using_local_bus")
        return

    consumer = AIOKafkaConsumer(
        *ALL_TOPICS,
        bootstrap_servers=bootstrap_servers,
        value_deserializer=lambda v: json.loads(v.decode("utf-8")),
        group_id="realtime-gateway-group",
        auto_offset_reset="latest",
    )
    await consumer.start()
    logger.info("realtime_gateway_consumer_started")
    try:
        async for msg in consumer:
            logger.info("broadcasting_event", topic=msg.topic)
            await broadcast_callback({"type": msg.topic, "payload": msg.value})
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.error("realtime_consumer_error", error=str(e))
    finally:
        await consumer.stop()
