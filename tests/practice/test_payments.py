import hashlib
import hmac
import json
import time
from decimal import Decimal as D
from unittest.mock import Mock

import httpx
import pytest

from app.core.config import settings
from app.core.exceptions import BusinessRuleError, ForbiddenError
from app.models import Order, Payment, PaymentWebhook, OutboxEvent, User
from app.modules.loyalty.repository import LoyaltyRepository
from app.modules.loyalty.service import LoyaltyService
from app.modules.orders.repository import OrderRepository
from app.modules.orders.service import OrderService
from app.modules.payments.gateway import StripeGateway, GatewayError
from app.modules.payments.repository import PaymentRepository
from app.modules.payments.service import PaymentService


@pytest.fixture
def setup_payment(db):
    oid = LoyaltyService(LoyaltyRepository(db)).create_order(1, 1, 1, payment_method="external")
    adapter = Mock()
    adapter.create.return_value = {"id": "pi_test", "client_secret": "pi_test_secret", "livemode": False}
    service = PaymentService(PaymentRepository(db), adapter)
    return oid, adapter, service


def event(kind="payment_intent.succeeded", event_id="evt_test", **overrides):
    obj = dict(id="pi_test", amount=20000, currency="rub", status="succeeded")
    obj.update(overrides)
    return {"id": event_id, "type": kind, "livemode": False, "data": {"object": obj}}


def test_double_request_is_one_gateway_call_and_one_row(db, setup_payment):
    oid, adapter, service = setup_payment
    first = service.initiate(oid, 1, D("200"), "test-key")
    second = service.initiate(oid, 1, D("200"), "test-key")
    assert first.id == second.id
    assert first.status == "pending"
    assert db.query(Payment).count() == 1
    adapter.create.assert_called_once_with(oid, D("200"), "rub", "test-key")
    assert db.get(User, 1).balance == 1000
    assert db.get(Order, oid).status == "processing"
    with pytest.raises(BusinessRuleError):
        service.initiate(oid, 1, D("200"), "another-key")


def test_amount_and_owner_cannot_be_forged(db, setup_payment):
    oid, adapter, service = setup_payment
    with pytest.raises(ForbiddenError):
        service.initiate(oid, 2, D("200"), "key")
    with pytest.raises(BusinessRuleError):
        service.initiate(oid, 1, D("0.01"), "key")
    adapter.create.assert_not_called()
    assert db.query(Payment).count() == 0


def test_key_cannot_be_used_for_another_order(db, setup_payment):
    oid, adapter, service = setup_payment
    service.initiate(oid, 1, D("200"), "key")
    other = LoyaltyService(LoyaltyRepository(db)).create_order(1, 1, 1, payment_method="external")
    with pytest.raises(BusinessRuleError, match="другими параметрами"):
        service.initiate(other, 1, D("200"), "key")
    assert adapter.create.call_count == 1


def test_webhook_deduplication_including_different_event_ids(db, setup_payment):
    oid, adapter, service = setup_payment
    service.initiate(oid, 1, D("200"), "key")
    assert service.webhook(event(), "cid") == "processed"
    assert service.webhook(event(), "cid") == "duplicate"
    assert service.webhook(event(event_id="evt_other"), "cid") == "processed"
    assert db.get(Order, oid).status == "paid"
    assert db.query(OutboxEvent).count() == 1
    assert db.query(PaymentWebhook).count() == 2
    service.webhook(event("payment_intent.payment_failed", "evt_old", status="requires_payment_method"), "cid")
    assert db.query(Payment).one().status == "succeeded"


@pytest.mark.parametrize("values", [{"amount": 1}, {"currency": "usd"}, {"status": "processing"}])
def test_webhook_checks_amount_currency_and_status(db, setup_payment, values):
    oid, adapter, service = setup_payment
    service.initiate(oid, 1, D("200"), "key")
    with pytest.raises(BusinessRuleError):
        service.webhook(event(**values), "cid")
    db.rollback()
    assert db.get(Order, oid).status == "processing"
    assert db.query(PaymentWebhook).count() == 0


def test_full_refund_is_idempotent_and_late_success_cannot_resurrect_order(db, setup_payment):
    oid, adapter, service = setup_payment
    service.initiate(oid, 1, D("200"), "key")
    service.webhook(event(), "cid")
    refund = event("refund.updated", "evt_refund", id="re_test", payment_intent="pi_test")
    service.webhook(refund, "cid")
    service.webhook(refund, "cid")
    service.webhook(event(event_id="evt_late"), "cid")
    assert db.query(Payment).one().status == "refunded"
    assert db.get(Order, oid).status == "cancelled"
    assert [e.routing_key for e in db.query(OutboxEvent).order_by(OutboxEvent.id)] == ["order.paid", "order.cancelled"]
    assert db.get(User, 1).balance == 1000


def test_timeout_recorded_and_late_signed_success_recovers(db, setup_payment):
    oid, adapter, service = setup_payment
    adapter.create.side_effect = GatewayError("timeout")
    payment = service.initiate(oid, 1, D("200"), "key")
    assert (payment.status, payment.error_code) == ("failed", "timeout")
    service.initiate(oid, 1, D("200"), "key")
    assert adapter.create.call_count == 1
    service.webhook(event(metadata={"idempotency_key": "key"}), "cid")
    assert payment.status == "succeeded"
    assert db.get(Order, oid).status == "paid"


def test_failed_card_payment_can_later_succeed(db, setup_payment):
    oid, adapter, service = setup_payment
    service.initiate(oid, 1, D("200"), "key")
    service.webhook(event("payment_intent.payment_failed", "evt_fail", status="requires_payment_method",
                          last_payment_error={"decline_code": "insufficient_funds"}), "cid")
    assert db.query(Payment).one().error_code == "insufficient_funds"
    service.webhook(event(), "cid")
    assert db.query(Payment).one().status == "succeeded"


def test_cancel_during_external_payment_is_rejected(db, setup_payment):
    oid, adapter, service = setup_payment
    service.initiate(oid, 1, D("200"), "key")
    with pytest.raises(ValueError, match="инициирован"):
        OrderService(OrderRepository(db)).cancel_order(oid, 1)
    assert db.get(Order, oid).status == "processing"


def test_gateway_retries_same_key_with_exponential_delay(monkeypatch):
    monkeypatch.setattr(settings, "STRIPE_SECRET_KEY", "sk_test_fixture")
    requests = []
    def respond(request):
        requests.append(request)
        return httpx.Response(503 if len(requests) < 5 else 200, json={"id": "pi_test", "livemode": False})
    delays = []
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        result = StripeGateway(client, sleep=delays.append, jitter=lambda: .1).create(1, D("12.34"), "rub", "same-key")
    assert result["id"] == "pi_test"
    assert delays == [1.1, 2.1, 4.1, 8.1]
    assert len(requests) == 5
    assert {r.headers["Idempotency-Key"] for r in requests} == {"same-key"}
    assert all(b"amount=1234" in r.content for r in requests)


@pytest.mark.parametrize("failure", ["timeout", "network_error", "gateway_unavailable"])
def test_retry_exhaustion(monkeypatch, failure):
    monkeypatch.setattr(settings, "STRIPE_SECRET_KEY", "sk_test_fixture")
    requests = []
    def respond(request):
        requests.append(request)
        if failure == "timeout":
            raise httpx.ReadTimeout("test")
        if failure == "network_error":
            raise httpx.ConnectError("test")
        return httpx.Response(500)
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(GatewayError, match=failure):
            StripeGateway(client, sleep=lambda _: None).create(1, D("1"), "rub", "same-key")
    assert len(requests) == 5


def test_insufficient_funds_does_not_retry(monkeypatch):
    monkeypatch.setattr(settings, "STRIPE_SECRET_KEY", "sk_test_fixture")
    handler = Mock(return_value=httpx.Response(402, json={"error": {"decline_code": "insufficient_funds"}}))
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(GatewayError, match="insufficient_funds"):
            StripeGateway(client).create(1, D("1"), "rub", "key")
    assert handler.call_count == 1


def signed(body, secret, timestamp=None):
    stamp = str(int(time.time()) if timestamp is None else timestamp)
    digest = hmac.new(secret.encode(), stamp.encode() + b"." + body, hashlib.sha256).hexdigest()
    return f"t={stamp},v1={digest}"


def test_signature_uses_raw_body_and_timestamp(monkeypatch):
    secret = "whsec_test_fixture"
    monkeypatch.setattr(settings, "STRIPE_WEBHOOK_SECRET", secret)
    body = json.dumps(event()).encode()
    adapter = StripeGateway()
    assert adapter.verify_webhook(body, signed(body, secret))["id"] == "evt_test"
    for signature, changed_body in [
        (signed(body, "wrong"), body),
        (signed(body, secret, int(time.time())-301), body),
        (signed(body, secret), body+b" "),
        ("bad-header", body),
    ]:
        with pytest.raises(GatewayError, match="invalid_webhook"):
            adapter.verify_webhook(changed_body, signature)


def test_live_keys_are_rejected(monkeypatch):
    monkeypatch.setattr(settings, "STRIPE_SECRET_KEY", "sk_live_not_allowed")
    with pytest.raises(GatewayError, match="gateway_not_configured"):
        StripeGateway().create(1, D("1"), "rub", "key")
