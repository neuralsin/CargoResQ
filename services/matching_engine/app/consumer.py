"""
Matching Engine Event Consumer (Phase 2.3).
Listens for 'breakdown.detected' events off Kafka (or internal stream)
and triggers candidate discovery without direct synchronous HTTP coupling.
"""
import json
import asyncio
from typing import Callable, Awaitable
from shared.observability import logger

try:
    from aiokafka import AIOKafkaConsumer
except ImportError:
    AIOKafkaConsumer = None


async def consume_breakdowns(
    bootstrap_servers: str,
    on_event: Callable[[dict], Awaitable[None]],
    topic: str = "breakdown.detected",
):
    """
    Subscribes to breakdown events and passes payload to on_event callback.
    """
    if not AIOKafkaConsumer:
        logger.warn("aiokafka_not_available_using_mock_consumer")
        return

    consumer = AIOKafkaConsumer(
        topic,
        bootstrap_servers=bootstrap_servers,
        value_deserializer=lambda v: json.loads(v.decode("utf-8")),
        group_id="matching-engine-group",
        auto_offset_reset="latest",
    )
    await consumer.start()
    logger.info("matching_engine_consumer_started", topic=topic)
    try:
        async for msg in consumer:
            logger.info("received_breakdown_event", offset=msg.offset)
            await on_event(msg.value)
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.error("consumer_error", error=str(e))
    finally:
        await consumer.stop()
        logger.info("matching_engine_consumer_stopped")
