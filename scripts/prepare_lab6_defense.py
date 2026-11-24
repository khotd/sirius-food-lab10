"""Подготовка четырёх учебных аккаунтов и заказа для ручной защиты ЛР 6."""
from app.core.security import hash_password
from app.database import SessionLocal
from app.models import Delivery, MenuItem, Order, Role, User
from scripts.seed_demo import PASSWORD, main as seed_demo


def main():
    seed_demo()
    with SessionLocal.begin() as db:
        accounts = (
            ("admin@sirius.ru", "admin"),
            ("courier@sirius.ru", "courier"),
            ("courier2@sirius.ru", "courier"),
            ("student@sirius.ru", "student"),
        )
        users = {}
        for email, role_name in accounts:
            role = db.query(Role).filter_by(name=role_name).one()
            user = db.query(User).filter_by(email=email).first()
            if user is None:
                user = User(email=email, balance=0)
                db.add(user)
            user.role_id = role.id
            user.password_hash = hash_password(PASSWORD)
            user.is_blocked = False
            db.flush()
            users[email] = user
            print(f"{role_name}: {email}, id={user.id}")

        student = users["student@sirius.ru"]
        if student.balance < 2000:
            student.balance = 2000
        courier = users["courier@sirius.ru"]
        active = db.query(Delivery).filter_by(courier_id=courier.id, status="in_progress").first()
        if active:
            print(f"Existing delivery: order_id={active.order_id}, delivery_id={active.id}")
        else:
            item = db.query(MenuItem).filter_by(name="Пицца Маргарита").one()
            order = (
                db.query(Order).filter(
                    Order.user_id == student.id, Order.status == "paid",
                    Order.charged_amount.is_(None),
                    ~Order.id.in_(db.query(Delivery.order_id)),
                ).order_by(Order.id.desc()).first()
            )
            if order is None:
                order = Order(user_id=student.id, item_id=item.id, quantity=1, status="paid")
                db.add(order)
                db.flush()
            print(f"Ready for assignment: order_id={order.id}, courier_id={courier.id}")


if __name__ == "__main__":
    main()
