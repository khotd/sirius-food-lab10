"""
Слой бизнес-логики модуля delivery (Круги 1 и 2: Entities и Use Cases).

ВАЖНО: в этом файле запрещены импорты fastapi и sqlalchemy.
"""
from typing import Protocol

from app.core.exceptions import BusinessRuleError, ForbiddenError, NotFoundError


class DeliveryRepositoryProtocol(Protocol):
    """Контракт, которого сервис ожидает от слоя хранения данных."""

    def get_order(self, order_id: int): ...

    def get_courier(self, courier_id: int): ...

    def get_active_delivery(self, courier_id: int): ...

    def create_delivery(self, order_id: int, courier_id: int) -> int: ...

    def get_delivery_by_order_id(self, order_id: int): ...

    def get_delivery(self, delivery_id: int): ...

    def complete_delivery(self, delivery) -> None: ...

    def list_deliveries_by_courier(self, courier_id: int) -> list: ...


class DeliveryService:
    """Бизнес-правила работы с доставками."""

    def __init__(self, repository: DeliveryRepositoryProtocol):
        self.repository = repository

    def assign_courier(self, order_id: int, courier_id: int) -> int:
        """
        Назначение курьера на заказ. Доступно роли admin (проверка роли —
        на транспортном слое). Возвращает идентификатор доставки.
        """
        order = self.repository.get_order(order_id)
        if not order:
            raise NotFoundError("Заказ не найден")

        # Бизнес-правило: назначить курьера можно только на оплаченный заказ.
        if order.status != "paid":
            raise BusinessRuleError(
                "Курьера можно назначить только на оплаченный заказ"
            )
        if self.repository.get_delivery_by_order_id(order_id):
            raise BusinessRuleError("Заказу уже назначена доставка")

        courier = self.repository.get_courier(courier_id)
        if not courier:
            raise NotFoundError("Курьер не найден")
        if courier.is_blocked:
            raise ForbiddenError(
                "Курьер заблокирован и не может быть назначен"
            )

        # Бизнес-правило: один курьер — одна активная доставка.
        if self.repository.get_active_delivery(courier_id):
            raise BusinessRuleError("У курьера уже есть активная доставка")

        return self.repository.create_delivery(order_id, courier_id)

    def complete_delivery(self, delivery_id: int, current_user_id: int) -> None:
        """
        Завершение доставки. Доступно роли courier (проверка роли —
        на транспортном слое).

        Защита от IDOR: курьер может завершать только свою доставку.
        Проверка прав на конкретный объект — в Service-слое.
        """
        delivery = self.repository.get_delivery(delivery_id)
        if not delivery:
            raise NotFoundError("Доставка не найдена")

        if delivery.courier_id != current_user_id:
            raise ForbiddenError("Можно завершать только свои доставки")

        if delivery.status == "completed":
            raise BusinessRuleError("Доставка уже завершена")
        if delivery.status == "cancelled":
            raise BusinessRuleError("Отменённую доставку нельзя завершить")

        self.repository.complete_delivery(delivery)

    def my_deliveries(self, courier_id: int) -> list[dict]:
        """
        Доставки текущего курьера. Курьер видит только свои доставки —
        выборка фильтруется по id из JWT, чужие доставки не возвращаются.
        """
        deliveries = self.repository.list_deliveries_by_courier(courier_id)
        return [
            {
                "delivery_id": delivery.id,
                "order_id": delivery.order_id,
                "status": delivery.status,
            }
            for delivery in deliveries
        ]
