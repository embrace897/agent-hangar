"""Conectores stdio (npx/uvx): o comando do servidor local que roda num container isolado.

Revision ID: 0043_connector_stdio
Revises: 0042_skill_files
"""
import sqlalchemy as sa
from alembic import op

revision = "0043_connector_stdio"
down_revision = "0042_skill_files"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("mcp_servers") as b:
        b.add_column(sa.Column("stdio", sa.JSON, nullable=True))


def downgrade():
    with op.batch_alter_table("mcp_servers") as b:
        b.drop_column("stdio")
