"""ЛР 10: 70% список, 20% карточка, 10% создание с настоящими JWT."""
import json
import os
from pathlib import Path

from locust import HttpUser, between, task
from locust.exception import StopUser

_data = None
_next_account = 0


class SiriusUser(HttpUser):
    wait_time = between(0.01, 0.05) if os.getenv("LAB10_STRESS") == "1" else between(1, 3)

    def on_start(self):
        global _data, _next_account
        if _data is None:
            filename = os.getenv("LAB10_USERS_FILE")
            if not filename:
                raise RuntimeError("Use python -m scripts.run_lab10_load --web (isolated database and tokens).")
            _data = json.loads(Path(filename).read_text(encoding="utf-8"))
        if _next_account >= len(_data["accounts"]):
            raise StopUser()
        account = _data["accounts"][_next_account]
        _next_account += 1
        self.item_id = _data["item_id"]
        self.client.headers["Authorization"] = "Bearer " + account["token"]

    @task(7)
    def menu(self):
        self.client.get("/menu/items", name="/menu/items")

    @task(2)
    def detail(self):
        self.client.get(f"/menu/items/{self.item_id}", name="/menu/items/{id}")

    @task(1)
    def create_order(self):
        with self.client.post(
            "/orders/create", json={"item_id": self.item_id, "quantity": 1},
            name="/orders/create", catch_response=True,
        ) as response:
            if response.status_code != 200:
                response.failure(f"Order creation HTTP {response.status_code}")
            elif "order_id" not in response.json():
                response.failure("Missing order_id")
