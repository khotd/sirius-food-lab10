"""
Dependencies транспортного слоя для аутентификации и авторизации (RBAC).

get_current_user отвечает на вопрос «Кто ты?» (аутентификация):
извлекает и проверяет JWT из заголовка Authorization.

require_role отвечает на вопрос «Что тебе можно?» (авторизация):
проверяет роль аутентифицированного пользователя.
"""
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.jwt import try_decode_token
from app.database import get_db
from app.models import User

# Схема извлечения токена из заголовка Authorization: Bearer <token>.
# HTTPBearer (в отличие от OAuth2PasswordBearer) даёт в Swagger-окне
# Authorize одиночное поле «Value» для вставки готового токена, а не
# форму username/password (та отправляет form-data и даёт 422).
bearer_scheme = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    """
    Извлекает текущего пользователя из JWT.
    Возбуждает HTTPException 401, если заголовок Authorization отсутствует
    или токен невалиден, либо пользователь не найден,
    и 403, если пользователь заблокирован.
    """
    if credentials is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        payload = try_decode_token(credentials.credentials)
        user_id = int(payload["sub"])
    except (TypeError, KeyError, ValueError):
        raise HTTPException(status_code=401, detail="Невалидный токен")

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=401, detail="Пользователь не найден")
    if user.is_blocked:
        raise HTTPException(status_code=403, detail="Пользователь заблокирован")
    return user


def require_role(*allowed_roles: str):
    """
    Фабрика dependency для проверки роли (RBAC).
    Возбуждает HTTPException 403, если роль пользователя не входит
    в число разрешённых.
    """
    def checker(current_user: User = Depends(get_current_user)) -> User:
        if current_user.role.name not in allowed_roles:
            raise HTTPException(status_code=403, detail="Доступ запрещён")
        return current_user

    return checker
