"""Реальный HTTP → FastAPI → PostgreSQL, полностью отдельный от .env процесс."""
import pytest
import httpx
import redis
from sqlalchemy import text

from scripts.lab10_environment import isolated_stack, running_api


@pytest.fixture(scope="session")
def infrastructure(tmp_path_factory):
    with isolated_stack(tmp_path_factory.mktemp("lab10")) as context:
        with running_api(context):
            yield context


@pytest.fixture
def db(infrastructure):
    engine = infrastructure["engine"]
    # API делает commit в своём процессе: rollback тестовой сессии недостаточен.
    # TRUNCATE выполняется только в контейнере, который создала эта фикстура.
    with engine.begin() as connection:
        connection.execute(text(
            "TRUNCATE outbox, processed_events, kitchen_orders, deliveries, orders, "
            "refresh_tokens, users, menu_items, menu_categories RESTART IDENTITY CASCADE"
        ))
    cache = redis.from_url(infrastructure["env"]["REDIS_URL"])
    cache.flushdb()
    cache.close()
    with engine.connect() as connection:
        yield connection
    # Открытая читающая транзакция закрывается до следующего TRUNCATE.


@pytest.fixture
def client(infrastructure, db):
    with httpx.Client(base_url=infrastructure["url"], timeout=10) as client:
        yield client


@pytest.fixture
def seed(client, db):
    """Реальная регистрация и логин: роль и bcrypt также проверяются через API."""
    counter = 0

    def make(balance=1000, price=150, available=True):
        nonlocal counter
        counter += 1
        email = f"lab10-{counter}@example.com"
        credentials = {"email": email, "password": "Lab10Password!"}
        response = client.post("/users/register", json=credentials)
        assert response.status_code == 200, response.text
        uid = db.execute(text("SELECT id FROM users WHERE email=:email"), {"email": email}).scalar_one()
        db.execute(text("UPDATE users SET balance=:balance WHERE id=:id"), {"balance": balance, "id": uid})
        item = db.execute(text(
            "INSERT INTO menu_items (name,price,is_available) VALUES ('Test dish',:price,:available) RETURNING id"
        ), {"price": price, "available": available}).scalar_one()
        db.commit()
        login = client.post("/auth/login", json=credentials)
        assert login.status_code == 200, login.text
        return {
            "user_id": uid, "item_id": item,
            "headers": {"Authorization": "Bearer " + login.json()["access_token"]},
        }

    return make
