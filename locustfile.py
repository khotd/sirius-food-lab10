"""
Locust-сценарий нагрузочного тестирования (ЛР № 4).

Симуляция обеденного перерыва: сотни пользователей читают меню.

Запуск:
    locust -f locustfile.py --host=http://127.0.0.1:8000
Открывается http://localhost:8089 (пользователей: 500, spawn rate: 10/с).

Headless-замер (числа в терминал и CSV для README):
    locust -f locustfile.py --host=http://127.0.0.1:8000 --headless \\
        -u 200 -r 20 -t 30s --csv menu_load

Baseline («до кеша»)…: запустить приложение с CACHE_ENABLED=false:
    $env:CACHE_ENABLED="false"; uvicorn app.main:app --port 8010
"""
from locust import HttpUser, between, task


class MenuUser(HttpUser):
    """Пользователь столовой: смотрит меню и категории."""

    wait_time = between(0.1, 0.5)

    @task(3)
    def get_menu(self):
        # Чтение меню — публичный hot-endpoint (токен не требуется).
        self.client.get("/menu/items")

    @task(1)
    def get_first_category(self):
        # Кеширование категорий (menu:category:{id}, TTL 10 минут).
        self.client.get("/menu/categories/1", name="/menu/category")
