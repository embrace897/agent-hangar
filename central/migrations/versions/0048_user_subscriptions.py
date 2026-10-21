"""Assinatura Claude/ChatGPT de uma pessoa para os jobs de harness que ela dispara (user_subscriptions), e quem pagou
cada job (jobs.billing, user_id, subscription_id, api_equiv_cost_usd, billing_note).

Revision ID: 0048_user_subscriptions
Revises: 0047_cross_review
"""
import sqlalchemy as sa
from alembic import op

revision = "0048_user_subscriptions"
down_revision = "0047_cross_review"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "user_subscriptions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("org_id", sa.Integer(), nullable=False, server_default="1", index=True),
        sa.Column("user_id", sa.Integer(), nullable=False, index=True),
        sa.Column("vendor", sa.String(20), nullable=False),
        sa.Column("credential", sa.Text(), nullable=False, server_default=""),
        sa.Column("hint", sa.String(20), nullable=False, server_default=""),
        sa.Column("label", sa.String(120), nullable=False, server_default=""),
        sa.Column("status", sa.String(12), nullable=False, server_default="active"),
        sa.Column("limited_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=False, server_default=""),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("org_id", "user_id", "vendor", name="uq_user_subscription"),
    )
    with op.batch_alter_table("jobs") as b:
        b.add_column(sa.Column("billing", sa.String(20), nullable=False, server_default="api"))
        b.add_column(sa.Column("user_id", sa.Integer(), nullable=True))
        b.add_column(sa.Column("subscription_id", sa.Integer(), nullable=True))
        b.add_column(sa.Column("api_equiv_cost_usd", sa.Float(), nullable=False, server_default="0"))
        b.add_column(sa.Column("billing_note", sa.Text(), nullable=False, server_default=""))
        b.create_index("ix_jobs_user_id", ["user_id"])
        b.create_index("ix_jobs_subscription_id", ["subscription_id"])


def downgrade():
    with op.batch_alter_table("jobs") as b:
        b.drop_index("ix_jobs_subscription_id")
        b.drop_index("ix_jobs_user_id")
        for c in ("billing_note", "api_equiv_cost_usd", "subscription_id", "user_id", "billing"):
            b.drop_column(c)
    op.drop_table("user_subscriptions")
