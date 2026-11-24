"""
gRPC-клиент модуля delivery (ЛР № 6: вынос модуля delivery в gRPC).

Адаптер между HTTP-роутером и микросервисом доставки: маршруты FastAPI
звонят сюда, а не в таблицу deliveries (шаг Strangler Fig — «вырезаем»
кусок монолита и перенаправляем трафик на новый сервис).

Сетевые ошибки gRPC (критерий 8) переводятся в понятные исключения,
которые router отображает в HTTP-статусы:
  NOT_FOUND          → NotFoundError     → 404
  PERMISSION_DENIED  → ForbiddenError    → 403
  INVALID_ARGUMENT   → BusinessRuleError → 400
  UNAVAILABLE        → ConnectionError   → 503 (сервис упал/недоступен)
  DEADLINE_EXCEEDED  → TimeoutError      → 504 (таймаут)
  прочие             → RuntimeError      → 500

Каждый RPC ограничен таймаутом (по умолчанию 5 с): зависший вызов не
висит вечно и не съедает поток uvicorn.
"""
from typing import Iterable, Iterator

import grpc

import delivery_pb2
import delivery_pb2_grpc
from app.core.config import settings
from app.core.exceptions import BusinessRuleError, ForbiddenError, NotFoundError

DEFAULT_TIMEOUT_SECONDS = 5.0
# Таймаут стриминга TrackDelivery: три статуса по 2 секунды + запас.
TRACK_TIMEOUT_SECONDS = 30.0


class DeliveryGrpcClient:
    """Обёртка над сгенерированным Stub: dicts на входе, dicts на выходе."""

    def __init__(
        self,
        host: str | None = None,
        port: int | None = None,
        stub: delivery_pb2_grpc.DeliveryServiceStub | None = None,
    ):
        # stub инжектируется тестами (unit без сети); в проде строим сами.
        if stub is not None:
            self.stub = stub
            return
        # insecure_channel — только для локальной разработки; в проде TLS.
        self.channel = grpc.insecure_channel(
            f"{host or settings.DELIVERY_SERVICE_HOST}:{port or settings.DELIVERY_SERVICE_PORT}"
        )
        self.stub = delivery_pb2_grpc.DeliveryServiceStub(self.channel)

    # --- unary -------------------------------------------------------------

    def assign_courier(self, order_id: int, courier_id: int) -> dict:
        """Назначение курьера на заказ (роль admin проверяет HTTP-слой)."""
        try:
            response = self.stub.AssignCourier(
                delivery_pb2.AssignCourierRequest(
                    order_id=order_id, courier_id=courier_id
                ),
                timeout=DEFAULT_TIMEOUT_SECONDS,
            )
            return {"delivery_id": response.delivery_id, "status": response.status}
        except grpc.RpcError as e:
            self._handle_error(e)

    def complete_delivery(self, delivery_id: int, courier_id: int) -> dict:
        """Завершение доставки (courier_id из JWT — проверка IDOR на сервере)."""
        try:
            response = self.stub.CompleteDelivery(
                delivery_pb2.CompleteDeliveryRequest(
                    delivery_id=delivery_id, courier_id=courier_id
                ),
                timeout=DEFAULT_TIMEOUT_SECONDS,
            )
            return {
                "delivery_status": response.delivery_status,
                "order_status": response.order_status,
            }
        except grpc.RpcError as e:
            self._handle_error(e)

    def get_delivery(self, order_id: int) -> dict:
        """Доставка по ID заказа."""
        try:
            response = self.stub.GetDelivery(
                delivery_pb2.GetDeliveryRequest(order_id=order_id),
                timeout=DEFAULT_TIMEOUT_SECONDS,
            )
            return {
                "delivery_id": response.delivery_id,
                "order_id": response.order_id,
                "courier_id": response.courier_id,
                "status": response.status,
            }
        except grpc.RpcError as e:
            self._handle_error(e)

    def list_deliveries(self, courier_id: int) -> list[dict]:
        """Доставки курьера (для GET /delivery/my, IDOR-фильтрация на сервере)."""
        try:
            response = self.stub.ListDeliveries(
                delivery_pb2.ListDeliveriesRequest(courier_id=courier_id),
                timeout=DEFAULT_TIMEOUT_SECONDS,
            )
            return [
                {
                    "delivery_id": d.delivery_id,
                    "order_id": d.order_id,
                    "status": d.status,
                }
                for d in response.deliveries
            ]
        except grpc.RpcError as e:
            self._handle_error(e)

    # --- стриминги ----------------------------------------------------------

    def track_delivery(self, order_id: int) -> Iterator[dict]:
        """
        Server Streaming (критерий 9): чтение потока статусов доставки.
        Генератор: каждое значение приходит с сервера по мере появления
        (in_progress → on_the_way → delivered, шаг 2 с).
        """
        try:
            stream = self.stub.TrackDelivery(
                delivery_pb2.TrackDeliveryRequest(order_id=order_id),
                timeout=TRACK_TIMEOUT_SECONDS,
            )
            for status in stream:
                yield {
                    "order_id": status.order_id,
                    "status": status.status,
                    "note": status.note,
                }
        except grpc.RpcError as e:
            self._handle_error(e)

    def chat_with_courier(
        self, outgoing: Iterable[dict], order_id: int = 0
    ) -> Iterator[dict]:
        """
        Bidirectional Streaming (критерий 10): чат «клиент ↔ курьер».
        Принимает поток исходящих сообщений, возвращает поток ответов —
        сервер отвечает по мере получения, без закрытия канала.
        """

        def _requests() -> Iterator[delivery_pb2.ChatMessage]:
            for message in outgoing:
                yield delivery_pb2.ChatMessage(
                    order_id=message.get("order_id", order_id),
                    author=message.get("author", "client"),
                    text=message["text"],
                )

        try:
            for reply in self.stub.ChatWithCourier(_requests()):
                yield {
                    "order_id": reply.order_id,
                    "author": reply.author,
                    "text": reply.text,
                }
        except grpc.RpcError as e:
            self._handle_error(e)

    # --- ошибки --------------------------------------------------------------

    @staticmethod
    def _handle_error(e: grpc.RpcError) -> None:
        """Маппинг статуса gRPC на доменное/сетевое исключение (критерий 8)."""
        code = e.code()
        details = e.details() or "gRPC error"
        if code == grpc.StatusCode.NOT_FOUND:
            raise NotFoundError(details)
        if code == grpc.StatusCode.PERMISSION_DENIED:
            raise ForbiddenError(details)
        if code == grpc.StatusCode.INVALID_ARGUMENT:
            raise BusinessRuleError(details)
        if code in (grpc.StatusCode.UNAVAILABLE, grpc.StatusCode.CANCELLED):
            raise ConnectionError("Delivery service is unavailable")
        if code == grpc.StatusCode.DEADLINE_EXCEEDED:
            raise TimeoutError("Delivery service timeout")
        raise RuntimeError(f"gRPC error: {details}")
