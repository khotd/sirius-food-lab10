from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

import pytest

from app.models import User, Order, PromoCode, PromoCodeUsage, LoyaltyTransaction, LoyaltyAccount, OutboxEvent
from app.modules.loyalty.repository import LoyaltyRepository
from app.modules.loyalty.service import LoyaltyService, tier_for
from app.modules.orders.repository import OrderRepository
from app.modules.orders.service import OrderService
from app.core.exceptions import BusinessRuleError


def promo(db, **changes):
    now = datetime.now(timezone.utc)
    values = dict(code="STUDENT10", discount_type="percent", discount_value=D("10"),
                  valid_from=now-timedelta(days=1), valid_until=now+timedelta(days=1),
                  max_uses=10, current_uses=0, min_order_amount=0, once_per_user=True, is_active=True)
    values.update(changes)
    record = PromoCode(**values)
    db.add(record)
    db.commit()
    return record


def checkout(db, **kwargs):
    return LoyaltyService(LoyaltyRepository(db)).create_order(1, 1, 1, **kwargs)


def test_discount_points_and_order_are_atomic(db):
    p = promo(db)
    oid = checkout(db, promo_code="student10", points=D("50"))
    order = db.get(Order, oid)
    assert (order.original_amount, order.discount_amount, order.points_spent, order.charged_amount) == (200, 20, 50, 130)
    assert db.get(User, 1).balance == 870
    assert p.current_uses == 1
    assert db.query(LoyaltyAccount).filter_by(user_id=1).one().balance == 50
    assert db.query(LoyaltyTransaction).one().idempotency_key == f"spend_order_{oid}"


@pytest.mark.parametrize("changes,message", [
    ({"is_active": False}, "неактивен"),
    ({"valid_until": datetime.now(timezone.utc)-timedelta(hours=1)}, "истёк"),
    ({"valid_from": datetime.now(timezone.utc)+timedelta(hours=1)}, "ещё не действует"),
    ({"max_uses": 1, "current_uses": 1}, "исчерпан"),
    ({"min_order_amount": 201}, "минимальной"),
])
def test_invalid_promo_does_not_charge(db, changes, message):
    promo(db, **changes)
    with pytest.raises(BusinessRuleError, match=message):
        checkout(db, promo_code="STUDENT10")
    db.rollback()
    assert db.get(User, 1).balance == 1000
    assert db.query(Order).count() == 0


def test_once_per_user_and_missing_code(db):
    promo(db)
    checkout(db, promo_code="STUDENT10")
    with pytest.raises(BusinessRuleError, match="уже использовали"):
        checkout(db, promo_code="STUDENT10")
    db.rollback()
    with pytest.raises(BusinessRuleError, match="не найден"):
        checkout(db, promo_code="MISSING")


@pytest.mark.parametrize("points", [D("100.01"), D("101")])
def test_points_limit(db, points):
    with pytest.raises(BusinessRuleError):
        checkout(db, points=points)
    db.rollback()
    assert db.query(LoyaltyTransaction).count() == 0
    assert db.get(User, 1).balance == 1000


def test_insufficient_points_and_money_rollback(db):
    account = db.query(LoyaltyAccount).one()
    account.balance = 1
    db.commit()
    with pytest.raises(BusinessRuleError, match="Недостаточно баллов"):
        checkout(db, points=D("2"))
    db.rollback()
    db.get(User, 1).balance = 0
    db.commit()
    promo(db)
    with pytest.raises(BusinessRuleError, match="Недостаточно средств"):
        checkout(db, promo_code="STUDENT10")
    db.rollback()
    assert db.query(PromoCode).one().current_uses == 0
    assert db.query(Order).count() == 0


def test_fixed_discount_never_creates_negative_charge(db):
    promo(db, discount_type="fixed", discount_value=500)
    oid = checkout(db, promo_code="STUDENT10")
    assert db.get(Order, oid).charged_amount == 0
    event = OrderService(OrderRepository(db)).pay_order(oid, 1, "test")
    assert event["amount"] == 0


@pytest.mark.parametrize("tier,rate", [("Bronze", D(".03")), ("Silver", D(".05")), ("Gold", D(".07"))])
def test_earn_is_idempotent_and_uses_tier(db, tier, rate):
    oid = checkout(db)
    order = db.get(Order, oid)
    order.status = "paid"
    account = db.query(LoyaltyAccount).one()
    account.tier = tier
    db.commit()
    service = LoyaltyService(LoyaltyRepository(db))
    service.earn(order)
    db.commit()
    service.earn(order)
    db.commit()
    assert account.balance == 100 + 200 * rate
    assert db.query(LoyaltyTransaction).filter_by(type="earn").count() == 1


@pytest.mark.parametrize("total,tier", [(999, "Bronze"), (1000, "Silver"), (2999, "Silver"), (3000, "Gold")])
def test_tier_boundaries(total, tier):
    assert tier_for(total) == tier


def test_compensation_and_promo_restore_once_with_different_events(db):
    p = promo(db)
    oid = checkout(db, promo_code="STUDENT10", points=D("50"))
    order = db.get(Order, oid)
    order.status = "paid"
    db.commit()
    repo = LoyaltyRepository(db)
    service = LoyaltyService(repo)
    service.earn(order)
    db.commit()
    order.status = "cancelled"
    db.commit()
    for _ in range(2):
        service.compensate(order)
        repo.restore_promo(order)
        db.commit()
    account = db.query(LoyaltyAccount).one()
    assert (account.balance, account.total_earned, account.tier) == (100, 0, "Bronze")
    assert p.current_uses == 0
    assert db.query(PromoCodeUsage).count() == 0
    assert db.query(LoyaltyTransaction).count() == 4
    service.earn(order)  # OrderPaid пришёл после OrderCancelled.
    db.commit()
    assert account.balance == 100
    checkout(db, promo_code="STUDENT10")


def test_cancel_refunds_reserved_balance_and_stages_event(db):
    oid = checkout(db)
    service = OrderService(OrderRepository(db))
    service.cancel_order(oid, 1)
    assert db.get(User, 1).balance == 1000
    assert db.query(OutboxEvent).one().routing_key == "order.cancelled"
    with pytest.raises(BusinessRuleError):
        service.cancel_order(oid, 1)
    assert db.get(User, 1).balance == 1000


def test_external_order_never_charges_internal_balance(db):
    oid = checkout(db, payment_method="external")
    assert db.get(User, 1).balance == 1000
    with pytest.raises(BusinessRuleError, match="вебхук"):
        OrderService(OrderRepository(db)).pay_order(oid, 1, "test")
