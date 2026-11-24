from app.models import Order, Payment, PaymentWebhook, OutboxEvent
from app.core.order_events import new_event


class PaymentRepository:
    def __init__(self, db):
        self.db = db

    def order(self, order_id):
        return self.db.query(Order).filter_by(id=order_id).with_for_update().populate_existing().first()

    def by_key(self, key):
        return self.db.query(Payment).filter_by(idempotency_key=key).first()

    def by_order(self, order_id):
        return self.db.query(Payment).filter_by(order_id=order_id).populate_existing().first()

    def new(self, order, amount, currency, key):
        payment = Payment(order_id=order.id, user_id=order.user_id, amount=amount,
                          currency=currency, idempotency_key=key, status="pending")
        self.db.add(payment)
        self.db.flush()
        return payment

    def event_seen(self, event_id):
        return self.db.get(PaymentWebhook, event_id) is not None

    def gateway_payment(self, gateway_id, key):
        payment = self.db.query(Payment).filter_by(gateway_payment_id=gateway_id).first()
        # Ответ мог потеряться после фактического создания PaymentIntent.
        return payment or (self.by_key(key) if key else None)

    def remember(self, event_id, payment):
        self.db.add(PaymentWebhook(event_id=event_id, payment_id=payment.id))

    def stage(self, order, routing_key, cid):
        self.db.add(OutboxEvent(routing_key=routing_key, correlation_id=cid,
                               payload=new_event(order.id, order.user_id, order.charged_amount)))

    def commit(self):
        self.db.commit()

    def get(self, payment_id):
        return self.db.get(Payment, payment_id)
