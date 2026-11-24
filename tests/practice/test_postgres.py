"""Настоящие блокировки и RabbitMQ handlers; только отдельная PostgreSQL."""
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal as D
from threading import Barrier
from unittest.mock import Mock

import pytest

from app.core.exceptions import BusinessRuleError
from app.core.order_events import new_event, OrderEvent
from app.models import Order, Payment, PromoCode, LoyaltyAccount, LoyaltyTransaction, PromoCodeUsage
from app.modules.loyalty.repository import LoyaltyRepository
from app.modules.loyalty.service import LoyaltyService
from app.modules.payments.repository import PaymentRepository
from app.modules.payments.service import PaymentService
from app.modules.orders.repository import OrderRepository
from app.modules.orders.service import OrderService
from tests.practice.test_loyalty import promo


@pytest.fixture(autouse=True)
def only_postgres(database):
    if database.kw["bind"].dialect.name != "postgresql":
        pytest.skip("Для блокировок задайте PRACTICE_TEST_DATABASE_URL=postgresql://.../practice_test_*")


def test_two_customers_compete_for_last_promo(database):
    with database() as db:
        promo(db, max_uses=1)
    barrier = Barrier(2)
    def attempt(user_id):
        with database() as db:
            barrier.wait(timeout=10)
            try:
                LoyaltyService(LoyaltyRepository(db)).create_order(user_id, 1, 1, promo_code="STUDENT10")
                return "ok"
            except BusinessRuleError:
                db.rollback()
                return "exhausted"
    with ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(attempt, [1, 2])) == ["exhausted", "ok"]
    with database() as db:
        assert db.query(PromoCode).one().current_uses == 1
        assert db.query(PromoCodeUsage).count() == db.query(Order).count() == 1


def test_concurrent_payment_requests_call_gateway_once(database):
    with database() as db:
        oid = LoyaltyService(LoyaltyRepository(db)).create_order(1, 1, 1, payment_method="external")
    barrier = Barrier(2)
    adapter = Mock()
    adapter.create.return_value = {"id": "pi_test", "client_secret": "secret"}
    def attempt(_):
        with database() as db:
            barrier.wait(timeout=10)
            return PaymentService(PaymentRepository(db), adapter).initiate(oid, 1, D("200"), "key").id
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(attempt, [1, 2]))
    assert results[0] == results[1]
    assert adapter.create.call_count == 1
    with database() as db:
        assert db.query(Payment).count() == 1


def test_consumers_compensate_paid_order_once(database, monkeypatch):
    from app.workers import handlers
    monkeypatch.setattr(handlers, "SessionLocal", database)
    with database() as db:
        promo(db)
        oid = LoyaltyService(LoyaltyRepository(db)).create_order(1, 1, 1, promo_code="STUDENT10", points=D("50"))
        event = OrderService(OrderRepository(db)).pay_order(oid, 1, "test")
    paid = OrderEvent.model_validate(event)
    handlers.handle("loyalty", "order.paid", paid, "test")
    assert handlers.handle("loyalty", "order.paid", paid, "test")[0] == "duplicate_ignored"
    with database() as db:
        OrderService(OrderRepository(db)).cancel_order(oid, 1)
    # Даже разные event_id одной отмены не должны повторять компенсации.
    for _ in range(2):
        cancel = OrderEvent.model_validate(new_event(oid, 1, 130))
        handlers.handle("loyalty", "order.cancelled", cancel, "test")
        handlers.handle("promo", "order.cancelled", cancel, "test")
    with database() as db:
        account = db.query(LoyaltyAccount).one()
        assert (account.balance, account.total_earned) == (100, 0)
        assert db.query(PromoCode).one().current_uses == 0
        assert db.query(LoyaltyTransaction).count() == 4
