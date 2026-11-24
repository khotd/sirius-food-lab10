"""
Слой бизнес-логики модуля users (Круги 1 и 2: Entities и Use Cases).

ВАЖНО: в этом файле запрещены импорты fastapi и sqlalchemy.
Репозиторий передаётся в конструктор (инверсия зависимостей).
"""
from dataclasses import dataclass
from typing import Optional, Protocol

from app.core.exceptions import BusinessRuleError, ForbiddenError, NotFoundError
from app.core.security import hash_password

MAX_TOPUP_AMOUNT = 50000
MIN_PHONE_LENGTH = 10
DEFAULT_ROLE = "student"


@dataclass(frozen=True)
class RegisteredUser:
    """Результат сценария регистрации (без привязки к ORM)."""

    id: int
    email: str


class UserRepositoryProtocol(Protocol):
    """Контракт, которого сервис ожидает от слоя хранения данных."""

    def get_user_by_id(self, user_id: int): ...

    def get_user_by_email(self, email: str): ...

    def create_user(self, email: str, phone, password_hash: str, role_id): ...

    def update_user_balance(self, user, new_balance: float) -> float: ...

    def get_role_id(self, name: str): ...


class UserService:
    """Бизнес-правила работы с пользователями."""

    def __init__(self, repository: UserRepositoryProtocol):
        # Внедрение зависимости через конструктор.
        self.repository = repository

    def register_user(self, email: str, password: str, phone: Optional[str]) -> RegisteredUser:
        """
        Регистрация нового пользователя.
        Пароль сохраняется только в виде bcrypt-хеша; новому пользователю
        назначается роль student (RBAC).
        Формат email уже проверен Pydantic (EmailStr) в транспортном слое;
        здесь проверяется только уникальность — это бизнес-правило.
        """
        if self.repository.get_user_by_email(email):
            raise BusinessRuleError(
                "Пользователь с таким email уже зарегистрирован"
            )

        # Порядок проверок сохранён как в MVP: сначала уникальность email.
        if phone and len(phone) < MIN_PHONE_LENGTH:
            raise BusinessRuleError("Некорректный формат номера телефона")

        password_hash = hash_password(password)
        role_id = self.repository.get_role_id(DEFAULT_ROLE)

        user_id, user_email = self.repository.create_user(
            email, phone, password_hash, role_id
        )
        return RegisteredUser(id=user_id, email=user_email)

    def top_up_balance(self, user_id: int, amount: float, current_user) -> float:
        """
        Пополнение баланса. Возвращает новый баланс.
        Положительность суммы проверяется в router (техническая валидация).

        Защита от IDOR: студент может пополнять только свой баланс
        (user_id из пути сравнивается с id из JWT). Администратору
        разрешено пополнять любой баланс.
        """
        if current_user.id != user_id and current_user.role.name != "admin":
            raise ForbiddenError("Можно пополнять только свой баланс")

        user = self.repository.get_user_by_id(user_id)
        if not user:
            raise NotFoundError("Пользователь не найден")

        if user.is_blocked:
            raise ForbiddenError("Пользователь заблокирован. Пополнение невозможно")

        if amount > MAX_TOPUP_AMOUNT:
            raise BusinessRuleError("Максимальная сумма пополнения — 50000 рублей")

        return self.repository.update_user_balance(user, user.balance + amount)
