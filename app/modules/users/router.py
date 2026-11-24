"""
Транспортный слой модуля users (Круг 4: Frameworks & Drivers).

Принимает HTTP-запрос, выполняет техническую валидацию, собирает слои
и переводит исключения сервиса в HTTP-статусы.

Регистрация доступна всем (анонимно), пополнение баланса требует JWT,
а user_id в пути сверяется с токеном (защита от IDOR) — в Service-слое.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user
from app.core.exceptions import ForbiddenError, NotFoundError
from app.database import get_db
from app.models import User
from app.schemas import UserCreate
from app.modules.users.repository import UserRepository
from app.modules.users.service import UserService

router = APIRouter()


def _to_http_error(error: ValueError) -> HTTPException:
    """Трансляция доменного исключения в HTTP-статус."""
    if isinstance(error, NotFoundError):
        return HTTPException(status_code=404, detail=str(error))
    if isinstance(error, ForbiddenError):
        return HTTPException(status_code=403, detail=str(error))
    return HTTPException(status_code=400, detail=str(error))


@router.post("/register")
def register_user(payload: UserCreate, db: Session = Depends(get_db)):
    # Формат email проверен Pydantic (EmailStr) ещё до входа в функцию.
    service = UserService(UserRepository(db))

    try:
        user = service.register_user(
            email=payload.email, password=payload.password, phone=payload.phone
        )
    except ValueError as e:
        raise _to_http_error(e)

    return {
        "status": "success",
        "user_id": user.id,
        "email": user.email,
    }


@router.post("/{user_id}/topup")
def top_up_balance(
    user_id: int,
    amount: float,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    # Техническая проверка положительности суммы пополнения.
    if amount <= 0:
        raise HTTPException(
            status_code=400,
            detail="Сумма пополнения должна быть положительной",
        )

    service = UserService(UserRepository(db))

    try:
        # current_user передаётся в сервис для проверки прав на объект (IDOR).
        new_balance = service.top_up_balance(
            user_id=user_id, amount=amount, current_user=current_user
        )
    except ValueError as e:
        raise _to_http_error(e)

    return {"status": "success", "new_balance": new_balance}
