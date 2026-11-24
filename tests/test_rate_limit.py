"""
Unit-тесты rate limiting (Redis-зависимость, критерий 9 баллов).

Redis подменяется заглушкой с тем же интерфейсом (incr/expire),
поэтому тесты не требуют запущенного Redis.
"""
import os
import unittest

os.environ.setdefault("SECRET_KEY", "test-secret-key-for-pytests-only")

from fastapi import HTTPException

from app.core.rate_limit import rate_limit


class FakeRedis:
    """Заглушка клиента Redis с интерфейсом incr/expire."""

    def __init__(self):
        self.store = {}

    def incr(self, key):
        self.store[key] = self.store.get(key, 0) + 1
        return self.store[key]

    def expire(self, key, window):
        pass


class ConnectionErrorRedis(FakeRedis):
    """Заглушка, имитирующая недоступный Redis (fail-open)."""

    def incr(self, key):
        from redis import ConnectionError
        raise ConnectionError()


class FakeClient:
    host = "203.0.113.7"


class FakeURL:
    path = "/auth/login"


class FakeRequest:
    client = FakeClient()
    url = FakeURL()


class RateLimitTests(unittest.TestCase):
    def make_checker(self, redis_client, max_requests=5, window=60):
        return rate_limit(max_requests=max_requests, window=window,
                          client=redis_client)

    def test_requests_within_limit_pass(self):
        checker = self.make_checker(FakeRedis(), max_requests=5)
        for _ in range(5):
            self.assertIsNone(checker(FakeRequest()))

    def test_sixth_request_is_429(self):
        checker = self.make_checker(FakeRedis(), max_requests=5)
        for _ in range(5):
            checker(FakeRequest())
        with self.assertRaises(HTTPException) as ctx:
            checker(FakeRequest())
        self.assertEqual(ctx.exception.status_code, 429)

    def test_counters_are_scoped_per_ip_and_path(self):
        redis_client = FakeRedis()
        checker = self.make_checker(redis_client, max_requests=3)

        class OtherIpClient:
            host = "198.51.100.9"

        class OtherPathURL:
            path = "/users/register"

        class OtherRequest:
            client = OtherIpClient()
            url = OtherPathURL()

        # Два запроса с другого IP и на другой путь: их счётчик = 2 из 3.
        for _ in range(2):
            checker(OtherRequest())
        # На основном IP/пути счётчик независим — все 3 запроса проходят.
        for _ in range(3):
            checker(FakeRequest())
        with self.assertRaises(HTTPException):
            checker(FakeRequest())

    def test_unavailable_redis_fails_open(self):
        """Redis недоступен → проверка пропускается, а не роняет сервис."""
        checker = self.make_checker(ConnectionErrorRedis())
        self.assertIsNone(checker(FakeRequest()))


if __name__ == "__main__":
    unittest.main()
