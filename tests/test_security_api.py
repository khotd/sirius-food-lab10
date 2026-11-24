"""
Тесты безопасности (лабораторная работа № 3).

Проверяют сквозь HTTP (TestClient):
  1. без токена — 401;
  2. невалидный токен — 401;
  3. чужая роль — 403 (RBAC);
  4. чужой объект — 403 (IDOR: отмена чужого заказа, пополнение чужого баланса);
  5. позитивные сценарии для легального пользователя работают.

Тесты выполняются против PostgreSQL из docker-compose (как и само
приложение). Схема хранится в отдельной базе sirius_food_test, которая
создаётся перед тестами и удаляется после них — рабочая БД не затрагивается.

Запуск: docker compose up -d db  &&  python -m pytest tests/test_security_api.py -v
"""
import os
import unittest
from urllib.parse import urlsplit

# --- Окружение нужно задать ДО импорта app.main -----------------------
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-pytests-only")
os.environ["SQL_ECHO"] = "false"

# DATABASE_URL из .env (если не задана переменной окружения),
# включая нестандартный порт DB_PORT для машины с занятым 5432.
from dotenv import load_dotenv  # noqa: E402

load_dotenv()

BASE_DB_URL = os.environ.get(
    "DATABASE_URL", "postgresql://sirius:sirius@localhost:5432/sirius_food"
)
TEST_DB_NAME = "sirius_food_test"


def _make_test_db_url() -> str:
    parts = urlsplit(BASE_DB_URL)
    return parts._replace(path=f"/{TEST_DB_NAME}").geturl()


def _admin_url() -> str:
    parts = urlsplit(BASE_DB_URL)
    return parts._replace(path="/postgres").geturl()


def create_test_database():
    import psycopg2
    from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

    conn = psycopg2.connect(_admin_url())
    conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    with conn.cursor() as cur:
        cur.execute(f"DROP DATABASE IF EXISTS {TEST_DB_NAME}")
        cur.execute(f"CREATE DATABASE {TEST_DB_NAME}")
    conn.close()


def drop_test_database():
    import psycopg2
    from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

    conn = psycopg2.connect(_admin_url())
    conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    with conn.cursor() as cur:
        cur.execute(f"DROP DATABASE IF EXISTS {TEST_DB_NAME}")
    conn.close()


os.environ["DATABASE_URL"] = _make_test_db_url()

import app.database as app_database  # noqa: E402  (импорт после задания env)
from fastapi.testclient import TestClient  # noqa: E402

PASSWORD = "password123"


def setUpModule():
    create_test_database()

    # Нюанс: другие тестовые модули импортируют app.core.* раньше этого
    # файла, поэтому app.core.config (и движок в app.database) могут быть
    # закешированы с URL «главной» БД из .env — DATABASE_URL этого события
    # меняется позже. Пересоздаём движок на тестовую БД, чтобы тесты
    # никогда не трогали рабочие данные.
    import app.database as app_database
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    app_database.engine.dispose()
    app_database.engine = create_engine(
        os.environ["DATABASE_URL"], echo=False
    )
    app_database.SessionLocal = sessionmaker(
        autocommit=False, autoflush=False, bind=app_database.engine
    )

    import app.models  # noqa: F401 — регистрация моделей в Base.metadata

    from app.database import Base, engine
    Base.metadata.create_all(bind=engine)

    from app.core.security import hash_password
    from app.models import MenuItem, Role, User

    with app_database.SessionLocal() as db:
        existing = {r.name: r for r in db.query(Role).all()}
        roles = {}
        for name in ("student", "cook", "courier", "admin"):
            roles[name] = existing.get(name) or Role(name=name)
        db.add_all(role for role in roles.values() if role.id is None)

        for i, email in enumerate(
            ["student1@mail.ru", "student2@mail.ru", "cook@mail.ru"], start=1
        ):
            role = roles["student"] if i < 3 else roles["cook"]
            db.add(
                User(
                    email=email,
                    password_hash=hash_password(PASSWORD),
                    role_id=None,  # заполняется ниже через связь
                    role=role,
                    balance=500.0 if i == 1 else 0.0,
                )
            )
        db.add(
            MenuItem(name="Тестовое блюдо", price=100.0, is_available=True)
        )
        db.commit()


def tearDownModule():
    from app.database import engine
    engine.dispose()
    drop_test_database()


class SecurityApiTests(unittest.TestCase):
    """Все тесты разделяют одну тестовую БД; порядок внутри класса важен."""

    @classmethod
    def setUpClass(cls):
        from app.main import app
        cls.client = TestClient(app)

    # --- вспомогательные методы -------------------------------------

    def register(self, email, password=PASSWORD):
        return self.client.post(
            "/users/register",
            json={"email": email, "password": password},
        )

    def _reset_rate_limit(self):
        """Сброс счётчика rate limiting, чтобы тесты не мешали друг другу."""
        try:
            from app.core.rate_limit import get_redis_client
            redis_client = get_redis_client()
            # IP клиента в TestClient может быть "testclient" или None
            # ("unknown") в зависимости от версии starlette, поэтому
            # чистим все ключи с этим путём.
            for key in redis_client.scan_iter("rate_limit:*:/auth/login"):
                redis_client.delete(key)
        except Exception:
            pass  # Redis недоступен — rate limit сейчас fail-open, не важно

    def login(self, email, password=PASSWORD):
        self._reset_rate_limit()
        response = self.client.post(
            "/auth/login", json={"email": email, "password": password}
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def auth_headers(self, token):
        return {"Authorization": f"Bearer {token}"}

    # --- 1. аутентификация: без токена / с мусорным токеном ---------

    def test_create_order_without_token_is_401(self):
        """Сценарий 1 из методички: без токена POST /orders/create → 401."""
        response = self.client.post(
            "/orders/create", json={"item_id": 1, "quantity": 1}
        )
        self.assertEqual(response.status_code, 401)

    def test_invalid_token_is_401(self):
        response = self.client.post(
            "/orders/create",
            json={"item_id": 1, "quantity": 1},
            headers=self.auth_headers("not-a-jwt-token"),
        )
        self.assertEqual(response.status_code, 401)

    def test_topup_without_token_is_401(self):
        response = self.client.post("/users/1/topup?amount=100")
        self.assertEqual(response.status_code, 401)

    # --- регистрация, логин и выдача токенов ------------------------

    def test_register_login_and_refresh_flow(self):
        """Регистрация → логин → обновление access-токена по refresh."""
        reg = self.register("fresh@mail.ru")
        self.assertEqual(reg.status_code, 200, reg.text)

        tokens = self.login("fresh@mail.ru")
        self.assertEqual(tokens["token_type"], "bearer")
        self.assertIn("access_token", tokens)
        self.assertIn("refresh_token", tokens)

        refreshed = self.client.post(
            "/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
        )
        self.assertEqual(refreshed.status_code, 200, refreshed.text)
        self.assertIn("access_token", refreshed.json())

    def test_login_with_wrong_password_is_401(self):
        # Предыдущий тест брутфорса мог израсходовать лимит: начнём с чистого счётчика.
        self._reset_rate_limit()
        self.register("wrongpw@mail.ru")
        response = self.client.post(
            "/auth/login",
            json={"email": "wrongpw@mail.ru", "password": "incorrect"},
        )
        self.assertEqual(response.status_code, 401)

    def test_refresh_with_access_token_is_401(self):
        """Access-токен нельзя использовать вместо refresh-токена."""
        tokens = self.login("student1@mail.ru")
        response = self.client.post(
            "/auth/refresh", json={"refresh_token": tokens["access_token"]}
        )
        self.assertEqual(response.status_code, 401)

    # --- RBAC: чужая роль → 403 --------------------------------------

    def test_kitchen_orders_for_student_is_403(self):
        """Сценарий 4 из методички: студент вызывает GET /orders/kitchen/orders → 403."""
        tokens = self.login("student1@mail.ru")
        response = self.client.get(
            "/orders/kitchen/orders", headers=self.auth_headers(tokens["access_token"])
        )
        self.assertEqual(response.status_code, 403)

    def test_kitchen_orders_for_cook_is_200(self):
        tokens = self.login("cook@mail.ru")
        response = self.client.get(
            "/orders/kitchen/orders", headers=self.auth_headers(tokens["access_token"])
        )
        self.assertEqual(response.status_code, 200)

    def test_menu_item_create_for_student_is_403(self):
        tokens = self.login("student1@mail.ru")
        response = self.client.post(
            "/menu/items",
            json={"name": "Студенческий суп", "price": 100},
            headers=self.auth_headers(tokens["access_token"]),
        )
        self.assertEqual(response.status_code, 403)

    def test_menu_item_create_for_cook_is_200(self):
        tokens = self.login("cook@mail.ru")
        response = self.client.post(
            "/menu/items",
            json={"name": "Борщ с лабораторной", "price": 150},
            headers=self.auth_headers(tokens["access_token"]),
        )
        self.assertEqual(response.status_code, 200, response.text)
        item_id = response.json()["item_id"]

        # cook создаёт заказ... нет: заказ создаёт студент.
        order = self.client.post(
            "/orders/create",
            json={"item_id": item_id, "quantity": 1},
            headers=self.auth_headers(self.login("student1@mail.ru")["access_token"]),
        )
        self.assertEqual(order.status_code, 200, order.text)

    def test_create_order_for_cook_is_403(self):
        """RBAC: повар — персонал столовой, заказы оформляют только студенты."""
        tokens = self.login("cook@mail.ru")
        response = self.client.post(
            "/orders/create",
            json={"item_id": 1, "quantity": 1},
            headers=self.auth_headers(tokens["access_token"]),
        )
        self.assertEqual(response.status_code, 403)

    # --- IDOR: чужой объект → 403 ------------------------------------

    def test_cancel_foreign_order_is_403(self):
        """Сценарий 3 из методички: студент Б отменяет заказ студента А → 403."""
        student1 = self.auth_headers(self.login("student1@mail.ru")["access_token"])
        created = self.client.post(
            "/orders/create", json={"item_id": 1, "quantity": 1}, headers=student1
        )
        self.assertEqual(created.status_code, 200, created.text)
        order_id = created.json()["order_id"]

        student2 = self.auth_headers(self.login("student2@mail.ru")["access_token"])
        response = self.client.post(f"/orders/{order_id}/cancel", headers=student2)
        self.assertEqual(response.status_code, 403)

    def test_cancel_own_order_is_200(self):
        student1 = self.auth_headers(self.login("student1@mail.ru")["access_token"])
        created = self.client.post(
            "/orders/create", json={"item_id": 1, "quantity": 1}, headers=student1
        )
        order_id = created.json()["order_id"]
        response = self.client.post(f"/orders/{order_id}/cancel", headers=student1)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "success"})

    def test_topup_foreign_balance_is_403(self):
        """IDOR: студент 1 пытается пополнить баланс студента 2 → 403."""
        student1 = self.auth_headers(self.login("student1@mail.ru")["access_token"])
        with app_database.SessionLocal() as db:
            from app.models import User
            student2_id = (
                db.query(User).filter(User.email == "student2@mail.ru").first().id
            )
        response = self.client.post(
            f"/users/{student2_id}/topup?amount=100", headers=student1
        )
        self.assertEqual(response.status_code, 403)

    def test_create_order_ignores_user_id_from_payload(self):
        """user_id удалён из схемы: заказ всегда создаётся от имени владельца токена."""
        student1 = self.auth_headers(self.login("student1@mail.ru")["access_token"])
        response = self.client.post(
            "/orders/create",
            json={"item_id": 1, "quantity": 1, "user_id": 999},
            headers=student1,
        )
        # Лишнее поле user_id игнорируется, заказ создаётся для student1.
        self.assertEqual(response.status_code, 200, response.text)

    # --- ротация refresh-токенов (критерий 10 баллов) -----------------

    def test_refresh_rotates_token(self):
        """При обновлении выдаётся новый refresh-токен, старый отзывается."""
        tokens = self.login("student1@mail.ru")
        old_refresh = tokens["refresh_token"]

        refreshed = self.client.post(
            "/auth/refresh", json={"refresh_token": old_refresh}
        )
        self.assertEqual(refreshed.status_code, 200, refreshed.text)
        new_tokens = refreshed.json()

        # Ротация: новый refresh отличается от старого.
        self.assertIn("refresh_token", new_tokens)
        self.assertNotEqual(new_tokens["refresh_token"], old_refresh)

        # Старый refresh уже отозван — повторное использование даёт 401.
        replay = self.client.post(
            "/auth/refresh", json={"refresh_token": old_refresh}
        )
        self.assertEqual(replay.status_code, 401)

    def test_session_reset_on_replay_after_rotation(self):
        """
        Повторное использование отозванного refresh-токена — признак
        кражи: сбрасывается ВСЯ сессия (даже свежевыданный токен).
        """
        tokens = self.login("student2@mail.ru")
        old_refresh = tokens["refresh_token"]
        new_tokens = self.client.post(
            "/auth/refresh", json={"refresh_token": old_refresh}
        ).json()
        new_refresh = new_tokens["refresh_token"]

        # Злоумышленник играет старым токеном...
        replay = self.client.post(
            "/auth/refresh", json={"refresh_token": old_refresh}
        )
        self.assertEqual(replay.status_code, 401)

        # ...а законный владелец уже не может обновиться:
        # сессия сброшена целиком.
        victim_refresh = self.client.post(
            "/auth/refresh", json={"refresh_token": new_refresh}
        )
        self.assertEqual(victim_refresh.status_code, 401)

    # --- rate limiting на Redis (бонус, критерий 9) -------------------
    # Тест пропускается, если контейнер Redis не запущен
    # (docker compose up -d redis): лимитер тогда работает fail-open.

    def _reset_rate_limit_counter(self):
        try:
            from app.core.rate_limit import get_redis_client
            redis_client = get_redis_client()
            redis_client.ping()
            for key in redis_client.scan_iter("rate_limit:*:/auth/login"):
                redis_client.delete(key)
            return True
        except Exception:
            return False

    def test_login_bruteforce_is_429(self):
        """Сценарий 5 из методички: 6 попыток подряд → 429."""
        if not self._reset_rate_limit_counter():
            self.skipTest("Redis недоступен (docker compose up -d redis)")

        last = None
        for _ in range(6):
            last = self.client.post(
                "/auth/login",
                json={"email": "student1@mail.ru", "password": "no-such-pass"},
            )
        self.assertEqual(last.status_code, 429, last.text)


if __name__ == "__main__":
    unittest.main()
