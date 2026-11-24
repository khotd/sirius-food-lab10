"""
Фоновые задачи оформления заказа (ЛР № 5).

Вынесено из синхронного запроса POST /orders/create в воркер Celery:
  * generate_order_pdf  — генерация PDF-чека («тяжёлая» операция, 2 с);
  * send_order_email    — письмо клиенту (нестабильный внешний сервис →
                          ретраи с экспоненциальной задержкой и jitter);
  * send_order_sms      — SMS-уведомление;
  * collect_daily_order_stats — периодическая статистика (Celery Beat).

Ключевые свойства:
  * Идемпотентность generate_order_pdf: флаг в Redis (pdf_generated:<id>)
    защищает от повторной генерации — при перезапуске/повторной доставке
    (acks_late) чек не создаётся дважды.
  * Ретраи send_order_email: autoretry_for + exponential backoff (1, 2, 4 c)
    + jitter; после 3 неудач задача уходит в Dead Letter Queue (ReliableTask).
  * Отказоустойчивость утилитарных операций: если Redis недоступен,
    идемпотентность деградирует в честное выполнение (fail-open),
    а не ломает задачу.

В задаче намеренно НЕ используются импорты FastAPI: задачи исполняет
воркер, а не HTTP-сервер. SQLAlchemy допускается только в beat-задаче,
которой нужен доступ к БД (воркер — инфраструктурный процесс).
"""
import logging
import time

import redis as redis_lib

from app.core.celery_app import ReliableTask, celery_app
from app.core.redis import redis_client

logger = logging.getLogger(__name__)


@celery_app.task(bind=True, base=ReliableTask)
def generate_order_pdf(self, order_id: int) -> str:  # noqa: ANN001
    """
    Генерация PDF-чека по заказу (имитация — time.sleep(2)).

    Идемпотентность: ключ pdf_generated:<order_id> в Redis. Если флаг
    уже стоит — PDF сгенерирован ранее, повтор пропускается. Это защита
    от двойного выполнения: повторная доставка после падения воркера
    (acks_late) или ручной re-run задачи не должны создать второй чек.
    Флаг живёт 1 час: срок, за который любые ретрансмиссии точно затухнут.
    """
    key = f"pdf_generated:{order_id}"

    # Fail-open: недоступный Redis не должен ломать генерацию —
    # она выполнится ещё раз, что лучше потерянного чека.
    try:
        already_done = bool(redis_client.exists(key))
    except redis_lib.RedisError:
        logger.warning("Redis недоступен, идемпотентность отключена", exc_info=True)
        already_done = False

    if already_done:
        return f"PDF already generated for order {order_id}"

    time.sleep(2)  # имитация работы с генератором PDF

    try:
        # TTL 1 час: после этого повторный вызов может честно пересоздать
        # чек, а ключ не копится в Redis вечно.
        redis_client.setex(key, 3600, "1")
    except redis_lib.RedisError:
        logger.warning("Не удалось сохранить флаг идемпотентности", exc_info=True)

    return f"PDF generated for order {order_id}"


@celery_app.task(
    base=ReliableTask,
    # Автоматический ретрай ЛЮБОГО исключения (внешний email-сервис
    # может недоступен по десятку причин — ловим все).
    autoretry_for=(Exception,),
    # Экспоненциальная задержка: 1 → 2 → 4 секунды...
    retry_backoff=True,
    # ...но не дольше 10 минут между попытками.
    retry_backoff_max=600,
    # Jitter (случайный сдвиг) — чтобы ретраи разных задач не
    # «выравнивались» в ударную волну повторных запросов.
    retry_jitter=True,
    # Максимум 3 повтора; дальше задача уходит в DLQ (ReliableTask).
    max_retries=3,
)
def send_order_email(order_id: int, email: str) -> str:
    """
    Отправка email клиенту о заказе (имитация — time.sleep(1)).

    Тестовая неустойчивость: каждый пятый заказ (order_id % 5 == 0)
    «падает» — так на защите демонстрируются ретраи с backoff в логе
    воркера и попадание в DLQ после исчерпания попыток.
    """
    time.sleep(1)
    if order_id % 5 == 0:
        raise Exception("Email service unavailable")  # noqa: TRY002, TRY301
    return f"Email sent to {email} for order {order_id}"


@celery_app.task(base=ReliableTask)
def send_order_sms(order_id: int, phone: str) -> str:
    """Отправка SMS клиенту о заказе (имитация — time.sleep(1))."""
    time.sleep(1)
    return f"SMS sent to {phone} for order {order_id}"


@celery_app.task(base=ReliableTask)
def collect_daily_order_stats() -> dict:
    """
    Периодическая задача (Celery Beat): статистика по заказам за день.

    Результат пишется в Redis ключом stats:orders:<YYYY-MM-DD> с TTL
    7 дней. Для подсчёта открывается короткоживущая сессия SQLAlchemy —
    воркер живёт долго, а соединения к БД нельзя держать открытыми
    между задачами (пул воркера не управляет этим lifecycle).
    """
    from datetime import datetime, timezone

    from sqlalchemy import func

    from app.database import SessionLocal
    from app.models import Order

    today = datetime.now(timezone.utc).date().isoformat()
    with SessionLocal() as db:
        total = db.query(func.count(Order.id)).scalar() or 0

    key = f"stats:orders:{today}"
    try:
        redis_client.set(key, str(total))
        redis_client.expire(key, 7 * 86400)
    except redis_lib.RedisError:
        logger.warning("Redis недоступен, статистика не сохранена", exc_info=True)

    return {"date": today, "total_orders": total}
