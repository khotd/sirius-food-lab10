"""Правила скидок и лояльности; репозиторий задаёт границу транзакции."""
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Protocol

from app.core.exceptions import BusinessRuleError, NotFoundError


def money(value) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def tier_for(total) -> str:
    return "Gold" if total >= 3000 else "Silver" if total >= 1000 else "Bronze"


class LoyaltyRepositoryProtocol(Protocol):
    def user(self, user_id): ...
    def item(self, item_id): ...
    def promo(self, code): ...
    def used(self, promo_id, user_id): ...
    def account(self, user_id): ...
    def transaction(self, key): ...
    def record(self, account, order_id, amount, kind, key): ...
    def create(self, **values): ...
    def use_promo(self, promo, order): ...
    def restore_promo(self, order): ...
    def commit(self): ...


class LoyaltyService:
    def __init__(self, repository: LoyaltyRepositoryProtocol):
        self.repository = repository

    def create_order(self, user_id, item_id, quantity, promo_code=None,
                     points=Decimal("0"), payment_method="balance"):
        repo = self.repository
        if quantity <= 0 or payment_method not in ("balance", "external"):
            raise BusinessRuleError("Некорректное количество или способ оплаты")
        user = repo.user(user_id)  # Все операции счёта сериализуются по пользователю.
        item = repo.item(item_id)
        if not user or not item:
            raise NotFoundError("Пользователь или блюдо не найдены")
        if user.is_blocked or not item.is_available:
            raise BusinessRuleError("Пользователь заблокирован или блюдо недоступно")
        original = money(item.price) * quantity
        if not Decimal("0") < original <= Decimal("9999999999.99"):
            raise BusinessRuleError("Сумма заказа выходит за допустимые границы")
        discount = Decimal("0.00")
        promo = None
        if promo_code:
            promo = repo.promo(promo_code.strip().upper())
            now = datetime.now(timezone.utc)
            if not promo or not promo.is_active:
                raise BusinessRuleError("Промокод не найден или неактивен")
            start = promo.valid_from.replace(tzinfo=timezone.utc) if promo.valid_from.tzinfo is None else promo.valid_from
            end = promo.valid_until.replace(tzinfo=timezone.utc) if promo.valid_until.tzinfo is None else promo.valid_until
            if not start <= now < end:
                raise BusinessRuleError("Промокод ещё не действует или истёк")
            if promo.current_uses >= promo.max_uses:
                raise BusinessRuleError("Промокод исчерпан")
            if original < promo.min_order_amount:
                raise BusinessRuleError("Сумма заказа меньше минимальной для промокода")
            if promo.once_per_user and repo.used(promo.id, user_id):
                raise BusinessRuleError("Вы уже использовали этот промокод")
            discount = min(original, money(original * promo.discount_value / 100)
                           if promo.discount_type == "percent" else money(promo.discount_value))
        subtotal = original - discount
        points = money(points)
        account = repo.account(user_id)
        if points < 0 or points > subtotal / 2:
            raise BusinessRuleError("Баллами можно оплатить не более 50% суммы после скидки")
        if points > max(Decimal("0"), account.balance):
            raise BusinessRuleError("Недостаточно баллов")
        amount = subtotal - points
        if payment_method == "external" and amount <= 0:
            raise BusinessRuleError("Для внешнего платежа сумма должна быть больше нуля")
        if payment_method == "balance":
            if money(user.balance) < amount:
                raise BusinessRuleError("Недостаточно средств на балансе студента")
            user.balance = float(money(user.balance) - amount)
        order = repo.create(user_id=user_id, item_id=item_id, quantity=quantity,
                            charged_amount=amount, original_amount=original,
                            discount_amount=discount, points_spent=points,
                            payment_method=payment_method, status="processing")
        if promo:
            repo.use_promo(promo, order)
        if points:
            account.balance -= points
            repo.record(account, order.id, -points, "spend", f"spend_order_{order.id}")
        repo.commit()
        return order.id

    def earn(self, order):
        repo = self.repository
        repo.user(order.user_id)
        key = f"earn_order_{order.id}"
        if order.status not in ("paid", "delivered") or repo.transaction(key):
            return
        account = repo.account(order.user_id)
        rate = {"Bronze": Decimal("0.03"), "Silver": Decimal("0.05"), "Gold": Decimal("0.07")}[account.tier]
        amount = money(money(order.charged_amount) * rate)
        account.balance += amount
        account.total_earned += amount
        account.tier = tier_for(account.total_earned)
        repo.record(account, order.id, amount, "earn", key)

    def compensate(self, order):
        repo = self.repository
        repo.user(order.user_id)
        account = repo.account(order.user_id)
        for original, kind, key in (
            (f"spend_order_{order.id}", "refund", f"refund_order_{order.id}"),
            (f"earn_order_{order.id}", "revoke", f"revoke_earn_order_{order.id}"),
        ):
            entry = repo.transaction(original)
            if entry and not repo.transaction(key):
                amount = -entry.amount
                account.balance += amount
                if kind == "revoke":
                    account.total_earned = max(Decimal("0"), account.total_earned + amount)
                repo.record(account, order.id, amount, kind, key)
        account.tier = tier_for(account.total_earned)

