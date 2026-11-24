"""Временный стенд ЛР 10. Никогда не использует DATABASE_URL из .env."""
from contextlib import contextmanager, ExitStack
from pathlib import Path
import os
import socket
import subprocess
import sys
import time

import httpx
from sqlalchemy import create_engine
from testcontainers.community.postgres import PostgresContainer
from testcontainers.community.redis import RedisContainer

ROOT = Path(__file__).resolve().parents[1]


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@contextmanager
def isolated_stack(log_dir):
    """Реальные PostgreSQL 15 и Redis 7, миграции, отдельный процесс API."""
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    with ExitStack() as stack:
        postgres = stack.enter_context(PostgresContainer("postgres:15"))
        redis = stack.enter_context(RedisContainer("redis:7"))
        env = {
            **os.environ,
            "DATABASE_URL": postgres.get_connection_url(),
            "REDIS_URL": f"redis://{redis.get_container_host_ip()}:{redis.get_exposed_port(6379)}/0",
            "SECRET_KEY": "lab10-isolated-test-key",
            "SQL_ECHO": "false",
            "CACHE_ENABLED": "true",
            "DEBUG": "false",
            "PYTHONUTF8": "1",
            "PYTHONUNBUFFERED": "1",
        }
        with (log_dir / "migrations.log").open("w", encoding="utf-8") as log:
            subprocess.run(
                [sys.executable, "-m", "alembic", "upgrade", "head"],
                cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT, check=True,
            )
        engine = create_engine(env["DATABASE_URL"])
        stack.callback(engine.dispose)
        yield_context = {
            "env": env, "engine": engine, "postgres": postgres,
            "log_dir": log_dir, "api": None, "url": None,
        }
        yield yield_context


@contextmanager
def running_api(context, workers=1):
    port = free_port()
    with (context["log_dir"] / "api.log").open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:app",
             "--host", "127.0.0.1", "--port", str(port), "--no-access-log",
             "--workers", str(workers)],
            cwd=ROOT, env=context["env"], stdout=log, stderr=subprocess.STDOUT,
        )
        context.update(api=process, url=f"http://127.0.0.1:{port}")
        try:
            with httpx.Client(timeout=1) as client:
                for _ in range(100):
                    if process.poll() is not None:
                        raise RuntimeError(f"API stopped; see {context['log_dir'] / 'api.log'}")
                    try:
                        response = client.get(context["url"] + "/health")
                        if response.status_code == 200 and response.json()["database"] == "ok":
                            break
                    except httpx.HTTPError:
                        pass
                    time.sleep(0.2)
                else:
                    raise RuntimeError("API startup timeout")
            yield context
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
