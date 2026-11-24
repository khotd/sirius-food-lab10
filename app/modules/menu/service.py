"""
Слой бизнес-логики модуля menu (Круги 1 и 2: Entities и Use Cases).

ВАЖНО: в этом файле запрещены импорты fastapi, sqlalchemy и redis.
Сервис не знает, кешированы ли данные и откуда они пришли — это детали
хранения, спрятанные в Repository (кеш у нас в Repository, критерий
Clean Architecture: если положить кеш в Service — зависимость от
инфраструктуры попадает в бизнес-логику).

Инвалидация кеша выполняется после мутаций — но через контракт
репозитория (invalidate_cache), а не напрямую через Redis.
"""
from typing import Protocol

from app.modules.menu.exceptions import (
    MenuItemNameExistsError,
    MenuItemNotFoundError,
    MenuCategoryNotFoundError,
)


class MenuRepositoryProtocol(Protocol):
    """Контракт, которого сервис ожидает от слоя хранения данных."""

    def list_items(self) -> list[dict]: ...

    def list_categories(self) -> list[dict]: ...

    def items_by_category(self, category_id: int) -> list[dict]: ...

    def get_by_id(self, item_id: int) -> object | None: ...

    def get_by_name(self, name: str) -> object | None: ...

    def get_category(self, category_id: int) -> object | None: ...

    def create_item(self, name: str, price: float, category_id: int | None) -> int: ...

    def update_item(self, item) -> None: ...

    def invalidate_cache(self) -> None: ...


class MenuService:
    """Бизнес-правила меню: уникальность названий, существование категорий."""

    def __init__(self, repository: MenuRepositoryProtocol):
        self.repository = repository

    # чтение (горячие пути, кеш внутри repository) 

    def menu_items(self) -> list[dict]:
        """Всё меню. Главная «горячая» точка приложения (обеденный пик)."""
        return self.repository.list_items()

    def menu_item(self, item_id: int) -> dict:
        item = self.repository.get_by_id(item_id)
        if item is None:
            raise MenuItemNotFoundError("Блюдо не найдено")
        return {
            "id": item.id, "name": item.name, "price": item.price,
            "is_available": item.is_available,
            "category": item.category.name if item.category else None,
        }

    def categories(self) -> list[dict]:
        return self.repository.list_categories()

    def category_items(self, category_id: int) -> list[dict]:
        """Меню одной категории или 404, если категории нет."""
        if not self.repository.get_category(category_id):
            raise MenuCategoryNotFoundError("Категория не найдена")
        return self.repository.items_by_category(category_id)

    # мутации (+ событийная инвалидация кеша)

    def create_item(self, name: str, price: float, category_id: int | None) -> int:
        """
        Создание блюда. Формат цены/названия проверяет router (техническая
        валидация); здесь — уникальность названия и существование категории.
        После сохранения кеш инвалидируется: иначе до истечения TTL (5 мин)
        пользователи видели бы меню без нового блюда.
        """
        if self.repository.get_by_name(name):
            raise MenuItemNameExistsError("Блюдо с таким названием уже существует")

        if category_id is not None and not self.repository.get_category(category_id):
            raise MenuCategoryNotFoundError("Категория не найдена")

        item_id = self.repository.create_item(name, price, category_id)
        # Меню изменилось → событийная инвалидация кеша.
        self.repository.invalidate_cache()
        return item_id

    def update_item(
        self,
        item_id: int,
        name: str | None,
        price: float | None,
        is_available: bool | None,
        category_id: int | None,
    ) -> int:
        """Правка блюда (частичная): None = поле не меняется."""
        item = self.repository.get_by_id(item_id)
        if item is None:
            raise MenuItemNotFoundError("Блюдо не найдено")

        if name is not None:
            if name != item.name and self.repository.get_by_name(name):
                raise MenuItemNameExistsError("Блюдо с таким названием уже существует")
            item.name = name
        if price is not None:
            item.price = price
        if is_available is not None:
            item.is_available = is_available
        if category_id is not None:
            if not self.repository.get_category(category_id):
                raise MenuCategoryNotFoundError("Категория не найдена")
            item.category_id = category_id

        self.repository.update_item(item)
        # Меню изменилось → событийная инвалидация кеша.
        self.repository.invalidate_cache()
        return item.id
