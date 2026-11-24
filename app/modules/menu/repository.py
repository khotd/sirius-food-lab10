"""
Инфраструктурный слой модуля menu (Круг 3: Interface Adapters).

Единственный файл модуля, который знает о SQLAlchemy и Redis.

Стратегия Cache-Aside (ЛР № 4):
  1. чтение — сначала Redis (ключ menu:all);
  2. hit — данные возвращаются из кеша, БД не трогается;
  3. miss — запрос в БД, результат кладётся в кеш с TTL и возвращается.

Кеш живёт именно в Repository, потому что кеш — это деталь хранения
данных (ускоритель СУБД). Service не знает, откуда пришли данные.

Защита от cache stampede (критерий 9):
  при промахе запрос сначала берёт distributed-лок (redis.lock).
  Только один запрос идёт в БД и греет кеш; остальные ждут и читают
  уже обновлённый кеш (double-check после захвата лока).

Fail-open: любой сбой Redis трактуется как «кеши нет» — данные честно
читаются из БД (приложение работает медленнее, но не падает).
"""
import json
import logging
from typing import Any, Protocol

from redis import RedisError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.redis import redis_client
from app.models import MenuCategory, MenuItem

logger = logging.getLogger(__name__)

# Ключи и TTL (критерий 10: у категорий TTL другой).
MENU_ALL_KEY = "menu:all"
MENU_ALL_TTL = 300              # 5 минут: меню читается постоянно.
CATEGORIES_KEY = "menu:categories"
CATEGORIES_TTL = 600            # 10 минут: категории меняются ещё реже.
CATEGORY_KEY = "menu:category:{id}"
CATEGORY_TTL = 600              # 10 минут: требование методички.
LOCK_TIMEOUT = 10               # секунд: максимальное владение локом.
LOCK_WAIT = 3                   # секунд: сколько «не первый» ждёт лок.


def _item_to_dict(item: MenuItem) -> dict:
    """Форма ответа единая и для БД, и для кеша (JSON-совместимая)."""
    return {
        "id": item.id,
        "name": item.name,
        "price": item.price,
        "is_available": item.is_available,
        "category_id": item.category_id,
    }


class MenuRepository:
    """Ввод-вывод меню + кеш (Cache-Aside). Бизнес-правил здесь нет."""

    def __init__(self, db: Session, cache: Any = None):
        # cache — для подмены в тестах (fail-open и unit-тесты).
        self.db = db
        self.cache = cache if cache is not None else redis_client

    #  кеш: тонкая обвязка с fail-open 

    def _cache_get(self, key: str) -> str | None:
        try:
            return self.cache.get(key)
        except RedisError:
            logger.warning("Redis недоступен при чтении %s — идём в БД", key)
            return None

    def _cache_set(self, key: str, ttl: int, value: str) -> None:
        try:
            self.cache.setex(key, ttl, value)
        except RedisError:
            logger.warning("Redis недоступен при записи %s — кеш пропущен", key)

    def _cache_delete(self, *keys: str) -> None:
        try:
            if keys:
                self.cache.delete(*keys)
        except RedisError:
            logger.warning("Redis недоступен при инвалидации %s", keys)

    # Cache-Aside c защитой от stampede 

    def _cache_aside(self, key: str, ttl: int, loader) -> list[dict]:
        """
        Общая обвязка Cache-Aside + лок от stampede:
          hit → из кеша; miss → один сходит в БД, остальные ждут лок
          и читают уже прогретый кеш (double-check).
        Любая ошибка Redis → читаем из БД напрямую (fail-open).
        """
        cached = self._cache_get(key)
        if cached is not None:
            return json.loads(cached)

        try:
            with self.cache.lock(
                f"{key}:lock", timeout=LOCK_TIMEOUT, blocking_timeout=LOCK_WAIT
            ):
                cached = self._cache_get(key)  # double-check: кто-то нагрел уже
                if cached is not None:
                    return json.loads(cached)
                data = loader()
                self._cache_set(key, ttl, json.dumps(data, ensure_ascii=False))
                return data
        except RedisError:
            # Не получили лок (стоит у другого) ИЛИ Redis упал.
            # Последняя попытка взять из кеша, иначе — честно в БД.
            cached = self._cache_get(key)
            if cached is not None:
                return json.loads(cached)
            return loader()

    #  чтение (горячие пути) 

    def _list_items_from_db(self) -> list[dict]:
        items = self.db.query(MenuItem).order_by(MenuItem.id).all()
        return [_item_to_dict(i) for i in items]

    def list_items(self) -> list[dict]:
        """Всё меню: Cache-Aside, ключ menu:all, TTL 5 минут."""
        if not settings.CACHE_ENABLED:
            return self._list_items_from_db()
        return self._cache_aside(MENU_ALL_KEY, MENU_ALL_TTL, self._list_items_from_db)

    def _list_categories_from_db(self) -> list[dict]:
        rows = self.db.query(MenuCategory).order_by(MenuCategory.id).all()
        return [{"id": c.id, "name": c.name} for c in rows]

    def list_categories(self) -> list[dict]:
        """Список категорий: ключ menu:categories, TTL 10 минут."""
        if not settings.CACHE_ENABLED:
            return self._list_categories_from_db()
        return self._cache_aside(
            CATEGORIES_KEY, CATEGORIES_TTL, self._list_categories_from_db
        )

    def _items_by_category_from_db(self, category_id: int) -> list[dict]:
        items = (
            self.db.query(MenuItem)
            .filter(MenuItem.category_id == category_id)
            .order_by(MenuItem.id)
            .all()
        )
        return [_item_to_dict(i) for i in items]

    def items_by_category(self, category_id: int) -> list[dict]:
        """Меню одной категории: ключ menu:category:{id}, TTL 10 минут."""
        key = CATEGORY_KEY.format(id=category_id)
        if not settings.CACHE_ENABLED:
            return self._items_by_category_from_db(category_id)
        return self._cache_aside(
            key, CATEGORY_TTL, lambda: self._items_by_category_from_db(category_id)
        )

    #  точечные операции (мутации и проверки) 

    def get_by_id(self, item_id: int) -> MenuItem | None:
        """Точечное чтение по id для мутаций — без кеша (не горячий путь)."""
        return self.db.query(MenuItem).filter(MenuItem.id == item_id).first()

    def get_by_name(self, name: str) -> MenuItem | None:
        """Проверка уникальности названия блюда (бизнес-правило)."""
        return self.db.query(MenuItem).filter(MenuItem.name == name).first()

    def get_category(self, category_id: int) -> MenuCategory | None:
        return (
            self.db.query(MenuCategory)
            .filter(MenuCategory.id == category_id)
            .first()
        )

    def create_item(self, name: str, price: float, category_id: int | None) -> int:
        item = MenuItem(name=name, price=price, is_available=True, category_id=category_id)
        self.db.add(item)
        self.db.commit()
        self.db.refresh(item)
        return item.id

    def update_item(self, item: MenuItem) -> None:
        self.db.add(item)
        self.db.commit()

    #  инвалидация и прогрев 

    def invalidate_cache(self) -> None:
        """
        Событийная инвалидация: меняется меню → сбрасываем все ключи.
        Ключи категорий ищем через scan (шаблон menu:category:*).
        """
        keys = [MENU_ALL_KEY, CATEGORIES_KEY]
        try:
            keys += [k for k in self.cache.scan_iter("menu:category:*")]
        except RedisError:
            pass
        self._cache_delete(*set(keys))

    def warm_cache(self) -> None:
        """
        Прогрев кеша (cache warm-up, критерий 10): при старте приложения
        заполняем ключи из БД, чтобы первый пользовательский запрос
        не попал в miss и не пошёл в PostgreSQL.
        """
        if not settings.CACHE_ENABLED:
            return
        self._cache_set(
            MENU_ALL_KEY, MENU_ALL_TTL, json.dumps(self._list_items_from_db(), ensure_ascii=False)
        )
        categories = self._list_categories_from_db()
        self._cache_set(
            CATEGORIES_KEY, CATEGORIES_TTL, json.dumps(categories, ensure_ascii=False)
        )
        for c in categories:
            self._cache_set(
                CATEGORY_KEY.format(id=c["id"]),
                CATEGORY_TTL,
                json.dumps(self._items_by_category_from_db(c["id"]), ensure_ascii=False),
            )
