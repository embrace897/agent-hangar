"""Conectores (MCP): transporte, autenticação pelo cofre, escopo, status, ferramentas aprovadas e cache de ícones.

Revision ID: 0041_connectors
Revises: 0040_model_profiles
"""
import sqlalchemy as sa
from alembic import op

revision = "0041_connectors"
down_revision = "0040_model_profiles"
branch_labels = None
depends_on = None

S = sa.String
TZ = sa.DateTime(timezone=True)


def upgrade():
    with op.batch_alter_table("mcp_servers") as b:
        b.add_column(sa.Column("source", S(12), nullable=False, server_default="manual"))
        b.add_column(sa.Column("upstream_url", S(500), nullable=False, server_default=""))
        b.add_column(sa.Column("transport", S(8), nullable=False, server_default="http"))
        b.add_column(sa.Column("auth", S(8), nullable=False, server_default="none"))
        b.add_column(sa.Column("headers", sa.JSON, nullable=True))
        b.add_column(sa.Column("timeout_s", sa.Integer, nullable=False, server_default="60"))
        b.add_column(sa.Column("icon", S(300), nullable=False, server_default=""))
        b.add_column(sa.Column("category", S(40), nullable=False, server_default=""))
        b.add_column(sa.Column("team_id", sa.Integer, nullable=True))
        b.add_column(sa.Column("owner_user_id", sa.Integer, nullable=True))
        b.add_column(sa.Column("status", S(10), nullable=False, server_default=""))
        b.add_column(sa.Column("status_detail", sa.Text, nullable=False, server_default=""))
        b.add_column(sa.Column("checked_at", TZ, nullable=True))
        b.add_column(sa.Column("server_info", sa.JSON, nullable=True))
        b.add_column(sa.Column("tools", sa.JSON, nullable=True))
        b.add_column(sa.Column("created_by", S(254), nullable=False, server_default=""))
    op.create_table(
        "icon_cache", sa.Column("key", S(200), primary_key=True),
        sa.Column("content_type", S(60), nullable=False, server_default="image/png"),
        sa.Column("data", sa.LargeBinary, nullable=False), sa.Column("fetched_at", TZ, nullable=True),
    )


def downgrade():
    op.drop_table("icon_cache")
    with op.batch_alter_table("mcp_servers") as b:
        for c in ("created_by", "tools", "server_info", "checked_at", "status_detail", "status", "owner_user_id", "team_id",
                  "category", "icon", "timeout_s", "headers", "auth", "transport", "upstream_url", "source"):
            b.drop_column(c)
