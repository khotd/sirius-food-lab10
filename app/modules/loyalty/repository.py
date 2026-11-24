from app.models import (User, MenuItem, Order, PromoCode, PromoCodeUsage,
                        PromoCompensation, LoyaltyAccount, LoyaltyTransaction)


class LoyaltyRepository:
    def __init__(self, db):
        self.db = db

    def user(self, user_id):
        return self.db.query(User).filter_by(id=user_id).with_for_update().populate_existing().first()

    def item(self, item_id):
        return self.db.get(MenuItem, item_id)

    def promo(self, code):
        return self.db.query(PromoCode).filter_by(code=code).with_for_update().populate_existing().first()

    def used(self, promo_id, user_id):
        return self.db.query(PromoCodeUsage).filter_by(promo_id=promo_id, user_id=user_id).first()

    def account(self, user_id):
        account = (self.db.query(LoyaltyAccount).filter_by(user_id=user_id)
                   .with_for_update().populate_existing().first())
        if account is None:
            account = LoyaltyAccount(user_id=user_id, balance=0, total_earned=0, tier="Bronze")
            self.db.add(account)
            self.db.flush()
        return account

    def transaction(self, key):
        return self.db.query(LoyaltyTransaction).filter_by(idempotency_key=key).first()

    def record(self, account, order_id, amount, kind, key):
        self.db.add(LoyaltyTransaction(account_id=account.id, order_id=order_id,
                                      amount=amount, type=kind, idempotency_key=key))
        self.db.flush()

    def create(self, **values):
        order = Order(**values)
        self.db.add(order)
        self.db.flush()
        return order

    def use_promo(self, promo, order):
        promo.current_uses += 1
        self.db.add(PromoCodeUsage(order_id=order.id, promo_id=promo.id, user_id=order.user_id))

    def restore_promo(self, order):
        self.user(order.user_id)
        key = f"restore_promo_order_{order.id}"
        if self.db.get(PromoCompensation, key):
            return
        usage = self.db.get(PromoCodeUsage, order.id)
        if usage:
            promo = self.db.query(PromoCode).filter_by(id=usage.promo_id).with_for_update().first()
            promo.current_uses -= 1
            self.db.delete(usage)
        self.db.add(PromoCompensation(compensation_key=key, order_id=order.id))

    def commit(self):
        self.db.commit()

    def create_promo(self, values):
        promo = PromoCode(**values)
        self.db.add(promo)
        self.db.commit()
        return promo

    def history(self, user_id):
        return (self.db.query(LoyaltyTransaction).join(LoyaltyAccount)
                .filter(LoyaltyAccount.user_id == user_id).order_by(LoyaltyTransaction.id).all())
