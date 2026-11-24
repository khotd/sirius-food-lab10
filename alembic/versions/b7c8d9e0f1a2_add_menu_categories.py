"""add menu categories (ЛР № 4: кеширование категорий с TTL 10 минут)

Revision ID: b7c8d9e0f1a2
Revises: a1b2c3d4e5f6
Create Date: 2026-11-06

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "b7c8d9e0f1a2"
down_revision: Union[str, None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Таблица категорий + связь блюд с категорией.
    op.create_table(
        "menu_categories",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
    )
    op.add_column("menu_items", sa.Column("category_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_menu_items_category", "menu_items", "menu_categories",
        ["category_id"], ["id"],
    )

    # Стартовые категории (идемпотентно — только если таблица пуста).
    op.execute(
        "INSERT INTO menu_categories (name) "
        "SELECT * FROM (VALUES ('Основные блюда'), ('Напитки')) AS v(name) "
        "WHERE NOT EXISTS (SELECT 1 FROM menu_categories)"
    )
    # Существующее меню (из предыдущих лаб) относим к «Основные блюда».
    op.execute(
        "UPDATE menu_items SET category_id = "
        "(SELECT id FROM menu_categories WHERE name = 'Основные блюда') "
        "WHERE category_id IS NULL"
    )


def downgrade() -> None:
    op.drop_constraint("fk_menu_items_category", "menu_items", type_="foreignkey")
    op.drop_column("menu_items", "category_id")
    op.drop_table("menu_categories")
