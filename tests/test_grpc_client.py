"""
Unit-тесты DeliveryGrpcClient (ЛР № 6, критерий 8: обработка сетевых ошибок).

Тесты не требуют ни сети, ни запущенного микросервиса: вместо
сгенерированного Stub в клиент инжектируется fake-stub, который
возвращает заготовленные ответы либо бросает grpc.RpcError с нужным
статусом (имитация сбоев gRPC-сервера).

Запуск: python -m pytest tests/test_grpc_client.py -q
"""
import unittest

import grpc

import delivery_pb2
import delivery_pb2_grpc
from app.core.exceptions import BusinessRuleError, ForbiddenError, NotFoundError
from app.modules.delivery.grpc_client import DeliveryGrpcClient


class FakeRpcError(grpc.RpcError):
    """Имитация исключения gRPC: code() и details() как у настоящего."""

    def __init__(self, code: grpc.StatusCode, details: str = "boom"):
        self._code = code
        self._details = details

    def code(self) -> grpc.StatusCode:
        return self._code

    def details(self) -> str:
        return self._details


class FakeStub:
    """
    Заглушка сгенерированного Stub. По умолчанию возвращает успешные
    ответы; в тестах подменяется вызываемый метод или ошибка.
    """

    def __init__(self):
        self.error: FakeRpcError | None = None
        self.last_request = None

    # --- unary -------------------------------------------------------------

    def AssignCourier(self, request, timeout=None):
        self.last_request = request
        if self.error:
            raise self.error
        return delivery_pb2.AssignCourierResponse(delivery_id=77, status="in_progress")

    def CompleteDelivery(self, request, timeout=None):
        self.last_request = request
        if self.error:
            raise self.error
        return delivery_pb2.CompleteDeliveryResponse(
            delivery_status="completed", order_status="delivered"
        )

    def GetDelivery(self, request, timeout=None):
        self.last_request = request
        if self.error:
            raise self.error
        return delivery_pb2.GetDeliveryResponse(
            delivery_id=77, order_id=5, courier_id=3, status="in_progress"
        )

    def ListDeliveries(self, request, timeout=None):
        self.last_request = request
        if self.error:
            raise self.error
        return delivery_pb2.DeliveryListResponse(
            deliveries=[delivery_pb2.DeliveryShort(delivery_id=77, order_id=5, status="in_progress")]
        )

    # --- стриминги ----------------------------------------------------------

    def TrackDelivery(self, request, timeout=None):
        self.last_request = request
        if self.error:
            raise self.error
        return iter(
            [
                delivery_pb2.DeliveryStatus(order_id=5, status="in_progress", note="собирает"),
                delivery_pb2.DeliveryStatus(order_id=5, status="on_the_way", note="выехал"),
                delivery_pb2.DeliveryStatus(order_id=5, status="delivered", note="доставлен"),
            ]
        )

    def ChatWithCourier(self, request_iterator, timeout=None):
        if self.error:
            raise self.error
        messages = list(request_iterator)
        return iter(
            [
                delivery_pb2.ChatMessage(
                    order_id=m.order_id, author="courier", text=f"Принял: {m.text}"
                )
                for m in messages
            ]
        )


def make_client(stub: FakeStub) -> DeliveryGrpcClient:
    """Клиент с инжектированным fake-stub: без канала и сети."""
    return DeliveryGrpcClient(stub=stub)


class UnaryMethodTests(unittest.TestCase):
    """Успешные вызовы unary-методов: protobuf-ответ → dict."""

    def setUp(self):
        self.stub = FakeStub()
        self.client = make_client(self.stub)

    def test_assign_courier_returns_dict(self):
        result = self.client.assign_courier(order_id=5, courier_id=3)
        self.assertEqual(result, {"delivery_id": 77, "status": "in_progress"})
        self.assertEqual(self.stub.last_request.order_id, 5)
        self.assertEqual(self.stub.last_request.courier_id, 3)

    def test_complete_delivery_returns_dict(self):
        result = self.client.complete_delivery(delivery_id=77, courier_id=3)
        self.assertEqual(
            result, {"delivery_status": "completed", "order_status": "delivered"}
        )

    def test_get_delivery_returns_dict(self):
        result = self.client.get_delivery(order_id=5)
        self.assertEqual(
            result,
            {
                "delivery_id": 77,
                "order_id": 5,
                "courier_id": 3,
                "status": "in_progress",
            },
        )

    def test_list_deliveries_returns_list(self):
        result = self.client.list_deliveries(courier_id=3)
        self.assertEqual(result, [{"delivery_id": 77, "order_id": 5, "status": "in_progress"}])


class ErrorMappingTests(unittest.TestCase):
    """
    Критерий 8: каждый статус ошибки gRPC транслируется в понятное
    исключение, которое router отображает в HTTP-статус.
    """

    def setUp(self):
        self.stub = FakeStub()
        self.client = make_client(self.stub)

    def _assert_raise(self, code, exception_cls):
        self.stub.error = FakeRpcError(code)
        with self.assertRaises(exception_cls):
            self.client.assign_courier(order_id=5, courier_id=3)

    def test_not_found_maps_to_not_found_error(self):
        self._assert_raise(grpc.StatusCode.NOT_FOUND, NotFoundError)

    def test_permission_denied_maps_to_forbidden_error(self):
        self._assert_raise(grpc.StatusCode.PERMISSION_DENIED, ForbiddenError)

    def test_invalid_argument_maps_to_business_rule_error(self):
        self._assert_raise(grpc.StatusCode.INVALID_ARGUMENT, BusinessRuleError)

    def test_unavailable_maps_to_connection_error(self):
        # gRPC-сервис упал: UNAVAILABLE → ConnectionError → HTTP 503
        self._assert_raise(grpc.StatusCode.UNAVAILABLE, ConnectionError)

    def test_cancelled_maps_to_connection_error(self):
        self._assert_raise(grpc.StatusCode.CANCELLED, ConnectionError)

    def test_deadline_exceeded_maps_to_timeout_error(self):
        # Таймаут: DEADLINE_EXCEEDED → TimeoutError → HTTP 504
        self._assert_raise(grpc.StatusCode.DEADLINE_EXCEEDED, TimeoutError)

    def test_unknown_code_maps_to_runtime_error(self):
        self._assert_raise(grpc.StatusCode.INTERNAL, RuntimeError)

    def test_error_message_preserved_from_details(self):
        self.stub.error = FakeRpcError(grpc.StatusCode.NOT_FOUND, "Доставка не найдена")
        with self.assertRaises(NotFoundError) as ctx:
            self.client.assign_courier(order_id=5, courier_id=3)
        self.assertIn("Доставка не найдена", str(ctx.exception))

    def test_error_mapping_on_complete_delivery(self):
        self.stub.error = FakeRpcError(grpc.StatusCode.UNAVAILABLE)
        with self.assertRaises(ConnectionError):
            self.client.complete_delivery(delivery_id=77, courier_id=3)

    def test_error_mapping_on_get_delivery(self):
        self.stub.error = FakeRpcError(grpc.StatusCode.DEADLINE_EXCEEDED)
        with self.assertRaises(TimeoutError):
            self.client.get_delivery(order_id=5)


class StreamingTests(unittest.TestCase):
    """Критерии 9 и 10: чтение Server Streaming и Bidirectional Streaming."""

    def setUp(self):
        self.stub = FakeStub()
        self.client = make_client(self.stub)

    def test_track_delivery_yields_all_statuses(self):
        statuses = list(self.client.track_delivery(order_id=5))
        self.assertEqual([s["status"] for s in statuses],
                         ["in_progress", "on_the_way", "delivered"])
        self.assertEqual(self.stub.last_request.order_id, 5)

    def test_track_delivery_propagates_error(self):
        self.stub.error = FakeRpcError(grpc.StatusCode.UNAVAILABLE)
        with self.assertRaises(ConnectionError):
            list(self.client.track_delivery(order_id=5))

    def test_chat_with_courier_sends_and_receives(self):
        outgoing = [
            {"order_id": 5, "author": "client", "text": "Где заказ?"},
            {"order_id": 5, "author": "client", "text": "Спасибо!"},
        ]
        replies = list(self.client.chat_with_courier(outgoing, order_id=5))
        self.assertEqual(len(replies), 2)
        self.assertEqual(replies[0]["author"], "courier")
        self.assertIn("Где заказ?", replies[0]["text"])
        self.assertIn("Спасибо!", replies[1]["text"])

    def test_chat_with_courier_propagates_error(self):
        self.stub.error = FakeRpcError(grpc.StatusCode.DEADLINE_EXCEEDED)
        with self.assertRaises(TimeoutError):
            list(self.client.chat_with_courier([{"text": "hi"}]))


if __name__ == "__main__":
    unittest.main()
