"""Долговечная топология RabbitMQ и публикация с подтверждением брокера."""
import json

import aio_pika
from pamqp.commands import Basic

from app.core.config import settings
from app.core.observability import CORRELATION_HEADER
from app.core.order_events import DELIVERY_FAILED, ORDER_CANCELLED, ORDER_PAID

EXCHANGE_NAME = "sirius_events"
DLX_NAME = "dlx_exchange"
SUBSCRIPTIONS = {
    "kitchen": (ORDER_PAID, ORDER_CANCELLED),
    "delivery": (ORDER_PAID,),
    "payment": (DELIVERY_FAILED,),
    "loyalty": (ORDER_PAID, ORDER_CANCELLED),
    "promo": (ORDER_CANCELLED,),
}


async def connect():
    return await aio_pika.connect_robust(settings.RABBITMQ_URL, timeout=5)


async def setup_topology(channel):
    exchange = await channel.declare_exchange(EXCHANGE_NAME, aio_pika.ExchangeType.TOPIC, durable=True)
    dlx = await channel.declare_exchange(DLX_NAME, aio_pika.ExchangeType.DIRECT, durable=True)
    queues = {}
    for consumer, routing_keys in SUBSCRIPTIONS.items():
        dead_key = f"{consumer}.dlq"
        dlq = await channel.declare_queue(f"{consumer}_dlq", durable=True)
        await dlq.bind(dlx, dead_key)
        queue = await channel.declare_queue(
            f"{consumer}_queue", durable=True,
            arguments={"x-dead-letter-exchange": DLX_NAME, "x-dead-letter-routing-key": dead_key},
        )
        for key in routing_keys:
            await queue.bind(exchange, key)
        queues[consumer] = queue
    return exchange, queues


async def publish_event(exchange, routing_key: str, payload: dict, correlation_id: str):
    result = await exchange.publish(
        aio_pika.Message(
            body=json.dumps(payload).encode("utf-8"),
            message_id=payload["event_id"],
            content_type="application/json",
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            headers={CORRELATION_HEADER: correlation_id},
        ),
        routing_key=routing_key,
        mandatory=True,
        timeout=5,
    )
    if not isinstance(result, Basic.Ack):
        raise RuntimeError("RabbitMQ did not confirm the publication")
