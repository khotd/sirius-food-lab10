"""Изолированная SQLite; PRACTICE_TEST_DATABASE_URL — отдельная тестовая PostgreSQL.

Таблицы пересоздаются только при явном запуске этой группы тестов.
"""
import os
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import Role, User, MenuItem, LoyaltyAccount


@pytest.fixture
def database():
    url = os.environ.get("PRACTICE_TEST_DATABASE_URL")
    if url:
        from sqlalchemy.engine import make_url
        if not make_url(url).database.startswith("practice_test_"):
            raise RuntimeError("Используйте отдельную БД с именем practice_test_*")
        engine = create_engine(url)
    else:
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with factory() as db:
        db.add(Role(id=1, name="student"))
        db.flush()
        db.add_all([User(id=1, email="one@example.com", password_hash="unused", role_id=1, balance=1000),
                    User(id=2, email="two@example.com", password_hash="unused", role_id=1, balance=1000),
                    MenuItem(id=1, name="Обед", price=200, is_available=True)])
        db.flush()
        db.add(LoyaltyAccount(user_id=1, balance=Decimal("100"), total_earned=0, tier="Bronze"))
        db.commit()
    yield factory
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture
def db(database):
    with database() as session:
        yield session
