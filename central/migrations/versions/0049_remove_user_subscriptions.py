"""Remove a assinatura pessoal nos jobs de harness (0048): os agentes com harness voltam a rodar sempre pela conexão de
API. Apaga as assinaturas guardadas, as colunas de cobrança dos jobs, `harness.billing`/`subscription_model` das specs
e a chave `subscriptions` da política de acesso das empresas.

Revision ID: 0049_remove_user_subscriptions
Revises: 0048_user_subscriptions
"""
import json

import sqlalchemy as sa
from alembic import op

revision = "0049_remove_user_subscriptions"
down_revision = "0048_user_subscriptions"
branch_labels = None
depends_on = None


def _load(v):
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return None
    return v


def upgrade():
    bind = op.get_bind()
    versions = sa.table("agent_versions", sa.column("id", sa.Integer), sa.column("spec", sa.JSON))
    for vid, spec in bind.execute(sa.select(versions.c.id, versions.c.spec)).fetchall():
        spec = _load(spec)
        h = spec.get("harness") if isinstance(spec, dict) else None
        if isinstance(h, dict) and ("billing" in h or "subscription_model" in h):
            h.pop("billing", None)
            h.pop("subscription_model", None)
            bind.execute(versions.update().where(versions.c.id == vid).values(spec=spec))
    orgs = sa.table("organizations", sa.column("id", sa.Integer), sa.column("access_policy", sa.JSON))
    for oid, pol in bind.execute(sa.select(orgs.c.id, orgs.c.access_policy)).fetchall():
        pol = _load(pol)
        if isinstance(pol, dict) and "subscriptions" in pol:
            pol.pop("subscriptions")
            bind.execute(orgs.update().where(orgs.c.id == oid).values(access_policy=pol))
    with op.batch_alter_table("jobs") as b:
        b.drop_index("ix_jobs_subscription_id")
        b.drop_index("ix_jobs_user_id")
        for c in ("billing_note", "api_equiv_cost_usd", "subscription_id", "user_id", "billing"):
            b.drop_column(c)
    op.drop_table("user_subscriptions")


def downgrade():
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
