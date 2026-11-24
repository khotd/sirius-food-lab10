"""
Транспортный слой модуля menu (Круг 4: Frameworks & Drivers).

Чтение меню — публично и «горячо» (GET /menu/items): на него и смотрим
в нагрузочных замерах. Данные приходят из MenuService, который внутри
Repository подкладывает Cache-Aside на Redis — router про кеш не знает.

Изменение меню — RBAC: только cook | admin (ЛР № 3).
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.dependencies import require_role
from app.database import get_db
from app.models import User
from app.schemas import MenuItemCreate, MenuItemUpdate
from app.modules.menu.exceptions import (
    MenuItemNotFoundError,
    MenuCategoryNotFoundError,
)
from app.modules.menu.repository import MenuRepository
from app.modules.menu.service import MenuService

router = APIRouter()


@router.get("/items")
def menu_items(db: Session = Depends(get_db)):
    """Всё меню. Публичный hot-endpoint: читается сотни раз в минуту."""
    return MenuService(MenuRepository(db)).menu_items()


@router.get("/items/{item_id}")
def menu_item(item_id: int, db: Session = Depends(get_db)):
    try:
        return MenuService(MenuRepository(db)).menu_item(item_id)
    except MenuItemNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.get("/categories")
def menu_categories(db: Session = Depends(get_db)):
    """Список категорий меню (кеш menu:categories, TTL 10 минут)."""
    return MenuService(MenuRepository(db)).categories()


@router.get("/categories/{category_id}")
def menu_category_items(category_id: int, db: Session = Depends(get_db)):
    """Блюда одной категории (кеш menu:category:{id}, TTL 10 минут)."""
    try:
        return MenuService(MenuRepository(db)).category_items(category_id)
    except MenuCategoryNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/items")
def create_menu_item(
    payload: MenuItemCreate,
    current_user: User = Depends(require_role("cook", "admin")),
    db: Session = Depends(get_db),
):
    # Техническая валидация уровня транспортного протокола.
    if payload.price <= 0:
        raise HTTPException(status_code=400, detail="Цена должна быть положительной")
    if len(payload.name) < 2:
        raise HTTPException(status_code=400, detail="Название блюда слишком короткое")

    service = MenuService(MenuRepository(db))
    try:
        item_id = service.create_item(
            name=payload.name, price=payload.price, category_id=payload.category_id
        )
    except (MenuItemNotFoundError, MenuCategoryNotFoundError) as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {"status": "success", "item_id": item_id}


@router.patch("/items/{item_id}")
def update_menu_item(
    item_id: int,
    payload: MenuItemUpdate,
    current_user: User = Depends(require_role("cook", "admin")),
    db: Session = Depends(get_db),
):
    # Техническая валидация уровня транспортного протокола.
    if payload.price is not None and payload.price <= 0:
        raise HTTPException(status_code=400, detail="Цена должна быть положительной")

    service = MenuService(MenuRepository(db))
    try:
        updated_id = service.update_item(
            item_id=item_id,
            name=payload.name,
            price=payload.price,
            is_available=payload.is_available,
            category_id=payload.category_id,
        )
    except (MenuItemNotFoundError, MenuCategoryNotFoundError) as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {"status": "success", "item_id": updated_id}
