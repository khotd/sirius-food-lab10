"""Подготовка учебных ролей, пользователей и меню после alembic upgrade head."""
from app.database import SessionLocal
from app.models import MenuCategory, MenuItem, Role, User
from app.core.security import hash_password
from app.modules.menu.repository import MenuRepository

PASSWORD = "Passw0rd!"
ACCOUNTS = {
    "student": "student@sirius.ru", "cook": "cook@sirius.ru",
    "courier": "courier@sirius.ru", "admin": "admin@sirius.ru",
}
DISHES = (
    ("Завтраки", "Сырники", 150),
    ("Завтраки", "Овсяная каша с ягодами", 120),
    ("Основные блюда", "Пицца Маргарита", 450),
    ("Основные блюда", "Паста с томатами", 320),
    ("Основные блюда", "Куриный суп", 210),
    ("Напитки", "Чай с лимоном", 80),
)


def main():
    with SessionLocal() as db:
        for name, email in ACCOUNTS.items():
            role = db.query(Role).filter_by(name=name).first()
            if role is None:
                role = Role(name=name)
                db.add(role)
                db.flush()
            if db.query(User.id).filter_by(email=email).first() is None:
                db.add(User(email=email, role_id=role.id, password_hash=hash_password(PASSWORD),
                            balance=2000 if name == "student" else 0))
        for category, name, price in DISHES:
            group = db.query(MenuCategory).filter_by(name=category).first()
            if group is None:
                group = MenuCategory(name=category)
                db.add(group)
                db.flush()
            if db.query(MenuItem.id).filter_by(name=name).first() is None:
                db.add(MenuItem(name=name, price=price, is_available=True, category_id=group.id))
        db.commit()
        MenuRepository(db).invalidate_cache()
    print("Demo accounts and menu are ready.")


if __name__ == "__main__":
    main()
