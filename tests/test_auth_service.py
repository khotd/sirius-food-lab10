"""
Тесты ротации refresh-токенов на тесте 10 баллов.

Выполняются на in-memory заглушке репозитория (AuthService зависит
только от Protocol) — PostgreSQL, Redis и HTTP не нужны.
"""
import hashlib
import os
import unittest
from datetime import datetime, timedelta

os.environ.setdefault("SECRET_KEY", "test-secret-key-for-pytests-only")

from app.core.jwt import create_access_token, create_refresh_token
from app.core.security import hash_password, verify_password
from app.modules.auth.service import (
    AuthService,
    InvalidCredentialsError,
    InvalidRefreshTokenError,
)


def _hash_token(token: str) -> str:
    """То же хеширование, что в AuthRepository: в БД лежит хеш токена."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class FakeRecord:
    """Строка таблицы refresh_tokens."""

    def __init__(self, user_id, token_hash):
        self.user_id = user_id
        self.token_hash = token_hash
        self.expires_at = datetime.utcnow() + timedelta(days=7)
        self.is_revoked = False


class FakeAuthRepository:
    """In-memory реализация AuthRepositoryProtocol (принцип подстановки)."""

    def __init__(self, users):
        # users: email -> {"id": int, "password_hash": str}
        self.users = users
        self.tokens = {}  # token_hash -> FakeRecord

    def get_user_by_email(self, email):
        data = self.users.get(email)
        if data is None:
            return None

        class _User:
            id = data["id"]
            password_hash = data["password_hash"]

        return _User()

    def save_refresh_token(self, user_id, token):
        record = FakeRecord(user_id, _hash_token(token))
        self.tokens[_hash_token(token)] = record

    def get_refresh_token_record(self, token):
        return self.tokens.get(_hash_token(token))

    def revoke_refresh_token(self, record):
        record.is_revoked = True

    def revoke_user_tokens(self, user_id):
        for record in self.tokens.values():
            if record.user_id == user_id:
                record.is_revoked = True


class AuthServiceUnitTests(unittest.TestCase):
    def setUp(self):
        self.repository = FakeAuthRepository(
            {
                "student@mail.ru": {
                    "id": 1,
                    "password_hash": hash_password("right-pass"),
                }
            }
        )
        self.service = AuthService(self.repository)

    # --- логин --------------------------------------------------------

    def test_login_with_wrong_password_is_rejected(self):
        with self.assertRaises(InvalidCredentialsError):
            self.service.login("student@mail.ru", "wrong-pass")

    def test_login_issues_token_pair(self):
        tokens = self.service.login("student@mail.ru", "right-pass")
        self.assertEqual(tokens["token_type"], "bearer")
        self.assertIn("access_token", tokens)
        self.assertIn("refresh_token", tokens)
        # В БД заглушки появилась ровно одна запись refresh-токена.
        self.assertEqual(len(self.repository.tokens), 1)

    # --- ротация refresh-токенов --------------------------------------

    def test_refresh_rotates_tokens(self):
        tokens = self.service.login("student@mail.ru", "right-pass")
        old_refresh = tokens["refresh_token"]

        refreshed = self.service.refresh(old_refresh)

        # Выдана ПАРА новых токенов, новый refresh отличается от старого.
        self.assertIn("access_token", refreshed)
        self.assertIn("refresh_token", refreshed)
        self.assertNotEqual(refreshed["refresh_token"], old_refresh)
        # Старый refresh отозван (шаг ротации).
        old_record = self.repository.get_refresh_token_record(old_refresh)
        self.assertTrue(old_record.is_revoked)
        # Новый refresh действителен.
        new_record = self.repository.get_refresh_token_record(
            refreshed["refresh_token"]
        )
        self.assertFalse(new_record.is_revoked)

    def test_reuse_of_revoked_token_resets_session(self):
        tokens = self.service.login("student@mail.ru", "right-pass")
        old_refresh = tokens["refresh_token"]
        refreshed = self.service.refresh(old_refresh)
        new_refresh = refreshed["refresh_token"]

        # Повторное использование старого refresh-токена → ошибка...
        with self.assertRaises(InvalidRefreshTokenError):
            self.service.refresh(old_refresh)

        # ...и НОВЫЙ токен тоже погиб: сессия сброшена целиком.
        new_record = self.repository.get_refresh_token_record(new_refresh)
        self.assertTrue(new_record.is_revoked)
        with self.assertRaises(InvalidRefreshTokenError):
            self.service.refresh(new_refresh)

    def test_access_token_cannot_be_used_as_refresh(self):
        access = create_access_token(1)
        with self.assertRaises(InvalidRefreshTokenError):
            self.service.refresh(access)

    def test_unknown_refresh_token_is_rejected(self):
        with self.assertRaises(InvalidRefreshTokenError):
            self.service.refresh("not-a-jwt-token")

    # --- безопасность данных ------------------------------------------

    def test_password_stored_as_bcrypt_hash(self):
        """Пароль не хранится в открытом виде — только проверка хешем."""
        stored = self.repository.users["student@mail.ru"]["password_hash"]
        self.assertTrue(verify_password("right-pass", stored))
        self.assertFalse(verify_password("wrong-pass", stored))


if __name__ == "__main__":
    unittest.main()
