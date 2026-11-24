"""Доставляет события из PostgreSQL в RabbitMQ, повторяя попытки при сбое."""
import asyncio
from datetime import datetime, timezone

from sqlalchemy import select

from app.core.observability import log, setup_logging
from app.core.rabbitmq import connect, publish_event, setup_topology
from app.database import SessionLocal
from app.models import OutboxEvent


async def publish_next(exchange):
    with SessionLocal.begin() as db:
        event = db.scalar(
            select(OutboxEvent).where(OutboxEvent.published_at.is_(None))
            .order_by(OutboxEvent.id).limit(1).with_for_update(skip_locked=True)
        )
        if event is None:
            return False
        await publish_event(exchange, event.routing_key, event.payload, event.correlation_id)
        event.published_at = datetime.now(timezone.utc)
        fields = dict(routing_key=event.routing_key, correlation_id=event.correlation_id,
                      event_id=event.payload["event_id"], order_id=event.payload["order_id"])
    log.info("event_published", **fields)
    return True


async def run():
    while True:
        try:
            async with await connect() as connection:
                channel = await connection.channel(publisher_confirms=True, on_return_raises=True)
                exchange, _ = await setup_topology(channel)
                log.info("outbox_ready")
                while True:
                    if not await publish_next(exchange):
                        await asyncio.sleep(0.5)
        except Exception:
            log.exception("outbox_retry", retry_seconds=3)
            await asyncio.sleep(3)


if __name__ == "__main__":
    setup_logging("outbox")
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass
