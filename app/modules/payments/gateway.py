"""Адаптер Stripe test mode. Не принимает и не хранит реквизиты карты."""
import hashlib
import hmac
import json
import random
import time

import httpx

from app.core.config import settings
from app.modules.payments.errors import GatewayError


class StripeGateway:
    def __init__(self, client=None, sleep=time.sleep, jitter=None):
        self.client = client
        self.sleep = sleep
        self.jitter = jitter or (lambda: random.uniform(0, 0.25))

    def create(self, order_id, amount, currency, key):
        if not settings.STRIPE_SECRET_KEY.startswith("sk_test_"):
            raise GatewayError("gateway_not_configured")
        payload = {"amount": str(int(amount * 100)), "currency": currency,
                   "payment_method_types[]": "card", "metadata[order_id]": str(order_id),
                   "metadata[idempotency_key]": key}
        # Пять попыток, между ними 1/2/4/8 секунд и jitter; ключ неизменен.
        with httpx.Client(timeout=5) if self.client is None else _Borrowed(self.client) as client:
            for attempt in range(5):
                try:
                    response = client.post("https://api.stripe.com/v1/payment_intents",
                                           data=payload, auth=(settings.STRIPE_SECRET_KEY, ""),
                                           headers={"Idempotency-Key": key})
                    if response.status_code < 500 and response.status_code != 429:
                        data = response.json()
                        if response.is_error:
                            error = data.get("error", {})
                            code = error.get("decline_code") or error.get("code") or "payment_failed"
                            raise GatewayError(code if code in ("insufficient_funds", "card_declined") else "payment_failed")
                        if data.get("livemode") is not False or not data.get("id"):
                            raise GatewayError("invalid_gateway_response")
                        return data
                    failure = "gateway_unavailable"
                except httpx.TimeoutException:
                    failure = "timeout"
                except httpx.TransportError:
                    failure = "network_error"
                except (ValueError, KeyError):
                    raise GatewayError("invalid_gateway_response")
                if attempt < 4:
                    self.sleep(2 ** attempt + self.jitter())
            raise GatewayError(failure)

    def verify_webhook(self, body: bytes, signature: str):
        secret = settings.STRIPE_WEBHOOK_SECRET
        if not secret:
            raise GatewayError("webhook_not_configured")
        try:
            parts = [part.split("=", 1) for part in signature.split(",")]
            timestamp = next(value for name, value in parts if name == "t")
            if abs(time.time() - int(timestamp)) > 300:
                raise ValueError("expired signature")
            expected = hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()
            if not any(hmac.compare_digest(expected, value) for name, value in parts if name == "v1"):
                raise ValueError("invalid signature")
            event = json.loads(body)
            if not isinstance(event, dict) or event.get("livemode") is not False:
                raise ValueError("test events required")
            return event
        except (ValueError, TypeError, StopIteration) as error:
            raise GatewayError("invalid_webhook") from error


class _Borrowed:
    def __init__(self, client):
        self.client = client

    def __enter__(self):
        return self.client

    def __exit__(self, *args):
        return False
