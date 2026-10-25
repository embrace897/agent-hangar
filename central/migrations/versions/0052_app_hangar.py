"""App Hangar (desktop) conectado à plataforma: escopos OAuth (`app` e `mcp`) no app registrado e na autorização,
software_id/software_version do registro dinâmico e os dispositivos (app_devices). Autorizações antigas ficam `mcp`.

Revision ID: 0052_app_hangar
Revises: 0051_agent_avatar
"""
import sqlalchemy as sa
from alembic import op

revision = "0052_app_hangar"
down_revision = "0051_agent_avatar"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("oauth_clients") as b:
        b.add_column(sa.Column("scope", sa.String(100), nullable=False, server_default="mcp"))
        b.add_column(sa.Column("software_id", sa.String(120), nullable=False, server_default=""))
        b.add_column(sa.Column("software_version", sa.String(60), nullable=False, server_default=""))
    with op.batch_alter_table("oauth_grants") as b:
        b.add_column(sa.Column("scope", sa.String(100), nullable=False, server_default="mcp"))
    op.create_table(
        "app_devices",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("org_id", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("device_id", sa.String(64), nullable=False),
        sa.Column("name", sa.String(120), nullable=False, server_default=""),
        sa.Column("os", sa.String(40), nullable=False, server_default=""),
        sa.Column("os_version", sa.String(60), nullable=False, server_default=""),
        sa.Column("app_version", sa.String(40), nullable=False, server_default=""),
        sa.Column("channel", sa.String(20), nullable=False, server_default=""),
        sa.Column("grant_id", sa.Integer(), sa.ForeignKey("oauth_grants.id"), nullable=True),
        sa.Column("status", sa.String(10), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_by", sa.String(254), nullable=False, server_default=""),
        sa.UniqueConstraint("user_id", "device_id", name="uq_app_device_user"),
    )
    op.create_index("ix_app_devices_org_id", "app_devices", ["org_id"])
    op.create_index("ix_app_devices_user_id", "app_devices", ["user_id"])
    op.create_index("ix_app_devices_grant_id", "app_devices", ["grant_id"])


def downgrade():
    op.drop_index("ix_app_devices_grant_id", "app_devices")
    op.drop_index("ix_app_devices_user_id", "app_devices")
    op.drop_index("ix_app_devices_org_id", "app_devices")
    op.drop_table("app_devices")
    with op.batch_alter_table("oauth_grants") as b:
        b.drop_column("scope")
    with op.batch_alter_table("oauth_clients") as b:
        b.drop_column("software_version")
        b.drop_column("software_id")
        b.drop_column("scope")
