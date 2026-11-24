"""HTTP create/pay + read-only state inspection, for labs 7 and 9."""
import argparse
import asyncio
import json
import os
from uuid import uuid4
from urllib.request import Request, urlopen

import aio_pika

from app.core.rabbitmq import connect, publish_event, setup_topology
from app.database import SessionLocal
from app.models import Delivery, KitchenOrder, Order, OutboxEvent, ProcessedEvent, User
from scripts.seed_demo import ACCOUNTS, PASSWORD

API = os.getenv("DEMO_API_URL", "http://127.0.0.1:8000")


def call(path, payload=None, *, token=None, cid=None, post=False):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if cid:
        headers["X-Correlation-ID"] = cid
    request = Request(
        API + path, data=None if payload is None else json.dumps(payload).encode(),
        headers=headers, method="POST" if post or payload is not None else "GET",
    )
    with urlopen(request, timeout=20) as response:
        if cid:
            assert response.headers["X-Correlation-ID"] == cid
        return json.load(response)


def show(order_id):
    with SessionLocal() as db:
        order = db.get(Order, order_id)
        if not order:
            raise SystemExit("Order does not exist")
        kitchen = db.get(KitchenOrder, order.id)
        delivery = db.query(Delivery).filter_by(order_id=order.id).all()
        events = []
        for row in db.query(OutboxEvent).filter(OutboxEvent.payload["order_id"].as_integer() == order_id).order_by(OutboxEvent.id):
            event_id = row.payload["event_id"]
            processed = db.query(ProcessedEvent.consumer).filter_by(event_id=event_id).order_by(ProcessedEvent.consumer)
            events.append(dict(key=row.routing_key, event_id=event_id, published=row.published_at is not None,
                               processed_by=[name for (name,) in processed], correlation_id=row.correlation_id))
        print(json.dumps(dict(
            order_id=order.id, status=order.status, charged=order.charged_amount,
            balance=db.get(User, order.user_id).balance, kitchen=kitchen.status if kitchen else None,
            deliveries=[dict(id=d.id, status=d.status, courier_id=d.courier_id) for d in delivery],
            events=events,
        ), indent=2))


async def send_demo_message(order_id):
    async with await connect() as connection:
        channel = await connection.channel(on_return_raises=True)
        exchange, _ = await setup_topology(channel)
        if order_id is None:
            await exchange.publish(aio_pika.Message(body=b"not-json"), routing_key="order.paid")
            print("Published not-json to both OrderPaid subscribers.")
            return
        with SessionLocal() as db:
            row = db.query(OutboxEvent).filter(
                OutboxEvent.payload["order_id"].as_integer() == order_id,
                OutboxEvent.routing_key == "order.paid", OutboxEvent.published_at.is_not(None),
            ).one_or_none()
            if row is None:
                raise SystemExit("Wait until order.paid has been published")
            await publish_event(exchange, row.routing_key, row.payload, row.correlation_id)
            print(f"Replayed event_id={row.payload['event_id']}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--show", type=int, help="Inspect order and outbox state")
    actions.add_argument("--replay", type=int, help="Republish the same OrderPaid event")
    actions.add_argument("--poison", action="store_true", help="Publish invalid JSON on the demo broker")
    parser.add_argument("--correlation-id", default=None)
    args = parser.parse_args()
    if args.show is not None:
        show(args.show)
    elif args.replay is not None or args.poison:
        asyncio.run(send_demo_message(args.replay))
    else:
        cid = args.correlation_id or f"demo-{uuid4().hex[:10]}"
        token = call("/auth/login", {"email": ACCOUNTS["student"], "password": PASSWORD})["access_token"]
        items = call("/menu/items")
        item = next((item for item in items if item["name"] == "\u0421\u044b\u0440\u043d\u0438\u043a\u0438" and item["is_available"]), None)
        if item is None:
            raise SystemExit("Run python -m scripts.seed_demo and make the demo dish available")
        order = call("/orders/create", {"item_id": item["id"], "quantity": 1}, token=token, cid=cid)
        payment = call(f"/orders/{order['order_id']}/pay", token=token, cid=cid, post=True)
        print(json.dumps(dict(
            order_id=order["order_id"], event_id=payment["event_id"],
            correlation_id=cid, status=payment["status"],
        )))


if __name__ == "__main__":
    main()
