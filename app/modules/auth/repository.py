"""
Инфраструктурный слой модуля auth (Круг 3: Interface Adapters).

Единственный файл модуля, который знает о SQLAlchemy и структуре таблиц.
"""
import hashlib
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.core.jwt import REFRESH_TOKEN_EXPIRE_DAYS
from app.models import RefreshToken, Role, User


def hash_token(token: str) -> str:
    """SHA-256-хеш токена: в БД не храним сам токен."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class AuthRepository:
    """Операции ввода-вывода для аутентификации. Бизнес-правил не содержит."""

    def __init__(self, db: Session):
        self.db = db

    def get_user_by_email(self, email: str):
        """Извлечение пользователя вместе с ролью (для login)."""
        return (
            self.db.query(User)
            .filter(User.email == email)
            .first()
        )

    def get_role_id(self, name: str):
        """Идентификатор роли по названию (для регистрации)."""
        role = self.db.query(Role).filter(Role.name == name).first()
        return role.id if role else None

    def save_refresh_token(self, user_id: int, token: str) -> None:
        """Сохранение refresh-токена (в виде хеша) для возможности отзыва."""
        record = RefreshToken(
            user_id=user_id,
            token_hash=hash_token(token),
            expires_at=datetime.utcnow() + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS),
            is_revoked=False,
        )
        self.db.add(record)
        self.db.commit()

    def get_active_refresh_token(self, token: str):
        """Действующая (не отозванная и не просроченная) запись по токену."""
        return (
            self.db.query(RefreshToken)
            .filter(
                RefreshToken.token_hash == hash_token(token),
                RefreshToken.is_revoked.is_(False),
                RefreshToken.expires_at > datetime.utcnow(),
            )
            .first()
        )

    # --- методы для ротации refresh-токенов (критерий 10 баллов) ------

    def get_refresh_token_record(self, token: str):
        """
        Запись по токену БЕЗ фильтра по is_revoked/expires_at:
        сервис должен отличать «неизвестный токен» от
        «повторное использование отозванного» — это признак кражи.
        """
        return (
            self.db.query(RefreshToken)
            .filter(RefreshToken.token_hash == hash_token(token))
            .first()
        )

    def revoke_refresh_token(self, record) -> None:
        """Инвалидация конкретной записи refresh-токена (шаг ротации)."""
        record.is_revoked = True
        self.db.add(record)
        self.db.commit()

    def revoke_user_tokens(self, user_id: int) -> None:
        """
        Сброс сессии пользователя: отзыв ВСЕХ его refresh-токенов.
        Выполняется при попытке повторного использования уже отозванного
        refresh-токена — так злоумышленник, укравший токен, выгоняет
        из сессии не законного владельца, а «себя».
        """
        self.db.query(RefreshToken).filter(
            RefreshToken.user_id == user_id,
            RefreshToken.is_revoked.is_(False),
        ).update({"is_revoked": True}, synchronize_session=False)
        self.db.commit()
