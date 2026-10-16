"""Issues nunca são apagadas: o membro tira da lista dele (removed_at); a visão da empresa (admin) continua vendo.

Revision ID: 0045_issue_removed
Revises: 0044_execution_trials
"""
import sqlalchemy as sa
from alembic import op

revision = "0045_issue_removed"
down_revision = "0044_execution_trials"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("issues") as b:
        b.add_column(sa.Column("removed_at", sa.DateTime(timezone=True), nullable=True))
        b.add_column(sa.Column("removed_by", sa.String(254), nullable=False, server_default=""))


def downgrade():
    with op.batch_alter_table("issues") as b:
        b.drop_column("removed_by")
        b.drop_column("removed_at")
