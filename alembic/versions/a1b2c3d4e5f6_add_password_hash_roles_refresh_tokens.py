"""add password_hash, roles, refresh_tokens

Revision ID: a1b2c3d4e5f6
Revises:
Create Date: 2026-10-18

"""
from typing import Sequence, Union
import secrets

from alembic import op
import sqlalchemy as sa

from app.core.security import hash_password

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Создаём таблицу ролей.
    op.create_table(
        "roles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(50), unique=True, nullable=False),
    )

    # Добавляем колонки в users. Пока nullable=True — заполним ниже.
    op.add_column("users", sa.Column("password_hash", sa.String(), nullable=True))
    op.add_column("users", sa.Column("role_id", sa.Integer(), nullable=True))
    op.create_foreign_key("fk_users_role", "users", "roles", ["role_id"], ["id"])

    # Создаём таблицу refresh-токенов.
    op.create_table(
        "refresh_tokens",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("token_hash", sa.String(), unique=True, nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("is_revoked", sa.Boolean(), server_default="false"),
    )

    # Заполняем роли.
    op.execute(
        "INSERT INTO roles (name) VALUES ('student'), ('cook'), ('courier'), ('admin')"
    )
    # Назначаем всем существующим пользователям роль student.
    op.execute(
        "UPDATE users SET role_id = (SELECT id FROM roles WHERE name = 'student')"
    )

    # Существующим пользователям (из лабораторной №2) назначаем хеш
    # случайного пароля, который никто не знает: войти со старым аккаунтом
    # нельзя, пока пользователь не пройдёт регистрацию заново. Иначе
    # SET NOT NULL ниже упадёт на NULL-значениях.
    legacy_hash = hash_password(secrets.token_hex(32))
    users_t = sa.table("users", sa.column("password_hash", sa.String))
    op.execute(
        users_t.update()
        .where(users_t.c.password_hash.is_(None))
        .values(password_hash=legacy_hash)
    )

    # Теперь можно сделать колонки обязательными.
    op.alter_column("users", "role_id", nullable=False)
    op.alter_column("users", "password_hash", nullable=False)


def downgrade() -> None:
    op.drop_table("refresh_tokens")
    op.drop_constraint("fk_users_role", "users", type_="foreignkey")
    op.drop_column("users", "role_id")
    op.drop_column("users", "password_hash")
    op.drop_table("roles")
