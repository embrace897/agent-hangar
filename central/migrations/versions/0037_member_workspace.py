"""Workspace do membro: sessões com aparelho e último acesso, política de acesso, códigos de ativação, agentes
pessoais e as inscrições de notificação do navegador (Web Push).

Revision ID: 0037_member_workspace
Revises: 0036_project_autonomy
"""
import sqlalchemy as sa
from alembic import op

revision = "0037_member_workspace"
down_revision = "0036_project_autonomy"
branch_labels = None
depends_on = None

S = sa.String
TZ = sa.DateTime(timezone=True)


def upgrade():
    with op.batch_alter_table("user_sessions") as b:
        b.add_column(sa.Column("method", S(12), nullable=False, server_default=""))
        b.add_column(sa.Column("user_agent", S(300), nullable=False, server_default=""))
        b.add_column(sa.Column("ip", S(64), nullable=False, server_default=""))
        b.add_column(sa.Column("last_seen_at", TZ, nullable=True))
    with op.batch_alter_table("organizations") as b:
        b.add_column(sa.Column("access_policy", sa.JSON, nullable=True))
    with op.batch_alter_table("agents") as b:
        b.add_column(sa.Column("personal_owner_id", sa.Integer, nullable=True))
        b.create_index("ix_agents_personal_owner_id", ["personal_owner_id"])
    op.create_table(
        "activation_codes", sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("org_id", sa.Integer, nullable=False, server_default="1"),
        sa.Column("user_id", sa.Integer, nullable=False), sa.Column("code_hash", S(64), nullable=False),
        sa.Column("created_by", S(254), nullable=False, server_default=""), sa.Column("expires_at", TZ, nullable=False),
        sa.Column("used_at", TZ, nullable=True), sa.Column("created_at", TZ, nullable=True),
    )
    op.create_index("ix_activation_codes_org_id", "activation_codes", ["org_id"])
    op.create_index("ix_activation_codes_user_id", "activation_codes", ["user_id"])
    op.create_index("ix_activation_codes_code_hash", "activation_codes", ["code_hash"], unique=True)
    op.create_table(
        "push_subscriptions", sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("org_id", sa.Integer, nullable=False, server_default="1"),
        sa.Column("user_id", sa.Integer, nullable=False), sa.Column("endpoint_hash", S(64), nullable=False),
        sa.Column("endpoint", sa.Text, nullable=False, server_default=""), sa.Column("p256dh", S(200), nullable=False, server_default=""),
        sa.Column("auth", S(300), nullable=False, server_default=""), sa.Column("user_agent", S(300), nullable=False, server_default=""),
        sa.Column("created_at", TZ, nullable=True), sa.Column("last_sent_at", TZ, nullable=True),
    )
    for c in ("org_id", "user_id", "endpoint_hash"):
        op.create_index(f"ix_push_subscriptions_{c}", "push_subscriptions", [c])


def downgrade():
    op.drop_table("push_subscriptions")
    op.drop_table("activation_codes")
    with op.batch_alter_table("agents") as b:
        b.drop_index("ix_agents_personal_owner_id")
        b.drop_column("personal_owner_id")
    with op.batch_alter_table("organizations") as b:
        b.drop_column("access_policy")
    with op.batch_alter_table("user_sessions") as b:
        for c in ("last_seen_at", "ip", "user_agent", "method"):
            b.drop_column(c)
