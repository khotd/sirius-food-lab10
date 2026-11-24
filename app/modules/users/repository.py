"""
Инфраструктурный слой модуля users (Круг 3: Interface Adapters).

Единственный файл модуля, который знает о SQLAlchemy и структуре таблиц.
"""
from sqlalchemy.orm import Session

from app.models import Role, User


class UserRepository:
    """Операции ввода-вывода для пользователей. Бизнес-правил не содержит."""

    def __init__(self, db: Session):
        self.db = db

    def get_user_by_id(self, user_id: int):
        """Извлечение пользователя по идентификатору (с ролью)."""
        return self.db.query(User).filter(User.id == user_id).first()

    def get_user_by_email(self, email: str):
        """Извлечение пользователя по email (для проверки уникальности)."""
        return self.db.query(User).filter(User.email == email).first()

    def get_role_id(self, name: str):
        """Идентификатор роли по названию (RBAC)."""
        role = self.db.query(Role).filter(Role.name == name).first()
        return role.id if role else None

    def create_user(self, email: str, phone, password_hash: str, role_id: int):
        """
        Создание пользователя с нулевым балансом, хешем пароля и ролью.
        Возвращает (id, email).
        """
        new_user = User(
            email=email,
            phone=phone,
            balance=0.0,
            is_blocked=False,
            password_hash=password_hash,
            role_id=role_id,
        )
        self.db.add(new_user)
        self.db.commit()
        self.db.refresh(new_user)
        return new_user.id, new_user.email

    def update_user_balance(self, user, new_balance: float) -> float:
        """Сохранение нового баланса. Возвращает баланс после записи."""
        user.balance = new_balance
        self.db.add(user)
        self.db.commit()
        self.db.refresh(user)
        return user.balance
