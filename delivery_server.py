"""
Микросервис доставки (ЛР № 6: вынос модуля delivery в gRPC).

Strangler Fig на практике: модуль delivery вырезан из монолита и
работает отдельным процессом на порту 50051 (HTTP/2, Protocol Buffers).
«Старая» часть системы (FastAPI) продолжает работать и обращается к
доставкам уже через gRPC-клиент, а не напрямую к таблице deliveries.

ВАЖНО (отличие от шаблона методички): бизнес-правила НЕ переписаны
внутри сервера. Servicer — тонкий транспортный слой: он принимает
protobuf-запрос, вызывает тот же DeliveryService (Круги 1–2) с тем же
DeliveryRepository (Круг 3) и переводит доменные исключения в статусы
gRPC. Так правило «один курьер — одна активная доставка», проверка
оплаченного заказа и защита от IDOR живут в одном месте, а не в двух.

Обработка сетевых ошибок (критерий 8): доменные исключения → статусы
gRPC (NOT_FOUND / PERMISSION_DENIED / INVALID_ARGUMENT), чтобы клиент
различал причины сбоя и честно транслировал их в HTTP.
"""
import logging
import time
from concurrent import futures

import grpc

import delivery_pb2
import delivery_pb2_grpc
from app.core.config import settings
from app.core.exceptions import BusinessRuleError, ForbiddenError, NotFoundError
from app.database import SessionLocal
from app.modules.delivery.repository import DeliveryRepository
from app.modules.delivery.service import DeliveryService

logger = logging.getLogger("delivery_server")

# Этапы эмуляции продвижения доставки для TrackDelivery (критерий 9).
TRACK_STAGES = (
    ("in_progress", "Курьер назначен, собирает заказ"),
    ("on_the_way", "Курьер выехал к клиенту"),
    ("delivered", "Заказ доставлен"),
)
TRACK_STEP_SECONDS = 2.0  # пауза между статусами в потоке


class DeliveryGrpcService(delivery_pb2_grpc.DeliveryServiceServicer):
    """Реализация Servicer: protobuf ↔ домен (DeliveryService)."""

    # --- вспомогательное -------------------------------------------------

    @staticmethod
    def _abort(context, exc: Exception) -> None:
        """Доменное исключение → статус gRPC (сетевой протокол ошибок)."""
        if isinstance(exc, NotFoundError):
            context.abort(grpc.StatusCode.NOT_FOUND, str(exc))
        if isinstance(exc, ForbiddenError):
            context.abort(grpc.StatusCode.PERMISSION_DENIED, str(exc))
        if isinstance(exc, BusinessRuleError):
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, str(exc))
        context.abort(grpc.StatusCode.INTERNAL, str(exc))

    # --- unary-методы ----------------------------------------------------

    def AssignCourier(self, request, context):  # noqa: N802 (grpc-конвенция)
        """Назначение курьера на заказ. Правила — в DeliveryService."""
        try:
            with SessionLocal() as db:
                delivery_id = DeliveryService(DeliveryRepository(db)).assign_courier(
                    order_id=request.order_id, courier_id=request.courier_id
                )
            return delivery_pb2.AssignCourierResponse(
                delivery_id=delivery_id, status="in_progress"
            )
        except (NotFoundError, ForbiddenError, BusinessRuleError) as e:
            self._abort(context, e)

    def CompleteDelivery(self, request, context):  # noqa: N802
        """
        Завершение доставки. courier_id приходит в запросе (клиент берёт
        его из JWT): проверка владельца (IDOR) — в Service-слое.
        """
        try:
            with SessionLocal() as db:
                service = DeliveryService(DeliveryRepository(db))
                service.complete_delivery(
                    delivery_id=request.delivery_id, current_user_id=request.courier_id
                )
                delivery = service.repository.get_delivery(request.delivery_id)
                order = service.repository.get_order(delivery.order_id)
                return delivery_pb2.CompleteDeliveryResponse(
                    delivery_status=delivery.status, order_status=order.status
                )
        except (NotFoundError, ForbiddenError, BusinessRuleError) as e:
            self._abort(context, e)

    def GetDelivery(self, request, context):  # noqa: N802
        """Информация о доставке по ID заказа."""
        with SessionLocal() as db:
            delivery = DeliveryRepository(db).get_delivery_by_order_id(
                request.order_id
            )
        if delivery is None:
            self._abort(context, NotFoundError("Доставка не найдена"))
            return
        return delivery_pb2.GetDeliveryResponse(
            delivery_id=delivery.id,
            order_id=delivery.order_id,
            courier_id=delivery.courier_id,
            status=delivery.status,
        )

    def ListDeliveries(self, request, context):  # noqa: N802
        """Список доставок курьера (для GET /delivery/my)."""
        with SessionLocal() as db:
            deliveries = DeliveryRepository(db).list_deliveries_by_courier(
                request.courier_id
            )
        return delivery_pb2.DeliveryListResponse(
            deliveries=[
                delivery_pb2.DeliveryShort(
                    delivery_id=d.id, order_id=d.order_id, status=d.status
                )
                for d in deliveries
            ]
        )

    # --- стриминги --------------------------------------------------------

    def TrackDelivery(self, request, context):  # noqa: N802
        """
        Server Streaming (критерий 9): сервер сам отправляет серию
        статусов доставки (in_progress → on_the_way → delivered) с паузой
        2 секунды. Клиент просто читает поток — никаких повторных запросов.
        """
        with SessionLocal() as db:
            delivery = DeliveryRepository(db).get_delivery_by_order_id(
                request.order_id
            )
        if delivery is None:
            self._abort(context, NotFoundError("Доставка не найдена"))
            return

        for status, note in TRACK_STAGES:
            # Клиент мог отключиться (отмена запроса) — не пишем «в никуда».
            if not context.is_active():
                return
            logger.info("TrackDelivery order=%s → %s", request.order_id, status)
            yield delivery_pb2.DeliveryStatus(
                order_id=request.order_id, status=status, note=note
            )
            time.sleep(TRACK_STEP_SECONDS)

    def ChatWithCourier(self, request_iterator, context):  # noqa: N802
        """
        Bidirectional Streaming (критерий 10): пока клиент пишет сообщения
        в поток, сервер тут же читает их и отправляет ответы — обе стороны
        работают с открытым каналом одновременно (чат «клиент ↔ курьер»).
        """
        for message in request_iterator:
            if not context.is_active():
                return
            text = message.text.lower()
            if "где" in text:
                reply = "Я в пути, буду через 10 минут"
            elif "спасибо" in text or "благодар" in text:
                reply = "Всегда пожалуйста! Приятного аппетита"
            else:
                reply = "Принял ваше сообщение, свяжусь при необходимости"
            logger.info(
                "ChatWithCourier order=%s: %r → %r",
                message.order_id, message.text, reply,
            )
            yield delivery_pb2.ChatMessage(
                order_id=message.order_id, author="courier", text=reply
            )


def serve() -> None:
    """Запуск gRPC-сервера доставки."""
    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s: %(levelname)s/%(name)s] %(message)s",
    )
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    delivery_pb2_grpc.add_DeliveryServiceServicer_to_server(
        DeliveryGrpcService(), server
    )
    # Порт берётся из конфига (DELIVERY_SERVICE_PORT), чтобы сервер и
    # клиент всегда читали его из одного источника.
    address = f"[::]:{settings.DELIVERY_SERVICE_PORT}"
    bound = server.add_insecure_port(address)
    if not bound:  # на системах без IPv6 — привязка к IPv4
        address = f"0.0.0.0:{settings.DELIVERY_SERVICE_PORT}"
        bound = server.add_insecure_port(address)
    if not bound:
        raise RuntimeError(f"Не удалось занять порт {settings.DELIVERY_SERVICE_PORT}")
    server.start()
    print(f"Delivery gRPC Server running on {address}", flush=True)
    try:
        server.wait_for_termination()
    except KeyboardInterrupt:
        server.stop(0)


if __name__ == "__main__":
    serve()
