from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.core.dependencies import require_role
from app.core.exceptions import ForbiddenError, NotFoundError
from app.database import get_db
from app.modules.payments.gateway import StripeGateway, GatewayError
from app.modules.payments.repository import PaymentRepository
from app.modules.payments.service import PaymentService

router = APIRouter()


class PaymentCreate(BaseModel):
    order_id: int = Field(gt=0)
    amount: Decimal = Field(gt=0, max_digits=12, decimal_places=2)


def gateway():
    return StripeGateway()


def view(payment):
    return {"id": payment.id, "order_id": payment.order_id, "amount": payment.amount,
            "currency": payment.currency, "status": payment.status,
            "gateway_payment_id": payment.gateway_payment_id,
            "client_secret": payment.client_secret, "error_code": payment.error_code}


@router.post("")
def initiate(payload: PaymentCreate,
             idempotency_key: Annotated[str, Header(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")],
             user=Depends(require_role("student")), db=Depends(get_db), adapter=Depends(gateway)):
    try:
        payment = PaymentService(PaymentRepository(db), adapter, settings.PAYMENT_CURRENCY).initiate(
            payload.order_id, user.id, payload.amount, idempotency_key)
    except NotFoundError as error:
        raise HTTPException(404, str(error)) from error
    except ForbiddenError as error:
        raise HTTPException(403, str(error)) from error
    except (ValueError, IntegrityError) as error:
        db.rollback()
        raise HTTPException(409, str(error) if isinstance(error, ValueError) else "Idempotency-Key уже занят") from error
    return view(payment)


@router.post("/webhook")
async def webhook(request: Request, stripe_signature: str = Header(default=""),
                  db=Depends(get_db), adapter=Depends(gateway)):
    try:
        event = adapter.verify_webhook(await request.body(), stripe_signature)
        # Синхронная работа с БД выполняется в потоке, не блокируя event loop.
        from starlette.concurrency import run_in_threadpool
        result = await run_in_threadpool(
            PaymentService(PaymentRepository(db), adapter).webhook, event, request.state.correlation_id)
    except GatewayError as error:
        raise HTTPException(503 if error.code == "webhook_not_configured" else 400, error.code) from error
    except NotFoundError as error:
        raise HTTPException(503, str(error)) from error
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    return {"status": result}


@router.get("/{payment_id}")
def get_payment(payment_id: int, user=Depends(require_role("student")), db=Depends(get_db)):
    payment = PaymentRepository(db).get(payment_id)
    if payment is None:
        raise HTTPException(404, "Платёж не найден")
    if payment.user_id != user.id:
        raise HTTPException(403, "Можно просматривать только свои платежи")
    return view(payment)
