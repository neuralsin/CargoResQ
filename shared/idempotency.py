"""
Idempotent Event Processing (Phase 18.1).
Guarantees deduplication across Kafka at-least-once deliveries and repeated client requests.
"""
from datetime import datetime
from typing import Callable, Awaitable, Any
from sqlalchemy import text, String, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.ext.asyncio import AsyncSession
from shared.database import Base


class ProcessedEvent(Base):
    __tablename__ = "processed_events"

    event_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    consumer: Mapped[str] = mapped_column(String(128), primary_key=True)
    processed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


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
