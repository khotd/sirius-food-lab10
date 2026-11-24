"""Baseline schema used before the RBAC migration."""
from alembic import op
import sqlalchemy as sa

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email", sa.String(), nullable=False, unique=True),
        sa.Column("phone", sa.String()),
        sa.Column("balance", sa.Float()),
        sa.Column("is_blocked", sa.Boolean()),
    )
    op.create_table(
        "menu_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("price", sa.Float(), nullable=False),
        sa.Column("is_available", sa.Boolean()),
    )
    op.create_table(
        "orders",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id")),
        sa.Column("item_id", sa.Integer(), sa.ForeignKey("menu_items.id")),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("status", sa.String()),
    )
    op.create_table(
        "deliveries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("order_id", sa.Integer(), sa.ForeignKey("orders.id")),
        sa.Column("courier_id", sa.Integer(), sa.ForeignKey("users.id")),
        sa.Column("status", sa.String()),
    )


def downgrade():
    for name in ("deliveries", "orders", "menu_items", "users"):
        op.drop_table(name)
