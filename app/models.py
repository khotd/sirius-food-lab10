"""
Определение ORM-моделей предметной области «Сириус.Еда».
"""
from sqlalchemy import Column, Integer, String, Float, Boolean, ForeignKey, DateTime, JSON, Numeric, CheckConstraint
from sqlalchemy.orm import relationship
from app.database import Base


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, nullable=False)
    phone = Column(String, nullable=True)
    balance = Column(Float, default=0.0)
    is_blocked = Column(Boolean, default=False)
    # bcrypt-хеш пароля. В открытом виде пароль нигде не хранится.
    password_hash = Column(String, nullable=False)
    # Роль пользователя (RBAC): student / cook / courier / admin.
    role_id = Column(Integer, ForeignKey("roles.id"), nullable=False, index=True)

    role = relationship("Role")


class Role(Base):
    """Роль пользователя: student, cook, courier, admin."""
    __tablename__ = "roles"
    id = Column(Integer, primary_key=True)
    name = Column(String(50), unique=True, nullable=False)


class RefreshToken(Base):
    """
    Refresh-токен для обновления access-токена.

    Храним SHA-256-хеш токена, а не сам токен: при утечке БД
    злоумышленник не получит действующие токены.
    """
    __tablename__ = "refresh_tokens"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    token_hash = Column(String, unique=True, nullable=False)
    expires_at = Column(DateTime, nullable=False)
    is_revoked = Column(Boolean, default=False)

    user = relationship("User")


class MenuCategory(Base):
    """Категория меню (кешируется с ключом menu:category:{id})."""
    __tablename__ = "menu_categories"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)


class MenuItem(Base):
    __tablename__ = "menu_items"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    price = Column(Float, nullable=False)
    is_available = Column(Boolean, default=True)
    # Категория необязательна: блюдо без категории видно в общем меню.
    category_id = Column(Integer, ForeignKey("menu_categories.id"), nullable=True)
    category = relationship("MenuCategory")


class Order(Base):
    __tablename__ = "orders"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    item_id = Column(Integer, ForeignKey("menu_items.id"))
    quantity = Column(Integer, nullable=False)
    # Сумма, зарезервированная при создании; цена меню может измениться.
    charged_amount = Column(Numeric(12, 2), nullable=True)
    status = Column(String, default="processing")
    payment_method = Column(String(16), nullable=False, default="balance", server_default="balance")
    original_amount = Column(Numeric(12, 2), nullable=True)
    discount_amount = Column(Numeric(12, 2), nullable=False, default=0, server_default="0")
    points_spent = Column(Numeric(12, 2), nullable=False, default=0, server_default="0")
    user = relationship("User")
    item = relationship("MenuItem")


class Delivery(Base):
    __tablename__ = "deliveries"
    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(Integer, ForeignKey("orders.id"))
    courier_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    status = Column(String, default="pending")
    order = relationship("Order")
    courier = relationship("User")


class KitchenOrder(Base):
    __tablename__ = "kitchen_orders"
    order_id = Column(Integer, ForeignKey("orders.id"), primary_key=True)
    status = Column(String, nullable=False)


class ProcessedEvent(Base):
    __tablename__ = "processed_events"
    consumer = Column(String(32), primary_key=True)
    event_id = Column(String(36), primary_key=True)


class OutboxEvent(Base):
    __tablename__ = "outbox"
    id = Column(Integer, primary_key=True)
    routing_key = Column(String(40), nullable=False)
    payload = Column(JSON, nullable=False)
    correlation_id = Column(String(128), nullable=False)
    published_at = Column(DateTime(timezone=True), index=True)


class PromoCode(Base):
    __tablename__ = "promo_codes"
    id = Column(Integer, primary_key=True)
    code = Column(String(64), unique=True, nullable=False)
    discount_type = Column(String(16), nullable=False)
    discount_value = Column(Numeric(12, 2), nullable=False)
    valid_from = Column(DateTime(timezone=True), nullable=False)
    valid_until = Column(DateTime(timezone=True), nullable=False)
    max_uses = Column(Integer, nullable=False)
    current_uses = Column(Integer, nullable=False, default=0)
    min_order_amount = Column(Numeric(12, 2), nullable=False, default=0)
    is_active = Column(Boolean, nullable=False, default=True)
    once_per_user = Column(Boolean, nullable=False, default=True)
    __table_args__ = (
        CheckConstraint("discount_type IN ('percent', 'fixed')"),
        CheckConstraint("discount_value > 0 AND (discount_type != 'percent' OR discount_value <= 100)"),
        CheckConstraint("max_uses > 0 AND current_uses >= 0 AND current_uses <= max_uses"),
        CheckConstraint("min_order_amount >= 0 AND valid_until > valid_from"),
    )


class PromoCodeUsage(Base):
    __tablename__ = "promo_code_usages"
    order_id = Column(Integer, ForeignKey("orders.id"), primary_key=True)
    promo_id = Column(Integer, ForeignKey("promo_codes.id"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)


class LoyaltyAccount(Base):
    __tablename__ = "loyalty_accounts"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True, nullable=False)
    balance = Column(Numeric(12, 2), nullable=False, default=0)
    total_earned = Column(Numeric(12, 2), nullable=False, default=0)
    tier = Column(String(8), nullable=False, default="Bronze")
    __table_args__ = (CheckConstraint("tier IN ('Bronze', 'Silver', 'Gold')"),)


class LoyaltyTransaction(Base):
    __tablename__ = "loyalty_transactions"
    id = Column(Integer, primary_key=True)
    account_id = Column(Integer, ForeignKey("loyalty_accounts.id"), nullable=False, index=True)
    amount = Column(Numeric(12, 2), nullable=False)
    type = Column(String(8), nullable=False)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=False)
    idempotency_key = Column(String(100), unique=True, nullable=False)
    __table_args__ = (CheckConstraint("type IN ('earn', 'spend', 'refund', 'revoke')"),)


class PromoCompensation(Base):
    __tablename__ = "promo_compensations"
    compensation_key = Column(String(100), primary_key=True)
    order_id = Column(Integer, ForeignKey("orders.id"), unique=True, nullable=False)


class Payment(Base):
    __tablename__ = "payments"
    id = Column(Integer, primary_key=True)
    order_id = Column(Integer, ForeignKey("orders.id"), unique=True, nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    amount = Column(Numeric(12, 2), nullable=False)
    currency = Column(String(3), nullable=False)
    status = Column(String(24), nullable=False, default="pending")
    gateway_payment_id = Column(String(128), unique=True, nullable=True)
    idempotency_key = Column(String(128), unique=True, nullable=False)
    client_secret = Column(String(255), nullable=True)
    error_code = Column(String(64), nullable=True)
    __table_args__ = (CheckConstraint("amount > 0"),)


class PaymentWebhook(Base):
    __tablename__ = "payment_webhooks"
    event_id = Column(String(128), primary_key=True)
    payment_id = Column(Integer, ForeignKey("payments.id"), nullable=False)
