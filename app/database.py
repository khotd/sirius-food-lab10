"""
Модуль инициализации подключения к СУБД PostgreSQL.
"""
import os

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

from app.core.config import settings

# SQL_ECHO=true выводит SQL-запросы в терминал —
# нужно для проверки INSERT/UPDATE при выполнении лабораторных работ.
SQL_ECHO = os.getenv("SQL_ECHO", "false").lower() in ("1", "true", "yes")

engine = create_engine(settings.DATABASE_URL, echo=SQL_ECHO)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    """Генератор сессии базы данных для использования в Depends."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
