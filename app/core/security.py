"""
Хеширование паролей (bcrypt).

Хеш необратим: из него нельзя восстановить исходный пароль, но можно
проверить соответствие. Соль генерируется автоматически для каждого
пароля, поэтому одинаковые пароли дают разные хеши.
"""
from passlib.context import CryptContext

# bcrypt — современный алгоритм с солью; deprecated="auto" помечает
# устаревшие схемы и позволяет бесшовно обновлять хеши при логине.
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    """Возвращает bcrypt-хеш пароля."""
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    """Проверяет соответствие пароля хешу."""
    return pwd_context.verify(plain, hashed)
