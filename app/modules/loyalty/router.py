from datetime import datetime
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.exc import IntegrityError

from app.core.dependencies import require_role
from app.database import get_db
from app.modules.loyalty.repository import LoyaltyRepository

router = APIRouter()


class PromoCreate(BaseModel):
    code: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    discount_type: Literal["percent", "fixed"]
    discount_value: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    valid_from: datetime
    valid_until: datetime
    max_uses: int = Field(gt=0)
    min_order_amount: Decimal = Field(default=Decimal("0"), ge=0, max_digits=12, decimal_places=2)
    once_per_user: bool = True
    is_active: bool = True

    @model_validator(mode="after")
    def check_rules(self):
        if not self.valid_from.tzinfo or not self.valid_until.tzinfo:
            raise ValueError("Укажите часовой пояс срока действия")
        if self.valid_until <= self.valid_from:
            raise ValueError("Конец срока должен быть позже начала")
        if self.discount_type == "percent" and self.discount_value > 100:
            raise ValueError("Скидка не может превышать 100%")
        self.code = self.code.upper()
        return self


@router.post("/promos", status_code=201)
def create_promo(payload: PromoCreate, user=Depends(require_role("admin")), db=Depends(get_db)):
    try:
        promo = LoyaltyRepository(db).create_promo(payload.model_dump())
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(409, "Промокод уже существует") from error
    return {"id": promo.id, "code": promo.code}


@router.get("/me")
def account(user=Depends(require_role("student")), db=Depends(get_db)):
    repo = LoyaltyRepository(db)
    repo.user(user.id)
    account = repo.account(user.id)
    result = {"balance": account.balance, "total_earned": account.total_earned, "tier": account.tier}
    repo.commit()
    return result


@router.get("/me/transactions")
def transactions(user=Depends(require_role("student")), db=Depends(get_db)):
    return [{"id": t.id, "order_id": t.order_id, "amount": t.amount,
             "type": t.type, "idempotency_key": t.idempotency_key}
            for t in LoyaltyRepository(db).history(user.id)]
