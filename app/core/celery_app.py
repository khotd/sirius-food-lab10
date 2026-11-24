"""
Фабрика приложения Celery (ЛР № 5: асинхронность и фоновые задачи).

Роли участников:
  * Producer — FastAPI: Service после создания заказа публикует задачи;
  * Broker   — Redis: хранит очередь сообщений между FastAPI и воркером;
  * Worker   — отдельный процесс `celery worker`, выполняет задачи;
  * Backend  — Redis: хранит результаты выполненных задач.

Сериализация — только JSON (без pickle): pickle-полезная нагрузка —
вектор атаки (исполнение произвольного кода при подделке сообщения
в брокере), поэтому она запрещена настройкой accept_content.

Очереди:
  * default — все рабочие задачи (единственная, которую слушает воркер);
  * dlq     — Dead Letter Queue. Неисправимо провалившаяся задача
              (все retry исчерпаны) публикуется хуком on_failure
              базового класса ReliableTask в очередь `dlq` валидным
              сообщением kombu: имя задачи, аргументы, ошибка, время.
              Просмотр: redis-cli lrange dlq 0 -1; разбор — отдельным
              воркером `celery ... worker -Q dlq`. Так «потерянные»
              задачи не пропадают, а разбираются вручную.
"""
import json
import logging
from datetime import datetime, timezone

from celery import Celery
from celery.schedules import crontab
from kombu import Exchange, Queue

from app.core.config import settings

logger = logging.getLogger(__name__)

celery_app = Celery(
    "sirius_food",
    # Broker (очередь сообщений) и Backend (результаты) — один Redis.
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    # Регистрация модулей с задачами: воркер знает, что исполнять.
    include=["app.tasks.orders"],
)

celery_app.conf.update(
    # JSON вместо pickle — безопасно (см. докстринг модуля).
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    # Очереди: только рабочая `default`. Dead Letter Queue НЕ объявляется
    # здесь — иначе каждый воркер потреблял бы `dlq` и «переисполнял»
    # упавшие задачи. Очередь dlq создаётся при первой публикации
    # (declare в ReliableTask.on_failure), наполняется хуком on_failure
    # и разбирается ОТДЕЛЬНЫМ воркером: celery -A app.core.celery_app
    # worker -Q dlq (проверка содержимого: redis-cli lrange dlq 0 -1).
    task_queues=(
        Queue("default", Exchange("default"), routing_key="default"),
    ),
    task_default_queue="default",
    task_default_exchange="default",
    task_default_routing_key="default",
    # Надёжность доставки:
    # * acks_late — подтверждение ПОСЛЕ выполнения: при падении воркера
    #   в середине задачи сообщение вернётся в очередь и будет повторено
    #   другим воркером (а не «съедено» и потеряно);
    # * prefetch=1 — воркер не накапливает очередь про запас, задачи
    #   честно распределяются между воркерами (fair dispatch).
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    broker_connection_retry_on_startup=True,
    # Результаты храним сутки: дольше они не нужны (backend чистится сам).
    result_expires=86400,
)

# Celery Beat (доп. задание 10 баллов): периодические задачи.
# Раз в сутки в 00:05 — статистика заказов за день пишется в Redis
# (ключ stats:orders:<дата>). Для демо можно временно заменить

celery_app.conf.beat_schedule = {
    "daily-order-stats": {
        "task": "app.tasks.orders.collect_daily_order_stats",
        "schedule": 30.0,
    },
}


class ReliableTask(celery_app.Task):
    """
    Базовый класс задач с обработкой Dead Letter.

    on_failure вызывается Celery, когда задача окончательно провалилась
    (все retry исчерпаны). Провалившаяся задача публикуется как
    ВАЛИДНОЕ сообщение kombu в очередь `dlq` (exchange dlq): её тело —
    JSON-запись с именем задачи, аргументами, текстом ошибки и временем.
    Просмотр: redis-cli lrange dlq 0 -1 (очередь Redis — это список).

    ВАЖНО: нельзя писать в список `dlq` сырые записи через redis.rpush —
    kombu хранит очередь именно в этом списке, и воркер, потребляющий
    dlq, упадёт на разборе «чужого» сообщения. Только продюсер Celery.
    Отказ брокера в хуке глушится: диагностика не должна ронять воркер.
    """

    abstract = True

    def on_failure(self, exc, task_id, args, kwargs, einfo) -> None:  # noqa: ANN001
        record = {
            "task": self.name,
            "task_id": task_id,
            "args": list(args),
            "kwargs": kwargs,
            "error": str(exc),
            "time": datetime.now(timezone.utc).isoformat(),
        }
        try:
            with celery_app.producer_or_acquire() as producer:
                producer.publish(
                    json.dumps(record, ensure_ascii=False),
                    exchange="dlq",
                    routing_key="dlq",
                    content_type="application/json",
                    declare=[Queue("dlq", Exchange("dlq"), routing_key="dlq")],
                )
        except Exception:  # noqa: BLE001 — диагностика не роняет воркер
            logger.warning("Не удалось записать dead letter", exc_info=True)
        super().on_failure(exc, task_id, args, kwargs, einfo)
