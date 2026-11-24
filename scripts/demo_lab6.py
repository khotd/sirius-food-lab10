"""
Живая демонстрация ЛР № 6: HTTP → gRPC → DeliveryService → PostgreSQL.

Последовательность:
  1. Логин admin и courier (получение JWT).
  2. POST /delivery/assign  (admin)  — назначение курьера через gRPC.
  3. Повторное назначение — демонстрация 400 (бизнес-правило, INVALID_ARGUMENT).
  4. GET  /delivery/{order_id}/track — SSE-поток статусов (Server Streaming).
  5. PATCH /delivery/{id}/complete (courier) — завершение доставки через gRPC.
  6. GET  /delivery/my (courier) — список доставок через gRPC.
  7. PATCH complete от студента — демонстрация 403 (RBAC на HTTP-слое).
"""
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

# Windows PowerShell может открыть stdout в cp1251 — переключаем на UTF-8,
# чтобы русский текст и стрелки не роняли скрипт UnicodeEncodeError.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

# Запуск из корня проекта: python scripts/demo_lab6.py
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import SessionLocal  # noqa: E402
from app.models import Order, User  # noqa: E402
from app.modules.delivery.grpc_client import DeliveryGrpcClient  # noqa: E402

BASE = "http://localhost:8000"


def call(method: str, path: str, token: str | None = None, payload: dict | None = None,
         timeout: float = 60.0):
    request = urllib.request.Request(
        BASE + path, method=method,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Content-Type": "application/json"},
    )
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode()
            return response.status, body
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def login(email: str) -> str:
    status, body = call("POST", "/auth/login", payload={"email": email, "password": "Passw0rd!"})
    assert status == 200, f"login {email}: {status} {body}"
    return json.loads(body)["access_token"]


def resolve_demo_ids() -> tuple[int, int, int, int]:
    """
    Берём реальные ID из БД, а не хардкодим их: на чистой базе
    admin/courier/student/order могут иметь любые номера.
    """
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
    return admin.id, courier.id, student.id, order.id


admin_id, courier_id, student_id, order_id = resolve_demo_ids()
print(
    f"ID из БД: admin={admin_id}, courier={courier_id}, "
    f"student={student_id}, order={order_id}"
)

print("=== 1. Логин ===")
admin_token = login("admin@sirius.ru")
courier_token = login("courier@sirius.ru")
student_token = login("student@sirius.ru")
print("OK: токены admin / courier / student получены")

print("\n=== 2. POST /delivery/assign (admin, через gRPC) ===")
status, body = call("POST", "/delivery/assign", admin_token,
                    payload={"order_id": order_id, "courier_id": courier_id})
print(f"HTTP {status}: {body}")
assert status == 200
delivery_id = json.loads(body)["delivery_id"]
delivery = DeliveryGrpcClient().get_delivery(order_id)
assert delivery["delivery_id"] == delivery_id and delivery["status"] == "in_progress", delivery
print(f"GetDelivery (gRPC): {delivery}")

print("\n=== 3. Повторное назначение (бизнес-правило → 400) ===")
status, body = call("POST", "/delivery/assign", admin_token,
                    payload={"order_id": order_id, "courier_id": courier_id})
print(f"HTTP {status}: {body}")
assert status == 400

print(f"\n=== 4. GET /delivery/{order_id}/track (SSE, Server Streaming ~6 с) ===")
status, body = call("GET", f"/delivery/{order_id}/track", student_token, timeout=60.0)
print(f"HTTP {status}, поток:")
assert status == 200
stages = []
for line in body.splitlines():
    if line.startswith("data: "):
        stages.append(json.loads(line[6:]).get("status"))
        print(f"  {line[6:]}")
assert stages == ["in_progress", "on_the_way", "delivered"], stages

print("\n=== 5. PATCH /delivery/{id}/complete (courier, через gRPC) ===")
status, body = call("PATCH", f"/delivery/{delivery_id}/complete", courier_token)
print(f"HTTP {status}: {body}")
assert status == 200 and json.loads(body)["delivery_status"] == "completed"

print("\n=== 6. GET /delivery/my (courier, через gRPC) ===")
status, body = call("GET", "/delivery/my", courier_token)
print(f"HTTP {status}: {body}")
assert status == 200 and any(d["delivery_id"] == delivery_id for d in json.loads(body))

print("\n=== 7. PATCH complete от студента (RBAC → 403) ===")
status, body = call("PATCH", f"/delivery/{delivery_id}/complete", student_token)
print(f"HTTP {status}: {body}")
assert status == 403

print("\n=== 8. Повторное complete (уже завершена → 400) ===")
status, body = call("PATCH", f"/delivery/{delivery_id}/complete", courier_token)
print(f"HTTP {status}: {body}")
assert status == 400

print("\nДемонстрация завершена.")
