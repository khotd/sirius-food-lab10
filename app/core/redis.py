"""
Клиент Redis для всех инфраструктурных нужд проекта:
  * rate limiting (ЛР № 3, ключи rate_limit:*);
  * Cache-Aside кеширование меню (ЛР № 4, ключи menu:*).

Один общий клиент = один пул соединений на приложение.
decode_responses=True: Redis возвращает строки, а не байты — удобно для JSON.

Клиент создаётся лениво (без немедленного подключения), поэтому импорт
модуля безопасен даже при выключенном Redis: приложения с кешем, но без
Redis не должно падать на импорте.
"""
import redis

from app.core.config import settings

redis_client = redis.from_url(settings.REDIS_URL, decode_responses=True)


def check_redis_connection() -> bool:
    """Проверка соединения с Redis (используется прогревом кеша)."""
    try:
        return bool(redis_client.ping())
    except redis.RedisError:
        return False
