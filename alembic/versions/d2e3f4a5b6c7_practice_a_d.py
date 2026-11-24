"""Practice A/D: promotions, loyalty ledger and Stripe test payments."""
from alembic import op
import sqlalchemy as sa

revision = "d2e3f4a5b6c7"
down_revision = "c1d2e3f4a5b6"
branch_labels = None
depends_on = None


def upgrade():
    # Старые БД создавались и через create_all, и через миграции. Приводим
    # существующие индексы к ORM без конфликтов для обоих вариантов.
    for table in ("users", "menu_items", "menu_categories", "orders", "deliveries"):
        op.execute(sa.text(f"CREATE INDEX IF NOT EXISTS ix_{table}_id ON {table} (id)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_users_role_id ON users (role_id)"))
    op.alter_column("orders", "charged_amount", type_=sa.Numeric(12, 2), existing_type=sa.Float(),
                    postgresql_using="round(charged_amount::numeric, 2)")
    op.add_column("orders", sa.Column("payment_method", sa.String(16), nullable=False, server_default="balance"))
    op.add_column("orders", sa.Column("original_amount", sa.Numeric(12, 2)))
    op.add_column("orders", sa.Column("discount_amount", sa.Numeric(12, 2), nullable=False, server_default="0"))
    op.add_column("orders", sa.Column("points_spent", sa.Numeric(12, 2), nullable=False, server_default="0"))
    op.create_table(
        "promo_codes", sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(64), unique=True, nullable=False),
        sa.Column("discount_type", sa.String(16), nullable=False),
        sa.Column("discount_value", sa.Numeric(12, 2), nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("max_uses", sa.Integer(), nullable=False),
        sa.Column("current_uses", sa.Integer(), nullable=False),
        sa.Column("min_order_amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("once_per_user", sa.Boolean(), nullable=False),
        sa.CheckConstraint("discount_type IN ('percent', 'fixed')"),
        sa.CheckConstraint("discount_value > 0 AND (discount_type != 'percent' OR discount_value <= 100)"),
        sa.CheckConstraint("max_uses > 0 AND current_uses >= 0 AND current_uses <= max_uses"),
        sa.CheckConstraint("min_order_amount >= 0 AND valid_until > valid_from"),
    )
    op.create_table(
        "promo_code_usages", sa.Column("order_id", sa.Integer(), sa.ForeignKey("orders.id"), primary_key=True),
        sa.Column("promo_id", sa.Integer(), sa.ForeignKey("promo_codes.id"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
    )
    for column in ("promo_id", "user_id"):
        op.create_index(f"ix_promo_code_usages_{column}", "promo_code_usages", [column])
    op.create_table(
        "loyalty_accounts", sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), unique=True, nullable=False),
        sa.Column("balance", sa.Numeric(12, 2), nullable=False),
        sa.Column("total_earned", sa.Numeric(12, 2), nullable=False),
        sa.Column("tier", sa.String(8), nullable=False),
        sa.CheckConstraint("tier IN ('Bronze', 'Silver', 'Gold')"),
    )
    op.create_table(
        "loyalty_transactions", sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("account_id", sa.Integer(), sa.ForeignKey("loyalty_accounts.id"), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False), sa.Column("type", sa.String(8), nullable=False),
        sa.Column("order_id", sa.Integer(), sa.ForeignKey("orders.id"), nullable=False),
        sa.Column("idempotency_key", sa.String(100), nullable=False, unique=True),
        sa.CheckConstraint("type IN ('earn', 'spend', 'refund', 'revoke')"),
    )
    op.create_index("ix_loyalty_transactions_account_id", "loyalty_transactions", ["account_id"])
    op.create_table(
        "promo_compensations", sa.Column("compensation_key", sa.String(100), primary_key=True),
        sa.Column("order_id", sa.Integer(), sa.ForeignKey("orders.id"), nullable=False, unique=True),
    )
    op.create_table(
        "payments", sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("order_id", sa.Integer(), sa.ForeignKey("orders.id"), nullable=False, unique=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False), sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("gateway_payment_id", sa.String(128), unique=True),
        sa.Column("idempotency_key", sa.String(128), unique=True, nullable=False),
        sa.Column("client_secret", sa.String(255)), sa.Column("error_code", sa.String(64)),
        sa.CheckConstraint("amount > 0"),
    )
    op.create_table(
        "payment_webhooks", sa.Column("event_id", sa.String(128), primary_key=True),
        sa.Column("payment_id", sa.Integer(), sa.ForeignKey("payments.id"), nullable=False),
    )


def downgrade():
    for table in ("payment_webhooks", "payments", "promo_compensations", "loyalty_transactions",
                  "loyalty_accounts", "promo_code_usages", "promo_codes"):
        op.drop_table(table)
    for column in ("points_spent", "discount_amount", "original_amount", "payment_method"):
        op.drop_column("orders", column)
    op.alter_column("orders", "charged_amount", type_=sa.Float(), existing_type=sa.Numeric(12, 2))
