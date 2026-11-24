"""Учебные аккаунты и данные A/D. Повторный запуск не пополняет баланс."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import os

from dotenv import load_dotenv

from app.core.security import hash_password
from app.database import SessionLocal
from app.models import User, Role, MenuItem, PromoCode, LoyaltyAccount


def main():
    load_dotenv()
    password = os.environ.get("PRACTICE_DEMO_PASSWORD")
    if not password or len(password) < 8:
        raise SystemExit("Задайте PRACTICE_DEMO_PASSWORD в .env (не менее 8 символов)")
    with SessionLocal.begin() as db:
        for name in ("student", "admin", "cook", "courier"):
            role = db.query(Role).filter_by(name=name).first()
            if role is None:
                role = Role(name=name)
                db.add(role)
                db.flush()
            email = f"practice-{name}@sirius.ru"
            user = db.query(User).filter_by(email=email).first()
            if user is None:
                user = User(email=email, password_hash=hash_password(password), role_id=role.id,
                            balance=5000 if name == "student" else 0)
                db.add(user)
                db.flush()
            if name == "student" and not db.query(LoyaltyAccount).filter_by(user_id=user.id).first():
                db.add(LoyaltyAccount(user_id=user.id, balance=Decimal("100"), total_earned=0, tier="Bronze"))
        if not db.query(MenuItem).filter_by(name="Обед для практики A+D").first():
            db.add(MenuItem(name="Обед для практики A+D", price=200, is_available=True))
        for code, kind, value in (("PRACTICE10", "percent", 10), ("PRACTICE50", "fixed", 50)):
            if not db.query(PromoCode).filter_by(code=code).first():
                now = datetime.now(timezone.utc)
                db.add(PromoCode(code=code, discount_type=kind, discount_value=value,
                                 valid_from=now-timedelta(days=1), valid_until=now+timedelta(days=30),
                                 max_uses=100, current_uses=0, min_order_amount=100,
                                 is_active=True, once_per_user=True))
    print("Данные практики готовы. Аккаунты: practice-student/admin/cook/courier@sirius.ru.")


if __name__ == "__main__":
    main()
