"""
Idempotent Event Processing (Phase 18.1).
Guarantees deduplication across Kafka at-least-once deliveries and repeated client requests.
"""
from typing import Callable, Awaitable, Any
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


IDEMPOTENCY_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS processed_events (
    event_id VARCHAR(128) NOT NULL,
    consumer VARCHAR(128) NOT NULL,
    processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (event_id, consumer)
);
"""


async def init_idempotency_table(session: AsyncSession) -> None:
    """Ensure the processed_events table exists."""
    await session.execute(text(IDEMPOTENCY_TABLE_SQL))
    await session.commit()


async def process_once(
    session: AsyncSession,
    event_id: str,
    consumer_name: str,
    handler: Callable[[], Awaitable[Any]],
) -> bool:
    """
    Executes handler only if (event_id, consumer_name) has not been processed before.
    Returns True if handled, False if skipped as a duplicate.
    """
    exists = await session.execute(
        text("SELECT 1 FROM processed_events WHERE event_id=:e AND consumer=:c"),
        {"e": event_id, "c": consumer_name},
    )
    if exists.first():
        return False  # already handled, ignore duplicate delivery

    await handler()

    await session.execute(
        text(
            "INSERT INTO processed_events (event_id, consumer, processed_at) "
            "VALUES (:e, :c, CURRENT_TIMESTAMP)"
        ),
        {"e": event_id, "c": consumer_name},
    )
    await session.commit()
    return True
