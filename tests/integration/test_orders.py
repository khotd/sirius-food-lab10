"""Тесты фактического API проекта: JWT, резерв суммы, оплата и Outbox."""
import pytest
from sqlalchemy import text


def create(client, account, quantity=2):
    return client.post("/orders/create", headers=account["headers"],
                       json={"item_id": account["item_id"], "quantity": quantity})


def balance(db, account):
    db.rollback()  # Новая транзакция читает результат commit процесса API.
    return db.execute(text("SELECT balance FROM users WHERE id=:id"),
                      {"id": account["user_id"]}).scalar_one()


def test_create_order_saves_charge_and_balance(client, db, seed):
    account = seed()
    response = create(client, account)
    assert response.status_code == 200, response.text
    row = db.execute(text("SELECT * FROM orders WHERE id=:id"),
                     {"id": response.json()["order_id"]}).mappings().one()
    assert (row["user_id"], row["item_id"], row["quantity"]) == (account["user_id"], account["item_id"], 2)
    assert row["charged_amount"] == 300
    assert row["status"] == "processing"
    assert balance(db, account) == 700


@pytest.mark.parametrize("quantity", [0, -1])
def test_invalid_quantity_does_not_charge(client, db, seed, quantity):
    account = seed()
    assert create(client, account, quantity).status_code == 400
    assert balance(db, account) == 1000
    assert db.execute(text("SELECT count(*) FROM orders")).scalar_one() == 0


def test_insufficient_balance_does_not_persist_partial_order(client, db, seed):
    account = seed(balance=299)
    assert create(client, account).status_code == 400
    assert balance(db, account) == 299
    assert db.execute(text("SELECT count(*) FROM orders")).scalar_one() == 0


def test_unavailable_dish_does_not_charge(client, db, seed):
    account = seed(available=False)
    assert create(client, account).status_code == 400
    assert balance(db, account) == 1000


def test_pay_is_idempotent_and_persists_outbox_with_correlation(client, db, seed):
    account = seed()
    oid = create(client, account).json()["order_id"]
    headers = {**account["headers"], "X-Correlation-ID": "lab10-pay"}
    paid = client.post(f"/orders/{oid}/pay", headers=headers)
    assert paid.status_code == 200, paid.text
    assert paid.headers["X-Correlation-ID"] == "lab10-pay"
    again = client.post(f"/orders/{oid}/pay", headers=headers)
    assert again.status_code == 200 and again.json()["event_id"] is None
    row = db.execute(text("SELECT * FROM outbox")).mappings().one()
    assert row["routing_key"] == "order.paid"
    assert row["payload"]["event_id"] == paid.json()["event_id"]
    assert row["payload"]["amount"] == 300
    assert row["payload"]["order_id"] == oid
    assert row["correlation_id"] == "lab10-pay"
    assert row["published_at"] is None
    assert db.execute(text("SELECT status FROM orders WHERE id=:id"), {"id": oid}).scalar_one() == "paid"
    assert balance(db, account) == 700


def test_foreign_payment_is_forbidden_and_creates_no_event(client, db, seed):
    owner, other = seed(), seed()
    oid = create(client, owner).json()["order_id"]
    assert client.post(f"/orders/{oid}/pay", headers=other["headers"]).status_code == 403
    assert db.execute(text("SELECT count(*) FROM outbox")).scalar_one() == 0
    assert balance(db, other) == 1000


def test_order_requires_authentication(client, db, seed):
    account = seed()
    response = client.post("/orders/create", json={"item_id": account["item_id"], "quantity": 1})
    assert response.status_code == 401
    assert balance(db, account) == 1000


def test_menu_list_detail_and_404(client, seed):
    account = seed()
    menu = client.get("/menu/items")
    assert menu.status_code == 200
    assert [item["id"] for item in menu.json()] == [account["item_id"]]
    detail = client.get(f"/menu/items/{account['item_id']}")
    assert detail.status_code == 200 and detail.json()["price"] == 150
    assert client.get("/menu/items/999999").status_code == 404
