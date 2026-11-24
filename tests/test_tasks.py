"""
Unit-тесты фоновых задач и публикации задач (ЛР № 5).

Не требуют запущенного воркера и брокера: задачи вызываются синхронно
(вызов task(...) исполняет тело), Redis и брокер подменяются заглушками.
"""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.modules.orders.service import OrderService
from app.tasks import orders as orders_tasks


class FakeRepo:
    """Минимальная in-memory заглушка репозитория для create_order."""

    def __init__(self, balance=100.0):
        self.user = SimpleNamespace(id=1, email="a@b.c", phone="+7", balance=balance)
        self.item = SimpleNamespace(id=10, price=10.0, is_available=True)
        self.saved = []

    def get_user_by_id(self, user_id):
        return self.user if user_id == 1 else None

    def get_menu_item_by_id(self, item_id):
        return self.item if item_id == 10 else None

    def update_user_balance(self, user, new_balance):
        user.balance = new_balance

    def save_order(self, user_id, item_id, quantity, charged_amount):
        self.saved.append((user_id, item_id, quantity))
        return len(self.saved)


class PdfIdempotencyTests(unittest.TestCase):
    """Идемпотентность generate_order_pdf (критерий 8: ЛР № 5)."""

    def test_second_run_skipped_when_flag_exists(self):
        fake = FakeRepoForRedis(exists=1)
        with patch.object(orders_tasks, "redis_client", fake), \
             patch.object(orders_tasks.time, "sleep") as sleep:
            result = orders_tasks.generate_order_pdf(1)
        self.assertEqual(result, "PDF already generated for order 1")
        sleep.assert_not_called()  # тяжёлая работа не выполнялась

    def test_first_run_executes_and_sets_flag_with_ttl(self):
        fake = FakeRepoForRedis(exists=0)
        with patch.object(orders_tasks, "redis_client", fake), \
             patch.object(orders_tasks.time, "sleep") as sleep:
            result = orders_tasks.generate_order_pdf(7)
        self.assertEqual(result, "PDF generated for order 7")
        sleep.assert_called_once_with(2)
        fake.setex.assert_called_once_with("pdf_generated:7", 3600, "1")

    def test_redis_down_fails_open(self):
        """Redis недоступен → задача честно выполняется, а не падает."""
        fake = FakeRepoForRedis(error=True)
        with patch.object(orders_tasks, "redis_client", fake), \
             patch.object(orders_tasks.time, "sleep"):
            result = orders_tasks.generate_order_pdf(3)
        self.assertEqual(result, "PDF generated for order 3")


class FakeRepoForRedis:
    def __init__(self, exists=0, error=False):
        self.exists_value = exists
        self.error = error
        self.setex = Mock()

    def exists(self, key):
        if self.error:
            raise orders_tasks.redis_lib.RedisError("down")
        return self.exists_value


class EmailRetryTests(unittest.TestCase):
    """Тестовая неустойчивость email-сервиса (критерий 7: ЛР № 5)."""

    def test_raises_when_email_service_down(self):
        # order_id % 5 == 0 — симуляция недоступности: на воркере это
        # даёт ретраи 1→2→4 c и запись в DLQ после исчерпания попыток.
        with patch.object(orders_tasks.time, "sleep"):
            with self.assertRaises(Exception):
                orders_tasks.send_order_email(5, "a@b.c")

    def test_ok_for_normal_order(self):
        with patch.object(orders_tasks.time, "sleep"):
            result = orders_tasks.send_order_email(1, "a@b.c")
        self.assertIn("a@b.c", result)


class DispatchTests(unittest.TestCase):
    """Публикация задач из Service: fail-open и корректные аргументы."""

    def test_broker_down_does_not_lose_order(self):
        repo = FakeRepo()
        service = OrderService(repo)
        with patch("app.modules.orders.service.generate_order_pdf") as pdf, \
             patch("app.modules.orders.service.send_order_email"), \
             patch("app.modules.orders.service.send_order_sms"):
            pdf.delay.side_effect = Exception("broker down")
            order_id = service.create_order(user_id=1, item_id=10, quantity=2)
        self.assertEqual(order_id, 1)          # заказ создан
        self.assertEqual(repo.user.balance, 80.0)  # баланс списан

    def test_tasks_published_with_order_data(self):
        repo = FakeRepo()
        service = OrderService(repo)
        with patch("app.modules.orders.service.generate_order_pdf") as pdf, \
             patch("app.modules.orders.service.send_order_email") as mail, \
             patch("app.modules.orders.service.send_order_sms") as sms:
            order_id = service.create_order(user_id=1, item_id=10, quantity=2)
        self.assertEqual(order_id, 1)
        pdf.delay.assert_called_once_with(1)
        mail.delay.assert_called_once_with(1, "a@b.c")
        sms.delay.assert_called_once_with(1, "+7")


if __name__ == "__main__":
    unittest.main()
