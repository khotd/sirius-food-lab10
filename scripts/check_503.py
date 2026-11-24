"""Проверка 503: FastAPI работает, gRPC-микросервис остановлен.

ID администратора, курьера и оплаченного заказа берутся из БД,
поэтому скрипт работает и на чистой базе (после seed_lab6.py).
"""
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import SessionLocal  # noqa: E402
from app.models import Order, User  # noqa: E402

BASE = "http://localhost:8000"


def call(method, path, token=None, payload=None):
    request = urllib.request.Request(
        BASE + path, method=method,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Content-Type": "application/json"},
    )
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, response.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def resolve_ids() -> tuple[str, int, int]:
    with SessionLocal() as db:
        admin = db.query(User).filter(User.email == "admin@sirius.ru").one()
        courier = db.query(User).filter(User.email == "courier@sirius.ru").one()
        student = db.query(User).filter(User.email == "student@sirius.ru").one()
        order = (
            db.query(Order)
            .filter(Order.user_id == student.id, Order.status == "paid")
            .order_by(Order.id.desc())
            .first()
        )
    if order is None:
        raise SystemExit(
            "Нет оплаченного заказа. Сначала выполните: python scripts/seed_lab6.py"
        )
    return admin.email, courier.id, order.id


admin_email, courier_id, order_id = resolve_ids()

status, body = call(
    "POST", "/auth/login",
    payload={"email": admin_email, "password": "Passw0rd!"},
)
if status != 200:
    raise SystemExit(f"логин не удался: HTTP {status}: {body}")
token = json.loads(body)["access_token"]
print(f"логин: HTTP {status} (FastAPI работает, gRPC-сервер при этом остановлен)")

status, body = call(
    "POST", "/delivery/assign", token,
    payload={"order_id": order_id, "courier_id": courier_id},
)
print(f"assign при недоступном gRPC-сервисе: HTTP {status}: {body}")
if status != 503:
    raise SystemExit(f"Ожидался HTTP 503, получен {status}")
