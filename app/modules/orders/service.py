"""
Слой бизнес-логики модуля orders (Круги 1 и 2: Entities и Use Cases).

ВАЖНО: в этом файле запрещены импорты fastapi и sqlalchemy.
Сервис зависит только от абстракции репозитория (Protocol, ниже),
а конкретная реализация передаётся в конструктор (инверсия зависимостей).

ЛР № 5: сервис публикует фоновые задачи Celery (.delay()) после создания
заказа — «заказ создан» это бизнес-событие. Celery здесь вызывается,
но не конфигурируется: приложение и очереди живут в app/core/celery_app.py,
исполняемый код задач — в app/tasks/. Публикация fail-open: брокер
недоступен → заказ не отменяется, ошибка логируется.
"""
from typing import Protocol

import logging

from app.core.exceptions import BusinessRuleError, ForbiddenError, NotFoundError
from app.core.order_events import ORDER_PAID, new_event
from app.tasks.orders import generate_order_pdf, send_order_email, send_order_sms

logger = logging.getLogger(__name__)


class OrderRepositoryProtocol(Protocol):
    """Контракт, которого сервис ожидает от слоя хранения данных."""

    def get_user_by_id(self, user_id: int): ...

    def get_menu_item_by_id(self, item_id: int): ...

    def update_user_balance(self, user, new_balance: float) -> None: ...

    def save_order(self, user_id: int, item_id: int, quantity: int, charged_amount: float) -> int: ...

    def get_order(self, order_id: int): ...

    def set_order_status(self, order, status: str) -> None: ...

    def list_kitchen_orders(self) -> list[tuple]: ...
    def has_active_delivery(self, order_id: int) -> bool: ...
    def save_payment(self, order, routing_key: str, payload: dict, correlation_id: str) -> None: ...


class OrderService:
    """
    Класс инкапсулирует бизнес-правила обработки заказов.
    Не имеет зависимостей от инфраструктурных фреймворков.
    """

    def __init__(self, repository: OrderRepositoryProtocol):
        # Внедрение зависимости через конструктор.
        self.repository = repository

    def create_order(self, user_id: int, item_id: int, quantity: int) -> int:
        """
        Исполнение сценария оформления заказа.
        Возвращает идентификатор созданного заказа.
        Возбуждает NotFoundError / BusinessRuleError (наследники ValueError)
        при нарушении бизнес-правил.
        """
        user = self.repository.get_user_by_id(user_id)
        if not user:
            raise NotFoundError("Пользователь не найден")

        item = self.repository.get_menu_item_by_id(item_id)
        if not item:
            raise NotFoundError("Блюдо не найдено")

        if not item.is_available:
            raise BusinessRuleError("Блюдо недоступно")

        total_cost = item.price * quantity
        if user.balance < total_cost:
            raise BusinessRuleError("Недостаточно средств на балансе студента")

        # Изменение состояния доменных данных.
        user.balance -= total_cost
        self.repository.update_user_balance(user, user.balance)

        # Делегирование операции сохранения инфраструктурному слою.
        order_id = self.repository.save_order(user_id, item_id, quantity, total_cost)

        # «Заказ создан» — бизнес-событие, поэтому публикация фоновых
        # операций (чек, email, SMS) находится в Service, а не в Router:
        # API лишь отвечает «заказ принят», воркеры доделывают остальное.
        self.dispatch_notifications(order_id, user)

        return order_id

    def pay_order(self, order_id: int, user_id: int, correlation_id: str) -> dict | None:
        """Подтвердить ранее зарезервированную оплату и записать событие атомарно."""
        order = self.repository.get_order(order_id)
        if not order:
            raise NotFoundError("Заказ не найден")
        if order.user_id != user_id:
            raise ForbiddenError("Можно оплатить только свой заказ")
        if order.status in ("paid", "delivered"):
            return None
        if getattr(order, "payment_method", "balance") == "external":
            raise BusinessRuleError("Внешнюю оплату подтверждает только вебхук шлюза")
        if order.status != "processing" or order.charged_amount is None:
            raise BusinessRuleError("Этот заказ нельзя оплатить. Создайте новый заказ.")
        event = new_event(order_id, user_id, order.charged_amount)
        self.repository.save_payment(order, ORDER_PAID, event, correlation_id)
        return event

    def dispatch_notifications(self, order_id: int, user) -> None:
        """
        Публикация фоновых задач Celery (ЛР № 5) после создания заказа.

        .delay() сериализует аргументы в JSON, кладёт сообщение в брокер
        (Redis) и ВОЗВРАЩАЕТСЯ МГНОВЕННО: HTTP-ответ больше не ждёт
        генерацию PDF и доставку уведомлений (4+ сек → ~50 мс).

        Fail-open: если брокер недоступен, заказ уже зафиксирован в БД
        (деньги списаны) — падать ради уведомлений нельзя. Ошибка
        логируется, чтобы оператор мог повторить публикацию вручную.
        """
        try:
            generate_order_pdf.delay(order_id)
            email = getattr(user, "email", None)
            if email:
                send_order_email.delay(order_id, email)
            phone = getattr(user, "phone", None)
            if phone:
                send_order_sms.delay(order_id, phone)
        except Exception:  # noqa: BLE001 — уведомления не отменяют заказ
            logger.warning(
                "Не удалось поставить фоновые задачи по заказу %s", order_id,
                exc_info=True,
            )

    def cancel_order(self, order_id: int, current_user_id: int) -> None:
        """
        Отмена заказа. Закрывает уязвимость IDOR: проверяется, что заказ
        принадлежит именно текущему пользователю (id берётся из JWT,
        а не из запроса). Проверка сознательно находится в Service —
        это бизнес-правило, которое можно протестировать без HTTP
        и переиспользовать в CLI или воркере.

        Возбуждает NotFoundError (404), ForbiddenError (403),
        BusinessRuleError (400).
        """
        order = self.repository.get_order(order_id)
        if not order:
            raise NotFoundError("Заказ не найден")

        # Защита от IDOR: чужой заказ отменять нельзя.
        if order.user_id != current_user_id:
            raise ForbiddenError("Можно отменять только свои заказы")

        if order.status not in ("processing", "paid"):
            raise BusinessRuleError("Можно отменить только заказ в обработке или оплаченный заказ")
        if order.status == "paid":
            if getattr(order, "payment_method", "balance") == "external":
                raise BusinessRuleError("Для внешней оплаты выполните возврат через Stripe Dashboard")
            if self.repository.has_active_delivery(order.id):
                raise BusinessRuleError("Заказ уже передан курьеру")

        self.repository.set_order_status(order, "cancelled")

    def get_kitchen_orders(self) -> list[dict]:
        """
        Очередь заказов, которые зарегистрировал consumer кухни.
        Доступ к сценарию разграничен ролью cook на транспортном слое.
        """
        return [
            {
                "order_id": order.id,
                "user_id": order.user_id,
                "item_id": order.item_id,
                "quantity": order.quantity,
                "status": status,
            }
            for order, status in self.repository.list_kitchen_orders()
        ]
