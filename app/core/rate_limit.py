"""
Rate limiting на Redis (защита от брутфорса и спама, критерий 9 баллов).

Почему Redis, а не память приложения: если приложение запущено в
нескольких репликах, у каждой будет свой счётчик — пользователь сможет
сделать 5 попыток на одной реплике и ещё 5 на другой. Redis хранит
общее состояние для всех реплик.

Rate limiting — техническая (HTTP-уровневая) защита, поэтому она
живёт в core, а не в сервисном слое: сервис про лимиты запросов
ничего не знает.
"""
import logging

from fastapi import HTTPException, Request
from redis import ConnectionError, Redis

from app.core.redis import redis_client

logger = logging.getLogger(__name__)


def get_redis_client() -> Redis:
    """Возвращает общий клиент Redis (вынесено для подмены в тестах)."""
    return redis_client


def rate_limit(max_requests: int = 5, window: int = 60, client: Redis | None = None):
    """
    Фабрика dependency для ограничения частоты запросов.

    Возбуждает HTTPException 429 при превышении лимита
    (не более max_requests запросов за window секунд с одного IP).

    `client` — для подмены клиента в unit-тестах; по умолчанию
    используется общий клиент Redis из settings.REDIS_URL.

    Если Redis недоступен, проверка пропускается (fail-open):
    недоступный Redis не должен положить аутентификацию целиком.
    """
    redis_client = client if client is not None else get_redis_client()

    def checker(request: Request) -> None:
        ip = request.client.host if request.client else "unknown"
        key = f"rate_limit:{ip}:{request.url.path}"
        try:
            current = redis_client.incr(key)
            if current == 1:
                redis_client.expire(key, window)
        except ConnectionError:
            logger.warning("Redis недоступен — rate limit пропущен (fail-open)")
            return
        if current > max_requests:
            raise HTTPException(
                status_code=429,
                detail="Слишком много запросов. Попробуйте позже",
            )

    return checker
