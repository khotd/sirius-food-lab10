"""Данные для нагрузки. Запускается только внутри временного стенда ЛР 10."""
from datetime import datetime, timedelta
import json
from pathlib import Path
import sys

from jose import jwt
from sqlalchemy import text

from app.core.config import settings
from app.database import engine
from app.core.security import hash_password


def main():
    filename, count = Path(sys.argv[1]), int(sys.argv[2])
    password_hash = hash_password("LoadTestOnly!")
    accounts = []
    with engine.begin() as db:
        role_id = db.execute(text("SELECT id FROM roles WHERE name='student'")).scalar_one()
        item_id = db.execute(text(
            "INSERT INTO menu_items(name,price,is_available) VALUES ('Load test dish',1,true) RETURNING id"
        )).scalar_one()
        for index in range(count):
            uid = db.execute(text(
                "INSERT INTO users(email,password_hash,role_id,balance,is_blocked) "
                "VALUES (:email,:password,:role,1000000,false) RETURNING id"
            ), {"email": f"load-{index}@example.com", "password": password_hash, "role": role_id}).scalar_one()
            token = jwt.encode({
                "sub": str(uid), "type": "access",
                "exp": datetime.utcnow() + timedelta(minutes=15),
            }, settings.SECRET_KEY, algorithm="HS256")
            accounts.append({"token": token, "user_id": uid})
    filename.write_text(json.dumps({"item_id": item_id, "accounts": accounts}), encoding="utf-8")
    print(f"Prepared {count} load accounts in the disposable database.")


if __name__ == "__main__":
    main()
