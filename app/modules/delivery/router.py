"""
Транспортный слой модуля delivery (Круг 4: Frameworks & Drivers).

ЛР № 6 (Strangler Fig): HTTP-маршруты больше НЕ читают таблицу
deliveries — они вызывают микросервис доставки через DeliveryGrpcClient
(gRPC на настраиваемом порту, по умолчанию 50061). Роль модуля: HTTP → gRPC.

На HTTP-слое остаются только транспортные свойства точки входа:
RBAC (assign — admin, complete/my — courier), аутентификация по JWT и
перевод исключений в HTTP-статусы. courier_id всюду берётся из токена,
а не из запроса — защита от IDOR не покидает систему вместе с модулем.

Сетевые ошибки микросервиса честно транслируются наружу (критерий 8):
404 / 403 / 400 / 503 (сервис недоступен) / 504 (таймаут) / 500.
"""
import json
from typing import Iterator

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from app.core.dependencies import get_current_user, require_role
from app.core.exceptions import ForbiddenError, NotFoundError
from app.models import User
from app.schemas import DeliveryAssign
from app.modules.delivery.grpc_client import DeliveryGrpcClient

router = APIRouter()


def _raise_http(e: Exception) -> None:
    """Исключение клиента/домена → HTTP-статус (критерий 8)."""
    if isinstance(e, NotFoundError):
        raise HTTPException(status_code=404, detail=str(e))
    if isinstance(e, ForbiddenError):
        raise HTTPException(status_code=403, detail=str(e))
    if isinstance(e, ValueError):
        raise HTTPException(status_code=400, detail=str(e))
    if isinstance(e, ConnectionError):
        raise HTTPException(status_code=503, detail=str(e))
    if isinstance(e, TimeoutError):
        raise HTTPException(status_code=504, detail=str(e))
    raise HTTPException(status_code=500, detail=str(e))


@router.post("/assign")
def assign_courier(
    payload: DeliveryAssign,
    current_user: User = Depends(require_role("admin")),
):
    """Назначение курьера на заказ. Только для роли admin (RBAC)."""
    client = DeliveryGrpcClient()
    try:
        result = client.assign_courier(payload.order_id, payload.courier_id)
    except (NotFoundError, ForbiddenError, ValueError, ConnectionError, TimeoutError, RuntimeError) as e:
        _raise_http(e)
    return {"status": "success", **result}


@router.patch("/{delivery_id}/complete")
def complete_delivery(
    delivery_id: int,
    current_user: User = Depends(require_role("courier")),
):
    """
    Завершение доставки. Только для роли courier (RBAC).
    courier_id берётся из JWT; проверка владельца (IDOR) — в сервисе.
    """
    client = DeliveryGrpcClient()
    try:
        result = client.complete_delivery(delivery_id, current_user.id)
    except (NotFoundError, ForbiddenError, ValueError, ConnectionError, TimeoutError, RuntimeError) as e:
        _raise_http(e)
    return {"status": "success", **result}


@router.get("/my")
def my_deliveries(
    current_user: User = Depends(require_role("courier")),
):
    """Доставки текущего курьера. Чужие доставки не видны (IDOR)."""
    client = DeliveryGrpcClient()
    try:
        return client.list_deliveries(current_user.id)
    except (NotFoundError, ForbiddenError, ValueError, ConnectionError, TimeoutError, RuntimeError) as e:
        _raise_http(e)


@router.get("/{order_id}/track")
def track_delivery(
    order_id: int,
    current_user: User = Depends(get_current_user),
):
    """
    Server Streaming через HTTP (критерий 9): SSE-поток хода доставки.
    HTTP-клиент получает статусы по мере генерации их gRPC-сервером
    (in_progress → on_the_way → delivered, шаг 2 с), без поллинга.
    """
    client = DeliveryGrpcClient()

    def events() -> Iterator[str]:
        try:
            for status in client.track_delivery(order_id):
                yield f"data: {json.dumps(status, ensure_ascii=False)}\n\n"
        except (ConnectionError, TimeoutError, RuntimeError, ValueError) as e:
            yield f"data: {json.dumps({'error': str(e)}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache"},
    )


@router.post("/{order_id}/chat")
def chat_with_courier(
    order_id: int,
    message: str,
    current_user: User = Depends(get_current_user),
):
    """
    Bidirectional Streaming через HTTP (критерий 10, «одношаговый»
    вариант для Swagger): сообщение кладётся в открытый gRPC-канал,
    читается ответ курьера, канал закрывается. Полноценный чат с
    двусторонним обменом — scripts/grpc_chat.py.
    """
    client = DeliveryGrpcClient()
    try:
        replies = list(
            client.chat_with_courier(
                [{"order_id": order_id, "author": "client", "text": message}],
                order_id=order_id,
            )
        )
    except (NotFoundError, ForbiddenError, ValueError, ConnectionError, TimeoutError, RuntimeError) as e:
        _raise_http(e)
    if not replies:
        raise HTTPException(status_code=502, detail="Курьер не ответил")
    return {"status": "success", "reply": replies[0]}
