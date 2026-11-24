"""
Настройки приложения, загружаемые из переменных окружения и файла .env.

Secure by Default: секреты (SECRET_KEY) не хранятся в коде. Без
SECRET_KEY приложение не запустится вовсе — это намеренно.
"""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Поля читаются из переменных окружения или файла .env."""

    SECRET_KEY: str
    DATABASE_URL: str = "postgresql://sirius:sirius@localhost:5432/sirius_food"
    # Redis нужен для rate limiting: счётчик должен быть общим
    # для всех реплик приложения (в памяти каждой реплики — свой).
    REDIS_URL: str = "redis://localhost:6379"
    # DEBUG=True — локальная разработка (Swagger включён).
    # DEBUG=False — продакшен: Swagger отключён (не раскрывает структуру API).
    DEBUG: bool = False
    # Разрешённые источники CORS через запятую. Пусто — разрешённых нет.
    ALLOWED_ORIGINS: str = ""
    # Кеширование меню на Redis (ЛР № 4). false — «baseline-режим»:
    # каждый запрос идёт в PostgreSQL (для снятия замеров «до кеша»).
    CACHE_ENABLED: bool = True
    # Адрес микросервиса доставки (ЛР № 6). В docker-compose/проде
    # переопределяется на имя контейнера: delivery:50051.
    DELIVERY_SERVICE_HOST: str = "localhost"
    DELIVERY_SERVICE_PORT: int = 50061
    RABBITMQ_URL: str = "amqp://sirius:sirius@127.0.0.1:5672/"
    STRIPE_SECRET_KEY: str = ""
    STRIPE_WEBHOOK_SECRET: str = ""
    PAYMENT_CURRENCY: str = "rub"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def allowed_origins(self) -> list[str]:
        """Список доменов для CORS из строки вида \"https://a.ru, https://b.ru\"."""
        return [o.strip() for o in self.ALLOWED_ORIGINS.split(",") if o.strip()]


settings = Settings()
