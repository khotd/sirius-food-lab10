"""Persist order charges, consumer progress and the transactional outbox."""
from alembic import op
import sqlalchemy as sa

revision = "c1d2e3f4a5b6"
down_revision = "b7c8d9e0f1a2"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("orders", sa.Column("charged_amount", sa.Float(), nullable=True))
    op.create_table(
        "kitchen_orders",
        sa.Column("order_id", sa.Integer(), sa.ForeignKey("orders.id"), primary_key=True),
        sa.Column("status", sa.String(), nullable=False),
    )
    op.create_table(
        "processed_events",
        sa.Column("consumer", sa.String(32), primary_key=True),
        sa.Column("event_id", sa.String(36), primary_key=True),
    )
    op.create_table(
        "outbox",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("routing_key", sa.String(40), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("correlation_id", sa.String(128), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_outbox_published_at", "outbox", ["published_at"])


def downgrade():
    op.drop_table("outbox")
    op.drop_table("processed_events")
    op.drop_table("kitchen_orders")
    op.drop_column("orders", "charged_amount")
