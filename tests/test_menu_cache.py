"""
Тесты кеширования (ЛР № 4): Cache-Aside, инвалидация, TTL, fail-open.

Интеграционная часть использует setUpModule/tearDownModule из
test_security_api (общая тестовая БД, в ней есть cook и «Тестовое
блюдо»); Redis должен быть запущен (docker compose up -d redis).

Unit-часть (fail-open, БД без кеша) работает на in-memory SQLite
без внешних систем.
"""
import json
import unittest

from redis.exceptions import ConnectionError as RedisConnectionError

from app.core.redis import redis_client
from app.modules.menu.repository import (
    MENU_ALL_KEY,
    MENU_ALL_TTL,
    CATEGORIES_KEY,
    CATEGORY_KEY,
    CATEGORY_TTL,
    MenuRepository,
)

# Пароль тестовых пользователей из test_security_api (используем одинаковый).
from tests.test_security_api import PASSWORD


def setUpModule():
    from tests.test_security_api import setUpModule as security_setup

    security_setup()


def tearDownModule():
    from tests.test_security_api import tearDownModule as security_teardown

    security_teardown()
    # Чистим ключи кеша, чтобы тесты не зависели от порядка прогонов.
    try:
        keys = list(redis_client.scan_iter("menu:*"))
        if keys:
            redis_client.delete(*keys)
    except Exception:
        pass


class MenuCacheApiTests(unittest.TestCase):
    """Интеграционные тесты Cache-Aside через живое приложение."""

    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient

        from app.main import app

        cls.client = TestClient(app)

    def _login_cook(self):
        # Сбрасываем счётчик логинов, чтобы тестовые calls не упёрлись в 429.
        try:
            from app.core.rate_limit import get_redis_client

            for key in get_redis_client().scan_iter("rate_limit:*:/auth/login"):
                get_redis_client().delete(key)
        except Exception:
            pass
        response = self.client.post(
            "/auth/login", json={"email": "cook@mail.ru", "password": PASSWORD}
        )
        self.assertEqual(response.status_code, 200, response.text)
        token = response.json()["access_token"]
        return {"Authorization": f"Bearer {token}"}

    def _ensure_category(self) -> int:
        """Гарантирует наличие категории в тестовой БД (seed создаёт только роли и пользователей)."""
        from app.database import SessionLocal
        from app.models import MenuCategory

        with SessionLocal() as db:
            category = db.query(MenuCategory).order_by(MenuCategory.id).first()
            if category is None:
                category = MenuCategory(name="Тестовая категория")
                db.add(category)
                db.commit()
                db.refresh(category)
            return category.id

    # ---------- Cache-Aside: чтение и причастность Redis --------------

    def test_menu_items_public_and_non_empty(self):
        """GET /menu/items — публичный и возвращает блюда (манекен для Locust)."""
        response = self.client.get("/menu/items")
        self.assertEqual(response.status_code, 200)
        items = response.json()
        self.assertIsInstance(items, list)
        self.assertGreaterEqual(len(items), 1)

    def test_menu_items_go_to_cache_with_ttl(self):
        """Чтение кладёт ключ menu:all с TTL ~300 секунд (hit/miss механика)."""
        keys_before = list(redis_client.scan_iter("menu:all*"))
        for k in keys_before:
            redis_client.delete(k)

        self.client.get("/menu/items")

        cached = redis_client.get(MENU_ALL_KEY)
        self.assertIsNotNone(cached, "после чтения меню кеш menu:all не заполнен")
        ttl = redis_client.ttl(MENU_ALL_KEY)
        self.assertGreater(ttl, MENU_ALL_TTL - 30)
        self.assertLessEqual(ttl, MENU_ALL_TTL)
        data = json.loads(cached)
        self.assertTrue(all("name" in i and "price" in i for i in data))

    # ---------- событийная инвалидация --------------------------------

    def test_new_item_appeared_immediately_after_create(self):
        """Событийная инвалидация: новое блюдо видно сразу, без ожидания TTL."""
        self.client.get("/menu/items")  # греем кеш старым меню

        name = "Кеш-инвалидация салат"
        response = self.client.post(
            "/menu/items",
            json={"name": name, "price": 99.9},
            headers=self._login_cook(),
        )
        self.assertEqual(response.status_code, 200, response.text)

        items = self.client.get("/menu/items").json()
        self.assertIn(name, [i["name"] for i in items])

    def test_update_invalidates_category_keys(self):
        """PATCH блюда с категорией удаляет ключи menu:category:* (инвалидация)."""
        cook = self._login_cook()

        # Готовим блюдо в категории через SQL (без лишнего API-оборота).
        from app.database import SessionLocal
        from app.models import MenuCategory, MenuItem

        category_id = self._ensure_category()
        with SessionLocal() as db:
            item = db.query(MenuItem).filter(MenuItem.name == "Кеш-инвалидация салат").first()
            if item is None:
                item = MenuItem(name="Кеш-инвалидация салат", price=50)
                db.add(item)
                db.commit()
                db.refresh(item)
            item_id = item.id

        self.client.get("/menu/items")
        self.client.get(f"/menu/categories/{category_id}")
        self.assertIsNotNone(redis_client.get(CATEGORY_KEY.format(id=category_id)))

        response = self.client.patch(
            f"/menu/items/{item_id}",
            json={"category_id": category_id},
            headers=cook,
        )
        self.assertEqual(response.status_code, 200, response.text)

        self.assertIsNone(redis_client.get(MENU_ALL_KEY), "menu:all не инвалидируется после PATCH")
        self.assertIsNone(
            redis_client.get(CATEGORY_KEY.format(id=category_id)),
            "ключ категории не инвалидируется после PATCH",
        )

    # ---------- категории с другим TTL (критерий 10) -------------------

    def test_category_endpoint_cached_with_longer_ttl(self):
        """menu:category:{id} живёт 10 минут — дольше, чем menu:all (5 мин)."""
        category_id = self._ensure_category()

        # Ключ мог остаться от живого приложения/прошлого прогона —
        # удаляем, чтобы замер TTL отражал именно эту запись кеша.
        try:
            redis_client.delete(CATEGORY_KEY.format(id=category_id))
        except Exception:
            pass

        response = self.client.get(f"/menu/categories/{category_id}")
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.json(), list)

        ttl = redis_client.ttl(CATEGORY_KEY.format(id=category_id))
        self.assertGreater(ttl, CATEGORY_TTL - 30, f"TTL ключа категории: {ttl}")
        self.assertLessEqual(ttl, CATEGORY_TTL)
        self.assertGreater(
            ttl, MENU_ALL_TTL, "у категорий должен быть ОТЛИЧНЫЙ (больший) TTL"
        )

    def test_missing_category_is_404(self):
        response = self.client.get("/menu/categories/999999")
        self.assertEqual(response.status_code, 404)

    def test_categories_list_cached(self):
        response = self.client.get("/menu/categories")
        self.assertEqual(response.status_code, 200)
        self.assertIsNotNone(redis_client.get(CATEGORIES_KEY))


class FailOpenUnitTests(unittest.TestCase):
    """Fail-open: сломанный Redis не ломает чтение — данные из БД."""

    class _BrokenRedis:
        """Заглушка: имитирует недоступный Redis (redis-ошибка, как в жизни)."""

        def get(self, key):
            raise RedisConnectionError()

        def setex(self, key, ttl, value):
            raise RedisConnectionError()

        def lock(self, *args, **kwargs):
            raise RedisConnectionError()

        def scan_iter(self, pattern):
            raise RedisConnectionError()

        def delete(self, *keys):
            raise RedisConnectionError()

    def _memory_db(self):
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        from app.database import Base
        from app.models import MenuCategory, MenuItem

        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        return engine, sessionmaker(bind=engine)()

    def test_broken_redis_still_returns_db_data(self):
        engine, db = self._memory_db()
        from app.models import MenuCategory, MenuItem

        db.add(MenuCategory(id=1, name="Напитки"))
        db.add(MenuItem(name="Компот", price=40.0))
        db.commit()

        repo = MenuRepository(db, cache=self._BrokenRedis())
        items = repo.list_items()          # должен вернуть из БД, не упасть
        self.assertEqual([i["name"] for i in items], ["Компот"])


if __name__ == "__main__":
    unittest.main()
