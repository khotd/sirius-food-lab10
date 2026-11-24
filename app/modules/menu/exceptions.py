"""
Доменные исключения модуля menu.

Наследники исключений из core.exceptions: router.js переводит их в
HTTP-статусы 404 / 400.
"""
from app.core.exceptions import BusinessRuleError, NotFoundError


class MenuItemNotFoundError(NotFoundError):
    """Блюдо не найдено (HTTP 404)."""


class MenuItemNameExistsError(BusinessRuleError):
    """Блюдо с таким названием уже существует (HTTP 400)."""


class MenuCategoryNotFoundError(NotFoundError):
    """Категория не найдена (HTTP 404)."""
