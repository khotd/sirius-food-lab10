"""Подтверждаем сообщение только после успешной транзакции."""
import asyncio

from sqlalchemy.exc import OperationalError

from app.core.observability import CORRELATION_HEADER, correlation_id, log, setup_logging
from app.core.order_events import OrderEvent
from app.core.rabbitmq import SUBSCRIPTIONS, connect, setup_topology
from app.workers.handlers import handle


async def run_consumer(name: str):
    setup_logging(name)

    async def process(message):
        cid = correlation_id((message.headers or {}).get(CORRELATION_HEADER))
        fields = {"correlation_id": cid, "routing_key": message.routing_key}
        try:
            if message.routing_key not in SUBSCRIPTIONS[name]:
                raise ValueError("unexpected routing key")
            event = OrderEvent.model_validate_json(message.body)
            fields.update(event_id=str(event.event_id), order_id=event.order_id)
            action, details = await asyncio.to_thread(handle, name, message.routing_key, event, cid)
        except ValueError as error:
            log.warning("message_rejected", reason=str(error)[:400], **fields)
            await message.reject(requeue=False)
        except OperationalError:
            log.exception("database_unavailable", **fields)
            await asyncio.sleep(2)
            await message.nack(requeue=True)
        except Exception:
            log.exception("message_failed", **fields)
            await message.reject(requeue=False)
        else:
            log.info(action, **fields, **details)
            await message.ack()

    async with await connect() as connection:
        channel = await connection.channel()
        await channel.set_qos(prefetch_count=1)
        _, queues = await setup_topology(channel)
        await queues[name].consume(process)
        log.info("consumer_ready", queue=f"{name}_queue")
        await asyncio.Future()


def main(name: str):
    try:
        asyncio.run(run_consumer(name))
    except KeyboardInterrupt:
        pass
