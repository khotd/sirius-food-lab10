"""Сценарии платежей без зависимостей от HTTP и ORM."""
from typing import Protocol

from app.core.exceptions import BusinessRuleError, ForbiddenError, NotFoundError
from app.core.order_events import ORDER_PAID, ORDER_CANCELLED
from app.modules.loyalty.service import money
from app.modules.payments.errors import GatewayError


class PaymentRepositoryProtocol(Protocol):
    def order(self, order_id): ...
    def by_key(self, key): ...
    def by_order(self, order_id): ...
    def new(self, order, amount, currency, key): ...
    def event_seen(self, event_id): ...
    def gateway_payment(self, gateway_id, key): ...
    def remember(self, event_id, payment): ...
    def stage(self, order, routing_key, cid): ...
    def commit(self): ...


class PaymentService:
    def __init__(self, repository: PaymentRepositoryProtocol, gateway, currency="rub"):
        self.repository = repository
        self.gateway = gateway
        self.currency = currency

    def initiate(self, order_id, user_id, amount, key):
        repo = self.repository
        order = repo.order(order_id)
        if order is None:
            raise NotFoundError("Заказ не найден")
        if order.user_id != user_id:
            raise ForbiddenError("Можно оплачивать только свой заказ")
        amount = money(amount)
        if amount <= 0 or amount != money(order.charged_amount):
            raise BusinessRuleError("Сумма платежа не совпадает с суммой заказа")
        existing = repo.by_key(key)
        if existing:
            if existing.order_id != order_id or existing.user_id != user_id or existing.amount != amount:
                raise BusinessRuleError("Idempotency-Key уже использован с другими параметрами")
            return existing
        if order.payment_method != "external" or order.status != "processing":
            raise BusinessRuleError("Заказ не ожидает внешней оплаты")
        if repo.by_order(order_id):
            raise BusinessRuleError("Для заказа уже создан платёж; используйте исходный Idempotency-Key")
        payment = repo.new(order, amount, self.currency, key)
        # Ошибки адаптера имеют только безопасный code, без ключей/ответов Stripe.
        try:
            result = self.gateway.create(order.id, amount, self.currency, key)
        except GatewayError as error:
            payment.status = "failed"
            payment.error_code = error.code
        else:
            payment.gateway_payment_id = result["id"]
            payment.client_secret = result.get("client_secret")
            payment.status = "pending"  # Только подписанный вебхук подтверждает оплату.
        repo.commit()
        return payment

    def webhook(self, event, cid):
        repo = self.repository
        kind = event.get("type")
        supported = ("payment_intent.succeeded", "payment_intent.payment_failed",
                     "refund.created", "refund.updated")
        if kind not in supported:
            return "ignored"
        data = event.get("data")
        if not isinstance(data, dict):
            raise BusinessRuleError("Некорректный вебхук")
        obj = data.get("object", {})
        event_id = event.get("id")
        if not isinstance(event_id, str) or not event_id or not isinstance(obj, dict):
            raise BusinessRuleError("Некорректный вебхук")
        gateway_id = obj.get("payment_intent") if kind.startswith("refund.") else obj.get("id")
        if not isinstance(gateway_id, str):
            raise BusinessRuleError("В вебхуке нет идентификатора платежа")
        # Сначала находим order_id, затем блокируем заказ, как и при POST /payments.
        metadata = obj.get("metadata") or {}
        if not isinstance(metadata, dict):
            raise BusinessRuleError("Некорректные метаданные вебхука")
        payment = repo.gateway_payment(gateway_id, metadata.get("idempotency_key"))
        if payment is None:
            raise NotFoundError("Платёж пока не найден; повторите вебхук")
        order = repo.order(payment.order_id)
        if repo.event_seen(event_id):
            return "duplicate"
        payment = repo.by_order(order.id)
        if payment.gateway_payment_id not in (None, gateway_id):
            raise BusinessRuleError("Идентификатор шлюза не совпадает")
        if (obj.get("amount") != int(payment.amount * 100)
                or obj.get("currency") != payment.currency):
            raise BusinessRuleError("Сумма или валюта вебхука не совпадает с платежом")
        payment.gateway_payment_id = gateway_id
        if kind == "payment_intent.succeeded":
            if obj.get("status") != "succeeded":
                raise BusinessRuleError("Некорректный статус PaymentIntent")
            if payment.status != "refunded" and order.status == "processing":
                payment.status = "succeeded"
                payment.error_code = None
                order.status = "paid"
                repo.stage(order, ORDER_PAID, cid)
        elif kind == "payment_intent.payment_failed":
            if payment.status not in ("succeeded", "refunded"):
                payment.status = "failed"
                error = obj.get("last_payment_error") or {}
                payment.error_code = "insufficient_funds" if error.get("decline_code") == "insufficient_funds" else "payment_failed"
        elif obj.get("status") == "succeeded":
            # Полный возврат, выполненный в Stripe Dashboard; частичные возвраты
            # в MVP не меняют заказ. Сумма проверена выше.
            if payment.status != "refunded":
                payment.status = "refunded"
                order.status = "cancelled"
                repo.stage(order, ORDER_CANCELLED, cid)
        repo.remember(event_id, payment)
        repo.commit()
        return "processed"
