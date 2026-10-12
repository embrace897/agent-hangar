"""Modo maximizador por projeto (mais rodadas e passos, nova tentativa, delegação, relatório).

Revision ID: 0036_project_autonomy
Revises: 0035_org_learning
"""
import sqlalchemy as sa
from alembic import op

revision = "0036_project_autonomy"
down_revision = "0035_org_learning"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("projects") as b:
        b.add_column(sa.Column("autonomy", sa.JSON, nullable=True))


def downgrade():
    with op.batch_alter_table("projects") as b:
        b.drop_column("autonomy")
