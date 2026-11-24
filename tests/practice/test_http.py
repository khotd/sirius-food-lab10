"""HTTP-проверки новых роутеров без импорта app.main и рабочей БД."""
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.dependencies import get_current_user
from app.database import get_db
from app.models import User
from app.modules.orders.router import router as orders_router
from app.modules.loyalty.router import router as loyalty_router
from app.modules.payments.router import router as payments_router, gateway
from app.modules.payments.gateway import StripeGateway
from app.core.observability import ObservabilityMiddleware


@pytest.fixture
def client(database, monkeypatch):
    app = FastAPI()
    app.add_middleware(ObservabilityMiddleware)
    app.include_router(orders_router, prefix="/orders")
    app.include_router(loyalty_router, prefix="/loyalty")
    app.include_router(payments_router, prefix="/payments")
    def session():
        with database() as db:
            yield db
    app.dependency_overrides[get_db] = session
    def user():
        with database() as db:
            user = db.get(User, 1)
            _ = user.role
            return user
    app.dependency_overrides[get_current_user] = user
    monkeypatch.setattr("app.modules.orders.service.OrderService.dispatch_notifications", Mock())
    with TestClient(app) as result:
        result.test_app = app
        yield result


def test_http_checkout_payment_and_idempotency_header(client):
    adapter = Mock()
    adapter.create.return_value = {"id": "pi_test", "client_secret": "secret"}
    client.test_app.dependency_overrides[gateway] = lambda: adapter
    response = client.post("/orders/create", json={"item_id": 1, "quantity": 1, "payment_method": "external"})
    assert response.status_code == 200, response.text
    payload = {"order_id": response.json()["order_id"], "amount": "200.00"}
    assert client.post("/payments", json=payload).status_code == 422
    for _ in range(2):
        response = client.post("/payments", json=payload, headers={"Idempotency-Key": "test"})
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "pending"
    assert adapter.create.call_count == 1
    assert client.get(f"/payments/{response.json()['id']}").status_code == 200


def test_http_requires_auth_and_admin_for_promo(client):
    assert client.post("/loyalty/promos", json={}).status_code == 403
    client.test_app.dependency_overrides.pop(get_current_user)
    assert client.post("/payments", json={"order_id": 1, "amount": 200},
                       headers={"Idempotency-Key": "test"}).status_code == 401


def test_http_invalid_signature_is_rejected(client, monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "STRIPE_WEBHOOK_SECRET", "whsec_test")
    client.test_app.dependency_overrides[gateway] = StripeGateway
    response = client.post("/payments/webhook", content=b"{}", headers={"Stripe-Signature": "forged"})
    assert response.status_code == 400


def test_http_loyalty_account_and_history(client):
    response = client.get("/loyalty/me")
    assert response.status_code == 200
    assert float(response.json()["balance"]) == 100
    assert client.get("/loyalty/me/transactions").json() == []
