"""Побочные эффекты и ключ идемпотентности фиксируются одной транзакцией."""
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.core.order_events import DELIVERY_FAILED, ORDER_CANCELLED, OrderEvent, new_event
from app.database import SessionLocal
from app.models import Delivery, KitchenOrder, Order, OutboxEvent, ProcessedEvent, Role, User
from app.modules.loyalty.repository import LoyaltyRepository
from app.modules.loyalty.service import LoyaltyService, money


def _stage(db, key, order, cid):
    db.add(OutboxEvent(
        routing_key=key, correlation_id=cid,
        payload=new_event(order.id, order.user_id, order.charged_amount),
    ))


def _kitchen(db, order, key):
    entry = db.get(KitchenOrder, order.id)
    if key == ORDER_CANCELLED:
        if entry:
            entry.status = "cancelled"
        return "kitchen_cancelled", {}
    if order.status != "paid" or entry:
        return "event_ignored", {}
    db.add(KitchenOrder(order_id=order.id, status="pending"))
    return "order_received", {}


def _delivery(db, order, cid):
    if order.status != "paid" or db.query(Delivery.id).filter_by(order_id=order.id).first():
        return "event_ignored", {}
    courier_ids = db.scalars(
        select(User.id).join(User.role).where(Role.name == "courier", User.is_blocked.is_(False))
        .order_by(User.id)
    ).all()
    for courier_id in courier_ids:
        courier = db.scalar(select(User).where(User.id == courier_id).with_for_update())
        if not courier or courier.is_blocked or courier.role.name != "courier":
            continue
        active = db.query(Delivery.id).filter_by(courier_id=courier_id, status="in_progress").first()
        if not active:
            db.add(Delivery(order_id=order.id, courier_id=courier_id, status="in_progress"))
            return "delivery_assigned", {"courier_id": courier_id}
    _stage(db, DELIVERY_FAILED, order, cid)
    return "delivery_failed", {"reason": "no_available_courier"}


def _refund(db, order, cid):
    if order.status != "paid" or db.query(Delivery.id).filter_by(order_id=order.id).first():
        return "event_ignored", {}
    customer = db.scalar(select(User).where(User.id == order.user_id).with_for_update())
    if order.payment_method == "external":
        # Возврат через внешний шлюз относится к расширению трека D на 10.
        # Нельзя компенсировать внешнюю оплату деньгами внутреннего баланса.
        return "external_refund_required", {"order_id": order.id}
    customer.balance = float(money(customer.balance) + money(order.charged_amount))
    order.status = "cancelled"
    _stage(db, ORDER_CANCELLED, order, cid)
    return "order_refunded", {"amount": order.charged_amount, "user_id": customer.id}


def handle(consumer: str, key: str, event: OrderEvent, cid: str):
    with SessionLocal.begin() as db:
        claimed = db.execute(
            insert(ProcessedEvent).values(consumer=consumer, event_id=str(event.event_id))
            .on_conflict_do_nothing().returning(ProcessedEvent.event_id)
        ).scalar_one_or_none()
        if claimed is None:
            return "duplicate_ignored", {}
        order = db.scalar(select(Order).where(Order.id == event.order_id).with_for_update())
        if (order is None or order.user_id != event.customer_id
                or order.charged_amount is None or money(order.charged_amount) != money(event.amount)):
            raise ValueError("event does not match a stored order charge")
        if consumer == "kitchen":
            return _kitchen(db, order, key)
        if consumer == "delivery":
            return _delivery(db, order, cid)
        if consumer == "payment":
            return _refund(db, order, cid)
        if consumer == "loyalty":
            service = LoyaltyService(LoyaltyRepository(db))
            if key == ORDER_CANCELLED and order.status == "cancelled":
                service.compensate(order)
            elif key != ORDER_CANCELLED:
                service.earn(order)
            return "loyalty_processed", {}
        if consumer == "promo":
            if order.status == "cancelled":
                LoyaltyRepository(db).restore_promo(order)
            return "promo_restored", {}
        raise ValueError(f"unknown consumer: {consumer}")
