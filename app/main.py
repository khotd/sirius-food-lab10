"""
Точка входа приложения. Выполняет инициализацию FastAPI,
подключение модулей и создание таблиц базы данных.

Secure by Default (критерий 9 баллов):
  * секреты читаются из .env (app/core/config.py);
  * CORS ограничен списком разрешённых доменов (пустой список —
    браузерные запросы с чужих доменов отклоняются);
  * Swagger/docs отключён в продакшене (DEBUG=False).
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.observability import ObservabilityMiddleware, metrics_response, setup_logging
from app.database import Base, engine, SessionLocal
from app.modules.auth.router import router as auth_router
from app.modules.users.router import router as users_router
from app.modules.menu.router import router as menu_router
from app.modules.orders.router import router as orders_router
from app.modules.delivery.router import router as delivery_router
from app.modules.loyalty.router import router as loyalty_router
from app.modules.payments.router import router as payments_router

Base.metadata.create_all(bind=engine)
setup_logging("monolith")


def seed_roles() -> None:
    """
    Идемпотентное заполнение таблицы ролей (RBAC) при старте.
    Нужно для «свежей» БД: регистрация назначает роль student,
    и без этой записи она бы падала на NOT NULL.
    """
    from app.models import Role

    with SessionLocal() as db:
        existing = {r.name for r in db.query(Role).all()}
        missing = [
            Role(name=name)
            for name in ("student", "cook", "courier", "admin")
            if name not in existing
        ]
        if missing:
            db.add_all(missing)
            db.commit()


seed_roles()


def _cache_warmup() -> None:
    """
    Прогрев кеша при старте (cache warm-up, критерий 10 ЛР № 4):
    первый пользовательский запрос получает данные из Redis, минуя
    PostgreSQL (вместо «холодного» miss на обеденном пике).
    """
    from app.core.redis import check_redis_connection
    from app.modules.menu.repository import MenuRepository

    if not settings.CACHE_ENABLED:
        return
    if not check_redis_connection():
        return
    try:
        with SessionLocal() as db:
            MenuRepository(db).warm_cache()
    except Exception:  # noqa: BLE001 — прогрев не должен ломать старт
        pass


app = FastAPI(
    title="Sirius.Food API",
    # Swagger раскрывает структуру API — в продакшене (DEBUG=False) отключён.
    docs_url="/docs" if settings.DEBUG else None,
    redoc_url="/redoc" if settings.DEBUG else None,
)

# CORS ограничен списком из .env (ALLOWED_ORIGINS). allow_origins=["*"]
# вместе с credentials — типовая дыра, поэтому так не делаем.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH"],
    allow_headers=["Authorization", "Content-Type", "X-Correlation-ID", "Idempotency-Key"],
    expose_headers=["X-Correlation-ID"],
)
app.add_middleware(ObservabilityMiddleware)
app.add_api_route("/metrics", metrics_response, include_in_schema=False)

app.include_router(auth_router, prefix="/auth", tags=["auth"])
app.include_router(users_router, prefix="/users", tags=["users"])
app.include_router(menu_router, prefix="/menu", tags=["menu"])
app.include_router(orders_router, prefix="/orders", tags=["orders"])
app.include_router(delivery_router, prefix="/delivery", tags=["delivery"])
app.include_router(loyalty_router, prefix="/loyalty", tags=["loyalty"])
app.include_router(payments_router, prefix="/payments", tags=["payments"])

# Startup-событие прогрева кеша (после создания app).
app.add_event_handler("startup", _cache_warmup)


@app.get("/health", tags=["health"])
def health() -> dict:
    """
    Health-check для мониторинга / оркестратора (Docker, VS Code Port
    Forward его постоянно пингует). Проверяет доступность PostgreSQL,
    чтобы монитор видел не только «процесс жив», но и «база отвечает».
    """
    from sqlalchemy import text

    try:
        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
        db_status = "ok"
    except Exception:  # noqa: BLE001 — health-check не должен ронять сервер
        db_status = "unreachable"

    return {"status": "ok" if db_status == "ok" else "degraded", "database": db_status}
