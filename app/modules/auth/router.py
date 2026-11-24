"""
Транспортный слой модуля auth (Круг 4: Frameworks & Drivers).

Принимает HTTP-запрос, выполняет техническую валидацию, собирает слои
и переводит исключения сервиса в HTTP-статусы.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session

from app.core.rate_limit import rate_limit
from app.database import get_db
from app.modules.auth.repository import AuthRepository
from app.modules.auth.service import (
    AuthService,
    InvalidCredentialsError,
    InvalidRefreshTokenError,
)

router = APIRouter()


class LoginSchema(BaseModel):
    """Схема запроса на вход."""
    email: EmailStr
    password: str


class RefreshSchema(BaseModel):
    """Схема запроса на обновление токена."""
    refresh_token: str


@router.post("/login", dependencies=[Depends(rate_limit(max_requests=5, window=60))])
def login(payload: LoginSchema, db: Session = Depends(get_db)):
    """
    Аутентификация пользователя и выдача пары токенов.
    Не более 5 попыток входа за 60 секунд с одного IP (rate limiting
    на Redis, защита от брутфорса) — превышение даёт HTTP 429.
    """
    service = AuthService(AuthRepository(db))
    try:
        tokens = service.login(payload.email, payload.password)
    except InvalidCredentialsError as e:
        raise HTTPException(status_code=401, detail=str(e))
    return tokens


@router.post("/refresh")
def refresh(payload: RefreshSchema, db: Session = Depends(get_db)):
    """Обновление access-токена по refresh-токену."""
    service = AuthService(AuthRepository(db))
    try:
        tokens = service.refresh(payload.refresh_token)
    except InvalidRefreshTokenError as e:
        raise HTTPException(status_code=401, detail=str(e))
    return tokens
