"""
Unit-тесты OrderService. Не требуют СУБД, FastAPI и SQLAlchemy:
вместо настоящего репозитория используется in-memory заглушка.
Запуск: python -m unittest discover -s tests -v   (или pytest)
"""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.core.exceptions import BusinessRuleError, ForbiddenError, NotFoundError
from app.modules.orders.service import OrderService


class FakeOrderRepository:
    """Заглушка репозитория заказов, хранящая данные в памяти."""

    def __init__(self, users=None, items=None, orders=None):
        self.users = {u.id: u for u in (users or [])}
        self.items = {i.id: i for i in (items or [])}
        self.orders = {o.id: o for o in (orders or [])}
        self.saved_orders = []
        self.balance_updates = []
        self.status_updates = []

    def get_user_by_id(self, user_id):
        return self.users.get(user_id)

    def get_menu_item_by_id(self, item_id):
        return self.items.get(item_id)

    def update_user_balance(self, user, new_balance):
        self.balance_updates.append((user.id, new_balance))
        user.balance = new_balance

    def save_order(self, user_id, item_id, quantity, charged_amount):
        self.saved_orders.append((user_id, item_id, quantity, charged_amount))
        return len(self.saved_orders)

    def get_order(self, order_id):
        return self.orders.get(order_id)

    def set_order_status(self, order, status):
        order.status = status
        self.status_updates.append((order.id, status))

    def list_kitchen_orders(self):
        return [(o, "pending") for o in self.orders.values() if o.status == "paid"]


def make_service(balance=500.0, price=100.0, available=True):
    user = SimpleNamespace(id=1, balance=balance)
    item = SimpleNamespace(id=10, price=price, is_available=available)
    repo = FakeOrderRepository([user], [item])
    return OrderService(repo), repo, user


class OrderServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        """
        Публикация задач Celery в брокер подменяется заглушкой:
        unit-тесты не должны требовать запущенного Redis-брокера
        (ЛР № 5 — .delay() в Service вызывается на happy-path).
        """
        cls._patches = [
            patch("app.modules.orders.service." + name)
            for name in ("generate_order_pdf", "send_order_email", "send_order_sms")
        ]
        for p in cls._patches:
            p.start()

    @classmethod
    def tearDownClass(cls):
        for p in cls._patches:
            p.stop()

    def test_success_debits_balance_and_saves_order(self):
        service, repo, user = make_service(balance=500, price=100)
        order_id = service.create_order(user_id=1, item_id=10, quantity=3)
        self.assertEqual(order_id, 1)
        self.assertEqual(user.balance, 200)
        self.assertEqual(repo.balance_updates, [(1, 200)])
        self.assertEqual(repo.saved_orders, [(1, 10, 3, 300.0)])

    def test_user_not_found(self):
        service, repo, _ = make_service()
        with self.assertRaises(NotFoundError) as ctx:
            service.create_order(user_id=999, item_id=10, quantity=1)
        self.assertEqual(str(ctx.exception), "Пользователь не найден")
        self.assertEqual(repo.saved_orders, [])

    def test_item_not_found(self):
        service, repo, _ = make_service()
        with self.assertRaises(NotFoundError) as ctx:
            service.create_order(user_id=1, item_id=999, quantity=1)
        self.assertEqual(str(ctx.exception), "Блюдо не найдено")
        self.assertEqual(repo.saved_orders, [])

    def test_item_unavailable(self):
        service, repo, _ = make_service(available=False)
        with self.assertRaises(BusinessRuleError) as ctx:
            service.create_order(user_id=1, item_id=10, quantity=1)
        self.assertEqual(str(ctx.exception), "Блюдо недоступно")
        self.assertEqual(repo.saved_orders, [])

    def test_insufficient_funds_does_not_change_state(self):
        service, repo, user = make_service(balance=50, price=100)
        with self.assertRaises(BusinessRuleError) as ctx:
            service.create_order(user_id=1, item_id=10, quantity=1)
        self.assertEqual(
            str(ctx.exception), "Недостаточно средств на балансе студента"
        )
        self.assertEqual(user.balance, 50)
        self.assertEqual(repo.balance_updates, [])
        self.assertEqual(repo.saved_orders, [])

    def test_exact_balance_is_enough(self):
        service, _, user = make_service(balance=300, price=100)
        service.create_order(user_id=1, item_id=10, quantity=3)
        self.assertEqual(user.balance, 0)

    def test_errors_are_value_errors(self):
        """Совместимость с шаблоном методички: except ValueError."""
        service, _, _ = make_service()
        with self.assertRaises(ValueError):
            service.create_order(user_id=999, item_id=10, quantity=1)


class CancelOrderTests(unittest.TestCase):
    """Защита от IDOR при отмене заказа."""

    def make_cancel_service(self, order_owner_id=1, status="processing"):
        order = SimpleNamespace(id=5, user_id=order_owner_id, status=status)
        repo = FakeOrderRepository(orders=[order])
        return OrderService(repo), repo, order

    def test_cancel_own_order(self):
        service, repo, order = self.make_cancel_service(order_owner_id=1)
        service.cancel_order(5, current_user_id=1)
        self.assertEqual(order.status, "cancelled")
        self.assertEqual(repo.status_updates, [(5, "cancelled")])

    def test_cancel_foreign_order_forbidden(self):
        """IDOR: пользователь 2 не может отменить заказ пользователя 1."""
        service, repo, order = self.make_cancel_service(order_owner_id=1)
        with self.assertRaises(ForbiddenError) as ctx:
            service.cancel_order(5, current_user_id=2)
        self.assertEqual(str(ctx.exception), "Можно отменять только свои заказы")
        self.assertEqual(order.status, "processing")  # заказ не изменился
        self.assertEqual(repo.status_updates, [])

    def test_cancel_missing_order_not_found(self):
        service, _, _ = self.make_cancel_service()
        with self.assertRaises(NotFoundError) as ctx:
            service.cancel_order(999, current_user_id=1)
        self.assertEqual(str(ctx.exception), "Заказ не найден")

    def test_cancel_processed_order_rejected(self):
        service, _, order = self.make_cancel_service(status="delivered")
        with self.assertRaises(BusinessRuleError):
            service.cancel_order(5, current_user_id=1)
        self.assertEqual(order.status, "delivered")


class KitchenOrdersTests(unittest.TestCase):
    def test_kitchen_returns_pending_entries(self):
        orders = [
            SimpleNamespace(id=1, user_id=1, item_id=10, quantity=2, status="paid"),
            SimpleNamespace(id=2, user_id=2, item_id=11, quantity=1, status="delivered"),
        ]
        repo = FakeOrderRepository(orders=orders)
        result = OrderService(repo).get_kitchen_orders()
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["order_id"], 1)
        self.assertEqual(result[0]["status"], "pending")


if __name__ == "__main__":
    unittest.main()
