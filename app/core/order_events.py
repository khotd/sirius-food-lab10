"""Контракт событий заказа, общий для отправителя и потребителей."""
from typing import Annotated
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, PositiveInt

ORDER_PAID = "order.paid"
DELIVERY_FAILED = "delivery.failed"
ORDER_CANCELLED = "order.cancelled"


class OrderEvent(BaseModel):
    event_id: UUID
    order_id: PositiveInt
    customer_id: PositiveInt
    amount: Annotated[float, Field(ge=0, allow_inf_nan=False)]


def new_event(order_id: int, customer_id: int, amount: float) -> dict:
    return OrderEvent(
        event_id=uuid4(), order_id=order_id, customer_id=customer_id, amount=amount
    ).model_dump(mode="json")
