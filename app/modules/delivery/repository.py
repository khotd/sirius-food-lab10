"""
Инфраструктурный слой модуля delivery (Круг 3: Interface Adapters).

Единственный файл модуля, который знает о SQLAlchemy и структуре таблиц.
"""
from sqlalchemy.orm import Session

from app.models import Delivery, Order, User


class DeliveryRepository:
    """Операции ввода-вывода для доставок. Бизнес-правил не содержит."""

    def __init__(self, db: Session):
        self.db = db

    def get_order(self, order_id: int):
        return self.db.query(Order).filter(Order.id == order_id).with_for_update().first()

    def get_courier(self, courier_id: int):
        courier = self.db.query(User).filter(User.id == courier_id).with_for_update().first()
        return courier if courier and courier.role.name == "courier" else None

    def get_active_delivery(self, courier_id: int):
        return (
            self.db.query(Delivery)
            .filter(
                Delivery.courier_id == courier_id,
                Delivery.status == "in_progress",
            )
            .first()
        )

    def create_delivery(self, order_id: int, courier_id: int) -> int:
        new_delivery = Delivery(
            order_id=order_id,
            courier_id=courier_id,
            status="in_progress",
        )
        self.db.add(new_delivery)
        self.db.commit()
        self.db.refresh(new_delivery)
        return new_delivery.id

    def get_delivery(self, delivery_id: int):
        return self.db.query(Delivery).filter(Delivery.id == delivery_id).first()

    def get_delivery_by_order_id(self, order_id: int):
        """Последняя доставка по ID заказа (для GetDelivery / TrackDelivery)."""
        return (
            self.db.query(Delivery)
            .filter(Delivery.order_id == order_id)
            .order_by(Delivery.id.desc())
            .first()
        )

    def complete_delivery(self, delivery) -> None:
        delivery.status = "completed"

        # Синхронное обновление статуса заказа.
        order = self.get_order(delivery.order_id)
        if order:
            order.status = "delivered"
            self.db.add(order)

        self.db.add(delivery)
        self.db.commit()

    def list_deliveries_by_courier(self, courier_id: int) -> list:
        return (
            self.db.query(Delivery)
            .filter(Delivery.courier_id == courier_id)
            .all()
        )
