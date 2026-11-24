"""Создаёт новый оплаченный заказ для ручного назначения через gRPC."""
import sys
from pathlib import Path

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import SessionLocal
from app.models import Delivery, MenuItem, Order, User
from scripts.seed_demo import ACCOUNTS, main as seed_demo


def main():
    seed_demo()
    with SessionLocal.begin() as db:
        student = db.query(User).filter_by(email=ACCOUNTS["student"]).one()
        courier = db.query(User).filter_by(email=ACCOUNTS["courier"]).one()
        item = db.query(MenuItem).filter_by(name="Пицца Маргарита").first()
        if db.query(Delivery.id).filter_by(courier_id=courier.id, status="in_progress").first():
            raise SystemExit("Complete the demo courier's active delivery before running lab 6 again.")
        order = Order(user_id=student.id, item_id=item.id, quantity=1, status="paid")
        db.add(order)
        db.flush()
        print(f"order_id={order.id}, courier_id={courier.id} (status=paid)")


if __name__ == "__main__":
    main()
