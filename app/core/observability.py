"""JSON-события, корреляция HTTP и метрики с ограниченным числом меток."""
import logging
import re
import sys
from time import perf_counter
from uuid import uuid4

import structlog
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from redis.exceptions import RedisError
from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import JSONResponse, Response

from app.core.redis import redis_client

CORRELATION_HEADER = "X-Correlation-ID"
REQUESTS = Counter("sirius_http_requests", "HTTP requests", ["method", "route", "status"])
DURATION = Histogram("sirius_http_request_duration_seconds", "HTTP duration", ["method", "route"])
log = structlog.get_logger()


def setup_logging(service: str) -> None:
    shared = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
    ]

    def add_service(_, __, event_dict):
        event_dict.setdefault("service", service)
        return event_dict

    shared.append(add_service)
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    logging.basicConfig(handlers=[handler], level=logging.INFO, force=True)
    structlog.configure(
        processors=[*shared, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=False,
    )


def correlation_id(value: object) -> str:
    if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", value):
        return value
    return str(uuid4())


class ObservabilityMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["path"] == "/metrics":
            return await self.app(scope, receive, send)
        cid = correlation_id(Headers(scope=scope).get(CORRELATION_HEADER))
        scope.setdefault("state", {})["correlation_id"] = cid
        tokens = structlog.contextvars.bind_contextvars(correlation_id=cid)
        started = perf_counter()
        status = 500
        sent = False

        async def add_header(message):
            nonlocal status, sent
            if message["type"] == "http.response.start":
                status = message["status"]
                MutableHeaders(scope=message)[CORRELATION_HEADER] = cid
                sent = True
            await send(message)

        try:
            await self.app(scope, receive, add_header)
        except Exception:
            log.exception("http_request_failed")
            if sent:
                raise
            await JSONResponse({"detail": "Internal Server Error"}, status_code=500)(
                scope, receive, add_header
            )
        finally:
            duration = perf_counter() - started
            route = getattr(scope.get("route"), "path", "unmatched")
            method = scope["method"]
            metric_method = method if method in {"GET", "POST", "PATCH", "OPTIONS", "HEAD"} else "OTHER"
            REQUESTS.labels(metric_method, route, str(status)).inc()
            DURATION.labels(metric_method, route).observe(duration)
            log.info("http_request", method=method, route=route, status=status,
                     duration_ms=round(duration * 1000, 2))
            structlog.contextvars.reset_contextvars(**tokens)


def metrics_response() -> Response:
    # INFO STATS — счётчики самого Redis. Экспортируем их без подмены нулём при сбое.
    body = generate_latest()
    try:
        stats = redis_client.info("stats")
    except RedisError:
        stats = None
    if stats:
        for field in ("keyspace_hits", "keyspace_misses"):
            name = f"redis_{field}_total"
            body += f"# TYPE {name} counter\n{name} {stats[field]}\n".encode()
    return Response(body, headers={"Content-Type": CONTENT_TYPE_LATEST})
