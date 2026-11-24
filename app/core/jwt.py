"""
Создание и проверка JWT-токенов.

Access-токен короткоживущий (15 минут): если украден — быстро сгорит.
Refresh-токен долгоживущий (7 дней): нужен для получения нового access.

Payload токена только закодирован в Base64, но НЕ зашифрован, поэтому
в нём нет персональных данных — только sub (id пользователя), exp и тип.
"""
from datetime import datetime, timedelta
from uuid import uuid4

from jose import JWTError, jwt

from app.core.config import settings

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 15
REFRESH_TOKEN_EXPIRE_DAYS = 7


def create_access_token(user_id: int) -> str:
    """Создаёт короткоживущий access-токен (15 минут)."""
    payload = {
        "sub": str(user_id),
        "exp": datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
        "type": "access",
        # Уникальный идентификатор токена: два токена, выпущенные
        # в одну секунду одному пользователю, остаются разными.
        "jti": uuid4().hex,
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=ALGORITHM)


def create_refresh_token(user_id: int) -> str:
    """Создаёт долгоживущий refresh-токен (7 дней)."""
    payload = {
        "sub": str(user_id),
        "exp": datetime.utcnow() + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS),
        "type": "refresh",
        "jti": uuid4().hex,
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> dict:
    """Декодирует и проверяет подпись токена. Бросает JWTError при ошибке."""
    return jwt.decode(token, settings.SECRET_KEY, algorithms=[ALGORITHM])


def try_decode_token(token: str):
    """
    Декодирует токен и возвращает payload либо None, если токен
    невалиден (подпись, срок действия, формат). Удобно для вызова
    из внутренних слоёв: им не нужно знать про библиотеку jose.
    """
    try:
        return jwt.decode(token, settings.SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        return None
