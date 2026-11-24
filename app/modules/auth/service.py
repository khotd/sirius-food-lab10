"""
Слой бизнес-логики модуля auth (Круги 1 и 2: Entities и Use Cases).

ВАЖНО: в этом файле запрещены импорты fastapi и sqlalchemy.
"""
from typing import Protocol

from app.core.jwt import (
    create_access_token,
    create_refresh_token,
    try_decode_token,
)
from app.core.security import verify_password


class AuthRepositoryProtocol(Protocol):
    """Контракт, которого сервис ожидает от слоя хранения данных."""

    def get_user_by_email(self, email: str): ...

    def save_refresh_token(self, user_id: int, token: str) -> None: ...

    def get_refresh_token_record(self, token: str): ...

    def revoke_refresh_token(self, record) -> None: ...

    def revoke_user_tokens(self, user_id: int) -> None: ...


class InvalidCredentialsError(ValueError):
    """Неверный email или пароль (HTTP 401)."""


class InvalidRefreshTokenError(ValueError):
    """Refresh-токен невалиден, отозван или просрочен (HTTP 401)."""


class AuthService:
    """Бизнес-правила аутентификации."""

    def __init__(self, repository: AuthRepositoryProtocol):
        self.repository = repository

    def login(self, email: str, password: str) -> dict:
        """
        Проверяет учётные данные и возвращает пару токенов.
        Неверный email ИЛИ пароль даёт одну и ту же ошибку — так
        злоумышленник не узнаёт, какие из email зарегистрированы.
        """
        user = self.repository.get_user_by_email(email)
        if not user or not verify_password(password, user.password_hash):
            raise InvalidCredentialsError("Неверный email или пароль")

        access_token = create_access_token(user.id)
        refresh_token = create_refresh_token(user.id)

        # Сохраняем refresh-токен в БД для возможности отзыва.
        self.repository.save_refresh_token(user.id, refresh_token)

        return {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "bearer",
        }

    def refresh(self, refresh_token: str) -> dict:
        """
        Refresh Token rotation.

        При каждом обновлении:
          1. проверяется подпись и тип refresh-токена;
          2. находится его запись в БД;
          3. если токен УЖЕ отозван — это попытка использования
             украденного токена: сбрасывается вся сессия пользователя
             (отзыв всех его refresh-токенов) и обновление отвергается;
          4. иначе старый токен отзывается, выдаётся НОВАЯ пара
             access + refresh.

        В результате украденный «окном» refresh-токен бесполезен:
        он уже отозван, а попытка его применения уничтожает сессию.
        """
        payload = try_decode_token(refresh_token)
        if payload is None or payload.get("type") != "refresh":
            raise InvalidRefreshTokenError("Невалидный refresh-токен")

        record = self.repository.get_refresh_token_record(refresh_token)
        if record is None:
            raise InvalidRefreshTokenError(
                "Refresh-токен отозван, просрочен или неизвестен"
            )

        # Повторное использование отозванного токена — признак кражи.
        if record.is_revoked:
            self.repository.revoke_user_tokens(record.user_id)
            raise InvalidRefreshTokenError(
                "Обнаружено повторное использование refresh-токена. "
                "Сессия сброшена, войдите заново"
            )

        access_token = create_access_token(record.user_id)
        new_refresh_token = create_refresh_token(record.user_id)

        # Ротация: старый refresh инвалидируется, новый — сохраняется.
        self.repository.revoke_refresh_token(record)
        self.repository.save_refresh_token(record.user_id, new_refresh_token)

        return {
            "access_token": access_token,
            "refresh_token": new_refresh_token,
            "token_type": "bearer",
        }
